import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient

from space_memory.app import create_app
from space_memory.vault import decrypt, encrypt, key_from_hex


VAULT_KEY = "aa" * 32  # 64 hex chars -> 32 bytes


def make_client(tmp_path, tokens, vault_key: str | None = VAULT_KEY):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'vault.db'}",
        token_pepper="test-pepper",
        bootstrap_tokens=tokens,
        vault_key=vault_key,
    )
    return TestClient(app)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


TWO = {
    "tok-a": {"space_id": "s", "agent_id": "agent-a"},
    "tok-b": {"space_id": "s", "agent_id": "agent-b"},
}


# --- crypto primitives -------------------------------------------------------


def test_vault_crypto_roundtrip():
    key = key_from_hex(VAULT_KEY)
    blob = encrypt(key, "s", "db/password", "s3cr3t-value")
    assert "s3cr3t-value" not in blob
    assert decrypt(key, "s", "db/password", blob) == "s3cr3t-value"


def test_vault_crypto_binds_to_identity():
    key = key_from_hex(VAULT_KEY)
    blob = encrypt(key, "s", "db/password", "value")
    with pytest.raises(InvalidTag):
        decrypt(key, "s", "other/key", blob)
    with pytest.raises(InvalidTag):
        decrypt(key, "other-space", "db/password", blob)


def test_key_from_hex_rejects_bad_length():
    with pytest.raises(ValueError):
        key_from_hex("abcd")


def test_key_from_hex_rejects_non_hex():
    with pytest.raises(ValueError):
        key_from_hex("z" * 64)


# --- REST: owner CRUD -------------------------------------------------------


def test_vault_set_get_delete_owner(tmp_path):
    client = make_client(tmp_path, TWO)
    r = client.post("/api/v1/vault/items", json={"key": "db/password", "value": "hunter2"}, headers=auth("tok-a"))
    assert r.status_code == 200
    assert r.json()["version"] == 1
    r = client.get("/api/v1/vault/items/db/password", headers=auth("tok-a"))
    assert r.status_code == 200
    assert r.json()["value"] == "hunter2"
    assert client.delete("/api/v1/vault/items/db/password", headers=auth("tok-a")).status_code == 200
    assert client.get("/api/v1/vault/items/db/password", headers=auth("tok-a")).status_code == 404


def test_vault_values_never_in_list(tmp_path):
    client = make_client(tmp_path, TWO)
    client.post("/api/v1/vault/items", json={"key": "api/token", "value": "secret-token"}, headers=auth("tok-a"))
    r = client.get("/api/v1/vault/items", headers=auth("tok-a"))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert "secret-token" not in r.text
    assert body["items"][0]["key"] == "api/token"


def test_vault_owner_only_update(tmp_path):
    client = make_client(tmp_path, TWO)
    client.post("/api/v1/vault/items", json={"key": "k", "value": "v1"}, headers=auth("tok-a"))
    r = client.post("/api/v1/vault/items", json={"key": "k", "value": "v2"}, headers=auth("tok-b"))
    assert r.status_code == 403


# --- access control ----------------------------------------------------------


def test_vault_shared_read_only(tmp_path):
    client = make_client(tmp_path, TWO)
    client.post(
        "/api/v1/vault/items",
        json={"key": "k", "value": "shared-secret", "shared_with": ["agent-b"]},
        headers=auth("tok-a"),
    )
    r = client.get("/api/v1/vault/items/k", headers=auth("tok-b"))
    assert r.status_code == 200
    assert r.json()["value"] == "shared-secret"
    assert client.post("/api/v1/vault/items", json={"key": "k", "value": "x"}, headers=auth("tok-b")).status_code == 403
    assert client.delete("/api/v1/vault/items/k", headers=auth("tok-b")).status_code == 403


def test_vault_denied_without_share(tmp_path):
    client = make_client(tmp_path, TWO)
    client.post("/api/v1/vault/items", json={"key": "k", "value": "private"}, headers=auth("tok-a"))
    assert client.get("/api/v1/vault/items/k", headers=auth("tok-b")).status_code == 403


# --- audit + gating ----------------------------------------------------------


def test_vault_audit_logs_access(tmp_path):
    client = make_client(tmp_path, TWO)
    client.post("/api/v1/vault/items", json={"key": "k", "value": "v"}, headers=auth("tok-a"))
    client.get("/api/v1/vault/items/k", headers=auth("tok-a"))
    r = client.get("/api/v1/vault/audit", headers=auth("tok-a"))
    assert r.status_code == 200
    actions = [(i["agent_id"], i["action"]) for i in r.json()["items"]]
    assert ("agent-a", "set") in actions
    assert ("agent-a", "get") in actions


def test_vault_disabled_without_key(tmp_path):
    client = make_client(tmp_path, TWO, vault_key=None)
    assert client.post("/api/v1/vault/items", json={"key": "k", "value": "v"}, headers=auth("tok-a")).status_code == 503
    assert client.get("/api/v1/vault/items", headers=auth("tok-a")).status_code == 503


def test_vault_requires_auth(tmp_path):
    client = make_client(tmp_path, TWO)
    assert client.get("/api/v1/vault/items").status_code == 401
