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
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

Windows-compatible command:

```powershell
py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
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

## SAME_PROJECT bundle-integrity invariant

On `TENOR_INIT_SAME_PROJECT`, `bootstrap_project()` MUST NOT call the forced installer (`run_installer(force=True)`) and MUST NOT rewrite tracked bundle files (`AGENTS.md`, `.agent/rules/scribe.md`, `.graphifyignore`, `.agent/.gitignore`).

Allowed mutations are confined to the operational runtime layer under `.agent/state`, plus the exact verified project-local MCP entry for the detected host. This narrow exception creates or updates only `agent-scribe-graphify` and its binding receipt; it preserves unrelated host settings and MCP servers. SCRIBE, Graphify, doctor and runtime state are still verified.

Bundle drift in the managed files is *surfaced as a warning and never silently repaired* — bundle repair stays explicit and separate (`scribe install --force`). This is the V2.16.1 terrain fix for the confirmed defect where `bootstrap_project()` rewrote tracked files on every `SAME_PROJECT` session init.

Invariant (single sentence):

> SAME_PROJECT never repairs the bundle; project-local MCP binding is the sole managed configuration exception.

`NEW_INSTALLATION`, `RELOCATED_PROJECT` and `LEGACY_INSTALLATION` keep the bundle install when required. The regression is covered by `test_scribe_bootstrap.py::test_same_project_session_init_is_tracked_file_read_only` and `test_new_installation_still_calls_installer`.

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
DETECT_AND_CONFIGURE_VERIFIED_HOST
RECONNECT_AND_RERUN_IF_CHANGED
VERIFY_LOCAL_MCP
VERIFY_ACTUAL_HOST_PROCESS_BINDING
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
- rejects A's sessions, proofs, locks and bindings;
- preserves canonical outputs byte-for-byte while refusing stale Graphify until B's root/fingerprint validation passes;
- writes B's installation manifest;
- rebuilds derived state for B.

Purge paths are validated, external symlinks are rejected and transient deletion failures use bounded backoff.

## Complete raw-copy portability contract

A byte-for-byte copy of the complete `.agent/` directory into an unrelated project is a mandatory supported installation path on Linux, macOS and Windows. No sync helper is required. TENOR compares the copied manifest root with the resolved destination root before consulting SCRIBE, purges only copied project-bound coordination state when relocation is proven, preserves an existing destination `AGENT-MEMOIRE_PROJECT_STATUS.scribe`, creates it only when absent, and never accepts a copied Graphify root/fingerprint as current without revalidation.

The local next action is returned as structured argv using the running Python interpreter. A bare `python` command is not emitted on POSIX systems; Windows documentation uses `py -3` while host entries use the platform-resolved Python command.

## Multi-agent and six-terminal contract

Shared bootstrap is serialized by `.agent/.tenor-init.lock`.

The owned lock records nonce, PID, hostname, root, stage, creation time and heartbeat. A fresh partial lock must fall back to its filesystem `mtime`; it must never be interpreted as epoch zero. A waiter may remove only the exact stale nonce it re-observed.

Each terminal then receives an independent session. Agents share runtime, SCRIBE, Graphify and coordination data, but never share:

- `agent_id`;
- server-side one-time proof;
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
tenor_task_start(objective, intent, resources, scope)
  -> internal targeted SCRIBE and Graphify
tenor_apply_changeset(task_id, changes[], validators[])
  -> preflight all paths, hashes and locks before any write
  -> commit every file or rollback every file
  -> runtime SCRIBE evidence and terminal closure
```

Native host shell/edit/write/apply-patch paths are not accepted as equivalent.

Le champ machine `intent` est un enum strict : `read`, `write`, `delete`. La
description libre reste dans l'objectif. L'identité est liée au processus MCP
après le bridge et n'est plus fournie par le LLM. Une identité ne possède
qu'une tâche active et ne peut ni retirer ni contrôler un autre agent.

Une tâche multi-fichier reste une seule transaction. Elle exige un hash frais
par fichier, refuse traversal/symlinks/scope escape, acquiert des locks ordonnés
et exécute des validateurs argv bornés sans shell. Toute erreur restaure tous
les fichiers. Un record runtime n'est émis qu'après commit et validation.

Les anciens outils fins restent internes pour compatibilité ; ils ne sont pas
annoncés au host et ne doivent pas être orchestrés manuellement.

## Graphify sans sortie root

Le build avant liaison utilise :

```text
.agent/workflow/scribe/scribe graph --project-build --timeout 180
```

Après liaison, le host appelle `graphify_project_build`. Le wrapper travaille
dans un miroir isolé et publie uniquement sous
`.agent/state/outputs/graphify-out/`. `graphify update .`, `graphify watch` et
root `graphify-out/` sont interdits dans le projet portable.

## Host integration

Correct order:

1. local TENOR INIT;
2. Graphify ready;
3. detect the real host from explicit identity, host environment or one unambiguous project marker;
4. configure only the verified workspace-local integration for OpenCode, Claude Code or Codex;
5. fail closed to the exact guide for any other or ambiguous host;
6. restart/reconnect and rerun TENOR when configuration changed;
7. on the local non-terminal marker, call `tenor_init_bridge` immediately from the actual LLM tool interface without responding to the user;
8. let that single call prove tool visibility, MCP process binding, config environment, binding receipt, config hash and resolved root;
9. atomically consume the server-side one-time proof and bind the independent session;
10. obtain the terminal public verdict `TENOR_INIT_READY` from that same call.

`server_entry.py --list-tools` and manually piped shell JSON-RPC never prove host visibility. Before host proof:

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

No global/user configuration and no Chrome/DevTools installation is performed without real need and explicit permission.

TENOR never prints or persists the full proof bearer token. `tenor_init_bridge` normally receives only `agent_session_id`, `host_tool` and `model_name`; the bound MCP server consumes the newest matching proof exactly once under an inter-process owned lock. The deprecated explicit `proof_token` argument remains compatibility-only.

The base bridge proof remains available as
`bridge_verdict=TENOR_INIT_BRIDGE_OK`. The public MCP wrapper returns
`TENOR_INIT_READY` only after the same actual host call has also proved the
process-bound root and bound the independent identity. There is no prose-only
promotion from bridge to readiness.

## Root binding

The host-launched MCP process must match the project-local binding receipt,
resolved root, host identity and current configuration hash. This proof is
framework-neutral and does not assume `package.json`, Cargo, Maven, Gradle,
CMake or any other project marker. A mismatch yields:

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

No product write is allowed while root binding is unproven.

## Non-terminal local continuation

The local CLI deliberately does not load general SCRIBE RAG context. Targeted
SCRIBE and Graphify retrieval belongs to `tenor_task_start`, after an objective
exists. The local success prints:

```text
TENOR_INIT_TERMINAL=false
TENOR_INIT_NEXT_TOOL=tenor_init_bridge
TENOR_INIT_RESPONSE_POLICY=CONTINUE_WITHOUT_USER_RESPONSE
```

A host model that summarizes, asks the user or stops after `SCRIBE BOOTSTRAP`
has violated the machine contract. The generated host instructions contain the
exact host id, so OpenCode must never try `--host auto` before the allowed
`--host opencode` command.

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
- `SAME_PROJECT` never repairs tracked bundle files (V2.16.1); only the verified project-local MCP entry is managed;
- local MCP tools listed successfully.
- complete raw-copy relocation and absent/present destination-memory cases covered by executable tests;
- OpenCode comments/unrelated keys preserved by idempotent project-local configuration tests;
- concurrent server-side proof issuance/consumption covered without token exposure.

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
- terminal `TENOR_INIT_READY` bridge result proved;
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
