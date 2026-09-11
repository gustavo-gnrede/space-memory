# Security Policy

Space Memory handles sensitive agent context and is under active development.

## Current status

The repository is an early development preview and is **not ready for production credentials or internet exposure**. The Secret Vault, encrypted backups, hardened installer, PostgreSQL RLS, administrator onboarding, and production TLS profile are not implemented yet.

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, personal data, or private infrastructure information. Use GitHub's private vulnerability reporting feature for this repository when available.

Include:

- affected version or commit;
- reproduction steps;
- expected and observed behavior;
- security impact;
- suggested mitigation, if known.

## Security invariants

- A credential is bound to one Space and one agent identity.
- Cross-Space access must fail even when IDs are manipulated.
- Credential secrets are never stored in plaintext.
- Stale concurrent writes must return a conflict, never silently overwrite.
- Events are visualized only after durable commit.
- Secrets must never be stored as ordinary searchable memories.

## Web panel authentication

The browser panel authenticates against the same Bearer contract as MCP/REST clients (`Authorization: Bearer <key>`), reusing the existing `authenticate` dependency — there is no second, parallel authentication system.

- The Space key is accepted through a password-type input and sent only in the `Authorization` header; it is never placed in the URL or a query string.
- The key is held in `sessionStorage` for the lifetime of the tab. It is **not** persisted in `localStorage`, cookies, or anywhere that survives the session.

**Decision and risk:** `sessionStorage` keeps this first functional slice minimal — it clears when the tab closes and needs no server-side session machinery. The residual risk is that a cross-site scripting (XSS) payload could read the in-memory key for the tab's lifetime; mitigations are the masked `type="password"` input, never writing the key to the visible DOM, logs, or error messages, and the server remaining the real authorization boundary. The planned evolution is short-lived, scoped human credentials (admin onboarding) delivered over an HTTP-only session cookie.
