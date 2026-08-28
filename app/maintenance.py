import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path, PurePosixPath

from .database import BASE_DIR, DB_PATH, UPLOAD_DIR, connect
from .security import utc_now


BACKUP_DIR = BASE_DIR / "backups"
_MANIFEST_NAME = "manifesto.json"


def validate_archive_name(name: str) -> None:
    """Aceita somente a estrutura produzida por ``create_backup``."""
    if not name or "\x00" in name or "\\" in name or "//" in name:
        raise RuntimeError("Backup inseguro: nome de arquivo inválido.")
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise RuntimeError(f"Backup inseguro: caminho fora da área permitida: {name}")
    parts = candidate.parts
    authorized = (
        parts == (_MANIFEST_NAME,)
        or parts == ("data", "atestados.db")
        or (
            len(parts) == 3
            and parts[:2] == ("data", "uploads")
            and re.fullmatch(r"[A-Za-z0-9._-]+", parts[2]) is not None
        )
    )
    if not authorized:
        raise RuntimeError(f"Backup inseguro: conteúdo não autorizado: {name}")


def _validate_archive_member(member: zipfile.ZipInfo) -> None:
    validate_archive_name(member.filename)
    unix_type = (member.external_attr >> 16) & 0o170000
    if member.is_dir() or unix_type == 0o120000:
        raise RuntimeError(f"Backup inseguro: entrada especial não permitida: {member.filename}")
    if member.flag_bits & 0x1:
        raise RuntimeError(f"Backup inseguro: entrada criptografada: {member.filename}")


def _verified_manifest(archive: zipfile.ZipFile) -> dict:
    members = archive.infolist()
    names = [member.filename for member in members]
    if len(names) != len(set(names)):
        raise RuntimeError("Backup inseguro: existem nomes duplicados no arquivo.")
    for member in members:
        _validate_archive_member(member)
    try:
        manifest_info = archive.getinfo(_MANIFEST_NAME)
    except KeyError as error:
        raise RuntimeError("Backup inválido: manifesto ausente.") from error
    if manifest_info.file_size > 1024 * 1024:
        raise RuntimeError("Backup inválido: manifesto excede o tamanho permitido.")
    try:
        manifest = json.loads(archive.read(manifest_info))
        declared = manifest["arquivos"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("Backup inválido: manifesto malformado.") from error
    if manifest.get("versao") != 1 or not isinstance(declared, dict):
        raise RuntimeError("Backup inválido: versão ou lista de arquivos inválida.")
    expected_names = set(declared) | {_MANIFEST_NAME}
    if set(names) != expected_names:
        raise RuntimeError("Backup inválido: conteúdo diverge do manifesto.")
    for name, expected_hash in declared.items():
        validate_archive_name(name)
        if name == _MANIFEST_NAME or not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash)):
            raise RuntimeError(f"Backup inválido: hash inválido para {name}")
        digest = hashlib.sha256()
        with archive.open(name, "r") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected_hash:
            raise RuntimeError(f"Backup corrompido: {name}")
    return manifest


def detect_orphan_files() -> list[dict]:
    """Lista anexos presentes em disco que não são referenciados pelo banco."""
    if not UPLOAD_DIR.exists():
        return []
    with connect() as connection:
        referenced = {
            row[0] for row in connection.execute(
                "SELECT arquivo_salvo FROM atestados UNION SELECT arquivo_salvo FROM fila_processamento"
            ).fetchall() if row[0]
        }
    return [
        {"name": path.name, "size": path.stat().st_size, "modified_at": path.stat().st_mtime}
        for path in sorted(UPLOAD_DIR.iterdir())
        if path.is_file() and path.name != ".gitkeep" and path.name not in referenced
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_backup() -> Path:
    """Cria ZIP consistente do banco e anexos, com manifesto verificável."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    destination = BACKUP_DIR / f"atestados-backup-{timestamp}.zip"
    with tempfile.TemporaryDirectory(prefix="rh-backup-") as temp_name:
        temp = Path(temp_name)
        database_copy = temp / "atestados.db"
        source = sqlite3.connect(DB_PATH)
        target = sqlite3.connect(database_copy)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        files = [(database_copy, "data/atestados.db")]
        files.extend((path, f"data/uploads/{path.name}") for path in UPLOAD_DIR.iterdir() if path.is_file())
        manifest = {
            "versao": 1,
            "criado_em": utc_now().isoformat(),
            "arquivos": {archive: _sha256(path) for path, archive in files},
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path, archive_name in files:
                archive.write(path, archive_name)
            archive.writestr("manifesto.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    verify_backup(destination)
    return destination


def verify_backup(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        _verified_manifest(archive)


def extract_verified_backup(path: Path, destination: Path) -> None:
    """Valida integralmente e extrai sem permitir que uma entrada escape do destino."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise RuntimeError("A pasta temporária de restauração precisa estar vazia.")
    with zipfile.ZipFile(path, "r") as archive:
        _verified_manifest(archive)
        for member in archive.infolist():
            target = (destination / PurePosixPath(member.filename)).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError(f"Backup inseguro: tentativa de Zip Slip: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def prune_backups() -> int:
    keep_days = max(1, int(os.getenv("BACKUP_RETENTION_DAYS", "30")))
    cutoff = utc_now() - timedelta(days=keep_days)
    removed = 0
    for path in BACKUP_DIR.glob("atestados-backup-*.zip") if BACKUP_DIR.exists() else []:
        if path.stat().st_mtime < cutoff.timestamp():
            path.unlink()
            removed += 1
    return removed


def apply_retention() -> dict:
    """Remove dados vencidos somente quando RETENTION_ENABLED=true."""
    if os.getenv("RETENTION_ENABLED", "false").lower() != "true":
        return {"enabled": False, "records": 0, "files": 0}
    days = max(1, int(os.getenv("DOCUMENT_RETENTION_DAYS", "365")))
    log_days = max(days, int(os.getenv("LOG_RETENTION_DAYS", "730")))
    cutoff = (utc_now() - timedelta(days=days)).isoformat()
    log_cutoff = (utc_now() - timedelta(days=log_days)).isoformat()
    files_removed = 0
    with connect() as connection:
        expired = connection.execute(
            "SELECT id,arquivo_salvo FROM atestados WHERE status IN ('confirmado','rejeitado') AND COALESCE(revisado_em,criado_em)<?",
            (cutoff,),
        ).fetchall()
        for row in expired:
            path = UPLOAD_DIR / row["arquivo_salvo"]
            if path.is_file():
                path.unlink()
                files_removed += 1
        ids = [row["id"] for row in expired]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            connection.execute(f"DELETE FROM fila_processamento WHERE atestado_id IN ({placeholders})", ids)
            connection.execute(f"DELETE FROM atestados WHERE id IN ({placeholders})", ids)
        connection.execute("DELETE FROM sessoes WHERE expira_em<?", (utc_now().isoformat(),))
        connection.execute("DELETE FROM codigos_pareamento WHERE expira_em<?", (utc_now().isoformat(),))
        connection.execute("DELETE FROM tentativas_login WHERE criado_em<?", (log_cutoff,))
        connection.execute("DELETE FROM logs WHERE criado_em<?", (log_cutoff,))
    return {"enabled": True, "records": len(expired), "files": files_removed}
