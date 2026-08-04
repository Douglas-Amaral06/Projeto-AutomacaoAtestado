import json
import zipfile

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


def test_retention_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RETENTION_ENABLED", raising=False)
    assert maintenance.apply_retention() == {"enabled": False, "records": 0, "files": 0}


def test_log_details_remove_sensitive_values():
    sanitized = sanitize_log_details({"conversa": "Nome da pessoa", "cpf": "12345678909", "fila_id": 7})
    assert sanitized["conversa"] == "[DADO PROTEGIDO]"
    assert sanitized["cpf"] == "[DADO PROTEGIDO]"
    assert sanitized["fila_id"] == 7
