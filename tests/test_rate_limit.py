import pytest
from fastapi import HTTPException

from app.rate_limit import check_daily_quota, check_rate_limit, reset_rate_limits


def setup_function():
    reset_rate_limits()


def test_upload_rate_limit_is_isolated_by_token_and_returns_retry_after():
    check_rate_limit("token-a", limit=2, window_seconds=3600)
    check_rate_limit("token-a", limit=2, window_seconds=3600)
    check_rate_limit("token-b", limit=2, window_seconds=3600)

    with pytest.raises(HTTPException) as error:
        check_rate_limit("token-a", limit=2, window_seconds=3600)

    assert error.value.status_code == 429
    assert int(error.value.headers["Retry-After"]) > 0


def test_daily_upload_quota_is_isolated_by_token():
    check_daily_quota("token-a", 60, max_bytes=100)
    check_daily_quota("token-b", 100, max_bytes=100)

    with pytest.raises(HTTPException) as error:
        check_daily_quota("token-a", 41, max_bytes=100)

    assert error.value.status_code == 413
