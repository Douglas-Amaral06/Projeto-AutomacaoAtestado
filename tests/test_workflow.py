from datetime import timedelta

from fastapi.testclient import TestClient

from app import database, main
from app.security import hash_token, utc_now


def prepare_database(tmp_path, monkeypatch):
    data = tmp_path / "data"
    uploads = data / "uploads"
    monkeypatch.setattr(database, "DATA_DIR", data)
    monkeypatch.setattr(database, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(database, "DB_PATH", data / "atestados.db")
    monkeypatch.setattr(main, "UPLOAD_DIR", uploads)
    database.initialize_database()
    with database.connect() as connection:
        user_id = connection.execute(
            "INSERT INTO usuarios(usuario,nome,senha_hash,totp_secret_encrypted,perfil) VALUES(?,?,?,?,?)",
            ("admin", "Administrador", "hash-teste", "totp-teste", "admin"),
        ).lastrowid
    return user_id, uploads


def test_pairing_is_one_time_and_upload_requires_generated_credential(tmp_path, monkeypatch):
    user_id, _ = prepare_database(tmp_path, monkeypatch)
    code = "482731"
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO codigos_pareamento(codigo_hash,criado_por,expira_em) VALUES(?,?,?)",
            (hash_token(code), user_id, (utc_now() + timedelta(minutes=10)).isoformat()),
        )
    client = TestClient(main.app, base_url="http://127.0.0.1")
    paired = client.post("/api/parear", json={"codigo": code, "nome": "Teste Chrome"})
    assert paired.status_code == 200
    token = paired.json()["token"]
    assert len(token) >= 40
    assert client.post("/api/parear", json={"codigo": code}).status_code == 401
    assert client.post("/api/parear", json={"codigo": "111111"}).status_code == 401
    assert client.post("/api/parear", json={"codigo": "222222"}).status_code == 401
    blocked = client.post("/api/parear", json={"codigo": "333333"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["codigo"] == "pareamento_bloqueado"

    monkeypatch.setattr(main, "process_queue_item", lambda queue_id: {"id": queue_id, "status": "pendente"})
    uploaded = client.post(
        "/api/atestados",
        headers={"X-API-Token": token},
        files={"file": ("atestado.jpg", b"\xff\xd8\xff\xe0imagem-teste", "image/jpeg")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "pendente"


def test_analyst_review_records_user(tmp_path, monkeypatch):
    user_id, uploads = prepare_database(tmp_path, monkeypatch)
    (uploads / "arquivo.jpg").write_bytes(b"\xff\xd8\xff")
    raw_session = "sessao-teste"
    csrf = "csrf-teste"
    with database.connect() as connection:
        record_id = connection.execute(
            "INSERT INTO atestados(arquivo_original,arquivo_salvo,status) VALUES(?,?,?)",
            ("arquivo.jpg", "arquivo.jpg", "pendente"),
        ).lastrowid
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,user_agent_hash,expira_em) VALUES(?,?,?,?,?)",
            (user_id, hash_token(raw_session), csrf, hash_token("ua:testclient"), (utc_now() + timedelta(hours=1)).isoformat()),
        )
    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.cookies.set("rh_session", raw_session)
    file_response = client.get(f"/atestados/{record_id}/arquivo")
    assert file_response.status_code == 200
    assert file_response.headers["x-frame-options"] == "SAMEORIGIN"
    self_disable = client.post(
        f"/usuarios/{user_id}/alternar", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert self_disable.status_code == 400
    response = client.post(
        f"/atestados/{record_id}/revisar",
        data={"acao": "aprovar", "csrf_token": csrf, "nome": "Pessoa Teste", "dias_afastamento": "2"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with database.connect() as connection:
        row = connection.execute("SELECT status,revisado_por FROM atestados WHERE id=?", (record_id,)).fetchone()
    assert row["status"] == "confirmado"
    assert row["revisado_por"] == user_id


def test_delete_removes_record_file_and_duplicate_marker(tmp_path, monkeypatch):
    user_id, uploads = prepare_database(tmp_path, monkeypatch)
    saved = uploads / "teste.jpg"
    saved.write_bytes(b"\xff\xd8\xff")
    raw_session, csrf, digest = "sessao-exclusao", "csrf-exclusao", "hash-arquivo"
    with database.connect() as connection:
        record_id = connection.execute(
            "INSERT INTO atestados(arquivo_original,arquivo_salvo,arquivo_hash,status) VALUES(?,?,?,?)",
            ("teste.jpg", saved.name, digest, "confirmado"),
        ).lastrowid
        connection.execute(
            "INSERT INTO fila_processamento(arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status,atestado_id) VALUES(?,?,?,?,?,?)",
            (digest, "teste.jpg", saved.name, "image/jpeg", "concluido", record_id),
        )
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,user_agent_hash,expira_em) VALUES(?,?,?,?,?)",
            (user_id, hash_token(raw_session), csrf, hash_token("ua:testclient"), (utc_now() + timedelta(hours=1)).isoformat()),
        )
    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.cookies.set("rh_session", raw_session)
    response = client.post(
        f"/atestados/{record_id}/excluir", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    assert not saved.exists()
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM atestados WHERE id=?", (record_id,)).fetchone() is None
        assert connection.execute("SELECT 1 FROM fila_processamento WHERE arquivo_hash=?", (digest,)).fetchone() is None


def test_export_is_streamed_without_creating_sensitive_xlsx(tmp_path, monkeypatch):
    user_id, _ = prepare_database(tmp_path, monkeypatch)
    raw_session, csrf = "sessao-exportacao", "csrf-exportacao"
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,user_agent_hash,expira_em) VALUES(?,?,?,?,?)",
            (user_id, hash_token(raw_session), csrf, hash_token("ua:testclient"), (utc_now() + timedelta(hours=1)).isoformat()),
        )
    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.cookies.set("rh_session", raw_session)
    response = client.get("/exportar.xlsx")
    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    assert not (database.DATA_DIR / "atestados_exportados.xlsx").exists()
