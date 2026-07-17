# SCRIBE/TENOR Internal Agent Rules — V2.16

This file governs maintenance of the SCRIBE/TENOR bundle itself. Product-project agents normally consume the shorter root `AGENTS.md`, `.agent/rules/scribe.md` and `.agent/skills/init-tenor/SKILL.md`.

## Canonical entry

Human/LLM trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical command:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

`tenor-init` is the only public V2.16 installation/relocation/recovery authority. `bootstrap` is internal/legacy and may not be presented as the normal start.

## Graphify scopes

- Application graph: `.agent/state/outputs/graphify-out/`.
- SCRIBE bundle graph: `.agent/state/outputs/scribe-out/bundle-graph/`.
- Root `graphify-out/` is legacy-only.
- Never run `graphify update .` or `graphify watch` in a portable application project. Use MCP `graphify_project_build` after host binding or the bounded SCRIBE project-build command before binding.
- Do not mix application and bundle graphs.
- `.agent/`, `.agents/`, `.codex/`, generated outputs, SCRIBE memory and host-rule surfaces must remain excluded from the application graph where appropriate.

Real application Graphify uses NetworkX node-link JSON with `nodes + links`. Historical supported fixtures may use `nodes + edges`. The readiness adapter must reject absent, invalid, stale, wrong-root, stub or contradictory edge collections.

## Canonical commands

```bash
SCRIBE=.agent/workflow/scribe/scribe
SCRIBE_RAG=.agent/workflow/scribe/scribe-rag
```

- Maintenance/write engine: `$SCRIBE`.
- Agent causal retrieval: `$SCRIBE_RAG`.
- Do not call SEL direct retrieval for normal agents.
- Root `./scribe` and root `scripts/` are compatibility adapters only.

## Identity and installation

Project identity is decided before SCRIBE from the installation manifest, current resolved root, previous root and project fingerprint.

A relocation may purge only copied project-bound `.agent/state/`. It must preserve destination SCRIBE memory and portable engine files.

The local server is non-destructive and returns exit code `78` with `TENOR_INIT_REQUIRED` until the installation manifest is ready.

## Host gate

`python3 .agent/mcp/server_entry.py --list-tools` and shell JSON-RPC prove only local server readiness.

Before `TENOR_INIT_READY`, a real host must prove:

```text
MCP tools visible to the LLM
correct workspace root binding
tenor_init_bridge OK
```

Without those proofs:

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

## Multi-agent contract

- Every terminal runs its own TENOR INIT.
- Shared init is serialized by an owned nonce lock.
- Fresh partial lock files use filesystem `mtime` for age fallback.
- `SAME_PROJECT` never purges active shared runtime.
- Every agent has a distinct identity bound to its successfully bridged MCP process.
- Reads may run concurrently.
- Public task calls do not accept caller-supplied identities or context tokens.
- Writes use one atomic multi-file changeset; TENOR acquires ordered locks, preflights every hash, validates and either commits all files or rolls all files back.
- No agent may retire, replace, pause, cancel or finish another agent's task.
- A daemon heartbeat and rolling task TTL keep genuinely active work alive; stale/dead processes still expire fail-closed.

## Atomic-write contract

All shared metadata/document writers must use an exclusively created temporary file in the destination directory, flush and `fsync`, then `os.replace`.

Timestamp-derived temporary names are forbidden as uniqueness guarantees. Use `tempfile.mkstemp()` or an equally strong exclusive creation primitive.

This applies to installation manifests, host instruction repair and future shared-surface writers.

## Task workflow

For code or architecture work:

```text
tenor_task_start(objective, intent, resources, scope)
  -> internal targeted SCRIBE + Graphify
tenor_apply_changeset(task_id, changes[], validators[])
  -> internal preflight + ordered locks + atomic apply/rollback
  -> internal SCRIBE record + terminal task closure
```

Machine intent is restricted to `read`, `write`, `delete`. One process-bound
identity owns at most one active task and cannot be replaced or retired to
escape a fail-closed verdict. `tenor_activity` reports agent/task/current/last/next
state. `tenor_task_control` is owner-only for pause/resume/cancel/read-finish.

The older fine-grained tools remain internal compatibility primitives and are
not advertised to host models. They are runtime implementation details, not a
workflow the host model should orchestrate.

For read-only or research work, use the smallest safe tier and do not manufacture write ceremony.

## SCRIBE causal policy

- Graphify stores/deduces structural facts.
- SCRIBE stores causal facts that Graphify cannot derive.
- A query is not a checkbox; use or explicitly reject its result.
- Do not fabricate SCAR/GHOST/PAT entries to improve dashboard ratios.
- Record concrete future pain, root cause, durable decision, rejected alternative, regression protection or debt.
- Before closing, ask: “What will hurt the next LLM if this is not documented?”

## Retrieval policy

Normal agents use:

```bash
$SCRIBE_RAG context
$SCRIBE_RAG query "<question>"
$SCRIBE_RAG explain <ID>
$SCRIBE_RAG challenge "<plan>"
$SCRIBE_RAG preflight --tier <NANO|STANDARD|CRITICAL> "<plan>"
```

BM25 remains canonical while recall is adequate. Hybrid embeddings require actual recall-loss evidence, not merely an installed local model.

## SCRIBE writes

Before a canonical memory mutation:

```bash
$SCRIBE workflow read --agent <agent> --type <type>
$SCRIBE workflow check --agent <agent>
$SCRIBE sync --agent <agent> --type <type>
$SCRIBE doctor --suggest-fix
$SCRIBE lock acquire --agent <agent> --type <type> --session <JOURNAL-ID>
```

After mutation:

```bash
$SCRIBE doctor --suggest-fix
$SCRIBE sync --repair --agent <agent> --type <type> --session <JOURNAL-ID> --changed-id <ID> --write-kind <kind>
$SCRIBE lock release --agent <agent>
```

Doctor/read-only commands must remain available without taking the memory write lock.

## Graphify hook compatibility

Known historical failures:

- unsupported `hookSpecificOutput.additionalContext` in PreToolUse output;
- hook commands exiting before consuming stdin and causing broken pipes.

Rules:

- never reintroduce `additionalContext` or `hookSpecificOutput` in project PreToolUse hooks;
- commands receiving hook stdin must consume it before exit;
- after Graphify reinstall/upgrade, reapply and simulate Codex/Gemini hooks with representative JSON stdin;
- installed upstream templates may reintroduce old defects, so checking only this repository is insufficient.

## Versioning boundaries

- Product commits normally include product source only.
- `.agent/state/outputs/graphify-out/` and `.agent/state/outputs/scribe-out/` are generated/local by default.
- Canonical SCRIBE memory is versioned only when the team intentionally shares it.
- `.agent/` is versioned only when maintaining/distributing the agent tooling.
- Run `$SCRIBE worktree` before delivery to separate product source, intentional tooling, memory and generated noise.

## Documentation synchronization

The authoritative maintenance process is `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.

Any change to protocol semantics must update, as relevant:

```text
README.md
AGENTS.md
.agent/rules/scribe.md
.agent/rules/tenor-init-v2.json
.agent/skills/init-tenor/SKILL.md
.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md
.agent/docs/V2.16_TERRAIN_FINDINGS.md
.agent/docs/hosts/README.md
.agent/workflow/scribe/README.md
.agent/workflow/scribe/sel/docs/AGENTS.md
.agent/workflow/scribe/sel/docs/scribe.md
.agent/workflow/scribe/sel/docs/multi-agent-installation.md
.agent/host_adapter/templates.py
.agent/workflow/scribe/sel/scripts/scribe_install_templates.py
PR body
```

Dated baselines, `.old` files and historical reports are evidence, not current authority.

## Acceptance

Bundle changes require:

```bash
$SCRIBE_RAG gate
python3 .agent/scripts/validation_suite.py
git diff --check
```

They also require the V2.16 portable matrix and a clean post-test checkout. CI does not replace real host-visible MCP proof.
