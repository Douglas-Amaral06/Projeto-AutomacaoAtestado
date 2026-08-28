"""Simulador local do contrato de entrega Documento + JSON para o Databricks."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .storage_client import DatabricksStorageClient, LocalFakeStorageClient, StorageClient


SCHEMA_VERSION = "1.0"
try:
    SAO_PAULO = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    # O pacote tzdata, declarado em requirements.txt, fornece a base IANA em
    # instalações Windows. O fallback atende ao período do simulador (2026),
    # em que São Paulo usa UTC-03:00, sem depender do ambiente local.
    SAO_PAULO = timezone(timedelta(hours=-3), name="America/Sao_Paulo")
OFFICIAL_VOLUME_ROOT = "/Volumes/renapsi_prd/bronze_atestados/atestado"
SUPPORTED_EXTENSIONS = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
}


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_document_id(unidade: str, data_recebimento: datetime, sha256: str) -> str:
    """Monta ``UNIDADE_AAAAMMDDTHHMMSS_sha8`` conforme o contrato."""
    if not unidade or not unidade.strip():
        raise ValueError("Unidade é obrigatória.")
    if len(sha256) != 64:
        raise ValueError("SHA-256 inválido.")
    local_time = _as_sao_paulo(data_recebimento)
    return f"{unidade.strip()}_{local_time:%Y%m%dT%H%M%S}_{sha256[:8]}"


def _as_sao_paulo(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SAO_PAULO)
    return value.astimezone(SAO_PAULO)


def parse_timestamp(value: datetime | str | None, *, fallback: datetime | None = None) -> datetime:
    """Normaliza timestamps do fluxo para o fuso exigido pelo contrato."""
    if isinstance(value, datetime):
        return _as_sao_paulo(value)
    if isinstance(value, str) and value.strip():
        try:
            return _as_sao_paulo(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
        except ValueError as error:
            raise ValueError("Data/hora de recebimento inválida.") from error
    if fallback is not None:
        return _as_sao_paulo(fallback)
    raise ValueError("Data/hora de recebimento é obrigatória.")


def _nullable(value):
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _digits(value) -> str | None:
    normalized = _nullable(value)
    if normalized is None:
        return None
    digits = re.sub(r"\D", "", str(normalized))
    return digits or None


def _normalized_cid(value) -> str | None:
    normalized = _nullable(value)
    return re.sub(r"[^A-Z0-9]", "", str(normalized).upper()) if normalized is not None else None


def prepare_delivery(
    *,
    document_content: bytes,
    original_name: str,
    mime: str,
    unidade: str,
    data_recebimento: datetime | str,
    origem: dict,
    extracao: dict,
    documento: dict,
    volume_root: str = OFFICIAL_VOLUME_ROOT,
) -> PreparedDelivery:
    """Prepara uma entrega real conforme contrato 1.0, sem acessar storage."""
    if not document_content:
        raise ValueError("O documento não pode ser vazio.")
    normalized_unit = unidade.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]+", normalized_unit):
        raise ValueError("Unidade deve estar em maiúsculas, sem espaço ou acento.")
    extension = SUPPORTED_EXTENSIONS.get(mime.strip().lower())
    if not extension:
        raise ValueError("Tipo de arquivo não previsto no contrato Databricks.")

    received_at = parse_timestamp(data_recebimento)
    extracted_at = parse_timestamp(extracao.get("data_extracao"), fallback=received_at)
    digest = sha256_bytes(document_content)
    document_id = build_document_id(normalized_unit, received_at, digest)
    relative = PurePosixPath(normalized_unit) / f"{received_at:%Y}" / f"{received_at:%m}" / f"{received_at:%d}"
    document_relative_path = relative / f"{document_id}.{extension}"
    json_relative_path = relative / f"{document_id}.json"

    document_values = {
        "tipo_documento": _nullable(documento.get("tipo_documento")),
        "cpf": _digits(documento.get("cpf")),
        "nome_paciente": _nullable(documento.get("nome_paciente")),
        "crm": _digits(documento.get("crm")),
        "crm_uf": str(documento["crm_uf"]).strip().upper() if _nullable(documento.get("crm_uf")) else None,
        "data_emissao": _nullable(documento.get("data_emissao")),
        "dias_afastamento": documento.get("dias_afastamento"),
        "cid": _normalized_cid(documento.get("cid")),
        "assinado": documento.get("assinado") if isinstance(documento.get("assinado"), bool) else None,
        "carimbado": documento.get("carimbado") if isinstance(documento.get("carimbado"), bool) else None,
    }
    missing = [key for key, value in document_values.items() if value is None]
    confidence = extracao.get("confianca_geral")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = None

    payload = {
        "versao_schema": SCHEMA_VERSION,
        "id_documento": document_id,
        "origem": {
            "canal": "whatsapp",
            "id_mensagem": _nullable(origem.get("id_mensagem")),
            "id_conversa": _nullable(origem.get("id_conversa")),
            "whatsapp_remetente": _nullable(origem.get("whatsapp_remetente")),
            "whatsapp_destinatario": _nullable(origem.get("whatsapp_destinatario")),
            "unidade": normalized_unit,
            "data_recebimento": received_at.isoformat(timespec="seconds"),
        },
        "arquivo": {
            "nome_original": original_name,
            "nome_armazenado": document_relative_path.name,
            "caminho": f"{volume_root.rstrip('/')}/{document_relative_path.as_posix()}",
            "extensao": extension,
            "mime": mime.strip().lower(),
            "tamanho_bytes": len(document_content),
            "sha256": digest,
        },
        "extracao": {
            "motor": _nullable(extracao.get("motor")),
            "versao": _nullable(extracao.get("versao")),
            "data_extracao": extracted_at.isoformat(timespec="seconds"),
            "confianca_geral": confidence,
            "campos_ausentes": missing,
            "revisao_humana": extracao.get("revisao_humana"),
            "observacao": _nullable(extracao.get("observacao")),
        },
        "documento": document_values,
    }
    return PreparedDelivery(document_relative_path, json_relative_path, document_content, payload)


def prepare_processed_delivery(item, extracted: dict, document_content: bytes) -> PreparedDelivery:
    """Mapeia o item real da fila e o resultado da extração para o contrato."""
    document_type = {
        "atestado_medico": "Atestado",
        "comprovante_horas": "Comprovante de horas",
    }.get(extracted.get("tipo_documento"), _nullable(extracted.get("tipo_documento")))
    received_at = item["data_recebimento"]
    if not received_at:
        created_at = datetime.fromisoformat(item["criado_em"])
        received_at = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
    return prepare_delivery(
        document_content=document_content,
        original_name=item["arquivo_original"],
        mime=item["mime_type"],
        unidade=(item["unidade"] if "unidade" in item.keys() else None) or os.getenv("DELIVERY_UNIT", "UNI001"),
        data_recebimento=received_at,
        origem={
            "id_mensagem": item["id_mensagem"],
            "id_conversa": item["id_conversa"],
            "whatsapp_remetente": item["whatsapp_remetente"],
            "whatsapp_destinatario": os.getenv("DELIVERY_WHATSAPP_DESTINATION") or None,
        },
        extracao={
            "motor": "google-gemini",
            "versao": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            "data_extracao": datetime.now(tz=SAO_PAULO),
            "confianca_geral": extracted.get("confianca") if isinstance(extracted.get("confianca"), (int, float)) else None,
            "revisao_humana": None,
            "observacao": extracted.get("observacoes"),
        },
        documento={
            "tipo_documento": document_type,
            "cpf": extracted.get("cpf"),
            "nome_paciente": extracted.get("nome"),
            "crm": extracted.get("crm"),
            "crm_uf": extracted.get("crm_uf"),
            "data_emissao": extracted.get("data_atestado"),
            "dias_afastamento": extracted.get("dias_afastamento"),
            "cid": extracted.get("cid"),
            "assinado": extracted.get("assinado"),
            "carimbado": extracted.get("carimbado"),
        },
        volume_root=os.getenv("DATABRICKS_VOLUME_ROOT", OFFICIAL_VOLUME_ROOT),
    )


def synthetic_pdf_bytes(nome: str, data_emissao: str) -> bytes:
    """Cria um PDF mínimo, sem dados reais e válido apenas para teste técnico."""
    text = f"DOCUMENTO FICTICIO - {nome} - {data_emissao}".encode("latin-1", "replace")
    stream = b"BT /F1 12 Tf 72 720 Td (" + text.replace(b"(", b"\\(").replace(b")", b"\\)") + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


@dataclass(frozen=True)
class PreparedDelivery:
    """Entrega preparada, ainda sem qualquer operação de armazenamento."""

    document_relative_path: PurePosixPath
    json_relative_path: PurePosixPath
    document_content: bytes
    payload: dict


class ContractValidationError(ValueError):
    """Indica que uma entrega não atende ao contrato Databricks v2."""


def _require_keys(value: object, expected: set[str], location: str) -> dict:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{location} deve ser um objeto.")
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        details = []
        if missing:
            details.append(f"ausentes: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"não previstos: {', '.join(sorted(extra))}")
        raise ContractValidationError(f"Campos inválidos em {location} ({'; '.join(details)}).")
    return value


def _require_aware_iso(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field} deve ser uma data ISO-8601 com fuso.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractValidationError(f"{field} possui formato inválido.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractValidationError(f"{field} deve incluir o fuso horário.")


def validate_prepared_delivery(prepared: PreparedDelivery) -> None:
    """Valida integralmente documento, caminhos e JSON antes do primeiro byte enviado."""
    payload = _require_keys(
        prepared.payload,
        {"versao_schema", "id_documento", "origem", "arquivo", "extracao", "documento"},
        "raiz",
    )
    origem = _require_keys(
        payload["origem"],
        {"canal", "id_mensagem", "id_conversa", "whatsapp_remetente", "whatsapp_destinatario", "unidade", "data_recebimento"},
        "origem",
    )
    arquivo = _require_keys(
        payload["arquivo"],
        {"nome_original", "nome_armazenado", "caminho", "extensao", "mime", "tamanho_bytes", "sha256"},
        "arquivo",
    )
    extracao = _require_keys(
        payload["extracao"],
        {"motor", "versao", "data_extracao", "confianca_geral", "campos_ausentes", "revisao_humana", "observacao"},
        "extracao",
    )
    documento = _require_keys(
        payload["documento"],
        {"tipo_documento", "cpf", "nome_paciente", "crm", "crm_uf", "data_emissao", "dias_afastamento", "cid", "assinado", "carimbado"},
        "documento",
    )

    document_id = payload["id_documento"]
    if payload["versao_schema"] != SCHEMA_VERSION:
        raise ContractValidationError("versao_schema não corresponde ao contrato vigente.")
    if not isinstance(document_id, str) or not re.fullmatch(r"[A-Z0-9_-]+_\d{8}T\d{6}_[0-9a-f]{8}", document_id):
        raise ContractValidationError("id_documento possui formato inválido.")
    if origem["canal"] != "whatsapp":
        raise ContractValidationError("origem.canal deve ser whatsapp.")
    if not isinstance(origem["unidade"], str) or not re.fullmatch(r"[A-Z0-9_-]+", origem["unidade"]):
        raise ContractValidationError("origem.unidade possui formato inválido.")
    if not document_id.startswith(f"{origem['unidade']}_"):
        raise ContractValidationError("id_documento e origem.unidade não correspondem.")
    for field in ("id_mensagem", "id_conversa"):
        if origem[field] is not None and not isinstance(origem[field], str):
            raise ContractValidationError(f"origem.{field} deve ser texto ou null.")
    for field in ("whatsapp_remetente", "whatsapp_destinatario"):
        if not isinstance(origem[field], str) or not re.fullmatch(r"\+[1-9]\d{7,14}", origem[field]):
            raise ContractValidationError(f"origem.{field} deve estar no formato E.164.")
    _require_aware_iso(origem["data_recebimento"], "origem.data_recebimento")

    expected_extension = SUPPORTED_EXTENSIONS.get(arquivo["mime"] if isinstance(arquivo["mime"], str) else "")
    if not expected_extension or arquivo["extensao"] != expected_extension:
        raise ContractValidationError("arquivo.mime e arquivo.extensao não correspondem.")
    if arquivo["nome_armazenado"] != prepared.document_relative_path.name:
        raise ContractValidationError("arquivo.nome_armazenado não corresponde ao documento.")
    if prepared.document_relative_path.stem != document_id or prepared.json_relative_path != prepared.document_relative_path.with_suffix(".json"):
        raise ContractValidationError("Documento e JSON não formam o par exigido pelo contrato.")
    if not isinstance(arquivo["caminho"], str) or not arquivo["caminho"].startswith("/Volumes/") or not arquivo["caminho"].endswith(prepared.document_relative_path.as_posix()):
        raise ContractValidationError("arquivo.caminho não corresponde ao caminho do Volume.")
    digest = sha256_bytes(prepared.document_content)
    if arquivo["sha256"] != digest or not document_id.endswith(f"_{digest[:8]}"):
        raise ContractValidationError("SHA-256 do documento não confere.")
    if arquivo["tamanho_bytes"] != len(prepared.document_content) or not prepared.document_content:
        raise ContractValidationError("Tamanho do documento não confere.")
    if not isinstance(arquivo["nome_original"], str) or not arquivo["nome_original"].strip():
        raise ContractValidationError("arquivo.nome_original é obrigatório.")

    for field in ("motor", "versao"):
        if not isinstance(extracao[field], str) or not extracao[field].strip():
            raise ContractValidationError(f"extracao.{field} é obrigatório.")
    _require_aware_iso(extracao["data_extracao"], "extracao.data_extracao")
    confidence = extracao["confianca_geral"]
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
        raise ContractValidationError("extracao.confianca_geral deve estar entre 0 e 1 ou ser null.")
    expected_missing = {field for field, value in documento.items() if value is None}
    missing_fields = extracao["campos_ausentes"]
    if not isinstance(missing_fields, list) or any(not isinstance(field, str) for field in missing_fields) or set(missing_fields) != expected_missing:
        raise ContractValidationError("extracao.campos_ausentes não corresponde aos campos nulos.")
    if extracao["revisao_humana"] is not None and not isinstance(extracao["revisao_humana"], dict):
        raise ContractValidationError("extracao.revisao_humana deve ser objeto ou null.")
    if extracao["observacao"] is not None and not isinstance(extracao["observacao"], str):
        raise ContractValidationError("extracao.observacao deve ser texto ou null.")

    if documento["tipo_documento"] is not None and not isinstance(documento["tipo_documento"], str):
        raise ContractValidationError("documento.tipo_documento deve ser texto ou null.")
    if documento["cpf"] is not None and (not isinstance(documento["cpf"], str) or not re.fullmatch(r"\d{11}", documento["cpf"])):
        raise ContractValidationError("documento.cpf deve conter exatamente 11 dígitos ou ser null.")
    if documento["crm"] is not None and (not isinstance(documento["crm"], str) or not documento["crm"].isdigit()):
        raise ContractValidationError("documento.crm deve conter somente dígitos como texto ou ser null.")
    if documento["crm_uf"] is not None and (not isinstance(documento["crm_uf"], str) or not re.fullmatch(r"[A-Z]{2}", documento["crm_uf"])):
        raise ContractValidationError("documento.crm_uf deve conter duas letras maiúsculas ou ser null.")
    if documento["data_emissao"] is not None:
        try:
            date.fromisoformat(documento["data_emissao"])
        except (TypeError, ValueError) as error:
            raise ContractValidationError("documento.data_emissao deve usar AAAA-MM-DD ou ser null.") from error
    days = documento["dias_afastamento"]
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 0):
        raise ContractValidationError("documento.dias_afastamento deve ser inteiro não negativo ou null.")
    if documento["cid"] is not None and (not isinstance(documento["cid"], str) or not re.fullmatch(r"[A-Z][0-9]{2}[0-9A-Z]?", documento["cid"])):
        raise ContractValidationError("documento.cid deve ser CID-10 sem ponto e em maiúsculo ou null.")
    for field in ("assinado", "carimbado"):
        if documento[field] is not None and not isinstance(documento[field], bool):
            raise ContractValidationError(f"documento.{field} deve ser booleano ou null.")


class DeliveryService:
    """Orquestra a entrega: documento confirmado antes do JSON-sinal."""

    def __init__(self, storage: StorageClient) -> None:
        self.storage = storage

    def deliver(self, prepared: PreparedDelivery) -> PreparedDelivery:
        validate_prepared_delivery(prepared)
        self.storage.write_binary(prepared.document_relative_path, prepared.document_content)
        stored_content = self.storage.read_binary(prepared.document_relative_path)
        expected_sha = prepared.payload["arquivo"]["sha256"]
        if sha256_bytes(stored_content) != expected_sha:
            raise RuntimeError("Falha ao confirmar a gravação do documento.")
        self.storage.write_json(prepared.json_relative_path, prepared.payload)
        return prepared


def configured_delivery_service() -> DeliveryService | None:
    """Seleciona o storage; o modo real exige habilitação explícita."""
    mode = os.getenv("DELIVERY_MODE", "disabled").strip().lower()
    if mode == "disabled":
        return None
    if mode == "fake":
        root = os.getenv("DELIVERY_FAKE_ROOT", "").strip()
        if not root:
            raise RuntimeError("DELIVERY_FAKE_ROOT é obrigatório no modo fake.")
        return DeliveryService(LocalFakeStorageClient(Path(root)))
    if mode == "databricks":
        if os.getenv("DATABRICKS_UPLOAD_ENABLED", "false").strip().lower() != "true":
            raise RuntimeError("Upload real bloqueado: DATABRICKS_UPLOAD_ENABLED não está habilitado.")
        return DeliveryService(databricks_storage_from_env())
    raise RuntimeError("DELIVERY_MODE deve ser 'disabled', 'fake' ou 'databricks'.")


def databricks_storage_from_env() -> DatabricksStorageClient:
    """Monta o cliente sem realizar rede e sem expor credenciais."""
    try:
        timeout = int(os.getenv("DATABRICKS_TIMEOUT_SECONDS", "60"))
        attempts = int(os.getenv("DATABRICKS_MAX_ATTEMPTS", "3"))
    except ValueError as error:
        raise RuntimeError("Timeout ou número de tentativas do Databricks inválido.") from error
    return DatabricksStorageClient(
        host=os.getenv("DATABRICKS_HOST", ""),
        client_id=os.getenv("DATABRICKS_CLIENT_ID", ""),
        client_secret=os.getenv("DATABRICKS_CLIENT_SECRET", ""),
        volume_root=os.getenv("DATABRICKS_VOLUME_ROOT", OFFICIAL_VOLUME_ROOT),
        timeout_seconds=timeout,
        max_attempts=attempts,
    )


class LocalDeliverySimulator:
    """Adaptador local que une preparação, delivery e o fake storage em testes."""

    def __init__(self, root: Path, unidade: str = "UNI001") -> None:
        self.root = Path(root)
        self.unidade = unidade
        self.storage = LocalFakeStorageClient(root)
        self.delivery_service = DeliveryService(self.storage)

    def deliver(self, registro: dict[str, str], data_recebimento: datetime) -> tuple[Path, Path, dict]:
        prepared = self.prepare(registro, data_recebimento)
        self.delivery_service.deliver(prepared)
        return (
            self.storage.path_for(prepared.document_relative_path),
            self.storage.path_for(prepared.json_relative_path),
            prepared.payload,
        )

    def prepare(
        self,
        registro: dict[str, str],
        data_recebimento: datetime,
        *,
        document_content: bytes | None = None,
        extension: str = "pdf",
        mime: str = "application/pdf",
        nome_original: str | None = None,
    ) -> PreparedDelivery:
        """Gera bytes e metadados, sem escrever no storage."""
        local_time = _as_sao_paulo(data_recebimento)
        normalized_extension = extension.strip().lower().lstrip(".")
        if normalized_extension not in {"pdf", "jpg", "png"}:
            raise ValueError("Extensão de documento não suportada no simulador.")
        content = document_content if document_content is not None else synthetic_pdf_bytes(registro["nome"], registro["data_atestado"])
        if not content:
            raise ValueError("O documento não pode ser vazio.")
        digest = sha256_bytes(content)
        document_id = build_document_id(self.unidade, local_time, digest)
        relative = PurePosixPath(self.unidade) / f"{local_time:%Y}" / f"{local_time:%m}" / f"{local_time:%d}"
        document_relative_path = relative / f"{document_id}.{normalized_extension}"
        json_relative_path = relative / f"{document_id}.json"
        payload = self._payload(
            registro, local_time, document_id, document_relative_path, content, digest,
            nome_original or f"atestado-ficticio-{registro['matricula']}.{normalized_extension}", mime, relative,
        )
        return PreparedDelivery(document_relative_path, json_relative_path, content, payload)

    def _payload(self, registro, data_recebimento, document_id, document_relative_path, content, digest, nome_original, mime, relative) -> dict:
        sequence = int(registro["matricula"][-2:])
        missing = []
        crm = f"{sequence:05d}"
        cid = "Z000"
        if sequence % 7 == 0:
            crm = None
            missing.extend(["crm", "crm_uf"])
        if sequence % 9 == 0:
            cid = None
            missing.append("cid")
        official_path = f"{OFFICIAL_VOLUME_ROOT}/{document_relative_path.as_posix()}"
        return {
            "versao_schema": SCHEMA_VERSION,
            "id_documento": document_id,
            "origem": {
                "canal": "whatsapp",
                "id_mensagem": f"wamid.SIMULADO.{registro['id_atestado']}",
                "id_conversa": f"simulado-{registro['matricula']}@c.us",
                "whatsapp_remetente": f"+551190000{sequence:04d}",
                "whatsapp_destinatario": "+5511980000000",
                "unidade": self.unidade,
                "data_recebimento": data_recebimento.isoformat(timespec="seconds"),
            },
            "arquivo": {
                "nome_original": nome_original,
                "nome_armazenado": document_relative_path.name,
                "caminho": official_path,
                "extensao": document_relative_path.suffix.lstrip(".").lower(),
                "mime": mime,
                "tamanho_bytes": len(content),
                "sha256": digest,
            },
            "extracao": {
                "motor": "SIMULATED-OCR",
                "versao": "test-1.0",
                "data_extracao": data_recebimento.isoformat(timespec="seconds"),
                "confianca_geral": 0.99,
                "campos_ausentes": missing,
                "revisao_humana": None,
                "observacao": "Metadados integralmente sintéticos para validação técnica.",
            },
            "documento": {
                "tipo_documento": "atestado_medico",
                "cpf": f"9000000{sequence:04d}",
                "nome_paciente": registro["nome"],
                "crm": crm,
                "crm_uf": "SP" if crm else None,
                "data_emissao": registro["data_atestado"],
                "dias_afastamento": 1,
                "cid": cid,
                "assinado": True,
                "carimbado": True,
            },
        }
