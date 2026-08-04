import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

from .database import BASE_DIR, DB_PATH, UPLOAD_DIR, connect
from .security import utc_now


BACKUP_DIR = BASE_DIR / "backups"


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
        manifest = json.loads(archive.read("manifesto.json"))
        for name, expected in manifest["arquivos"].items():
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != expected:
                raise RuntimeError(f"Backup corrompido: {name}")


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

