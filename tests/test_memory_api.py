import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from space_memory.app import create_app


def make_client(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'space-memory.db'}",
        token_pepper="test-pepper",
        bootstrap_tokens={
            "spm_test_claude": {"space_id": "space-a", "agent_id": "claude"},
            "spm_test_codex": {"space_id": "space-a", "agent_id": "codex"},
            "spm_test_other": {"space_id": "space-b", "agent_id": "other"},
        },
    )
    return TestClient(app)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_agent_writes_and_other_agent_in_same_space_reads_memory(tmp_path):
    client = make_client(tmp_path)

    created = client.post(
        "/api/v1/memories",
        headers={**auth("spm_test_claude"), "Idempotency-Key": "write-1"},
        json={
            "session_id": "claude-session-1",
            "content": "The project uses event sourcing.",
            "kind": "decision",
        },
    )

    assert created.status_code == 201
    assert created.json()["written_by"] == "claude"
    assert created.json()["version"] == 1

    found = client.get(
        "/api/v1/memories?query=event+sourcing",
        headers=auth("spm_test_codex"),
    )

    assert found.status_code == 200
    assert [item["content"] for item in found.json()["items"]] == [
        "The project uses event sourcing."
    ]


def test_concurrent_retries_create_one_memory_and_return_same_result(tmp_path):
    client = make_client(tmp_path)

    def create(_index):
        return client.post(
            "/api/v1/memories",
            headers={**auth("spm_test_claude"), "Idempotency-Key": "concurrent-retry"},
            json={"session_id": "s1", "content": "Exactly once", "kind": "fact"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(create, range(8)))

    assert all(response.status_code in {200, 201} for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1
    assert client.get(
        "/api/v1/memories", headers=auth("spm_test_claude")
    ).json()["total"] == 1


def test_idempotency_key_does_not_duplicate_memory(tmp_path):
    client = make_client(tmp_path)
    request = {
        "headers": {**auth("spm_test_claude"), "Idempotency-Key": "same-write"},
        "json": {
            "session_id": "session-1",
            "content": "Durable once.",
            "kind": "fact",
        },
    }

    first = client.post("/api/v1/memories", **request)
    second = client.post("/api/v1/memories", **request)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert client.get("/api/v1/memories", headers=auth("spm_test_claude")).json()["total"] == 1


def test_token_cannot_cross_space_boundary(tmp_path):
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/memories",
        headers={**auth("spm_test_claude"), "Idempotency-Key": "private-a"},
        json={"session_id": "s1", "content": "Only Space A", "kind": "fact"},
    ).json()

    response = client.get(
        f"/api/v1/memories/{created['id']}", headers=auth("spm_test_other")
    )

    assert response.status_code == 404


def test_update_rejects_stale_base_version(tmp_path):
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/memories",
        headers={**auth("spm_test_claude"), "Idempotency-Key": "initial"},
        json={"session_id": "s1", "content": "Version one", "kind": "fact"},
    ).json()

    accepted = client.put(
        f"/api/v1/memories/{created['id']}",
        headers={**auth("spm_test_claude"), "Idempotency-Key": "update-1"},
        json={"session_id": "s1", "content": "Version two", "base_version": 1},
    )
    conflict = client.put(
        f"/api/v1/memories/{created['id']}",
        headers={**auth("spm_test_codex"), "Idempotency-Key": "update-2"},
        json={"session_id": "s2", "content": "Stale overwrite", "base_version": 1},
    )

    assert accepted.status_code == 200
    assert accepted.json()["version"] == 2
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current_version"] == 2


def test_concurrent_updates_allow_only_one_writer(tmp_path):
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/memories",
        headers={**auth("spm_test_claude"), "Idempotency-Key": "race-base"},
        json={"session_id": "s1", "content": "Original", "kind": "fact"},
    ).json()

    def update(index):
        return client.put(
            f"/api/v1/memories/{created['id']}",
            headers={**auth("spm_test_claude"), "Idempotency-Key": f"race-{index}"},
            json={"session_id": f"s{index}", "content": f"Writer {index}", "base_version": 1},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(update, [1, 2]))

    assert sorted(response.status_code for response in responses) == [200, 409]
    current = client.get(
        f"/api/v1/memories/{created['id']}", headers=auth("spm_test_claude")
    ).json()
    assert current["version"] == 2


def test_retried_update_is_idempotent(tmp_path):
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/memories",
        headers={**auth("spm_test_claude"), "Idempotency-Key": "create-for-retry"},
        json={"session_id": "s1", "content": "Version one", "kind": "fact"},
    ).json()
    request = {
        "headers": {**auth("spm_test_claude"), "Idempotency-Key": "retry-update"},
        "json": {"session_id": "s1", "content": "Version two", "base_version": 1},
    }

    first = client.put(f"/api/v1/memories/{created['id']}", **request)
    retry = client.put(f"/api/v1/memories/{created['id']}", **request)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["version"] == 2
    events = client.get("/api/v1/events?after=0", headers=auth("spm_test_claude")).json()
    assert [event["type"] for event in events["items"]] == [
        "memory.created",
        "memory.updated",
    ]


def test_panel_serves_living_brain_with_accessible_fallback(tmp_path):
    client = make_client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert 'id="brain-canvas"' in response.text
    assert 'id="activity-list"' in response.text
    assert "prefers-reduced-motion" in response.text
    assert "Space Memory" in response.text


def test_mcp_exposes_memory_tools(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'mcp.db'}",
        token_pepper="test-pepper",
        bootstrap_tokens={"spm_test_claude": {"space_id": "space-a", "agent_id": "claude"}},
    )

    tools = {tool.name: tool for tool in asyncio.run(app.state.mcp.list_tools())}
    assert set(tools) >= {
        "memory_remember",
        "memory_search",
        "events_after",
    }
    assert "token" not in tools["memory_remember"].inputSchema["properties"]


def test_events_resume_after_cursor_and_survive_app_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'space-memory.db'}"
    settings = dict(
        database_url=database_url,
        token_pepper="test-pepper",
        bootstrap_tokens={
            "spm_test_claude": {"space_id": "space-a", "agent_id": "claude"},
            "spm_test_codex": {"space_id": "space-a", "agent_id": "codex"},
        },
    )
    first_client = TestClient(create_app(**settings))
    first_client.post(
        "/api/v1/memories",
        headers={**auth("spm_test_claude"), "Idempotency-Key": "event-1"},
        json={"session_id": "s1", "content": "Persist me", "kind": "fact"},
    )
    cursor = first_client.get(
        "/api/v1/events?after=0", headers=auth("spm_test_claude")
    ).json()["next_cursor"]

    second_client = TestClient(create_app(**settings))
    memories = second_client.get(
        "/api/v1/memories", headers=auth("spm_test_codex")
    ).json()
    no_duplicates = second_client.get(
        f"/api/v1/events?after={cursor}", headers=auth("spm_test_codex")
    ).json()

    assert memories["total"] == 1
    assert memories["items"][0]["content"] == "Persist me"
    assert no_duplicates["items"] == []
