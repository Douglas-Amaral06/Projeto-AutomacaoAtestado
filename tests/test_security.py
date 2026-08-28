import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import database
from app.safe_errors import format_safe_error
from app.security import (
    hash_password,
    hash_token,
    permissions_for,
    redact,
    require_permission,
    trusted_client_ip,
    verify_password,
)


def request_from(peer: str, forwarded: str | None = None) -> Request:
    headers = [] if forwarded is None else [(b"cf-connecting-ip", forwarded.encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (peer, 1234)})


def test_cloudflare_header_is_ignored_when_proxy_trust_is_disabled(monkeypatch):
    monkeypatch.setenv("TRUST_CLOUDFLARE", "false")
    assert trusted_client_ip(request_from("192.0.2.10", "8.8.8.8")) == "192.0.2.10"


def test_cloudflare_header_requires_trusted_peer_and_valid_ip(monkeypatch):
    monkeypatch.setenv("TRUST_CLOUDFLARE", "true")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1,::1")
    assert trusted_client_ip(request_from("127.0.0.1", "2001:0db8::1")) == "2001:db8::1"

    with pytest.raises(HTTPException, match="Proxy não autorizado"):
        trusted_client_ip(request_from("192.0.2.10", "8.8.8.8"))
    with pytest.raises(HTTPException, match="Formato de IP inválido"):
        trusted_client_ip(request_from("127.0.0.1", "8.8.8.8, 1.1.1.1"))


def test_rbac_is_fail_closed_and_limits_analyst_permissions():
    analyst = {"perfil": "analista"}
    assert permissions_for(analyst) == frozenset({"review"})
    require_permission(analyst, "review")

    for permission in ("delete", "reprocess", "export", "reports"):
        with pytest.raises(HTTPException) as error:
            require_permission(analyst, permission)
        assert error.value.status_code == 403

    with pytest.raises(HTTPException) as error:
        require_permission({}, "review")
    assert error.value.status_code == 403


def test_rbac_accepts_sqlite_rows_returned_by_current_user(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "security.db")
    database.initialize_database()
    with database.connect() as connection:
        user_id = connection.execute(
            "INSERT INTO usuarios(usuario,nome,senha_hash,totp_secret_encrypted,perfil) VALUES(?,?,?,?,?)",
            ("analista", "Analista", "hash", "secret", "analista"),
        ).lastrowid
        user = connection.execute("SELECT * FROM usuarios WHERE id=?", (user_id,)).fetchone()

    require_permission(user, "review")
    with pytest.raises(HTTPException):
        require_permission(user, "delete")


def test_password_uses_unique_salted_argon2_hashes():
    first = hash_password("Uma-Senha-Forte-123!")
    second = hash_password("Uma-Senha-Forte-123!")
    assert first != second
    assert first.startswith("$argon2id$")
    assert verify_password(first, "Uma-Senha-Forte-123!")
    assert not verify_password(first, "senha-errada")


def test_token_is_stored_as_fingerprint():
    assert hash_token("token") != "token"


def test_sensitive_cpf_is_redacted():
    assert "123" not in redact("CPF 123.456.789-09")
    redacted = redact("Authorization: Bearer segredo API_KEY=minha-chave email@empresa.com")
    assert "segredo" not in redacted
    assert "minha-chave" not in redacted
    assert "email@empresa.com" not in redacted


def test_safe_error_never_logs_exception_message_or_credentials(caplog):
    secret = "dapi12345678901234567890"
    try:
        raise RuntimeError(
            f"jdbc:databricks://usuario:senha@host/path?token={secret} payload=CPF 12345678909"
        )
    except RuntimeError as error:
        message, details = format_safe_error(error)

    serialized = f"{message} {details} {caplog.text}"
    assert secret not in serialized
    assert "usuario:senha" not in serialized
    assert "12345678909" not in serialized
    assert details["error_type"] == "RuntimeError"
    assert len(details["correlation_id"]) == 16


def test_error_level_business_log_rejects_raw_message(tmp_path, monkeypatch):
    from app.processing import add_log

    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "logs.db")
    database.initialize_database()
    add_log("erro", "erro_da_extensao", "token=dapi12345678901234567890 CPF 12345678909")
    with database.connect() as connection:
        row = connection.execute("SELECT mensagem,detalhes FROM logs").fetchone()

    assert "dapi" not in row["mensagem"] + row["detalhes"]
    assert "12345678909" not in row["mensagem"] + row["detalhes"]
    assert "Referência:" in row["mensagem"]


def test_extension_manifest_has_security_permissions():
    with open("extension/manifest.json", encoding="utf-8") as file:
        manifest = json.load(file)
    assert manifest["manifest_version"] == 3
    assert "https://web.whatsapp.com/*" in manifest["host_permissions"]


def test_renapsi_brand_assets_and_sidebar_control_are_packaged():
    assert Path("app/static/img/renapsi-logo.png").is_file()
    assert Path("extension/renapsi-logo.png").is_file()
    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    login = Path("app/templates/login.html").read_text(encoding="utf-8")
    popup = Path("extension/popup.html").read_text(encoding="utf-8")
    ui = Path("app/static/js/ui.js").read_text(encoding="utf-8")
    assert "renapsi-logo.png" in base
    assert "renapsi-logo.png" in login
    assert "renapsi-logo.png" in popup
    assert "admin-sidebar-collapsed" in ui
    assert 'setAttribute("aria-expanded"' in ui


def test_sensitive_runtime_files_are_ignored_by_git():
    ignore = Path(".gitignore").read_text(encoding="utf-8")
    for rule in (".env", "data/*.db", "data/*.xlsx", "data/uploads/*", "data/onboarding/*", "backups/*.zip", ".spreadsheet-work/"):
        assert rule in ignore


def test_no_real_secret_in_example_environment():
    values = {}
    for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    assert values["GEMINI_API_KEY"] == "coloque_a_chave_aqui"
    assert values["PROCESSOR_CONTRACT_APPROVED"] == "false"
    assert values["PROCESSOR_REGION"] == "configure_a_regiao_aprovada"
    assert values["GEMINI_MAX_OUTPUT_TOKENS"] == "1024"
    assert values["GEMINI_MAX_DOCUMENT_MB"] == "8"
    assert values["GEMINI_DAILY_REQUEST_LIMIT"] == "50"
    assert values["GEMINI_DAILY_OUTPUT_TOKEN_BUDGET"] == "50000"
    assert values["APP_SECRET_KEY"] == "gere_com_configurar_seguranca.ps1"
    assert values["PIPELINE_ATESTADOS_PATH"] == "configure_localmente_no_env"
    assert values["PIPELINE_BASE_GERAL_PATH"] == "configure_localmente_no_env"
    assert values["EXTENSION_AUTH_REQUIRED"] == "true"
    assert values["TRUSTED_PROXY_IPS"] == "127.0.0.1,::1"


def test_unread_chat_monitor_waits_before_inspecting_attachments():
    content = Path("extension/content.js").read_text(encoding="utf-8")
    assert "CHAT_INSPECTION_DELAY_MS = 10000" in content
    assert "await waitForChatInspection(openedIdentity, runId)" in content
    assert content.index("await waitForChatInspection(openedIdentity, runId)") < content.index(
        "processUnreadAttachments(unreadCount, isSelfConversation(openedIdentity))"
    )
    assert "findAllConversationMedia().filter" in content
    assert "attachmentDiagnostics" in content


def test_unread_chat_marking_is_limited_to_the_current_monitoring_run():
    content = Path("extension/content.js").read_text(encoding="utf-8")
    assert "const chatsMarkedUnreadThisRun = new Set()" in content
    assert "filter((item) => !chatsMarkedUnreadThisRun.has(item.name))" in content
    assert "async function markChatAsUnread" in content
    assert "TRUSTED_CONTEXT_CLICK" in content
    assert content.count("chatsMarkedUnreadThisRun.clear()") >= 2
