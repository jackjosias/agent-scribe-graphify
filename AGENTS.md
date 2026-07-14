<!-- SCRIBE-PORTABLE-WORKFLOW:START -->
## AGENT-SCRIBE-GRAPHIFY — V2.16 CANONICAL OPERATING CONTRACT

### Canonical session entry

Human/LLM trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical command from the current project root:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

The project-local `.agent/skills/init-tenor/SKILL.md` and `.agent/rules/tenor-init-v2.json` are authoritative. `bootstrap` is an internal/legacy primitive, not the public V2.16 installation authority.

### Authority order

```text
resolve root
classify installation
purge only old project-bound runtime when relocation is proven
preserve canonical outputs and quarantine legacy conflicts
adopt/create target SCRIBE
verify/build and bind Graphify
finalize local installation
detect/configure the verified project-local host
reconnect and rerun if host configuration changed
verify local MCP
verify tools visible in the real host
prove MCP root binding
bridge the independent session
TENOR_INIT_READY
```

### Hard rules

- Never start product work before `TENOR_INIT_READY`.
- `server_entry.py --list-tools` and shell JSON-RPC prove only local MCP readiness, never host visibility.
- Never read `AGENT-MEMOIRE_PROJECT_STATUS.scribe` directly for normal agent retrieval; use `.agent/workflow/scribe/scribe-rag` or MCP `scribe_query`.
- SCRIBE results must change the plan or be explicitly challenged; retrieval is not a checkbox.
- Use Graphify before architecture or broad code changes; prefer targeted structure/blast-radius queries over mass file reads.
- Every mutation requires `pre_action_guard`, an action lease, resource lock/claim, file hash, patch queue, `workspace_audit`, release and `finish_task`.
- Native shell/edit/write/apply-patch paths outside MCP are forbidden for project mutation.
- A prose-only “done” without `finish_task` and `READY_FOR_NEXT_TASK` is not completion.
- Each terminal uses its own `agent_id`, server-side one-time proof and lease. The full bearer token is never printed or persisted.
- `TENOR_INIT_SAME_PROJECT` never repairs the bundle; only the verified project-local MCP entry and binding receipt may be managed automatically.
- A complete raw copy of `.agent/` is a mandatory supported installation path on Linux, macOS and Windows; relocation is classified from the current root and manifest.
- Runtime purge preserves `.agent/state/outputs/`; canonical output wins and conflicting legacy output is quarantined under `_legacy_migrated/`.
- Preserved Graphify output is never trusted automatically; root/fingerprint readiness must pass again.
- Graphify supports explicit `nodes + links` and historical `nodes + edges`; missing, stale, wrong-root, stub or contradictory graphs are rejected.
- Default commit/push scope is the host product source; `.agent/` changes require intentional tooling maintenance.
- Always keep `.agent/state/outputs/graphify-out/` and `.agent/state/outputs/scribe-out/` out of commits by default.
- Documentation and generators move together under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.

### Canonical surfaces

- `.agent/skills/init-tenor/SKILL.md`
- `.agent/rules/tenor-init-v2.json`
- `.agent/rules/scribe.md`
- `.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md`
- `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`
- `.agent/workflow/scribe/README.md`
- `.agent/workflow/scribe/sel/docs/scribe.md`
- `.agent/workflow/scribe/sel/docs/multi-agent-installation.md`
- `.agent/docs/hosts/README.md`

Historical `.old` files and dated baselines are not authoritative.
<!-- SCRIBE-PORTABLE-WORKFLOW:END -->
