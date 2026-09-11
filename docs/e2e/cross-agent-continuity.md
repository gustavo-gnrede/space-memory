# Cross-Agent Continuity — E2E evidence

Proves the core promise: **Agente A starts → Agente B continues without manual
briefing → Agente C recovers the correct decision**, with attribution, timestamps,
conversation id, and a repeatable procedure.

## Scenario

Three distinct agent identities (separate keys) share one Space (`demo-space`)
and one project (`demo`), linked by `conversation_id=demo-conv`.

1. **Agent A = OpenClaw** — writes the initial handoff.
2. **Agent B = OpenCode** — resumes via `project_resume` (no manual briefing) and
   continues from what it recovered.
3. **Agent C = Hermes** — recovers the final chain and confirms the decision.

## Result (real agents, 2026-09-11)

| seq | agent | action | content |
|-----|-------|--------|---------|
| 1 | openclaw | handoff | "We chose FastAPI for the API layer. Next step: add authentication." |
| 2 | opencode | handoff (after `project_resume`) | "Authentication added using JWT tokens with bcrypt password hashing. Configured /auth/login and /auth/register endpoints. Next step: implement role-based access control (RBAC)." |

`project_resume` (Hermes key) returned `last_agent: opencode` and the full chain
above. OpenCode's continuation ("authentication") was derived from what it read in
`project_resume`, not from the prompt — the prompt only said "resume and continue".

## What the schema records

- `written_by`: `openclaw` / `opencode` (attribution per memory).
- `project_id`: `demo` (scope).
- `conversation_id`: `demo-conv` (incremental conversation grouping).
- `updated_at` + `created_at`: timestamps.
- Event journal: `memory.created` per agent, ordered by `sequence`.

## Reproduce (real agents)

```bash
# 1. Start server with three keys in the same Space
rm -f /tmp/space-memory-e2e.db
.venv/bin/python /tmp/space_memory_e2e_server.py   # port 8787, 3 tokens

# 2. Wire agents to the Space Memory MCP (once)
openclaw mcp add space-memory --url http://127.0.0.1:8787/mcp/ \
  --transport streamable-http --header "Authorization=Bearer spm_openclaw" --approval approve
opencode mcp add space-memory --url http://127.0.0.1:8787/mcp/ \
  --header "Authorization=Bearer spm_opencode"

# 3. Agent A (OpenClaw) writes the first handoff
openclaw agent exec --cwd /root/space-memory --json --timeout 300 \
  "Use space-memory MCP. Call memory_remember with content='We chose FastAPI... next: add authentication', session_id='openclaw-1', idempotency_key='demo-handoff-1', kind='handoff', project_id='demo', conversation_id='demo-conv'."

# 4. Agent B (OpenCode) resumes and continues (no manual briefing)
opencode run -m omniroute/auto/best-coding \
  "Use space-memory MCP. Resume project 'demo' via project_resume(project_id='demo'). Take the next step and record a new handoff via memory_remember(kind='handoff', project_id='demo', conversation_id='demo-conv', session_id='opencode-1', idempotency_key='demo-handoff-2')."

# 5. Agent C (Hermes) recovers and confirms
curl -s http://127.0.0.1:8787/api/v1/projects/demo/resume -H 'Authorization: Bearer spm_hermes'
```

## Acceptance criteria

- [x] Agent A writes a handoff (`written_by=openclaw`).
- [x] Agent B recovers via `project_resume` and continues from the recovered state.
- [x] Agent C recovers the correct final decision (`last_agent=opencode`).
- [x] Attribution, timestamps, `project_id` and `conversation_id` are recorded.
- [x] Cross-Space isolation unchanged (Space B key sees nothing — covered by
      `tests/test_project_continuity.py::test_project_resume_is_isolated_by_space`).
- [x] Deterministic resume logic covered by `tests/test_project_continuity.py`.
