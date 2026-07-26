<!-- SCRIBE-PORTABLE-WORKFLOW:START -->
## AGENT-SCRIBE-GRAPHIFY — V2.16 CANONICAL OPERATING CONTRACT

### Canonical session entry

Human/LLM trigger:

```text
TENOR INIT ::[— depuis la racine du workspace courant, lis comme un fichier local avec l’outil normal de lecture de fichiers — jamais avec un résolveur de skills — le chemin exact "./.agent/skills/init-tenor/SKILL.md"; n’utilise jamais "~/.agent", "~/.agents" ni aucun chemin global; applique ensuite intégralement ce fichier et continue automatiquement jusqu’à TENOR_INIT_READY, HOST_RECONNECT_REQUIRED ou un verdict FAIL_CLOSED explicite.]
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
- The public task surface is exactly `tenor_task_start`, `tenor_apply_changeset`, `tenor_activity`, `tenor_task_control`; bootstrap retains the five bounded init tools.
- `tenor_task_start` performs targeted SCRIBE and Graphify retrieval server-side and returns a hash-bound decision capsule. The host model must not replay the legacy internal choreography.
- Every mutation is submitted as one atomic multi-file `tenor_apply_changeset` with exact structured edits, fresh hashes and mandatory bounded validator argv arrays; TENOR owns locks, conditional non-destructive rollback, SCRIBE admission and closure.
- `TENOR_CHANGESET_ACCEPTED` and `GRAPHIFY_BUILD_ACCEPTED` are durable non-terminal acknowledgements. Poll `tenor_activity` or `graphify_required_check`; only the terminal job result proves commit, rollback or build completion.
- Long validators and Graphify builds run in bounded isolated workers so the MCP stdio loop remains available. Never resubmit an active job or control its task concurrently.
- A worker is authoritative only while its SQLite lease is live and its exact `(job_id, worker_instance_id, fence_token)` matches. PIDs are diagnostic only; recovery transfers a monotone fence and an older worker may not heartbeat, publish, rollback or release locks.
- Rollback preflights every current hash and restores only bytes still equal to the changeset's declared `new_hash`. A later writer or validator drift produces `TENOR_CHANGESET_ROLLBACK_CONFLICT` and its evidence is preserved.
- Graphify freshness uses `relative-path-size-content-sha256-v2`; `mtime` never changes graph identity. A rebuild is single-flight, waits for active changesets and fences new writers until the content-bound graph is current.
- Canonical SCRIBE promotion publishes the entry, deterministic digest proof, tripwire receipt and task-context flag transactionally. Every promoted summary is copied to `l0_abstract` and must be semantically retrievable.
- `replace` means complete file content. Destructive shrink requires path/base/new-hash confirmation; fragments and manual-patch fallback are forbidden.
- Every completed task has an explicit memory-admission verdict. Durable validated source knowledge is promoted; non-causal noise is retained runtime-only with a reason.
- Native shell/edit/write/apply-patch paths outside MCP are forbidden for project mutation.
- A prose-only “done” without a terminal machine verdict, decision capsule, validator evidence and memory admission is not completion.
- Each terminal uses its own process-bound identity and server-side one-time proof. Task calls never accept caller-supplied `agent_id` or context tokens.
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
