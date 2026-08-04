import json
import mimetypes
import os
import re
import threading
import time
from pathlib import Path

from google import genai
from google.genai import types


_request_lock = threading.Lock()
_last_request_at = 0.0


class QuotaExceededError(RuntimeError):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


EXTRACTION_PROMPT = """
Primeiro determine se o arquivo e realmente um atestado medico ou odontologico.
Receitas, fotos comuns, conversas, comprovantes e outros documentos nao sao
atestados. Extraia somente os dados visiveis e devolva JSON conforme o schema.
Nao deduza nem invente informacoes. Use null quando um campo estiver ausente ou
ilegivel. Em observacoes, registre campos duvidosos. A confianca deve ser alta,
media ou baixa.

Campos:
- is_atestado: true somente quando o documento for um atestado
- tipo_documento: atestado, declaracao, receita, foto, outro ou ilegivel
- motivo_classificacao: explicacao curta da classificacao
- nome: nome completo do paciente/funcionario
- cpf: somente os 11 digitos, se estiver visivel
- cid: codigo CID exatamente como aparece
- dias_afastamento: numero inteiro de dias de afastamento
- data_atestado: data de emissao em AAAA-MM-DD
- observacoes
- confianca
"""


SCHEMA = {
    "type": "object",
    "properties": {
        "is_atestado": {"type": "boolean"},
        "tipo_documento": {"type": "string"},
        "motivo_classificacao": {"type": "string"},
        "nome": {"type": ["string", "null"]},
        "cpf": {"type": ["string", "null"]},
        "cid": {"type": ["string", "null"]},
        "dias_afastamento": {"type": ["integer", "null"]},
        "data_atestado": {"type": ["string", "null"]},
        "observacoes": {"type": ["string", "null"]},
        "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
    },
    "required": [
        "is_atestado",
        "tipo_documento",
        "motivo_classificacao",
        "nome",
        "cpf",
        "cid",
        "dias_afastamento",
        "data_atestado",
        "observacoes",
        "confianca",
    ],
}


def extract_document(path: Path) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "coloque_a_chave_aqui":
        raise RuntimeError("GEMINI_API_KEY nao configurada no arquivo .env")

    mime_by_suffix = {
        ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }
    mime_type = mime_by_suffix.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    client = genai.Client(api_key=api_key)
    global _last_request_at
    with _request_lock:
        minimum_interval = float(os.getenv("GEMINI_MIN_INTERVAL_SECONDS", "13"))
        wait_time = minimum_interval - (time.monotonic() - _last_request_at)
        if wait_time > 0:
            time.sleep(wait_time)
        try:
            response = client.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=[
                    EXTRACTION_PROMPT,
                    types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=SCHEMA,
                    temperature=0,
                ),
            )
            _last_request_at = time.monotonic()
        except Exception as error:
            _last_request_at = time.monotonic()
            if "429" not in str(error) and "RESOURCE_EXHAUSTED" not in str(error):
                raise
            retry_match = re.search(r"retryDelay[^0-9]+(\d+)s", str(error))
            retry_after = int(retry_match.group(1)) if retry_match else None
            raise QuotaExceededError("Limite da API Gemini atingido.", retry_after) from error
    return json.loads(response.text)
