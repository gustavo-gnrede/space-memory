from fastapi.testclient import TestClient

from space_memory.app import create_app


def make_client(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'continuity.db'}",
        token_pepper="test-pepper",
        bootstrap_tokens={
            "spm_agent_a": {"space_id": "space-a", "agent_id": "agent-a"},
            "spm_agent_b": {"space_id": "space-a", "agent_id": "agent-b"},
            "spm_agent_other": {"space_id": "space-b", "agent_id": "other"},
        },
    )
    return TestClient(app)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_memory_records_project_conversation_and_valid_at(tmp_path):
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/memories",
        headers={**auth("spm_agent_a"), "Idempotency-Key": "c1"},
        json={
            "session_id": "s1",
            "content": "Router IP changed to 10.0.0.9",
            "kind": "fact",
            "project_id": "proj-1",
            "conversation_id": "conv-1",
            "valid_at": "2026-09-01T00:00:00Z",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] == "proj-1"
    assert body["conversation_id"] == "conv-1"
    assert body["valid_at"] == "2026-09-01T00:00:00+00:00"


def test_search_filters_by_project_and_kind(tmp_path):
    client = make_client(tmp_path)
    client.post(
        "/api/v1/memories",
        headers={**auth("spm_agent_a"), "Idempotency-Key": "d1"},
        json={"session_id": "s1", "content": "Use event sourcing", "kind": "decision", "project_id": "proj-1"},
    )
    client.post(
        "/api/v1/memories",
        headers={**auth("spm_agent_a"), "Idempotency-Key": "d2"},
        json={"session_id": "s1", "content": "Unrelated memory", "kind": "fact", "project_id": "proj-2"},
    )

    by_project = client.get(
        "/api/v1/memories", params={"project_id": "proj-1"}, headers=auth("spm_agent_b")
    ).json()
    by_kind = client.get(
        "/api/v1/memories", params={"kind": "decision"}, headers=auth("spm_agent_b")
    ).json()

    assert by_project["total"] == 1
    assert by_project["items"][0]["content"] == "Use event sourcing"
    assert by_kind["total"] == 1
    assert by_kind["items"][0]["project_id"] == "proj-1"


def test_project_resume_returns_latest_handoff_with_attribution(tmp_path):
    client = make_client(tmp_path)

    client.post(
        "/api/v1/memories",
        headers={**auth("spm_agent_a"), "Idempotency-Key": "h1"},
        json={"session_id": "s1", "content": "Handoff: implement auth, tests pending", "kind": "handoff", "project_id": "proj-1"},
    )
    client.post(
        "/api/v1/memories",
        headers={**auth("spm_agent_b"), "Idempotency-Key": "h2"},
        json={"session_id": "s2", "content": "Auth done, tests green, next: rate limit", "kind": "handoff", "project_id": "proj-1"},
    )

    resume = client.get("/api/v1/projects/proj-1/resume", headers=auth("spm_agent_b")).json()

    assert resume["project_id"] == "proj-1"
    assert resume["last_handoff"]["written_by"] == "agent-b"
    assert resume["last_handoff"]["content"] == "Auth done, tests green, next: rate limit"
    assert resume["last_agent"] == "agent-b"


def test_project_resume_is_isolated_by_space(tmp_path):
    client = make_client(tmp_path)
    client.post(
        "/api/v1/memories",
        headers={**auth("spm_agent_a"), "Idempotency-Key": "iso1"},
        json={"session_id": "s1", "content": "Private handoff", "kind": "handoff", "project_id": "proj-secret"},
    )

    response = client.get("/api/v1/projects/proj-secret/resume", headers=auth("spm_agent_other"))

    assert response.status_code == 404
