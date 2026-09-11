import asyncio
import json
import socket
import threading
import time

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from space_memory.app import create_app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _bootstrap_app(tmp_path):
    return create_app(
        database_url=f"sqlite:///{tmp_path / 'transport.db'}",
        token_pepper="test-pepper",
        bootstrap_tokens={
            "spm_test_claude": {"space_id": "space-a", "agent_id": "claude"},
            "spm_test_other": {"space_id": "space-b", "agent_id": "other"},
        },
    )


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
            return server, thread
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("server did not start")


def test_mcp_search_returns_items_and_total_over_transport(tmp_path):
    app = _bootstrap_app(tmp_path)
    port = _free_port()
    server, thread = _serve(app, port)
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": "Bearer spm_test_claude"}
    try:
        for i, content in enumerate(["alpha fact", "beta fact"]):
            resp = httpx.post(
                f"{base}/api/v1/memories",
                headers={**headers, "Idempotency-Key": f"seed-{i}"},
                json={"session_id": "s1", "content": content, "kind": "fact"},
            )
            assert resp.status_code == 201

        async def run():
            async with httpx.AsyncClient(headers=headers, timeout=30) as http:
                async with streamable_http_client(f"{base}/mcp/", http_client=http) as (r, w, _):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        result = await session.call_tool("memory_search", {"query": "fact"})
                        return json.loads(result.content[0].text)

        found = asyncio.run(run())
        assert isinstance(found, dict), f"expected dict, got {type(found).__name__}"
        assert found["total"] == 2
        assert [item["content"] for item in found["items"]] == ["beta fact", "alpha fact"]
    finally:
        server.should_exit = True
        thread.join(timeout=5)
