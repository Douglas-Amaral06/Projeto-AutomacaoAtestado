import hashlib
import io
import json
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest
from PIL import Image
from starlette.requests import Request

from app import database, main, processing
from app.rate_limit import reset_rate_limits
from app.security import hash_password, hash_token, utc_now


def jpeg_bytes(color=(240, 240, 240)):
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="JPEG")
    return output.getvalue()


def prepare_database(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTENSION_AUTH_REQUIRED", "false")
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


def test_analyst_cannot_call_sensitive_admin_routes(tmp_path, monkeypatch):
    _admin_id, _uploads = prepare_database(tmp_path, monkeypatch)
    raw_session = "sessao-analista-rbac"
    csrf = "csrf-analista-rbac"
    with database.connect() as connection:
        analyst_id = connection.execute(
            "INSERT INTO usuarios(usuario,nome,senha_hash,totp_secret_encrypted,perfil) VALUES(?,?,?,?,?)",
            ("analista", "Pessoa Analista", "hash-teste", "totp-teste", "analista"),
        ).lastrowid
        record_id = connection.execute(
            "INSERT INTO atestados(arquivo_original,arquivo_salvo,status) VALUES(?,?,?)",
            ("atestado.pdf", "atestado.pdf", "pendente"),
        ).lastrowid
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,user_agent_hash,expira_em) VALUES(?,?,?,?,?)",
            (
                analyst_id, hash_token(raw_session), csrf, hash_token("ua:testclient"),
                (utc_now() + timedelta(hours=1)).isoformat(),
            ),
        )

    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.cookies.set("rh_session", raw_session)

    review = client.get(f"/atestados/{record_id}")
    assert review.status_code == 200
    assert "Aprovar e salvar" in review.text
    assert "Excluir atestado" not in review.text
    assert 'href="/relatorios"' not in review.text

    assert client.get("/relatorios").status_code == 403
    assert client.get("/exportar.xlsx").status_code == 403
    assert client.post(
        f"/atestados/{record_id}/excluir", data={"csrf_token": csrf}
    ).status_code == 403
    assert client.post(
        "/fila/999/reprocessar", data={"csrf_token": csrf}
    ).status_code == 403


def test_dashboard_renders_server_side_ui_with_records(tmp_path, monkeypatch):
    user_id, _ = prepare_database(tmp_path, monkeypatch)
    raw_session = "sessao-dashboard"
    csrf = "csrf-dashboard"
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO atestados(
                arquivo_original,arquivo_salvo,status,nome,tipo_documento,
                data_atestado,dias_afastamento,confianca
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "atestado.pdf", "atestado.pdf", "confirmado", "Pessoa Teste",
                "atestado_medico", "2026-08-21", 2, 0.98,
            ),
        )
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,user_agent_hash,expira_em) VALUES(?,?,?,?,?)",
            (
                user_id, hash_token(raw_session), csrf, hash_token("ua:testclient"),
                (utc_now() + timedelta(hours=1)).isoformat(),
            ),
        )

    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.cookies.set("rh_session", raw_session)
    response = client.get("/")

    assert response.status_code == 200
    assert "Pessoa Teste" in response.text
    assert "21/08/2026" in response.text
    assert "Confirmado" in response.text
    assert "UI." not in response.text


def test_dashboard_paginates_large_result_sets(tmp_path, monkeypatch):
    user_id, _uploads = prepare_database(tmp_path, monkeypatch)
    raw_session, csrf = "sessao-paginacao", "csrf-paginacao"
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO atestados(arquivo_original,arquivo_salvo,status,nome) VALUES(?,?,?,?)",
            [(f"{index}.pdf", f"{index}.pdf", "pendente", f"Pessoa {index:03d}") for index in range(1, 61)],
        )
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,user_agent_hash,expira_em) VALUES(?,?,?,?,?)",
            (user_id, hash_token(raw_session), csrf, hash_token("ua:testclient"), (utc_now() + timedelta(hours=1)).isoformat()),
        )
    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.cookies.set("rh_session", raw_session)

    first = client.get("/?page=1&per_page=50")
    second = client.get("/?page=2&per_page=50")
    assert "Pessoa 060" in first.text
    assert "Pessoa 001" not in first.text
    assert "Pessoa 001" in second.text
    assert "Página 1 de 2" in first.text


def test_upload_without_content_length_fails_before_body_parsing(tmp_path, monkeypatch):
    prepare_database(tmp_path, monkeypatch)
    request = Request({
        "type": "http", "method": "POST", "path": "/api/atestados",
        "headers": [], "client": ("127.0.0.1", 1234), "scheme": "http",
        "server": ("127.0.0.1", 8000), "query_string": b"",
    })

    async def should_not_run(_request):
        raise AssertionError("o parser do corpo não deveria ser chamado")

    response = asyncio.run(main.upload_limits(request, should_not_run))
    assert response.status_code == 411


def test_real_queue_flow_delivers_document_and_json_to_fake_storage(tmp_path, monkeypatch):
    _user_id, uploads = prepare_database(tmp_path, monkeypatch)
    monkeypatch.setattr(processing, "UPLOAD_DIR", uploads)
    fake_volume = tmp_path / "fake-volume"
    monkeypatch.setenv("DELIVERY_MODE", "fake")
    monkeypatch.setenv("DELIVERY_FAKE_ROOT", str(fake_volume))
    monkeypatch.setenv("DELIVERY_UNIT", "UNI001")
    monkeypatch.setenv("DELIVERY_WHATSAPP_DESTINATION", "+5511988887777")
    monkeypatch.setattr(processing, "extract_document", lambda _path: {
        "is_atestado": True,
        "tipo_documento": "atestado_medico",
        "motivo_classificacao": "Documento válido",
        "nome": "Pessoa Fictícia",
        "cpf": "52998224725",
        "crm": "00000",
        "crm_uf": "SP",
        "cid": "N39.0",
        "dias_afastamento": 2,
        "data_atestado": "2026-08-18",
        "observacoes": None,
        "confianca": "alta",
        "assinado": True,
        "carimbado": False,
    })
    monkeypatch.setattr(processing, "find_employee", lambda _name, _cpf: (None, "NAO_ENCONTRADO"))
    monkeypatch.setattr(processing, "append_received_document", lambda *_args: {"status": "teste"})

    content = b"%PDF-documento-integracao-ficticio"
    saved = uploads / "documento.pdf"
    saved.write_bytes(content)
    with database.connect() as connection:
        queue_id = connection.execute(
            """INSERT INTO fila_processamento(
                arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status,
                id_mensagem,id_conversa,whatsapp_remetente,data_recebimento
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "a" * 64, "atestado-original.pdf", saved.name, "application/pdf", "processando",
                "messageId-e2e", "5511999990000@c.us", "+5511999990000",
                "2026-08-19T11:44:03-03:00",
            ),
        ).lastrowid

    result = processing.process_queue_item(queue_id)
    documents = list(fake_volume.rglob("*.pdf"))
    json_files = list(fake_volume.rglob("*.json"))

    assert result["status"] == "pendente"
    assert result["id_documento"]
    assert len(documents) == 1
    assert len(json_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert documents[0].stem == json_files[0].stem == payload["id_documento"]
    assert documents[0].read_bytes() == content
    assert payload["origem"]["id_mensagem"] == "messageId-e2e"
    assert payload["arquivo"]["sha256"] == hashlib.sha256(content).hexdigest()
    with database.connect() as connection:
        saved_record = connection.execute(
            "SELECT crm,crm_uf,assinado,carimbado FROM atestados WHERE id=?", (result["id"],)
        ).fetchone()
    assert dict(saved_record) == {"crm": "00000", "crm_uf": "SP", "assinado": 1, "carimbado": 0}


def test_processing_failure_stores_only_correlation_data(tmp_path, monkeypatch):
    _user_id, uploads = prepare_database(tmp_path, monkeypatch)
    monkeypatch.setattr(processing, "UPLOAD_DIR", uploads)
    saved = uploads / "falha.pdf"
    saved.write_bytes(b"%PDF-ficticio")
    secret = "dapi12345678901234567890"
    monkeypatch.setattr(
        processing,
        "extract_document",
        lambda _path: (_ for _ in ()).throw(
            RuntimeError(f"jdbc:databricks://usuario:senha@host?token={secret} CPF 12345678909")
        ),
    )
    with database.connect() as connection:
        queue_id = connection.execute(
            """INSERT INTO fila_processamento(arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status)
               VALUES(?,?,?,?,?)""",
            ("e" * 64, "falha.pdf", saved.name, "application/pdf", "aguardando_retentativa"),
        ).lastrowid

    with pytest.raises(RuntimeError):
        processing.process_queue_item(queue_id)

    with database.connect() as connection:
        queue = connection.execute(
            "SELECT ultimo_erro,erro_amigavel FROM fila_processamento WHERE id=?", (queue_id,)
        ).fetchone()
        log = connection.execute(
            "SELECT mensagem,detalhes FROM logs WHERE evento='processamento_falhou' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    persisted = " ".join((queue["ultimo_erro"], queue["erro_amigavel"], log["mensagem"], log["detalhes"]))
    assert secret not in persisted
    assert "usuario:senha" not in persisted
    assert "12345678909" not in persisted
    assert "Referência:" in persisted
    assert '"error_type": "RuntimeError"' in log["detalhes"]


def test_fastapi_unhandled_error_response_contains_only_correlation_id(tmp_path, monkeypatch):
    prepare_database(tmp_path, monkeypatch)
    secret = "dapi12345678901234567890"
    response = asyncio.run(
        main.unexpected_exception_handler(
            None,
            RuntimeError(f"https://usuario:senha@host?token={secret} CPF 12345678909"),
        )
    )
    body = response.body.decode()

    assert response.status_code == 500
    assert '"codigo":"internal_error"' in body
    assert "Referência" in body
    assert secret not in body
    assert "usuario:senha" not in body
    assert "12345678909" not in body


def test_queue_item_can_only_be_claimed_by_one_server_instance(tmp_path, monkeypatch):
    _user_id, uploads = prepare_database(tmp_path, monkeypatch)
    monkeypatch.setattr(processing, "UPLOAD_DIR", uploads)
    with database.connect() as connection:
        queue_id = connection.execute(
            """INSERT INTO fila_processamento(arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status)
               VALUES(?,?,?,?,?)""",
            ("b" * 64, "falha.pdf", "falha.pdf", "application/pdf", "aguardando_retentativa"),
        ).lastrowid
    item, token = processing._claim_queue_item(queue_id)
    assert item["lock_token"] == token
    try:
        processing._claim_queue_item(queue_id)
        assert False, "a segunda instância não deveria assumir o item"
    except processing.QueueItemBusyError:
        pass


def test_worker_recovers_abandoned_processing_item(tmp_path, monkeypatch):
    prepare_database(tmp_path, monkeypatch)
    with database.connect() as connection:
        queue_id = connection.execute(
            """INSERT INTO fila_processamento(
                   arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status,lock_token,lock_expires_em
               ) VALUES(?,?,?,?,?,?,?)""",
            ("f" * 64, "abandonado.pdf", "abandonado.pdf", "application/pdf", "processando", None, None),
        ).lastrowid
    recovered = []
    monkeypatch.setattr(processing, "process_queue_item", lambda item_id: recovered.append(item_id))

    assert processing.resume_pending_once() == 1
    assert recovered == [queue_id]


def test_lost_queue_lease_cannot_be_renewed(tmp_path, monkeypatch):
    prepare_database(tmp_path, monkeypatch)
    with database.connect() as connection:
        queue_id = connection.execute(
            """INSERT INTO fila_processamento(
                   arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status,lock_token
               ) VALUES(?,?,?,?,?,?)""",
            ("1" * 64, "lease.pdf", "lease.pdf", "application/pdf", "processando", "owner-real"),
        ).lastrowid

    with pytest.raises(processing.QueueItemBusyError):
        processing.renew_queue_lease(queue_id, "owner-incorreto")


def test_failed_extraction_can_be_reprocessed_from_dashboard(tmp_path, monkeypatch):
    user_id, _uploads = prepare_database(tmp_path, monkeypatch)
    raw_session, csrf = "sessao-reprocessar", "csrf-reprocessar"
    with database.connect() as connection:
        queue_id = connection.execute(
            """INSERT INTO fila_processamento(
                arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status,tentativas,ultimo_erro
            ) VALUES(?,?,?,?,?,?,?)""",
            ("c" * 64, "falha.pdf", "falha.pdf", "application/pdf", "falhou", 3, "timeout"),
        ).lastrowid
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,user_agent_hash,expira_em) VALUES(?,?,?,?,?)",
            (user_id, hash_token(raw_session), csrf, hash_token("ua:testclient"), (utc_now() + timedelta(hours=1)).isoformat()),
        )
    processed = []
    monkeypatch.setattr(main, "process_queue_item", lambda item_id: processed.append(item_id) or {"status": "pendente"})
    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.cookies.set("rh_session", raw_session)
    response = client.post(f"/fila/{queue_id}/reprocessar", data={"csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert processed == [queue_id]
    with database.connect() as connection:
        item = connection.execute("SELECT status,tentativas,ultimo_erro FROM fila_processamento WHERE id=?", (queue_id,)).fetchone()
    assert dict(item) == {"status": "aguardando_retentativa", "tentativas": 0, "ultimo_erro": None}


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

    monkeypatch.setenv("EXTENSION_AUTH_REQUIRED", "true")
    assert client.get("/api/extensao/status").status_code == 401

    monkeypatch.setattr(main, "process_queue_item", lambda queue_id: {"id": queue_id, "status": "pendente"})
    uploaded = client.post(
        "/api/atestados",
        headers={"X-API-Token": token},
        files={"file": ("atestado.jpg", jpeg_bytes(), "image/jpeg")},
        data={"id_mensagem": "messageId-teste", "data_recebimento": "2026-08-19T15:22:10-03:00", "unidade": "uni009"},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "pendente"
    with database.connect() as connection:
        queued = connection.execute("SELECT id_mensagem,data_recebimento,unidade FROM fila_processamento").fetchone()
    assert queued["id_mensagem"] == "messageId-teste"
    assert queued["unidade"] == "UNI009"
    assert queued["data_recebimento"] == "2026-08-19T15:22:10-03:00"


def test_pairing_payload_rejects_unknown_fields(tmp_path, monkeypatch):
    prepare_database(tmp_path, monkeypatch)
    client = TestClient(main.app, base_url="http://127.0.0.1")
    response = client.post(
        "/api/parear",
        json={"codigo": "123456", "nome": "Extensão", "campo_inesperado": "valor"},
    )
    assert response.status_code == 422


def test_corrupted_upload_is_rejected_and_removed_before_queue(tmp_path, monkeypatch):
    _user_id, uploads = prepare_database(tmp_path, monkeypatch)
    client = TestClient(main.app, base_url="http://127.0.0.1")
    response = client.post(
        "/api/atestados",
        files={"file": ("corrompido.pdf", b"%PDF-sem-final", "application/pdf")},
        data={"id_mensagem": "corrompido-001"},
    )

    assert response.status_code == 400
    assert not list(uploads.iterdir())
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM fila_processamento").fetchone()[0] == 0


def test_document_above_gemini_input_budget_is_removed(tmp_path, monkeypatch):
    _user_id, uploads = prepare_database(tmp_path, monkeypatch)
    monkeypatch.setenv("GEMINI_MAX_DOCUMENT_MB", "1")
    client = TestClient(main.app, base_url="http://127.0.0.1")
    content = b"%PDF-" + (b"0" * (1024 * 1024)) + b"\n%%EOF"
    response = client.post(
        "/api/atestados",
        files={"file": ("grande.pdf", content, "application/pdf")},
        data={"id_mensagem": "grande-001"},
    )

    assert response.status_code == 413
    assert not list(uploads.iterdir())


def test_upload_rate_limit_blocks_one_token_before_processing(tmp_path, monkeypatch):
    user_id, _uploads = prepare_database(tmp_path, monkeypatch)
    raw_token = "token-limitado-de-teste"
    monkeypatch.setenv("EXTENSION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("UPLOAD_RATE_LIMIT_PER_HOUR", "1")
    reset_rate_limits()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO tokens_servico(nome,token_hash,criado_por,expira_em) VALUES(?,?,?,?)",
            (
                "Extensão limitada", hash_token(raw_token), user_id,
                (utc_now() + timedelta(days=1)).isoformat(),
            ),
        )
    processed = []
    monkeypatch.setattr(
        main,
        "process_queue_item",
        lambda queue_id: processed.append(queue_id) or {"id": queue_id, "status": "pendente"},
    )
    client = TestClient(main.app, base_url="http://127.0.0.1")
    headers = {"X-API-Token": raw_token}

    first = client.post(
        "/api/atestados",
        headers=headers,
        files={"file": ("primeiro.jpg", jpeg_bytes((255, 0, 0)), "image/jpeg")},
        data={"id_mensagem": "rate-001"},
    )
    blocked = client.post(
        "/api/atestados",
        headers=headers,
        files={"file": ("segundo.jpg", jpeg_bytes((0, 255, 0)), "image/jpeg")},
        data={"id_mensagem": "rate-002"},
    )

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert len(processed) == 1


def test_admin_can_generate_extension_pairing_code_without_phone(tmp_path, monkeypatch):
    user_id, _ = prepare_database(tmp_path, monkeypatch)
    raw_session, csrf = "sessao-extensao-2fa", "csrf-extensao-2fa"
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,user_agent_hash,expira_em) VALUES(?,?,?,?,?)",
            (user_id, hash_token(raw_session), csrf, hash_token("ua:testclient"), (utc_now() + timedelta(hours=1)).isoformat()),
        )
    client = TestClient(main.app, base_url="http://127.0.0.1")
    client.cookies.set("rh_session", raw_session)
    allowed = client.post(
        "/extensao/gerar-codigo",
        data={"csrf_token": csrf},
    )
    assert allowed.status_code == 200
    assert re.search(r">\d{6}<", allowed.text)


def test_admin_login_requires_only_valid_user_and_password(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "chave-de-teste-com-mais-de-trinta-e-dois-caracteres")
    user_id, _ = prepare_database(tmp_path, monkeypatch)
    with database.connect() as connection:
        connection.execute(
            "UPDATE usuarios SET senha_hash=? WHERE id=?",
            (hash_password("Senha-Admin-Forte-123!"), user_id),
        )
    client = TestClient(main.app, base_url="http://127.0.0.1")
    accepted = client.post(
        "/login", data={"usuario": "admin", "senha": "Senha-Admin-Forte-123!"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/"
    assert "httponly" in accepted.headers["set-cookie"].lower()
    rejected = TestClient(main.app, base_url="http://127.0.0.1").post(
        "/login", data={"usuario": "admin", "senha": "senha-incorreta"},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert "Credenciais+invalidas" in rejected.headers["location"]


def test_same_binary_is_historical_but_same_message_is_idempotent(tmp_path, monkeypatch):
    prepare_database(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "process_queue_item", lambda queue_id: {"id": queue_id, "status": "pendente"})
    client = TestClient(main.app, base_url="http://127.0.0.1")
    content = jpeg_bytes()

    first = client.post(
        "/api/atestados",
        files={"file": ("atestado.jpg", content, "image/jpeg")},
        data={"id_mensagem": "messageId-001"},
    )
    resend = client.post(
        "/api/atestados",
        files={"file": ("atestado.jpg", content, "image/jpeg")},
        data={"id_mensagem": "messageId-002"},
    )
    repeated_message = client.post(
        "/api/atestados",
        files={"file": ("atestado.jpg", content, "image/jpeg")},
        data={"id_mensagem": "messageId-001"},
    )

    assert first.json()["status"] == "pendente"
    assert resend.json()["status"] == "pendente"
    assert resend.json()["possivel_repeticao"] is True
    assert "reenvio foi aceito" in resend.json()["aviso"]
    assert repeated_message.json()["status"] == "duplicado"
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT arquivo_hash,id_mensagem FROM fila_processamento ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["arquivo_hash"] == rows[1]["arquivo_hash"]
    assert {row["id_mensagem"] for row in rows} == {"messageId-001", "messageId-002"}


def test_two_extensions_can_upload_concurrently_without_overwriting_files(tmp_path, monkeypatch):
    prepare_database(tmp_path, monkeypatch)
    reset_rate_limits()
    monkeypatch.setattr(main, "process_queue_item", lambda queue_id: {"id": queue_id, "status": "pendente"})

    def upload(index):
        client = TestClient(main.app, base_url="http://127.0.0.1")
        return client.post(
            "/api/atestados",
            files={"file": (f"atestado-{index}.jpg", jpeg_bytes((index * 30, 20, 20)), "image/jpeg")},
            data={"id_mensagem": f"concorrente-{index}"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(upload, (1, 2)))

    assert [response.status_code for response in responses] == [200, 200]
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT arquivo_salvo FROM fila_processamento WHERE id_mensagem LIKE 'concorrente-%'"
        ).fetchall()
    assert len(rows) == 2
    assert len({row["arquivo_salvo"] for row in rows}) == 2
    assert all((database.UPLOAD_DIR / row["arquivo_salvo"]).is_file() for row in rows)


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
        version = connection.execute(
            "SELECT criado_em FROM atestados WHERE id=?", (record_id,)
        ).fetchone()[0]
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
    monkeypatch.setattr(main, "find_employee", lambda name, cpf: ({"matricula": "1", "telefone": None, "email": None, "empresa": None}, "ENCONTRADO_CPF_NOME"))
    response = client.post(
        f"/atestados/{record_id}/revisar",
            data={"acao": "aprovar", "csrf_token": csrf, "versao_registro": version, "nome": "Pessoa Teste", "cpf": "529.982.247-25", "tipo_documento": "atestado_medico", "data_atestado": "2026-08-01", "dias_afastamento": "2", "crm": "00123", "crm_uf": "sp", "assinado": "true", "carimbado": "false"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with database.connect() as connection:
        row = connection.execute("SELECT status,revisado_por,crm,crm_uf,assinado,carimbado FROM atestados WHERE id=?", (record_id,)).fetchone()
    assert row["status"] == "confirmado"
    assert row["revisado_por"] == user_id
    assert row["crm"] == "00123"
    assert row["crm_uf"] == "SP"
    assert row["assinado"] == 1
    assert row["carimbado"] == 0
    stale_response = client.post(
        f"/atestados/{record_id}/revisar",
        data={"acao": "rejeitar", "csrf_token": csrf, "versao_registro": version, "motivo_rejeicao": "Tela antiga"},
        follow_redirects=False,
    )
    assert stale_response.status_code == 409


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
            "INSERT INTO atestados(arquivo_original,arquivo_salvo,status,nome,observacoes,dias_afastamento) VALUES(?,?,?,?,?,?)",
            ("teste.pdf", "teste.pdf", "confirmado", "=HYPERLINK(\"https://example.test\")", "  +cmd", 2),
        )
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
    workbook = load_workbook(io.BytesIO(response.content), data_only=False)
    sheet = workbook["Atestados"]
    assert sheet["B2"].value == "'=HYPERLINK(\"https://example.test\")"
    assert sheet["Q2"].value == "'  +cmd"
    assert sheet["M2"].value == 2
    assert sheet["B2"].data_type == "s"
    workbook.close()
