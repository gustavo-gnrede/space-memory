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
