# MCP enforcement levels — V2.16 compact surface

## Level 0 — advisory

Documentation alone cannot prevent a writable host from bypassing MCP.

## Level 1 — host permissions

Project-local host configuration denies native autonomous edit and broad shell
mutation where the host supports it. Unrelated MCP servers are preserved.

## Level 2 — compact public API

The host sees four normal task tools, not the internal state machine. Targeted
SCRIBE/Graphify retrieval and task creation are one server-side operation.
Multi-file mutation, validation, evidence and closure are one server-side
operation.

## Level 3 — process-bound authority

`tenor_init_bridge` binds the agent identity to the MCP process. Task calls do
not accept caller-provided identity or context tokens. Owner mismatch blocks
changeset application and task control.

## Level 4 — atomic mutation authority

The runtime enforces project-relative scope, symlink refusal, fresh per-file
hashes, deterministic locks, durable staging/backups, bounded no-shell
validators, all-file rollback, crash recovery and idempotent request ids.

## Level 5 — liveness and multi-agent evidence

Daemon heartbeat, rolling activity TTL and PID liveness distinguish an active
process from abandoned work. Parallel agents are preserved and observable via
`tenor_activity`; they are not heuristically retired.

## Residual boundary

A host or human with unrestricted operating-system write access can still
modify files outside MCP. Host permissions plus the direct-filesystem tripwire
reduce and detect that risk, but cannot turn an untrusted OS principal into a
trusted one. This boundary must be stated, not hidden behind a “100%” claim.
