import json
import mimetypes
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

from .database import connect


_request_lock = threading.Lock()
_last_request_at = 0.0


class QuotaExceededError(RuntimeError):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _positive_env_int(name: str, default: int, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = value if value > 0 else default
    return min(value, maximum) if maximum is not None else value


def reserve_gemini_budget(output_tokens: int) -> None:
    """Reserva uma chamada e seu teto de saída antes de acessar a API."""
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    daily_requests = _positive_env_int("GEMINI_DAILY_REQUEST_LIMIT", 50)
    daily_tokens = _positive_env_int("GEMINI_DAILY_OUTPUT_TOKEN_BUDGET", 50_000)
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO gemini_consumo(dia,chamadas,tokens_reservados) VALUES(?,0,0)",
            (day,),
        )
        cursor = connection.execute(
            """UPDATE gemini_consumo
               SET chamadas=chamadas+1,tokens_reservados=tokens_reservados+?
               WHERE dia=? AND chamadas<? AND tokens_reservados+?<=?""",
            (output_tokens, day, daily_requests, output_tokens, daily_tokens),
        )
        connection.execute(
            "DELETE FROM gemini_consumo WHERE dia<?",
            ((now.date() - timedelta(days=7)).isoformat(),),
        )
    if cursor.rowcount != 1:
        tomorrow = datetime.combine(
            now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
        )
        raise QuotaExceededError(
            "Orçamento diário local do Gemini atingido.",
            max(1, int((tomorrow - now).total_seconds())),
        )


def wait_for_gemini_slot() -> None:
    """Reserva espaçamento entre chamadas sem segurar o lock durante a rede."""
    global _last_request_at
    interval = max(0.0, float(os.getenv("GEMINI_MIN_INTERVAL_SECONDS", "13")))
    with _request_lock:
        now = time.monotonic()
        scheduled_at = max(now, _last_request_at + interval)
        _last_request_at = scheduled_at
    wait_time = scheduled_at - now
    if wait_time > 0:
        time.sleep(wait_time)


def require_approved_processor() -> None:
    """Falha fechado antes de qualquer envio de dados sensíveis ao Gemini."""
    required = {
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", "").strip(),
        "PROCESSOR_CONTRACT_APPROVED": os.getenv("PROCESSOR_CONTRACT_APPROVED", "").strip(),
        "PROCESSOR_REGION": os.getenv("PROCESSOR_REGION", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Bloqueio de segurança (LGPD): configuração de processamento externo incompleta: "
            + ", ".join(missing)
        )
    if required["GEMINI_API_KEY"] == "coloque_a_chave_aqui":
        raise RuntimeError("Bloqueio de segurança (LGPD): credencial do processador não configurada.")
    if required["PROCESSOR_CONTRACT_APPROVED"].casefold() != "true":
        raise RuntimeError(
            "Bloqueio de segurança (LGPD): tratamento externo de dados médicos não autorizado."
        )
    region = required["PROCESSOR_REGION"]
    if region.casefold() in {"configure_a_regiao_aprovada", "unknown", "none"} or not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)+", region.casefold()
    ):
        raise RuntimeError("Bloqueio de segurança (LGPD): região do processador inválida.")


EXTRACTION_PROMPT = """
Primeiro determine se o arquivo e um atestado medico/odontologico ou um
comprovante/declaracao de comparecimento ou de horas. Para compatibilidade,
is_atestado deve ser true para qualquer um desses documentos validos de RH.
Receitas, fotos comuns, conversas e outros documentos nao sao validos. Extraia
somente os dados visiveis e devolva JSON conforme o schema.
Nao deduza nem invente informacoes. Use null quando um campo estiver ausente ou
ilegivel. Em observacoes, registre campos duvidosos. A confianca deve ser alta,
media ou baixa.

Se a foto estiver inclinada, de lado ou de cabeca para baixo, examine o
documento considerando as orientacoes 0, 90, 180 e 270 graus antes de concluir
que um campo esta ausente. Compare a imagem original com a versao auxiliar de
contraste quando ela for fornecida. Em foto borrada, cortada, com reflexo ou
baixa resolucao, nunca complete caracteres por suposicao: use null e descreva
o problema em observacoes.

Antes de devolver CPF como null, examine novamente toda a imagem, especialmente
as linhas rotuladas CPF, paciente, interessado ou identificado(a). Diferencie
CPF de CRO, CRM, CNPJ, telefone e outros numeros. Retorne CPF apenas quando os
11 digitos estiverem realmente visiveis; nunca complete digitos por suposicao.

Para CRM ou CRO, extraia somente o numero no campo crm e a sigla de duas letras
no campo crm_uf. Nao confunda CRM/CRO com CPF, CID, telefone ou CNPJ. Preserve
zeros a esquerda. Se houver apenas o numero sem UF, retorne crm_uf como null.

assinado deve ser true somente quando houver assinatura manuscrita, assinatura
digital claramente indicada ou outro sinal inequivoco de assinatura; false
quando a area correspondente estiver legivel e sem assinatura; null quando a
foto nao permitir decidir. carimbado segue a mesma regra para carimbo do
profissional ou estabelecimento.

Campos:
- is_atestado: true para atestado ou comprovante/declaracao de comparecimento/horas
- tipo_documento: atestado, comprovante de hora, receita, foto, outro ou ilegivel
- motivo_classificacao: explicacao curta da classificacao
- nome: nome completo do paciente/funcionario
- cpf: somente os 11 digitos, se estiver visivel
- crm: somente os digitos do CRM ou CRO, preservando zeros a esquerda
- crm_uf: sigla da UF em duas letras maiusculas
- cid: codigo CID exatamente como aparece
- dias_afastamento: numero inteiro de dias de afastamento
- data_atestado: data de emissao em AAAA-MM-DD
- assinado: true, false ou null
- carimbado: true, false ou null
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
        "crm": {"type": ["string", "null"]},
        "crm_uf": {"type": ["string", "null"]},
        "cid": {"type": ["string", "null"]},
        "dias_afastamento": {"type": ["integer", "null"]},
        "data_atestado": {"type": ["string", "null"]},
        "assinado": {"type": ["boolean", "null"]},
        "carimbado": {"type": ["boolean", "null"]},
        "observacoes": {"type": ["string", "null"]},
        "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
    },
    "required": [
        "is_atestado",
        "tipo_documento",
        "motivo_classificacao",
        "nome",
        "cpf",
        "crm",
        "crm_uf",
        "cid",
        "dias_afastamento",
        "data_atestado",
        "assinado",
        "carimbado",
        "observacoes",
        "confianca",
    ],
}


def enhanced_image_bytes(content: bytes) -> bytes | None:
    """Cria cópia auxiliar legível; nunca modifica ou substitui o original."""
    try:
        with Image.open(BytesIO(content)) as source:
            if source.width * source.height > 40_000_000:
                return None
            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"}:
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
            image = ImageOps.autocontrast(image, cutoff=1)
            image = ImageEnhance.Contrast(image).enhance(1.08)
            image = ImageEnhance.Sharpness(image).enhance(1.35)
            output = BytesIO()
            image.save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def extraction_contents(path: Path, mime_type: str) -> list:
    original = path.read_bytes()
    contents = [
        EXTRACTION_PROMPT,
        "Documento original:",
        types.Part.from_bytes(data=original, mime_type=mime_type),
    ]
    enhancement_enabled = os.getenv("GEMINI_IMAGE_ENHANCEMENT", "true").lower() == "true"
    if enhancement_enabled and mime_type.startswith("image/"):
        enhanced = enhanced_image_bytes(original)
        if enhanced:
            contents.extend([
                "Versao auxiliar com orientacao EXIF, contraste e nitidez ajustados. Use apenas para apoiar a leitura do original:",
                types.Part.from_bytes(data=enhanced, mime_type="image/jpeg"),
            ])
    return contents


def extract_document(path: Path) -> dict:
    require_approved_processor()
    api_key = os.environ["GEMINI_API_KEY"]

    mime_by_suffix = {
        ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }
    mime_type = mime_by_suffix.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    timeout_ms = _positive_env_int("GEMINI_TIMEOUT_SECONDS", 60, maximum=180) * 1000
    max_output_tokens = _positive_env_int("GEMINI_MAX_OUTPUT_TOKENS", 1024, maximum=4096)
    max_attempts = _positive_env_int("GEMINI_MAX_ATTEMPTS", 2, maximum=3)
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
    contents = extraction_contents(path, mime_type)
    response = None
    for attempt in range(max_attempts):
        reserve_gemini_budget(max_output_tokens)
        wait_for_gemini_slot()
        try:
            response = client.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=SCHEMA,
                    temperature=0,
                    max_output_tokens=max_output_tokens,
                ),
            )
            break
        except Exception as error:
            message = str(error)
            status_code = getattr(error, "status_code", None) or getattr(error, "code", None)
            if "429" in message or "RESOURCE_EXHAUSTED" in message or status_code == 429:
                retry_match = re.search(r"retryDelay[^0-9]+(\d+)s", message)
                retry_after = int(retry_match.group(1)) if retry_match else None
                raise QuotaExceededError("Limite da API Gemini atingido.", retry_after) from error
            transient = isinstance(error, (TimeoutError, ConnectionError)) or status_code in {
                408, 500, 502, 503, 504,
            }
            if not transient or attempt + 1 >= max_attempts:
                raise
            time.sleep(2 ** attempt)
    if response is None:
        raise RuntimeError("O serviço de leitura não retornou resposta.")
    return json.loads(response.text)
