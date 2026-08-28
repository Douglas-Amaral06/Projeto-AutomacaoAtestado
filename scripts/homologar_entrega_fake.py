"""Homologa localmente a entrega contratual com um documento fictício."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.databricks_delivery import (
    DeliveryService,
    SAO_PAULO,
    prepare_delivery,
    sha256_bytes,
    synthetic_pdf_bytes,
)
from app.storage_client import LocalFakeStorageClient


DEFAULT_OUTPUT = ROOT / "data" / "homologacao_fake"


def homologar(destino: Path = DEFAULT_OUTPUT) -> tuple[Path, Path, dict]:
    """Gera, entrega e verifica um par fictício documento/JSON."""
    received_at = datetime.now(tz=SAO_PAULO).replace(microsecond=0)
    content = synthetic_pdf_bytes("PESSOA FICTICIA", received_at.date().isoformat())
    prepared = prepare_delivery(
        document_content=content,
        original_name="atestado-homologacao-ficticio.pdf",
        mime="application/pdf",
        unidade="UNI001",
        data_recebimento=received_at,
        origem={
            "id_mensagem": f"messageId-HOMOLOGACAO-{received_at:%Y%m%d%H%M%S}",
            "id_conversa": "5511999990000@c.us",
            "whatsapp_remetente": "+5511999990000",
            "whatsapp_destinatario": "+5511988887777",
        },
        extracao={
            "motor": "HOMOLOGACAO-LOCAL",
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
    )
    storage = LocalFakeStorageClient(destino)
    DeliveryService(storage).deliver(prepared)
    document_path = storage.path_for(prepared.document_relative_path)
    json_path = storage.path_for(prepared.json_relative_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    if document_path.stem != json_path.stem or payload["id_documento"] != document_path.stem:
        raise RuntimeError("Documento e JSON não formam um par válido.")
    if sha256_bytes(document_path.read_bytes()) != payload["arquivo"]["sha256"]:
        raise RuntimeError("SHA-256 da homologação não confere.")
    return document_path, json_path, payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Homologa a entrega local sem acessar Databricks.")
    parser.add_argument("--destino", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    document_path, json_path, payload = homologar(args.destino)
    print("HOMOLOGACAO APROVADA")
    print(f"ID: {payload['id_documento']}")
    print(f"Documento: {document_path}")
    print(f"JSON: {json_path}")
    print(f"SHA-256: {payload['arquivo']['sha256']}")


if __name__ == "__main__":
    main()
