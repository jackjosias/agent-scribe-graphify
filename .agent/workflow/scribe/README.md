# SCRIBE Portable Workflow Bundle — V2.16

`.agent/workflow/scribe/` is the single portable SCRIBE/TENOR workflow root.

## Canonical entry

For a host LLM session, the human prompt is:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

The project-local skill is read first. The deterministic command is:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

Windows:

```powershell
py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
```

`tenor-init` is the public authority for installation, relocation and recovery. It classifies the project before touching SCRIBE, verifies Graphify, finalizes the installation, configures the verified project-local host entry, records a session and prints a redacted machine receipt.

`bootstrap` remains an internal/legacy command. It must not be documented as the normal V2.16 start and must not be used to bypass `TENOR_INIT_REQUIRED`.

## Local versus host readiness

After local init:

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

This proves only the project-local MCP server can start. Shell JSON-RPC is equally insufficient; the actual configured host process must prove the tools are visible.

Global readiness requires:

```text
local installation ready
real Graphify bound
local MCP listable
host tools visible
root binding proved
tenor_init_bridge OK
TENOR_INIT_READY
```

## Graphify contract

Canonical application outputs:

```text
.agent/state/outputs/graphify-out/graph.json
.agent/state/outputs/graphify-out/GRAPH_REPORT.md
.agent/state/outputs/graphify-out/graph.html
.agent/state/outputs/graphify-out/GRAPHIFY_READY.json
```

Supported explicit edge representations:

```text
nodes + edges
nodes + links
```

Real Graphify currently produces NetworkX node-link data with `links`. Missing, stale, wrong-root, stub, invalid or contradictory graphs block writes.

Bounded project build:

```bash
.agent/workflow/scribe/scribe graph --project-build --timeout 180
```

A human may explicitly increase the timeout for a large codebase.

## Layout

- `scribe` — canonical maintenance and TENOR CLI.
- `scribe-rag` — canonical agent memory retrieval interface.
- `sel/` — internal SCRIBE engine and manuals.
- `rag/` — BM25/hybrid retrieval implementation.
- `sel/docs/friction-policy.md` — smallest-safe-tier selector.
- `sel/docs/live-coordination.md` — agent-pool live coordination.
- `sel/docs/multi-agent-installation.md` — installation and six-terminal contract.

Root `./scribe`, root `scripts/` and root `graphify-out/` are legacy compatibility surfaces, not canonical V2.16 paths.

## Retrieval policy

Agents retrieve through `scribe-rag` or MCP `scribe_query`, not by reading `AGENT-MEMOIRE_PROJECT_STATUS.scribe` directly.

SCRIBE answers why, what pain occurred, what was rejected and what must not be repeated. Graphify answers what exists, where it lives, how it connects and what the blast radius is.

A memory query is valid only when its result changes the plan or is explicitly challenged.

## Task workflow

A product mutation requires:

```text
tenor_task_start(objective, intent, resources, scope)
  -> targeted SCRIBE + Graphify inside TENOR
tenor_apply_changeset(task_id, changes[], validators[])
  -> all-file preflight + deterministic locks + atomic commit/rollback
  -> runtime SCRIBE receipt + terminal closure
```

Direct native writes are not an equivalent fallback.

Machine invariants:

- `intent` is exactly `read`, `write` or `delete`;
- one process-bound identity owns at most one active task;
- task tools reject caller-supplied identity/context credentials;
- cross-agent task control is forbidden;
- daemon heartbeat and rolling TTL preserve live work but expire dead work;
- a multi-file changeset commits all files or restores all files;
- a runtime SCRIBE receipt requires a validated committed changeset.

The host sees only four normal task tools. Fine-grained legacy tools remain
internal compatibility primitives and are not a public workflow.

Graphify is rebuilt with `graphify_project_build` from a bound MCP host, or
with `.agent/workflow/scribe/scribe graph --project-build --timeout 180` before
host binding. Both publish only to `.agent/state/outputs/graphify-out/`.
Standalone `graphify update .` and root `graphify-out/` are forbidden.

## Multi-agent startup

Every terminal runs its own TENOR INIT. The shared bootstrap is serialized; each terminal receives a separate identity and proof.

Agents share runtime SQLite, SCRIBE, Graphify and transaction authority, but
never share process-bound identity or proof. `tenor_activity` shows consolidated
presence and current/last/next task state without granting cross-agent control.

`TENOR_INIT_SAME_PROJECT` must never purge active coordination.

## CI and validation

Primary gates:

```bash
.agent/workflow/scribe/scribe-rag gate
python3 .agent/scripts/validation_suite.py
```

The V2.16 portability workflow covers Ubuntu, macOS and Windows. Linux deep validation covers integration/red-team scenarios and Git hygiene.

A green CI run does not replace host-UI terrain proof.

## Documentation synchronization

After every protocol evolution, update code, tests, generated templates, canonical docs and the PR body as one set.

Mandatory policy:

```text
.agent/docs/DOCUMENTATION_SYNC_POLICY.md
```

Canonical surfaces include:

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
```

Dated baselines and `.old` files are historical and non-authoritative.
