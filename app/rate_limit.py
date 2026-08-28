import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import HTTPException


_lock = threading.Lock()
_request_history: dict[str, deque[float]] = defaultdict(deque)
_daily_quota: dict[tuple[str, str], int] = {}


def check_rate_limit(token_id: str, limit: int = 30, window_seconds: int = 3600) -> None:
    """Registra uma chamada autenticada e bloqueia excesso por token."""
    now = time.monotonic()
    with _lock:
        history = _request_history[str(token_id)]
        cutoff = now - window_seconds
        while history and history[0] <= cutoff:
            history.popleft()
        if len(history) >= limit:
            retry_after = max(1, int(window_seconds - (now - history[0])))
            raise HTTPException(
                429,
                "Limite de uploads excedido. Tente novamente mais tarde.",
                headers={"Retry-After": str(retry_after)},
            )
        history.append(now)


def check_daily_quota(token_id: str, incoming_bytes: int, max_bytes: int = 300 * 1024 * 1024) -> None:
    """Reserva, de forma atômica, a quantidade declarada na cota UTC do token."""
    incoming_bytes = max(0, int(incoming_bytes))
    today = datetime.now(timezone.utc).date().isoformat()
    key = (str(token_id), today)
    with _lock:
        current = _daily_quota.get(key, 0)
        if current + incoming_bytes > max_bytes:
            raise HTTPException(
                413,
                "Cota diária de processamento excedida para este token.",
            )
        _daily_quota[key] = current + incoming_bytes
        stale = [entry for entry in _daily_quota if entry[1] != today]
        for entry in stale:
            del _daily_quota[entry]


def reset_rate_limits() -> None:
    """Limpa o estado volátil; usado na inicialização controlada e nos testes."""
    with _lock:
        _request_history.clear()
        _daily_quota.clear()
