import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import PurePosixPath

import pytest

from app.databricks_delivery import (
    ContractValidationError, DeliveryService, LocalDeliverySimulator, SAO_PAULO,
    configured_delivery_service, prepare_delivery, prepare_processed_delivery,
    sha256_bytes, validate_prepared_delivery,
)
from app.storage_client import DatabricksStorageClient, LocalFakeStorageClient
from scripts.simular_entrega_databricks import carregar_registros, simular_entregas
from scripts.homologar_entrega_fake import homologar
from scripts import homologar_databricks


ID_PATTERN = re.compile(r"^UNI001_\d{8}T\d{6}_[0-9a-f]{8}$")
E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
TOP_LEVEL_KEYS = {"versao_schema", "id_documento", "origem", "arquivo", "extracao", "documento"}


def test_fake_storage_atomic_writes_do_not_share_temporary_name(tmp_path):
    storage = LocalFakeStorageClient(tmp_path)
    relative = PurePosixPath("UNI001/2026/08/24/documento.pdf")
    contents = [b"primeiro", b"segundo"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda content: storage.write_binary(relative, content), contents))

    assert storage.read_binary(relative) in contents
    assert not list(tmp_path.rglob("*.tmp"))


def test_simulador_gera_50_documentos_e_jsons_pareados(tmp_path):
    deliveries = simular_entregas(tmp_path)
    assert len(deliveries) == 50
    assert len(list(tmp_path.rglob("*.pdf"))) == 50
    assert len(list(tmp_path.rglob("*.json"))) == 50
    for document_path, json_path, _payload in deliveries:
        assert document_path.with_suffix(".json") == json_path
        assert document_path.is_file()
        assert json_path.is_file()


def test_json_respeita_contrato_e_integridade(tmp_path):
    document_path, json_path, _ = simular_entregas(tmp_path)[0]
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert set(payload) == TOP_LEVEL_KEYS
    assert payload["versao_schema"] == "1.0"
    assert ID_PATTERN.fullmatch(payload["id_documento"])
    assert payload["id_documento"].endswith(payload["arquivo"]["sha256"][:8])
    assert sha256_bytes(document_path.read_bytes()) == payload["arquivo"]["sha256"]
    assert payload["arquivo"]["nome_armazenado"] == document_path.name
    assert payload["arquivo"]["extensao"] == "pdf"
    assert payload["arquivo"]["tamanho_bytes"] == document_path.stat().st_size
    assert payload["arquivo"]["caminho"].startswith("/Volumes/renapsi_prd/bronze_atestados/atestado/UNI001/2026/08/21/")
    assert E164_PATTERN.fullmatch(payload["origem"]["whatsapp_remetente"])
    assert E164_PATTERN.fullmatch(payload["origem"]["whatsapp_destinatario"])
    received = datetime.fromisoformat(payload["origem"]["data_recebimento"])
    assert received.tzinfo is not None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["documento"]["data_emissao"])
    assert re.fullmatch(r"\d{11}", payload["documento"]["cpf"])
    assert isinstance(payload["documento"]["dias_afastamento"], int)
    assert isinstance(payload["documento"]["assinado"], bool)
    assert isinstance(payload["documento"]["carimbado"], bool)
    assert payload["extracao"]["revisao_humana"] is None
    assert re.fullmatch(r"\d+", payload["documento"]["crm"])


def test_campos_ausentes_sao_null_e_nao_strings_vazias(tmp_path):
    payloads = [item[2] for item in simular_entregas(tmp_path)]
    missing_crm = next(payload for payload in payloads if "crm" in payload["extracao"]["campos_ausentes"])
    assert missing_crm["documento"]["crm"] is None
    assert missing_crm["documento"]["crm_uf"] is None
    assert "" not in (missing_crm["documento"]["crm"], missing_crm["documento"]["crm_uf"])


def test_document_failure_never_creates_json(tmp_path, monkeypatch):
    simulator = LocalDeliverySimulator(tmp_path)
    registro = carregar_registros()[0]

    def fail(_path, _content):
        raise OSError("falha simulada")

    monkeypatch.setattr(simulator.storage, "write_binary", fail)
    with pytest.raises(OSError):
        simulator.deliver(registro, datetime(2026, 8, 21, 13, tzinfo=SAO_PAULO))
    assert not list(tmp_path.rglob("*.json"))


def test_delivery_layer_writes_document_before_json(tmp_path):
    events = []

    class RecordingLocalStorage(LocalFakeStorageClient):
        def write_binary(self, relative_path, content):
            events.append(("documento", relative_path))
            super().write_binary(relative_path, content)

        def write_json(self, relative_path, payload):
            events.append(("json", relative_path))
            super().write_json(relative_path, payload)

    simulator = LocalDeliverySimulator(tmp_path)
    prepared = simulator.prepare(carregar_registros()[0], datetime(2026, 8, 21, 13, tzinfo=SAO_PAULO))
    storage = RecordingLocalStorage(tmp_path)
    DeliveryService(storage).deliver(prepared)
    assert [event[0] for event in events] == ["documento", "json"]
    assert storage.path_for(prepared.document_relative_path).is_file()
    assert storage.path_for(prepared.json_relative_path).is_file()


@pytest.mark.parametrize(("extension", "mime", "content"), [
    ("pdf", "application/pdf", b"%PDF-sintetico"),
    ("jpg", "image/jpeg", b"\xff\xd8\xff-sintetico"),
    ("png", "image/png", b"\x89PNG\r\n\x1a\n-sintetico"),
])
def test_extensao_contratual_nao_tem_ponto(tmp_path, extension, mime, content):
    simulator = LocalDeliverySimulator(tmp_path)
    prepared = simulator.prepare(
        carregar_registros()[0], datetime(2026, 8, 21, 13, tzinfo=SAO_PAULO),
        document_content=content, extension=extension, mime=mime,
    )
    simulator.delivery_service.deliver(prepared)
    assert prepared.payload["arquivo"]["extensao"] == extension
    assert prepared.document_relative_path.suffix == f".{extension}"
    assert prepared.payload["arquivo"]["mime"] == mime


def test_reenvio_do_mesmo_binario_cria_dois_registros_historicos(tmp_path):
    simulator = LocalDeliverySimulator(tmp_path)
    original = carregar_registros()[0]
    reenvio = {**original, "id_atestado": "REENVIO-FICTICIO-001"}
    start = datetime(2026, 8, 21, 13, tzinfo=SAO_PAULO)
    first = simulator.deliver(original, start)
    second = simulator.deliver(reenvio, start + timedelta(seconds=1))
    assert first[2]["arquivo"]["sha256"] == second[2]["arquivo"]["sha256"]
    assert first[2]["id_documento"] != second[2]["id_documento"]
    assert first[2]["origem"]["id_mensagem"] != second[2]["origem"]["id_mensagem"]
    assert first[0].is_file() and second[0].is_file()


def test_contrato_atual_colide_para_mesmo_binario_unidade_e_segundo(tmp_path):
    """Documenta a pendência contratual sem alterar unilateralmente o ID v2."""
    simulator = LocalDeliverySimulator(tmp_path)
    registro = carregar_registros()[0]
    received_at = datetime(2026, 8, 21, 13, tzinfo=SAO_PAULO)
    content = b"%PDF-mesmo-binario-ficticio"
    first = simulator.prepare(registro, received_at, document_content=content)
    second = simulator.prepare(
        {**registro, "id_atestado": "OUTRA-MENSAGEM"},
        received_at,
        document_content=content,
    )
    assert first.payload["id_documento"] == second.payload["id_documento"]
    assert first.document_relative_path == second.document_relative_path
    assert first.payload["origem"]["id_mensagem"] != second.payload["origem"]["id_mensagem"]


def test_preparador_real_mapeia_campos_e_nulos_do_contrato():
    content = b"%PDF-documento-real-ficticio"
    prepared = prepare_delivery(
        document_content=content,
        original_name="atestado_001.pdf",
        mime="application/pdf",
        unidade="uni001",
        data_recebimento="2026-08-19T14:44:03Z",
        origem={
            "id_mensagem": "wamid.TESTE",
            "id_conversa": "5511999990000@c.us",
            "whatsapp_remetente": "+5511999990000",
            "whatsapp_destinatario": "+5511988887777",
        },
        extracao={
            "motor": "google-gemini",
            "versao": "gemini-test",
            "data_extracao": "2026-08-19T14:44:09Z",
            "confianca_geral": None,
            "revisao_humana": None,
            "observacao": "CID não legível",
        },
        documento={
            "tipo_documento": "Atestado",
            "cpf": "529.982.247-25",
            "nome_paciente": "Pessoa Fictícia",
            "crm": None,
            "crm_uf": None,
            "data_emissao": "2026-08-18",
            "dias_afastamento": 1,
            "cid": None,
            "assinado": True,
            "carimbado": False,
        },
    )

    payload = prepared.payload
    assert payload["id_documento"].startswith("UNI001_20260819T114403_")
    assert payload["origem"]["data_recebimento"] == "2026-08-19T11:44:03-03:00"
    assert payload["documento"]["cpf"] == "52998224725"
    assert payload["documento"]["crm"] is None
    assert payload["documento"]["cid"] is None
    assert set(payload["extracao"]["campos_ausentes"]) == {"crm", "crm_uf", "cid"}
    assert payload["arquivo"]["sha256"] == sha256_bytes(content)


def test_preparador_do_fluxo_real_nao_inventa_campos_ausentes(monkeypatch):
    monkeypatch.setenv("DELIVERY_UNIT", "UNI001")
    monkeypatch.setenv("DELIVERY_WHATSAPP_DESTINATION", "+5511988887777")
    item = {
        "arquivo_original": "documento.jpeg",
        "mime_type": "image/jpeg",
        "data_recebimento": "2026-08-19T15:22:10-03:00",
        "criado_em": "2026-08-19 18:22:10",
        "id_mensagem": "messageId-ficticio",
        "id_conversa": None,
        "whatsapp_remetente": None,
    }
    prepared = prepare_processed_delivery(item, {
        "tipo_documento": "atestado_medico",
        "nome": "Pessoa Fictícia",
        "cpf": None,
        "cid": "n39.0",
        "dias_afastamento": 2,
        "data_atestado": "2026-08-18",
        "observacoes": None,
        "confianca": "baixa",
    }, b"\xff\xd8\xff-documento-ficticio")

    assert prepared.document_relative_path.suffix == ".jpg"
    assert prepared.payload["documento"]["tipo_documento"] == "Atestado"
    assert prepared.payload["documento"]["cid"] == "N390"
    assert prepared.payload["documento"]["cpf"] is None
    assert prepared.payload["documento"]["assinado"] is None
    assert prepared.payload["extracao"]["confianca_geral"] is None
    assert prepared.payload["origem"]["id_mensagem"] == "messageId-ficticio"


def test_homologacao_fake_gera_par_verificado(tmp_path):
    document_path, json_path, payload = homologar(tmp_path)
    assert document_path.is_file()
    assert json_path.is_file()
    assert document_path.stem == json_path.stem == payload["id_documento"]
    assert sha256_bytes(document_path.read_bytes()) == payload["arquivo"]["sha256"]
    assert payload["extracao"]["motor"] == "HOMOLOGACAO-LOCAL"


class _FakeHttpResponse:
    def __init__(self, status=204, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return self._body


def test_databricks_client_usa_oauth_cache_e_files_api(monkeypatch):
    calls = []
    document = b"%PDF-conteudo-ficticio"

    def fake_urlopen(request, timeout):
        calls.append((request.method, request.full_url, dict(request.header_items()), request.data, timeout))
        if request.full_url.endswith("/oidc/v1/token"):
            return _FakeHttpResponse(
                200, json.dumps({"access_token": "token-ficticio", "expires_in": 3600}).encode()
            )
        if request.method == "GET":
            return _FakeHttpResponse(200, document)
        return _FakeHttpResponse(204)

    monkeypatch.setattr("app.storage_client.urlopen", fake_urlopen)
    storage = DatabricksStorageClient(
        host="https://dbc-32044e02-fb27.cloud.databricks.com",
        client_id="cliente-ficticio",
        client_secret="segredo-ficticio",
        volume_root="/Volumes/renapsi_prd/bronze_atestados/atestado",
    )
    prepared = prepare_delivery(
        document_content=document,
        original_name="atestado.pdf",
        mime="application/pdf",
        unidade="UNI001",
        data_recebimento="2026-08-19T11:44:03-03:00",
        origem={
            "id_mensagem": "wamid.TESTE",
            "whatsapp_remetente": "+5511999990000",
            "whatsapp_destinatario": "+5511988887777",
        },
        extracao={"motor": "teste", "versao": "1", "data_extracao": "2026-08-19T11:44:04-03:00"},
        documento={},
    )
    DeliveryService(storage).deliver(prepared)

    token_calls = [call for call in calls if call[1].endswith("/oidc/v1/token")]
    api_calls = [call for call in calls if "/api/2.0/fs/" in call[1]]
    assert len(token_calls) == 1
    assert [call[0] for call in api_calls] == ["PUT", "PUT", "GET", "PUT", "PUT"]
    assert "/api/2.0/fs/directories/Volumes/renapsi_prd/bronze_atestados/atestado/UNI001/2026/08/19" in api_calls[0][1]
    assert api_calls[1][1].endswith(".pdf?overwrite=true")
    assert api_calls[-1][1].endswith(".json?overwrite=true")
    assert api_calls[1][3] == document
    assert api_calls[-1][3].decode("utf-8").endswith("\n")
    assert all("Bearer token-ficticio" in call[2].values() for call in api_calls)


def test_modo_databricks_exige_habilitacao_explicita(monkeypatch):
    monkeypatch.setenv("DELIVERY_MODE", "databricks")
    monkeypatch.setenv("DATABRICKS_UPLOAD_ENABLED", "false")
    with pytest.raises(RuntimeError, match="Upload real bloqueado"):
        configured_delivery_service()


def test_verificacao_de_acesso_databricks_e_somente_leitura(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.method, request.full_url))
        if request.full_url.endswith("/oidc/v1/token"):
            return _FakeHttpResponse(200, b'{"access_token":"token","expires_in":3600}')
        return _FakeHttpResponse(200, b'{"contents":[]}')

    monkeypatch.setattr("app.storage_client.urlopen", fake_urlopen)
    storage = DatabricksStorageClient(
        host="https://dbc-32044e02-fb27.cloud.databricks.com",
        client_id="cliente",
        client_secret="segredo",
        volume_root="/Volumes/renapsi_prd/bronze_atestados/atestado",
    )
    storage.check_directory_access()
    assert [method for method, _url in calls] == ["POST", "GET"]
    assert calls[-1][1].endswith(
        "/api/2.0/fs/directories/Volumes/renapsi_prd/bronze_atestados/atestado"
    )


def test_exclusao_databricks_remove_somente_arquivo_relativo_validado(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.method, request.full_url))
        if request.full_url.endswith("/oidc/v1/token"):
            return _FakeHttpResponse(200, b'{"access_token":"token","expires_in":3600}')
        return _FakeHttpResponse(204)

    monkeypatch.setattr("app.storage_client.urlopen", fake_urlopen)
    storage = DatabricksStorageClient(
        host="https://dbc-32044e02-fb27.cloud.databricks.com",
        client_id="cliente",
        client_secret="segredo",
        volume_root="/Volumes/renapsi_prd/bronze_atestados/atestado",
    )
    storage.delete_file(PurePosixPath("UNI001/2026/08/26/teste.json"))

    assert calls[-1][0] == "DELETE"
    assert calls[-1][1].endswith(
        "/api/2.0/fs/files/Volumes/renapsi_prd/bronze_atestados/atestado/UNI001/2026/08/26/teste.json"
    )
    with pytest.raises(ValueError, match="relativo e seguro"):
        storage.delete_file(PurePosixPath("../fora.json"))


def test_homologacao_real_permanece_bloqueada_sem_chave_explicita(monkeypatch):
    class FakeStorage:
        volume_root = PurePosixPath("/Volumes/renapsi_prd/bronze_atestados/atestado")

    monkeypatch.setattr(homologar_databricks, "databricks_storage_from_env", lambda: FakeStorage())
    monkeypatch.setenv("DATABRICKS_UPLOAD_ENABLED", "false")
    with pytest.raises(RuntimeError, match="Upload bloqueado"):
        homologar_databricks.upload_fictitious(
            "/Volumes/renapsi_prd/bronze_atestados/atestado"
        )


def test_limpeza_remove_apenas_par_marcado_como_homologacao(monkeypatch):
    relative = PurePosixPath("UNI001/2026/08/26/UNI001_20260826T120000_12345678.pdf")
    confirmed_id = relative.stem

    class FakeStorage:
        volume_root = PurePosixPath("/Volumes/renapsi_prd/bronze_atestados/atestado")

        def __init__(self):
            self.deleted = []

        def read_binary(self, path):
            assert path == relative.with_suffix(".json")
            return json.dumps(
                {
                    "id_documento": confirmed_id,
                    "extracao": {"motor": "HOMOLOGACAO-CONTROLADA"},
                    "arquivo": {"caminho": self.volume_root.joinpath(relative).as_posix()},
                }
            ).encode()

        def delete_file(self, path):
            self.deleted.append(path)

    storage = FakeStorage()
    monkeypatch.setattr(homologar_databricks, "databricks_storage_from_env", lambda: storage)

    result = homologar_databricks.cleanup_fictitious(relative.as_posix(), confirmed_id)

    assert storage.deleted == [relative.with_suffix(".json"), relative]
    assert result["id_documento"] == confirmed_id


def test_limpeza_bloqueia_json_que_nao_e_de_homologacao(monkeypatch):
    relative = PurePosixPath("UNI001/2026/08/26/UNI001_20260826T120000_12345678.pdf")

    class FakeStorage:
        volume_root = PurePosixPath("/Volumes/renapsi_prd/bronze_atestados/atestado")

        def read_binary(self, _path):
            return b'{"id_documento":"outro","extracao":{"motor":"GEMINI"}}'

        def delete_file(self, _path):
            raise AssertionError("não deveria apagar")

    monkeypatch.setattr(homologar_databricks, "databricks_storage_from_env", lambda: FakeStorage())
    with pytest.raises(RuntimeError, match="limpeza bloqueada"):
        homologar_databricks.cleanup_fictitious(relative.as_posix(), relative.stem)


def test_preflight_databricks_e_offline_e_nao_retorna_credenciais(monkeypatch):
    secret = "segredo-super-secreto-ficticio"
    client_id = "cliente-interno-ficticio"
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-32044e02-fb27.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_VOLUME_ROOT", "/Volumes/renapsi_prd/bronze_atestados/atestado")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", client_id)
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", secret)
    monkeypatch.setenv("DATABRICKS_TEST_UNIT", "uni001")
    monkeypatch.setenv("DATABRICKS_UPLOAD_ENABLED", "false")

    result = homologar_databricks.preflight_config()

    rendered = json.dumps(result)
    assert result["configuracao_valida"] is True
    assert result["ambiente_inferido"] == "producao"
    assert result["upload_habilitado"] is False
    assert result["unidade_teste"] == "UNI001"
    assert secret not in rendered
    assert client_id not in rendered


def test_preflight_databricks_falha_fechado_com_placeholder(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-32044e02-fb27.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_VOLUME_ROOT", "/Volumes/renapsi_prd/bronze_atestados/atestado")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "configure_externamente")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "")
    monkeypatch.setenv("DATABRICKS_TEST_UNIT", "UNI001")
    with pytest.raises(RuntimeError, match="DATABRICKS_CLIENT_ID.*DATABRICKS_CLIENT_SECRET"):
        homologar_databricks.preflight_config()


def test_contrato_invalido_e_bloqueado_antes_do_storage(tmp_path):
    simulator = LocalDeliverySimulator(tmp_path)
    prepared = simulator.prepare(
        carregar_registros()[0], datetime(2026, 8, 21, 13, tzinfo=SAO_PAULO)
    )
    prepared.payload["documento"]["cpf"] = "123"
    with pytest.raises(ContractValidationError, match="cpf"):
        simulator.delivery_service.deliver(prepared)
    assert not list(tmp_path.rglob("*.pdf"))
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload["origem"].__setitem__("data_recebimento", "2026-08-21T13:00:00"), "fuso"),
        (lambda payload: payload["arquivo"].__setitem__("sha256", "0" * 64), "SHA-256"),
        (lambda payload: payload["documento"].__setitem__("campo_surpresa", "x"), "não previstos"),
    ],
)
def test_validador_rejeita_inconsistencias_do_contrato(tmp_path, mutate, message):
    prepared = LocalDeliverySimulator(tmp_path).prepare(
        carregar_registros()[0], datetime(2026, 8, 21, 13, tzinfo=SAO_PAULO)
    )
    mutate(prepared.payload)
    with pytest.raises(ContractValidationError, match=message):
        validate_prepared_delivery(prepared)
