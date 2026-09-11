# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated technical choice: an open-source, cross-platform server distributed through Docker for Linux/VPS and Windows, with MCP over Streamable HTTP, a browser-based control panel, persistent database, object/file storage, and official agent connectors. The implementation stack must prioritize easy installation, security, portability, and low operational complexity. No language model is bundled.

## Users

Individuals and teams that use multiple AI agents—such as Claude Code, Codex, Hermes, OpenClaw, OpenCode, DeepSeek harnesses, and other MCP clients—and need to continue the same project across agents without losing memory, context, files, or state.

## Product Purpose

Space Memory is an open-source shared memory and workspace plugin for AI agents. It persists agent sessions, durable memories, project state, handoffs, files, artifacts, checkpoints, backups, and audit provenance independently of any agent's lifecycle. An agent can stop or restart and reconnect to the same Space to save new activity or resume an existing project.

Success means a user can start work with one agent, switch to another, and resume from a durable, attributable, conflict-safe state.

## Positioning

Space Memory is a neutral shared layer rather than an AI model or chatbot: one persistent Space can be consumed and fed by heterogeneous agents through MCP, while native connectors provide verified real-time mirroring and restart reconnection where each runtime supports hooks.

## Operating Context

- Runs locally on Windows/Linux or continuously on a VPS/server.
- Starts automatically with the host; memory remains available with zero agents online.
- Provides a web panel and an authenticated remote MCP endpoint.
- Agents receive separate credentials tied to the same Space for revocation, permissions, and attribution.
- Official connectors continuously synchronize events, spool encrypted events while offline, and resume from the last acknowledged cursor.
- Git remains the source of truth for versioned code; Space Memory stores context, handoffs, manifests, artifacts, and checkpoint references.

## Capabilities and Constraints

- Open source under the MIT license.
- Compatible with any MCP client at the generic integration level.
- Official connectors may provide managed auto-connect, full-session mirroring, offline spool, and automatic project resume.
- Generic MCP alone cannot force a model to call memory tools or inspect private agent state; the UI must disclose each integration's actual guarantee: `managed`, `configured`, or `manual`.
- No bundled generative AI, embedding model, Ollama, or GPU dependency.
- Supports full session and artifact capture when explicitly enabled for a Space.
- Uses an append-only event journal as the authoritative collaboration history.
- Concurrent updates use optimistic versioning; active work may use scoped leases. Silent last-write-wins is forbidden.
- Events are isolated by account, Space, project, agent, and session to prevent agents from undoing or reading unrelated work.
- Memories, sessions, project state, files, and secrets are separate domains with stable versioned schemas to reduce coupling.
- Passwords, API keys, tokens, and private keys live in an encrypted Secret Vault, never in ordinary searchable memory.
- The normal memory store contains only opaque references to secrets.
- Access credentials authenticate and authorize an agent but are not directly used as encryption keys.
- Installation must be guided and return panel URL, MCP URL, temporary administrator user, and one-time password after real health checks.
- Open decisions: final application framework, database choice for the first vertical slice versus stable release, and whether the first public alpha includes object storage or only manifests/local artifacts.

## Brand Commitments

- Product name: **Space Memory**.
- Repository: `gustavo-gnrede/space-memory`.
- Core promise: **Start with any agent. Continue with any other.**
- Visual direction: a modern, original, game-like world with the energy and accessibility of social games such as Roblox, without copying Roblox branding, assets, characters, or trade dress.
- The first authenticated viewport is a living animated brain representing the Space, with neural connections created and consumed in real time from confirmed server events.
- Agents visibly appear as connected, feeding, consuming, syncing, waiting, conflicted, or offline.

## Evidence on Hand

The project currently has the public GitHub repository, MIT license, initial README, and a product/security planning document. There are no production benchmarks, customer claims, deployed endpoints, screenshots, or approved visual assets; future work must not fabricate them.

## Product Principles

1. **Durability before animation:** the UI only visualizes events after durable acknowledgement.
2. **Continuity with honest guarantees:** managed connectors automate capture and resume; generic MCP limitations remain visible.
3. **Collaboration without trampling:** event history, provenance, isolation, optimistic concurrency, and leases prevent silent overwrite.
4. **Secrets are not memories:** searchable context and encrypted credentials remain structurally separated.
5. **One-command operation without hidden risk:** installation is simple, verifiable, least-privileged, and recoverable.

## Accessibility & Inclusion

The animated brain must have a complete textual/table alternative, keyboard navigation, WCAG AA contrast, and `prefers-reduced-motion` support. Animation intensity degrades gracefully from 3D to simplified Canvas/SVG to a static operational view without removing functionality.
