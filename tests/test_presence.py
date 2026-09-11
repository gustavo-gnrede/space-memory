from fastapi.testclient import TestClient

from space_memory.app import create_app


def make_client(tmp_path, tokens):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'presence.db'}",
        token_pepper="test-pepper",
        bootstrap_tokens=tokens,
    )
    return TestClient(app)


THREE = {
    "tok-a": {"space_id": "s", "agent_id": "agent-a"},
    "tok-b": {"space_id": "s", "agent_id": "agent-b"},
    "tok-c": {"space_id": "s", "agent_id": "agent-c"},
}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_presence_lists_recently_active_agents_excluding_caller(tmp_path):
    client = make_client(tmp_path, THREE)
    assert client.get("/api/v1/events?after=0", headers=auth("tok-b")).status_code == 200
    assert client.get("/api/v1/events?after=0", headers=auth("tok-c")).status_code == 200
    resp = client.get("/api/v1/presence", headers=auth("tok-a"))
    assert resp.status_code == 200
    body = resp.json()
    ids = {a["agent_id"] for a in body["agents"]}
    assert ids == {"agent-b", "agent-c"}
    assert all(a["online"] for a in body["agents"])
    assert "now" in body


def test_presence_requires_auth(tmp_path):
    client = make_client(tmp_path, THREE)
    assert client.get("/api/v1/presence").status_code == 401


def test_presence_is_per_space(tmp_path):
    tokens = {
        "tok-a": {"space_id": "space-a", "agent_id": "agent-a"},
        "tok-b": {"space_id": "space-b", "agent_id": "agent-b"},
    }
    client = make_client(tmp_path, tokens)
    client.get("/api/v1/events?after=0", headers=auth("tok-b"))
    resp = client.get("/api/v1/presence", headers=auth("tok-a"))
    assert resp.json()["agents"] == []


def test_presence_marks_agent_from_authenticated_read(tmp_path):
    client = make_client(tmp_path, THREE)
    client.get("/api/v1/memories", headers=auth("tok-b"))
    resp = client.get("/api/v1/presence", headers=auth("tok-a"))
    assert "agent-b" in {a["agent_id"] for a in resp.json()["agents"]}
