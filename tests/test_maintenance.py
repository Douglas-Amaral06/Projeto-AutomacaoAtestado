import json
import hashlib
import zipfile

import pytest

from app import maintenance
from app.processing import sanitize_log_details


def test_backup_contains_database_uploads_and_valid_manifest(tmp_path, monkeypatch):
    data = tmp_path / "data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    database = data / "atestados.db"
    import sqlite3
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE exemplo(id INTEGER)")
    (uploads / "documento.pdf").write_bytes(b"%PDF-1.7\n")
    monkeypatch.setattr(maintenance, "DB_PATH", database)
    monkeypatch.setattr(maintenance, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(maintenance, "BACKUP_DIR", tmp_path / "backups")
    backup = maintenance.create_backup()
    maintenance.verify_backup(backup)
    with zipfile.ZipFile(backup) as archive:
        names = set(archive.namelist())
        assert "data/atestados.db" in names
        assert "data/uploads/documento.pdf" in names
        assert json.loads(archive.read("manifesto.json"))["versao"] == 1
    restored = tmp_path / "restore"
    maintenance.extract_verified_backup(backup, restored)
    assert (restored / "data" / "atestados.db").is_file()
    assert (restored / "data" / "uploads" / "documento.pdf").read_bytes() == b"%PDF-1.7\n"


def test_zip_slip_is_rejected_before_any_member_is_extracted(tmp_path):
    backup = tmp_path / "malicious.zip"
    payload = b"nao-deve-sair-do-destino"
    unsafe_name = "../../outside.txt"
    manifest = {
        "versao": 1,
        "arquivos": {unsafe_name: hashlib.sha256(payload).hexdigest()},
    }
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(unsafe_name, payload)
        archive.writestr("manifesto.json", json.dumps(manifest))

    destination = tmp_path / "restore"
    with pytest.raises(RuntimeError, match="inseguro"):
        maintenance.extract_verified_backup(backup, destination)

    assert not (tmp_path / "outside.txt").exists()
    assert not list(destination.rglob("*"))


@pytest.mark.parametrize(
    "name",
    [r"data\uploads\evil.txt", "/data/atestados.db", "data/other.txt", "data/uploads/file:stream"],
)
def test_archive_name_rejects_windows_absolute_and_unauthorized_paths(name):
    with pytest.raises(RuntimeError, match="inseguro"):
        maintenance.validate_archive_name(name)


def test_symbolic_link_entry_is_rejected(tmp_path):
    backup = tmp_path / "symlink.zip"
    link_name = "data/uploads/link.pdf"
    content = b"../../outside.txt"
    manifest = {"versao": 1, "arquivos": {link_name: hashlib.sha256(content).hexdigest()}}
    link = zipfile.ZipInfo(link_name)
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(link, content)
        archive.writestr("manifesto.json", json.dumps(manifest))

    with pytest.raises(RuntimeError, match="entrada especial"):
        maintenance.extract_verified_backup(backup, tmp_path / "restore")


def test_retention_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RETENTION_ENABLED", raising=False)
    assert maintenance.apply_retention() == {"enabled": False, "records": 0, "files": 0}


def test_log_details_remove_sensitive_values():
    sanitized = sanitize_log_details({"conversa": "Nome da pessoa", "cpf": "12345678909", "fila_id": 7})
    assert sanitized["conversa"] == "[DADO PROTEGIDO]"
    assert sanitized["cpf"] == "[DADO PROTEGIDO]"
    assert sanitized["fila_id"] == 7


def test_detect_orphan_files_does_not_remove_them(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    linked = uploads / "linked.pdf"
    orphan = uploads / "orphan.pdf"
    linked.write_bytes(b"linked")
    orphan.write_bytes(b"orphan")
    monkeypatch.setattr(maintenance, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(maintenance, "connect", lambda: _orphan_connection(tmp_path / "orphan.db"))
    with maintenance.connect() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS atestados(arquivo_salvo TEXT)")
        connection.execute("CREATE TABLE IF NOT EXISTS fila_processamento(arquivo_salvo TEXT)")
        connection.execute("INSERT INTO atestados VALUES(?)", (linked.name,))
    result = maintenance.detect_orphan_files()
    assert [item["name"] for item in result] == [orphan.name]
    assert orphan.exists()


def _orphan_connection(path):
    import sqlite3
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection
