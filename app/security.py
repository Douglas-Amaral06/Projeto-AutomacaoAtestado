import base64
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request

from .database import connect


SESSION_COOKIE = "rh_session"
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _app_secret() -> str:
    value = os.getenv("APP_SECRET_KEY", "")
    if len(value) < 32:
        raise RuntimeError("APP_SECRET_KEY deve ter pelo menos 32 caracteres aleatorios")
    return value


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_app_secret().encode()).digest())
    return Fernet(key)


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("A senha deve ter ao menos 12 caracteres")
    return PASSWORD_HASHER.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(stored_hash, password)
    except VerifyMismatchError:
        return False


def encrypt_totp(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_totp(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_fingerprint(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    if os.getenv("TRUST_CLOUDFLARE", "false").lower() == "true":
        ip = request.headers.get("cf-connecting-ip", ip)
    ua = request.headers.get("user-agent", "")
    return hash_token(f"ip:{ip}"), hash_token(f"ua:{ua}")


def login_key(request: Request, username: str) -> str:
    ip_hash, _ = client_fingerprint(request)
    return hash_token(f"{ip_hash}:{username.casefold()}")


def is_login_blocked(key_hash: str) -> bool:
    cutoff = (utc_now() - timedelta(minutes=15)).isoformat()
    with connect() as connection:
        failures = connection.execute(
            "SELECT COUNT(*) FROM tentativas_login WHERE chave_hash=? AND sucesso=0 AND criado_em>=?",
            (key_hash, cutoff),
        ).fetchone()[0]
    return failures >= 5


def is_attempt_blocked(key_hash: str, max_failures: int, window_minutes: int) -> bool:
    cutoff = (utc_now() - timedelta(minutes=window_minutes)).isoformat()
    with connect() as connection:
        failures = connection.execute(
            "SELECT COUNT(*) FROM tentativas_login WHERE chave_hash=? AND sucesso=0 AND criado_em>=?",
            (key_hash, cutoff),
        ).fetchone()[0]
    return failures >= max_failures


def attempt_retry_after(key_hash: str, window_minutes: int) -> int:
    cutoff = (utc_now() - timedelta(minutes=window_minutes)).isoformat()
    with connect() as connection:
        first = connection.execute(
            "SELECT criado_em FROM tentativas_login WHERE chave_hash=? AND sucesso=0 AND criado_em>=? ORDER BY criado_em LIMIT 1",
            (key_hash, cutoff),
        ).fetchone()
    if not first:
        return 0
    unlock_at = datetime.fromisoformat(first["criado_em"]) + timedelta(minutes=window_minutes)
    return max(1, int((unlock_at - utc_now()).total_seconds()))


def record_login(key_hash: str, success: bool) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO tentativas_login(chave_hash, sucesso, criado_em) VALUES(?,?,?)",
            (key_hash, int(success), utc_now().isoformat()),
        )
        if success:
            connection.execute("DELETE FROM tentativas_login WHERE chave_hash=?", (key_hash,))


def create_session(user_id: int, request: Request) -> tuple[str, str, datetime]:
    raw_token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    expires = utc_now() + timedelta(hours=8)
    ip_hash, ua_hash = client_fingerprint(request)
    with connect() as connection:
        connection.execute(
            "INSERT INTO sessoes(usuario_id,token_hash,csrf_token,ip_hash,user_agent_hash,expira_em) VALUES(?,?,?,?,?,?)",
            (user_id, hash_token(raw_token), csrf, ip_hash, ua_hash, expires.isoformat()),
        )
    return raw_token, csrf, expires


def current_user(request: Request, required: bool = True):
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        if required:
            raise HTTPException(401, "Login necessario")
        return None
    ip_hash, ua_hash = client_fingerprint(request)
    with connect() as connection:
        row = connection.execute(
            """SELECT u.*, s.csrf_token, s.expira_em, s.user_agent_hash, s.id session_id
               FROM sessoes s JOIN usuarios u ON u.id=s.usuario_id
               WHERE s.token_hash=? AND u.ativo=1""",
            (hash_token(raw),),
        ).fetchone()
    if not row or datetime.fromisoformat(row["expira_em"]) <= utc_now():
        raise HTTPException(401, "Sessao expirada")
    # User-Agent é vinculado; IP não é bloqueante para tolerar redes corporativas móveis.
    if row["user_agent_hash"] != ua_hash:
        raise HTTPException(401, "Sessao invalida")
    return row


def require_csrf(request: Request, user, supplied: str) -> None:
    if not supplied or not secrets.compare_digest(user["csrf_token"], supplied):
        raise HTTPException(403, "Token CSRF invalido")


def verify_service_token(request: Request) -> None:
    raw = request.headers.get("x-api-token", "")
    if not raw:
        raise HTTPException(401, "Token da extensao ausente")
    with connect() as connection:
        row = connection.execute(
            "SELECT id FROM tokens_servico WHERE token_hash=? AND ativo=1", (hash_token(raw),)
        ).fetchone()
        if row:
            connection.execute(
                "UPDATE tokens_servico SET ultimo_uso=? WHERE id=?", (utc_now().isoformat(), row["id"])
            )
    if not row:
        raise HTTPException(401, "Token da extensao invalido")


def redact(value: str) -> str:
    value = re.sub(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", "***CPF***", value)
    return value
