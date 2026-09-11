from fastapi.testclient import TestClient

from space_memory.app import create_app
from space_memory.ratelimit import SlidingWindowRateLimiter


def make_client(tmp_path, rate_limit_per_minute):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        token_pepper="test-pepper",
        bootstrap_tokens={
            "spm_a": {"space_id": "s", "agent_id": "a"},
            "spm_b": {"space_id": "s", "agent_id": "b"},
        },
        rate_limit_per_minute=rate_limit_per_minute,
    )
    return TestClient(app)


# --- limiter unit tests -----------------------------------------------------


def test_limiter_blocks_after_limit():
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
    assert limiter.check("k") == (True, 0.0)
    assert limiter.check("k") == (True, 0.0)
    allowed, retry = limiter.check("k")
    assert allowed is False
    assert retry > 0


def test_limiter_keys_are_independent():
    limiter = SlidingWindowRateLimiter(limit=1)
    assert limiter.check("a") == (True, 0.0)
    assert limiter.check("b") == (True, 0.0)
    assert limiter.check("a")[0] is False


# --- middleware tests -------------------------------------------------------


def test_rate_limit_returns_429_with_retry_after(tmp_path):
    client = make_client(tmp_path, rate_limit_per_minute=2)
    headers = {"Authorization": "Bearer spm_a"}
    assert client.get("/api/v1/memories", headers=headers).status_code == 200
    assert client.get("/api/v1/memories", headers=headers).status_code == 200
    third = client.get("/api/v1/memories", headers=headers)
    assert third.status_code == 429
    assert "Retry-After" in third.headers
    assert third.json()["detail"] == "Rate limit exceeded"


def test_rate_limit_is_keyed_per_token(tmp_path):
    client = make_client(tmp_path, rate_limit_per_minute=1)
    a = {"Authorization": "Bearer spm_a"}
    b = {"Authorization": "Bearer spm_b"}
    assert client.get("/api/v1/memories", headers=a).status_code == 200
    assert client.get("/api/v1/memories", headers=a).status_code == 429
    assert client.get("/api/v1/memories", headers=b).status_code == 200


def test_health_is_exempt_from_rate_limit(tmp_path):
    client = make_client(tmp_path, rate_limit_per_minute=1)
    for _ in range(5):
        assert client.get("/health").status_code == 200


def test_rate_limit_disabled_when_zero(tmp_path):
    client = make_client(tmp_path, rate_limit_per_minute=0)
    headers = {"Authorization": "Bearer spm_a"}
    for _ in range(5):
        assert client.get("/api/v1/memories", headers=headers).status_code == 200
