# First Vertical Slice

## Goal

Prove the complete continuity loop with real persistence:

1. An authenticated agent writes a memory event.
2. The server durably appends it to a journal before acknowledging it.
3. A materialized memory view becomes searchable.
4. A second agent using another key in the same Space can retrieve it.
5. The web panel receives the confirmed event and animates a neural connection.
6. Restarting server or agent does not lose the memory or cursor.

## Chosen stack

- Python 3.11+
- FastAPI + Uvicorn
- SQLAlchemy 2
- PostgreSQL 16 in deployment; SQLite only for isolated unit/integration tests
- Official Python MCP SDK for Streamable HTTP
- Server-rendered/static HTML, CSS and Canvas 2D for the first live brain
- SSE delivered through authenticated `fetch()` streaming
- Docker/Compose for deployment

## Why

This keeps the first slice small and cross-platform, while preserving a direct path to PostgreSQL RLS. Canvas 2D proves the real-time visual mechanism without committing the project to a large frontend framework or WebGL before the interaction is validated.

## Domain boundaries

- Identity: Spaces, agents, credentials
- Journal: immutable accepted events
- Memory: versioned materialized memory records
- Projects: resume state and cursors
- Vault: reserved boundary; no secret values in the first slice

Dependencies flow from API/MCP adapters into application services, then repositories. Domain events do not depend on MCP or web schemas.

## Write contract

Every write includes:

- `space_id` from the authenticated credential, never trusted from payload
- `agent_id` from the authenticated credential
- `session_id`
- `idempotency_key`
- event type and payload
- optional aggregate ID and `base_version`

The database transaction inserts the immutable event and updates the materialized view. A unique constraint deduplicates `(space_id, agent_id, idempotency_key)`. Version mismatch returns conflict; no silent last-write-wins.

## Read contract

All reads are scoped by the authenticated credential's `space_id`. Project and memory IDs are secondary filters, never tenant selectors.

## Real-time contract

Events are published to the panel only after transaction commit. Browser reconnection passes a durable sequence cursor and catches up from the journal before following live events.

## Deferred

- Semantic search and embeddings
- OAuth 2.1
- S3/MinIO
- Multiple human administrators and enterprise RBAC
- Full Secret Vault implementation
- WebGL/Three.js brain
- Native hooks for every agent
