import json
import mimetypes
import os
from pathlib import Path

from google import genai
from google.genai import types


EXTRACTION_PROMPT = """
Extraia somente os dados visiveis neste atestado e devolva JSON conforme o
schema. Nao deduza nem invente informacoes. Use null quando um campo estiver
ausente ou ilegivel. Em observacoes, registre campos duvidosos. A confianca
deve ser alta, media ou baixa.

Campos:
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
        "nome": {"type": ["string", "null"]},
        "cpf": {"type": ["string", "null"]},
        "cid": {"type": ["string", "null"]},
        "dias_afastamento": {"type": ["integer", "null"]},
        "data_atestado": {"type": ["string", "null"]},
        "observacoes": {"type": ["string", "null"]},
        "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
    },
    "required": [
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

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    client = genai.Client(api_key=api_key)
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
    return json.loads(response.text)

