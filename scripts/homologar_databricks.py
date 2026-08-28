"""Verifica acesso ou envia um par totalmente fictício ao Volume Databricks."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.databricks_delivery import (
    DeliveryService,
    SAO_PAULO,
    databricks_storage_from_env,
    prepare_delivery,
    synthetic_pdf_bytes,
)


def preflight_config() -> dict:
    """Valida configuração sem abrir conexão e sem retornar credenciais."""
    required = {
        "DATABRICKS_HOST": os.getenv("DATABRICKS_HOST", "").strip(),
        "DATABRICKS_VOLUME_ROOT": os.getenv("DATABRICKS_VOLUME_ROOT", "").strip(),
        "DATABRICKS_CLIENT_ID": os.getenv("DATABRICKS_CLIENT_ID", "").strip(),
        "DATABRICKS_CLIENT_SECRET": os.getenv("DATABRICKS_CLIENT_SECRET", "").strip(),
        "DATABRICKS_TEST_UNIT": os.getenv("DATABRICKS_TEST_UNIT", "").strip(),
    }
    placeholders = ("configure", "coloque", "change-me", "changeme")
    missing = [
        name for name, value in required.items()
        if not value or any(marker in value.casefold() for marker in placeholders)
    ]
    if missing:
        raise RuntimeError(f"Configuração incompleta: {', '.join(missing)}.")

    host = required["DATABRICKS_HOST"].rstrip("/")
    volume = required["DATABRICKS_VOLUME_ROOT"].rstrip("/")
    unit = required["DATABRICKS_TEST_UNIT"].upper()
    if not host.startswith("https://"):
        raise RuntimeError("DATABRICKS_HOST deve utilizar HTTPS.")
    if not volume.startswith("/Volumes/") or ".." in Path(volume).parts:
        raise RuntimeError("DATABRICKS_VOLUME_ROOT não é um caminho seguro de Volume.")
    if not re.fullmatch(r"[A-Z0-9_-]+", unit):
        raise RuntimeError("DATABRICKS_TEST_UNIT possui formato inválido.")
    try:
        timeout = int(os.getenv("DATABRICKS_TIMEOUT_SECONDS", "60"))
        attempts = int(os.getenv("DATABRICKS_MAX_ATTEMPTS", "3"))
    except ValueError as error:
        raise RuntimeError("Timeout ou tentativas do Databricks devem ser números inteiros.") from error
    if not 5 <= timeout <= 180 or not 1 <= attempts <= 3:
        raise RuntimeError("Timeout deve ficar entre 5 e 180; tentativas entre 1 e 3.")

    catalog = volume.split("/")[2] if len(volume.split("/")) > 2 else ""
    environment = "producao" if catalog.lower().endswith(("_prd", "_prod")) else "homologacao_ou_desenvolvimento"
    return {
        "configuracao_valida": True,
        "host": host,
        "volume": volume,
        "ambiente_inferido": environment,
        "unidade_teste": unit,
        "credenciais_configuradas": True,
        "upload_habilitado": os.getenv("DATABRICKS_UPLOAD_ENABLED", "false").strip().lower() == "true",
        "timeout_segundos": timeout,
        "tentativas": attempts,
    }


def check_access() -> str:
    """Autentica e lista o Volume; não grava nada."""
    storage = databricks_storage_from_env()
    storage.check_directory_access()
    return storage.volume_root.as_posix()


def upload_fictitious(confirmed_volume: str) -> dict:
    """Envia um único documento sintético somente após confirmações explícitas."""
    storage = databricks_storage_from_env()
    volume = storage.volume_root.as_posix()
    if os.getenv("DATABRICKS_UPLOAD_ENABLED", "false").strip().lower() != "true":
        raise RuntimeError("Upload bloqueado por DATABRICKS_UPLOAD_ENABLED=false.")
    if confirmed_volume.rstrip("/") != volume.rstrip("/"):
        raise RuntimeError("O Volume confirmado não corresponde ao Volume configurado.")

    received_at = datetime.now(tz=SAO_PAULO).replace(microsecond=0)
    content = synthetic_pdf_bytes("PESSOA FICTICIA", received_at.date().isoformat())
    prepared = prepare_delivery(
        document_content=content,
        original_name="atestado-homologacao-ficticio.pdf",
        mime="application/pdf",
        unidade=os.getenv("DATABRICKS_TEST_UNIT", "UNI001"),
        data_recebimento=received_at,
        origem={
            "id_mensagem": f"messageId-HOMOLOGACAO-{received_at:%Y%m%d%H%M%S}",
            "id_conversa": "conversa-ficticia",
            "whatsapp_remetente": "+5511999990000",
            "whatsapp_destinatario": "+5511988887777",
        },
        extracao={
            "motor": "HOMOLOGACAO-CONTROLADA",
            "versao": "1.0",
            "data_extracao": received_at,
            "confianca_geral": 1.0,
            "revisao_humana": None,
            "observacao": "Documento e dados integralmente fictícios.",
        },
        documento={
            "tipo_documento": "Atestado",
            "cpf": "52998224725",
            "nome_paciente": "PESSOA FICTICIA",
            "crm": "00000",
            "crm_uf": "SP",
            "data_emissao": received_at.date().isoformat(),
            "dias_afastamento": 1,
            "cid": "Z000",
            "assinado": True,
            "carimbado": True,
        },
        volume_root=volume,
    )
    DeliveryService(storage).deliver(prepared)
    return prepared.payload


def cleanup_fictitious(document_relative_path: str, confirmed_id: str) -> dict:
    """Apaga somente um par criado pela homologação e previamente conferido."""
    storage = databricks_storage_from_env()
    relative = PurePosixPath(document_relative_path)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".pdf":
        raise RuntimeError("O caminho de limpeza deve ser relativo, seguro e apontar para um PDF.")
    if not confirmed_id or relative.stem != confirmed_id:
        raise RuntimeError("O ID confirmado não corresponde ao nome do documento.")

    json_relative = relative.with_suffix(".json")
    try:
        payload = json.loads(storage.read_binary(json_relative).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("O JSON remoto não é válido; limpeza automática bloqueada.") from error

    expected_document = storage.volume_root.joinpath(relative).as_posix()
    if (
        payload.get("id_documento") != confirmed_id
        or payload.get("extracao", {}).get("motor") != "HOMOLOGACAO-CONTROLADA"
        or payload.get("arquivo", {}).get("caminho") != expected_document
    ):
        raise RuntimeError("O par remoto não foi reconhecido como homologação controlada; limpeza bloqueada.")

    # Remove primeiro o gatilho JSON e depois o documento. Nunca remove diretórios.
    storage.delete_file(json_relative)
    storage.delete_file(relative)
    return {
        "id_documento": confirmed_id,
        "documento_removido": expected_document,
        "json_removido": storage.volume_root.joinpath(json_relative).as_posix(),
    }


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Homologação segura da Files API do Databricks.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check-config", action="store_true", help="Valida o .env sem acessar a rede.")
    action.add_argument("--check-access", action="store_true", help="Valida OAuth e leitura, sem gravar.")
    action.add_argument("--upload-fictitious", action="store_true", help="Envia um PDF e JSON fictícios.")
    action.add_argument(
        "--cleanup-fictitious",
        action="store_true",
        help="Remove apenas um par reconhecido como homologação controlada.",
    )
    parser.add_argument(
        "--confirm-volume",
        default="",
        help="Caminho exato obrigatório para confirmar conscientemente uma gravação.",
    )
    parser.add_argument(
        "--document-relative-path",
        default="",
        help="Caminho relativo exato do PDF fictício retornado no upload.",
    )
    parser.add_argument(
        "--confirm-id",
        default="",
        help="ID exato do documento fictício que será removido.",
    )
    args = parser.parse_args()
    if args.check_config:
        print(json.dumps(preflight_config(), ensure_ascii=False, indent=2))
        return
    if args.check_access:
        preflight_config()
        print(f"ACESSO VALIDADO: {check_access()}")
        return
    if args.cleanup_fictitious:
        preflight_config()
        result = cleanup_fictitious(args.document_relative_path, args.confirm_id)
        print("ARQUIVOS FICTICIOS REMOVIDOS DO VOLUME")
        print(f"ID: {result['id_documento']}")
        print("A eventual linha já ingerida na Bronze deve ser tratada separadamente.")
        return
    payload = upload_fictitious(args.confirm_volume)
    print("HOMOLOGACAO DATABRICKS APROVADA")
    print(f"ID: {payload['id_documento']}")
    print(f"Documento: {payload['arquivo']['caminho']}")
    print("JSON gravado ao lado do documento.")


if __name__ == "__main__":
    main()
