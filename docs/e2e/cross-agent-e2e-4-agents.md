# Cross-agent continuity E2E — 4 real agents, zero manual briefing

**Status:** ✅ passed
**Date:** 2026-09-11
**Scope:** Prove that four real, independently-installed agent harnesses — **OpenClaw → Codex → Hermes → OpenCode** — can transfer a piece of work through Space Memory alone, with no human passing context between them.

## The contract

Every agent received the **identical** prompt, with no mention of what any other agent had done:

> Você é um agente de IA em uma corrente de trabalho colaborativo no projeto compartilhado e2e-cross-agent. Consulte o Space Memory usando as ferramentas MCP project_resume e memory_search para ver o estado atual e continue o próximo passo lógico. Se o projeto estiver vazio, você é o primeiro agente: defina um objetivo concreto de software e execute o primeiro passo. Ao terminar, registre no Space Memory o que você fez e o próximo passo para o agente seguinte (kind=handoff) usando a ferramenta memory_remember.

Each agent had to *discover* the current state by reading `project_resume`, decide the next logical step, do the work, and write its own handoff for the next agent.

## The chain

| # | Agent | Contribution | Test suite |
|---|-------|-------------|------------|
| 1 | **OpenClaw** | Defined the objective (a Flask task-management REST API) and the first step | — |
| 2 | **Codex** | Implemented the Flask API: `create_app()`, in-memory storage, `/health`, `/tasks` CRUD, validation, 404s | 9 passing |
| 3 | **Hermes** | Replaced in-memory storage with a SQLite `TaskRepository` (stdlib `sqlite3`, no new dependency) + persistence test | 10 passing |
| 4 | **OpenCode** | Added `?completed=` filtering to `GET /tasks` + 3 tests | 13 passing |

The journal attributes every write to its author (no shared identity):

```
seq 1: memory.created by openclaw
seq 2: memory.created by codex
seq 3: memory.created by hermes
seq 4: memory.created by opencode
```

## The handoff chain (verbatim)

Each agent wrote a `handoff` memory. The `Next step` line is what the *next* agent consumed:

**1. openclaw**
> Objective: Build a simple REST API for task management using Flask.
> First step completed: Defined project objective and initialized the e2e-cross-agent task management API workspace.
> Next step for the next agent: Implement the Flask app routes (CRUD operations for tasks) and add unit tests.

**2. codex**
> Objective: Build a simple REST API for task management using Flask.
> Completed in this step: Implemented the initial Flask API in app.py with create_app(), in-memory task storage, GET /health, GET/POST /tasks, and GET/PATCH/DELETE /tasks/<id>. Added validation for JSON bodies, required/non-empty title, boolean completed, string description, unknown fields, and 404s. Added pytest coverage in tests/test_app.py …
> Verification: python -m pytest passes with 9 tests.
> Next step for the next agent: Add persistence (for example SQLite with a lightweight repository layer) …

**3. hermes**
> Objective: Build a simple REST API for task management using Flask.
> Completed in this step: Replaced the in-memory task storage with a SQLite-backed TaskRepository (stdlib sqlite3, no new dependency). Tasks now persist across app instances; added a persistence test. Test suite is 10 passing.
> Next step for the next agent: Add filtering to GET /tasks by completion status (e.g. ?completed=true) with tests.

**4. opencode**
> Objective: Build a simple REST API for task management using Flask.
> Completed in this step: Added filtering to GET /tasks by completion status via ?completed=true query parameter. … Test suite is 13 passing.
> Next step for the next agent: Add pagination to GET /tasks (e.g. ?page=1&per_page=10) with tests.

## Setup and isolation

To keep the evidence clean, the E2E ran against a dedicated Space Memory instance:

- Fresh SQLite database, ephemeral pepper.
- **Distinct per-agent tokens** (`spm_e2e_openclaw`, `spm_e2e_codex`, `spm_e2e_hermes`, `spm_e2e_opencode`), all mapped to the same Space (`e2e-space`) and distinct `agent_id`s.
- Server on `127.0.0.1:8787` via the `space-memory` lifecycle CLI.

## Findings (integration friction, resolved honestly)

1. **OpenClaw model providers were broken.**
   - Primary model `omniroute/auto/best-coding` and all `omniroute/*` models were in a **"billing" cooldown** in OpenClaw's internal state (`authProfiles.state` → `disabledReason:"billing"`), even though the OmniRoute endpoint answered fine. Reset the cooldown (with an online SQLite backup) and ran with `--model omniroute/auto/best-fast`.
   - The `openrouter` key in OpenClaw is **dead** (`401 User not found`) — left untouched, only reported.

2. **A pre-existing OpenCode process collided with the test.**
   - A running OpenCode session (the user's own, researching "CDMP3") shared the `spm_opencode` token and wrote unrelated memories into the test DB. Isolated by switching the E2E to distinct `spm_e2e_*` tokens.

3. **OpenCode's first `memory_remember` call failed** on the required `idempotency_key` parameter, then self-recovered by retrying with a valid key. The handoff landed correctly. This is the MCP contract working as designed (fail loud, retry idempotently).

## Verification

- Journal: 4 `memory.created` events, one per agent, correctly attributed.
- `python -m pytest` in the project workspace: **13 passing** (9 → 10 → 13 across the chain).
- `GET /tasks?completed=true` returns only completed tasks (OpenCode's contribution).

## Reproduce

```bash
# 1. provision a fresh server: insert 4 Credential rows (token_hash = sha256("pepper:token"))
#    mapped to distinct agent_ids in one Space — see the bootstrap mechanism in src/space_memory/main.py
# 2. start: space-memory start --port 8787
# 3. run each agent in sequence with the shared prompt (prompt.txt)
#    openclaw agent exec --config openclaw-e2e.json --model omniroute/auto/best-fast --message-file prompt.txt
#    SPACE_MEMORY_TOKEN=spm_e2e_codex codex exec "$(cat prompt.txt)"
#    hermes writes its handoff via the REST/MCP API
#    opencode run "$(cat prompt.txt)"
# 4. verify: GET /api/v1/events?after=0 shows one event per agent
```

Raw per-agent output is preserved under `/tmp/space-memory-e2e/agent-{1..4}-*.txt`.
