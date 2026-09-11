# Space Memory

**Start with any agent. Continue with any other.**

Space Memory is an open-source shared memory and workspace layer for AI agents. It persists attributable memories in an append-only event journal and lets multiple agent-specific keys access the same isolated Space through MCP or HTTP.

> Early development preview. The current vertical slice proves durable memory, cross-agent reads, restart recovery, idempotency, optimistic concurrency, MCP tools, and the first living-brain web surface. It is not ready for production secrets.

## What works now

- Bearer keys stored as hashes
- Separate agents sharing one Space
- Append-only event journal
- Durable memory projection
- Keyword search without bundled AI
- Idempotent create/update operations
- Atomic optimistic version checks (`409` on stale writes)
- Cursor-based event catch-up after restart
- MCP Streamable HTTP tools:
  - `memory_remember`
  - `memory_search`
  - `events_after`
- Animated Canvas dashboard with accessible textual fallback

## Development

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Run a local development instance:

```bash
mkdir -p data
export SPACE_MEMORY_TOKEN_PEPPER="replace-with-a-random-secret"
export SPACE_MEMORY_BOOTSTRAP_TOKEN="spm_local_replace_me"
export SPACE_MEMORY_SPACE_ID="my-space"
export SPACE_MEMORY_AGENT_ID="my-agent"
.venv/bin/uvicorn space_memory.main:app --host 127.0.0.1 --port 8787
```

Open:

- Panel: `http://127.0.0.1:8787`
- MCP: `http://127.0.0.1:8787/mcp/`
- Health: `http://127.0.0.1:8787/health`

Never expose this development configuration directly to the internet. Production installation, PostgreSQL, TLS, admin onboarding, encrypted vault, backups, and signed installers are planned but not implemented yet.

## Architecture and product record

- [Product definition](PRODUCT.md)
- [First vertical slice](docs/architecture/vertical-slice.md)

## License

MIT
