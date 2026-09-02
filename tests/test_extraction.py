from io import BytesIO

from PIL import Image

import pytest

from app import database, gemini_service
from app.gemini_service import (
    EXTRACTION_PROMPT,
    SCHEMA,
    enhanced_image_bytes,
    extraction_contents,
    require_approved_processor,
    parse_gemini_json,
)


def test_gemini_json_parser_accepts_structured_and_fenced_response():
    structured = type("Response", (), {"parsed": {"is_atestado": True}, "text": ""})()
    fenced = type("Response", (), {"parsed": None, "text": '```json\n{"is_atestado": true}\n```'})()
    assert parse_gemini_json(structured) == {"is_atestado": True}
    assert parse_gemini_json(fenced) == {"is_atestado": True}


def test_lgpd_breaker_fails_closed_when_approval_is_missing(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "chave-ficticia")
    monkeypatch.delenv("PROCESSOR_CONTRACT_APPROVED", raising=False)
    monkeypatch.setenv("PROCESSOR_REGION", "us-central1")

    with pytest.raises(RuntimeError, match="LGPD"):
        require_approved_processor()


def test_lgpd_breaker_accepts_only_explicit_approval_and_valid_region(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "chave-ficticia")
    monkeypatch.setenv("PROCESSOR_CONTRACT_APPROVED", "true")
    monkeypatch.setenv("PROCESSOR_REGION", "us-central1")
    require_approved_processor()

    monkeypatch.setenv("PROCESSOR_REGION", "configure_a_regiao_aprovada")
    with pytest.raises(RuntimeError, match="região"):
        require_approved_processor()


def test_extraction_does_not_create_client_before_lgpd_approval(tmp_path, monkeypatch):
    document = tmp_path / "documento.pdf"
    document.write_bytes(b"%PDF-dado-medico-ficticio")
    monkeypatch.setenv("GEMINI_API_KEY", "chave-ficticia")
    monkeypatch.setenv("PROCESSOR_CONTRACT_APPROVED", "false")
    monkeypatch.setenv("PROCESSOR_REGION", "us-central1")
    client_created = False

    def forbidden_client(**_kwargs):
        nonlocal client_created
        client_created = True
        raise AssertionError("o cliente externo não deveria ser criado")

    monkeypatch.setattr(gemini_service.genai, "Client", forbidden_client)
    with pytest.raises(RuntimeError, match="não autorizado"):
        gemini_service.extract_document(document)
    assert client_created is False


def test_daily_gemini_budget_fails_closed_before_external_call(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "gemini-budget.db")
    monkeypatch.setenv("GEMINI_DAILY_REQUEST_LIMIT", "1")
    monkeypatch.setenv("GEMINI_DAILY_OUTPUT_TOKEN_BUDGET", "1024")
    database.initialize_database()

    gemini_service.reserve_gemini_budget(1024)
    with pytest.raises(gemini_service.QuotaExceededError, match="Orçamento diário"):
        gemini_service.reserve_gemini_budget(1024)


def test_gemini_client_receives_timeout_and_output_token_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "gemini-config.db")
    database.initialize_database()
    document = tmp_path / "documento.pdf"
    document.write_bytes(b"%PDF-ficticio\n%%EOF")
    monkeypatch.setenv("GEMINI_API_KEY", "chave-ficticia")
    monkeypatch.setenv("PROCESSOR_CONTRACT_APPROVED", "true")
    monkeypatch.setenv("PROCESSOR_REGION", "us-central1")
    monkeypatch.setenv("GEMINI_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("GEMINI_MAX_OUTPUT_TOKENS", "512")
    monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "1")
    captured = {}

    class Models:
        def generate_content(self, **kwargs):
            captured["config"] = kwargs["config"]
            return type("Response", (), {"text": "{}"})()

    class Client:
        def __init__(self, **kwargs):
            captured["http_options"] = kwargs["http_options"]
            self.models = Models()

    monkeypatch.setattr(gemini_service.genai, "Client", Client)
    assert gemini_service.extract_document(document) == {}
    assert captured["config"].max_output_tokens == 512
    assert captured["http_options"].timeout == 25_000


def test_invalid_gemini_json_is_retried_and_structured_response_is_preferred(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "gemini-retry.db")
    database.initialize_database()
    document = tmp_path / "documento.pdf"
    document.write_bytes(b"%PDF-ficticio\n%%EOF")
    monkeypatch.setenv("GEMINI_API_KEY", "chave-ficticia")
    monkeypatch.setenv("PROCESSOR_CONTRACT_APPROVED", "true")
    monkeypatch.setenv("PROCESSOR_REGION", "us-central1")
    monkeypatch.setenv("GEMINI_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "2")
    calls = []

    class Models:
        def generate_content(self, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                return type("Response", (), {"text": "{json-incompleto", "parsed": None})()
            return type("Response", (), {"text": "não deve ser usado", "parsed": {"is_atestado": True}})()

    class Client:
        def __init__(self, **_kwargs):
            self.models = Models()

    monkeypatch.setattr(gemini_service.genai, "Client", Client)
    assert gemini_service.extract_document(document) == {"is_atestado": True}
    assert len(calls) == 2


def test_schema_includes_professional_identification_and_visual_signals():
    expected = {"crm", "crm_uf", "assinado", "carimbado"}
    assert expected <= set(SCHEMA["properties"])
    assert expected <= set(SCHEMA["required"])
    assert "0, 90, 180 e 270" in EXTRACTION_PROMPT
    assert "nunca complete caracteres por suposicao" in EXTRACTION_PROMPT


def test_low_contrast_image_gets_auxiliary_copy_without_changing_original(tmp_path):
    source = Image.new("RGB", (320, 180), (225, 225, 225))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    original = buffer.getvalue()
    path = tmp_path / "foto.png"
    path.write_bytes(original)

    enhanced = enhanced_image_bytes(original)
    contents = extraction_contents(path, "image/png")

    assert enhanced is not None
    assert enhanced.startswith(b"\xff\xd8\xff")
    assert path.read_bytes() == original
    assert len(contents) == 5


def test_pdf_is_sent_only_in_original_form(tmp_path):
    path = tmp_path / "documento.pdf"
    path.write_bytes(b"%PDF-conteudo-ficticio")
    assert len(extraction_contents(path, "application/pdf")) == 3
