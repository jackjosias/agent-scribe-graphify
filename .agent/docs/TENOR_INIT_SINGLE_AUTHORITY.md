# TENOR INIT V2.16 — Single Authority

## Status and proof categories

This document is the architectural authority for V2.16. It distinguishes:

- **implemented** — present in branch code;
- **tested** — covered by an executable test;
- **CI-proven** — passed on a referenced commit/matrix;
- **terrain-proven** — observed in an isolated real project or host;
- **not yet proven** — an explicit release gate.

No category substitutes for another.

The current branch has already proved the local engine on Linux/macOS/Windows CI and on isolated projects, including a real codebase with more than 1,000 source files. The remaining global gate is the real host-LLM proof: tool visibility, root binding, session bridge, complete MCP micro-write and direct-write bypass test.

## Canonical entry

Human/LLM trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical command from the current project root:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown>
```

Windows-compatible command:

```powershell
python .agent/workflow/scribe/scribe tenor-init --type cli
```

The old `[[.agent/skills/init-tenor/SKILL.md]]` form is compatibility-only. New documentation and templates must emit the canonical trigger above.

`bootstrap` is an internal/legacy primitive. It is not the public V2.16 authority for installation, relocation or recovery.

## Purpose: LLM Experience

`.agent` is not a script collection. It externalizes cognitive and operational capacities:

- **Graphify** compresses structure, dependency, centrality, communities and blast radius;
- **SCRIBE** retains causality, pain, decisions, regressions, prohibitions, SCAR, GHOST and `ne_pas_reproposer`;
- **TENOR** turns the protocol into the next mechanically safe action;
- **runtime/MCP** supplies shared live coordination: identity, claims, resource locks, leases, patch queue and closure.

A disciplined small LLM should gain durable operational reflexes without relying on its conversation memory or reading the entire repository.

## Installation identity authority

Project identity is decided before SCRIBE from:

1. the actually resolved project root;
2. `.agent/state/install/agent-installation.json`;
3. the previously recorded root;
4. the current project marker fingerprint.

Classifications:

```text
TENOR_INIT_NEW_INSTALLATION
TENOR_INIT_SAME_PROJECT
TENOR_INIT_RELOCATED_PROJECT
TENOR_INIT_LEGACY_INSTALLATION
TENOR_INIT_CORRUPT_INSTALLATION
```

`AGENT-MEMOIRE_PROJECT_STATUS.scribe` never decides whether the project is new. After classification, it produces only:

```text
SCRIBE_MEMORY_ADOPT
SCRIBE_MEMORY_CREATE
```

## Local transaction

Installation states:

```text
preparing
ready
```

Canonical order:

```text
RESOLVE
CLASSIFY
RESET_IF_REQUIRED
ADOPT_PROJECT
ADOPT_MEMORY
VERIFY_GRAPH
FINALIZE_INSTALLATION
VERIFY_LOCAL_MCP
CONFIGURE_AND_VERIFY_HOST
PROVE_ROOT_BINDING
BRIDGE_SESSION
TENOR_INIT_READY
```

`server_entry.py` is non-destructive. If the manifest is not ready it returns exit code `78` with `TENOR_INIT_REQUIRED`. It does not purge, relocate or create hidden runtime state.

Any failure before finalization leaves the manifest in `preparing`.

## Relocation contract

A relocation from A to B:

- purges only copied `.agent/state/` bound to A;
- preserves the portable `.agent` engine;
- preserves canonical SCRIBE memory already present in B;
- rejects A's sessions, proofs, locks, outputs and bindings;
- writes B's installation manifest;
- rebuilds derived state for B.

Purge paths are validated, external symlinks are rejected and transient deletion failures use bounded backoff.

## Multi-agent and six-terminal contract

Shared bootstrap is serialized by `.agent/.tenor-init.lock`.

The owned lock records nonce, PID, hostname, root, stage, creation time and heartbeat. A fresh partial lock must fall back to its filesystem `mtime`; it must never be interpreted as epoch zero. A waiter may remove only the exact stale nonce it re-observed.

Each terminal then receives an independent session. Agents share runtime, SCRIBE, Graphify and coordination data, but never share:

- `agent_id`;
- proof token;
- action lease;
- claim ownership;
- resource-lock ownership.

Manifest finalization is an in-process transaction around write plus gate inspection; the TENOR file lock remains the inter-process authority.

## Atomic file writes

Portable atomic writes must use exclusively created temporary files in the destination directory, `fsync`, then `os.replace`.

Timestamp-derived temporary names are forbidden as uniqueness guarantees. V2.16 uses `tempfile.mkstemp()` for installation manifests and host instruction updates. Tests cover concurrent finalization and concurrent host-instruction repair.

## Graphify readiness

Canonical outputs:

```text
.agent/state/outputs/graphify-out/graph.json
.agent/state/outputs/graphify-out/GRAPH_REPORT.md
.agent/state/outputs/graphify-out/graph.html
.agent/state/outputs/graphify-out/GRAPHIFY_READY.json
```

File presence is not proof. The validator checks:

- parseable JSON;
- `nodes` as a list;
- exactly one supported edge representation, or two equivalent non-contradictory representations:
  - historical `edges` list;
  - real NetworkX node-link `links` list;
- non-empty report and HTML;
- supported readiness manifest;
- bound root equal to current root;
- bound workspace fingerprint equal to current fingerprint;
- no forbidden smoke/placeholder marker;
- authorized manifest kind;
- a real non-empty graph for a project containing application code.

Primary verdicts:

```text
GRAPHIFY_READY
GRAPHIFY_EMPTY_PROJECT_READY
GRAPHIFY_TEST_FIXTURE_READY
GRAPHIFY_MISSING
GRAPHIFY_OUTPUTS_INCOMPLETE
GRAPHIFY_STUB_INVALID
GRAPHIFY_CORRUPT
GRAPHIFY_LEGACY_UNBOUND
GRAPHIFY_STALE_ROOT
GRAPHIFY_STALE_WORKSPACE
GRAPHIFY_FIXTURE_FORBIDDEN
GRAPHIFY_MANIFEST_INVALID
```

Smoke fixtures have an explicit scoped lifecycle and are forbidden in terrain TENOR INIT.

Project build is explicit and bounded:

```bash
.agent/workflow/scribe/scribe graph --project-build --timeout 180
```

TENOR INIT never launches a hidden heavy build. A human may explicitly increase the bound for a large codebase.

## SCRIBE operational contract

Before any significant task, retrieve targeted causal context. Results must influence the plan:

- SCAR — prior wound and protective test;
- GHOST — rejected approach or detected drift;
- `ne_pas_reproposer` — negative memory;
- decision/invariant — current constraint;
- debt — accepted active risk.

Executing a query and ignoring it is not memory use. A runtime `scribe_record` receipt is not automatically canonical memory.

The Graphify/SCRIBE bridge refuses structural drift analysis on missing, stub, stale or wrong-root graphs.

## Task write contract

A mutation requires:

```text
workflow_next
before_task
targeted scribe_query
targeted graphify_query
pre_action_guard
resource_lock_claim
claim_resource
file_hash
propose_patch
apply_patch
workspace_audit
scribe_record or auditable causal skip
release claim and lock
finish_task
workflow_next -> READY_FOR_NEXT_TASK
```

Native host shell/edit/write/apply-patch paths are not accepted as equivalent.

## Host integration

Correct order:

1. local TENOR INIT;
2. Graphify ready;
3. local MCP server listable;
4. read the actual host guide;
5. configure workspace-local integration when supported;
6. restart/reconnect the host if required;
7. prove tools are visible in the LLM interface;
8. prove root binding;
9. call `tenor_init_bridge`;
10. obtain `TENOR_INIT_READY`.

`server_entry.py --list-tools` never proves host visibility. Before host proof:

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

No global/user configuration and no Chrome/DevTools installation is performed without real need and explicit permission.

## Root binding

Host and MCP must hash the same stable sentinel in the current workspace. A mismatch yields:

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

No product write is allowed while root binding is unproven.

## Retry and degradation

Exit code `78` is a deterministic safety verdict, not a transient network error. Policy, import, JSON and argument failures surface immediately. Exponential retries are reserved for explicitly transient conditions and remain bounded.

If the Graphify binary is missing but a valid, current, bound graph exists, structural reads may continue while rebuild remains unavailable. If the graph becomes stale, writes are blocked.

## Portability

The canonical path uses:

- `pathlib.Path`;
- subprocess argument lists with `shell=False`;
- Python timeouts;
- exclusive temporary files plus `fsync + os.replace`;
- owned `O_EXCL` locks;
- `os.pathsep`;
- no required `/tmp`, GNU `timeout`, `grep`, `sed`, `flock` or POSIX-only chmod behavior.

The portability workflow runs Ubuntu, macOS and Windows. Linux deep validation separately covers integration and red-team scenarios.

## Terrain evidence acquired

Isolated minimal project:

- relocation detected and old state purged;
- target SCRIBE created;
- Graphify missing blocked false readiness;
- real Graphify schema identified as `nodes + links`;
- second TENOR INIT became `SAME_PROJECT` without repurge;
- local MCP tools listed successfully.

Isolated copy of `algowebsite`:

- original branch/head/status and SCRIBE hash remained unchanged;
- 18,760-line SCRIBE memory adopted byte-for-byte;
- 1,025 files analyzed;
- real graph built with 3,661 nodes and 5,714 links;
- final installation manifest became `ready`;
- second TENOR INIT was idempotent;
- 51 local MCP tools listed.

These are local/codebase proofs, not host-visibility proofs.

## Remaining release gates

The branch must remain draft until all are complete on the final head:

- full portable matrix green;
- Linux deep validation green;
- post-test checkout clean;
- complete diff audit;
- real host LLM sees the tools;
- correct root binding proved;
- `TENOR_INIT_BRIDGE_OK` proved;
- one complete MCP micro-write;
- native direct-write bypass attempt refused or detected;
- real six-terminal terrain replay;
- docs, generators and PR body synchronized.

## Terminal success criterion

The only global success is:

```text
TENOR_INIT_READY
```

It requires local installation ready, SCRIBE adopted/created, Graphify valid, local MCP ready, tools visible in the real host, root binding proved and the independent session bridged.

## Documentation governance

All future protocol changes must follow `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`. Code, tests, canonical docs, generated templates and PR description must move together.
