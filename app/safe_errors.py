import hashlib
import logging
import re
import secrets
import traceback
from pathlib import Path


logger = logging.getLogger("sistema_interno")


def _safe_identifier(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120]
    return cleaned or fallback


def format_safe_error(error: Exception) -> tuple[str, dict]:
    """Cria rastreabilidade sem serializar mensagem, argumentos ou payload da exceção."""
    correlation_id = secrets.token_hex(8)
    error_type = _safe_identifier(type(error).__name__, "InternalError")
    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    origin = frames[-1] if frames else None
    origin_file = _safe_identifier(Path(origin.filename).name, "unknown") if origin else "unknown"
    origin_function = _safe_identifier(origin.name, "unknown") if origin else "unknown"
    origin_line = origin.lineno if origin else 0
    fingerprint_source = f"{error_type}:{origin_file}:{origin_function}:{origin_line}"
    fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()[:16]
    details = {
        "correlation_id": correlation_id,
        "error_type": error_type,
        "fingerprint": fingerprint,
    }
    safe_message = f"Falha de processamento. Referência: {correlation_id}"
    # Deliberadamente sem exc_info e sem str(error): tracebacks formatados repetem
    # a mensagem da exceção e podem carregar tokens, URLs ou dados médicos.
    logger.error(
        "internal_failure correlation_id=%s error_type=%s fingerprint=%s origin=%s:%s:%d",
        correlation_id,
        error_type,
        fingerprint,
        origin_file,
        origin_function,
        origin_line,
    )
    return safe_message, details
