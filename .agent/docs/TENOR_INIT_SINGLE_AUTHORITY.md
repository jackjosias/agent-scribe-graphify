# TENOR INIT — Single Authority Contract (V2.16 rescue)

## Mission

`.agent` is not a collection of scripts. It is a portable operating layer for
LLM agents working inside a codebase.

Its purpose is not only to constrain agents. Its purpose is to reduce the
capability gap between small and large language models by giving every compliant
agent the same external cognitive machinery:

- Graphify for compressed structural understanding;
- SCRIBE for causal memory, negative memory and proven project history;
- TENOR for the safe execution order;
- the runtime for live multi-agent coordination;
- MCP tools for mechanical enforcement instead of prompt-only discipline.

A small model that follows this operating layer should acquire reliable reflexes
that would otherwise require a much larger context window and stronger native
reasoning.

## LLM Experience is a first-class requirement

Developer Experience and LLM Experience are equally important.

A valid design must minimise the amount of state the model has to remember in
free-form text. The system must answer, mechanically and compactly:

1. Where am I?
2. What matters around the requested change?
3. What happened here before?
4. What must not be repeated?
5. Who else is working nearby?
6. What is the next safe action?
7. What proof is required before completion?

Verdicts must be short, stable and actionable. A weak model must not need to
reconstruct the workflow from a long prompt.

## The four cognitive pillars

### Graphify — structural context compression

Graphify is not merely a locator for code. It is the model's compressed map of
the codebase. It should let an agent understand a large project without reading
hundreds of files and exhausting its context window.

Graphify must provide, when relevant:

- dependency and reverse-dependency context;
- centrality and god-node information;
- blast radius;
- module and boundary structure;
- likely neighbouring files;
- change-impact context;
- stale or mismatched graph detection;
- a compact task-specific structural digest.

The goal is not to dump the graph into the prompt. The goal is to retrieve the
smallest structural context that makes the next action safe.

### SCRIBE — operational causal memory

SCRIBE is not a passive archive and not a checkbox query. It is the operational
memory that must influence the current plan.

Before a relevant action, the agent must retrieve nearby:

- prior failures and regressions;
- SCARs and their test bindings;
- decisions and rejected alternatives;
- patterns that proved reliable;
- GHOSTs and `ne_pas_reproposer` constraints;
- active debt and invariants;
- previous work on the same files, modules or domain.

The agent must not merely say that memory was read. The workflow must bind the
retrieved evidence to the task context, and later writes must respect it or
explain an auditable contradiction.

The purpose of memory is to prevent the next agent from repeating pain already
paid for by previous agents.

### TENOR — safe order of action

TENOR is the deterministic order in which an agent resolves identity, project
state, memory, graph context, coordination, mutation and final proof.

TENOR must be a machine-guided path, not a long ritual that a small model has to
memorise.

### Runtime — live multi-agent coordination

The runtime answers who is doing what now. It coordinates active identities,
tasks, leases, claims, locks, patches and terminal state.

It is intentionally shorter-lived than SCRIBE. Runtime state may be purged after
a confirmed relocation; canonical project memory must not be purged with it.

## TENOR INIT is the sole installation authority

No other entrypoint may independently decide whether the bundle belongs to the
same project.

The required order is:

1. `RESOLVE_ENVIRONMENT`
   - resolve the current project root;
   - detect OS, host and execution surface;
   - never trust a cached absolute root as authority.

2. `CLASSIFY_INSTALLATION`
   - compare the current root/fingerprint with the installation manifest;
   - classify `NEW_INSTALLATION`, `SAME_PROJECT`, `RELOCATED_PROJECT`,
     `LEGACY_INSTALLATION` or `CORRUPT_INSTALLATION`;
   - the existence of `AGENT-MEMOIRE_PROJECT_STATUS.scribe` does not determine
     this classification.

3. `RESET_PROJECT_BOUND_STATE_IF_REQUIRED`
   - only after a positive relocation/legacy/corruption verdict;
   - purge runtime, sessions, proofs, locks, caches and outputs bound to the old
     project;
   - preserve portable engine files and the target project's canonical memory.

4. `ADOPT_CURRENT_PROJECT`
   - write the new installation manifest;
   - establish the current project identity and fingerprint.

5. `ADOPT_OR_CREATE_MEMORY`
   - existing valid memory: adopt and continue it;
   - absent memory: create the current project's initial memory;
   - invalid memory: never overwrite silently.

6. `PREPARE_GRAPH_CONTEXT`
   - reject graphs from another root, corrupt graphs and smoke stubs;
   - build or request a bounded build with visible progress;
   - never declare ready from file existence alone.

7. `CONFIGURE_HOST`
   - select the adapter for OpenCode, Codex, Claude Code, Cursor, Cline, Roo,
     Kilo, Windsurf, VS Code or another supported host;
   - prefer workspace-local configuration;
   - require permission before global/user configuration;
   - configure browser/Chrome surfaces only when supported and needed.

8. `VERIFY_MCP_BINDING`
   - distinguish local server availability from tools visible to the host model;
   - prove the root binding belongs to the current project.

9. `REGISTER_AGENT_SESSION`
   - issue the project-bound proof;
   - register the session;
   - verify active status;
   - perform discipline ping;
   - retire safe ghost identities from the same host.

10. `READY`
    - return one terminal machine verdict or one blocking verdict with an exact
      next action.

## Multi-agent invariant: six terminals in one project

A human may start six TENOR INIT commands in six terminals inside the same
project.

The system must support this scenario without destructive races:

- the shared installation/bootstrap phase is serialised;
- only the first process performs shared repair or initialisation;
- later processes re-check the manifest after the lock is released;
- `SAME_PROJECT` must never purge active runtime state;
- each terminal receives a distinct stable agent identity;
- all six sessions share the same coordination database;
- claims, resource locks and leases prevent overlapping writes;
- agents do not need to chat with one another in prose;
- the runtime and MCP verdicts coordinate them mechanically;
- SCRIBE preserves long-term causal continuity across all sessions.

A second or sixth TENOR INIT in the same project is not a relocation and is not a
reason to reset state.

## Portability invariant

The engine must remain portable across Linux, macOS and Windows and across Git
and non-Git projects.

Portable code must use Python standard-library abstractions where possible:

- `pathlib.Path` for paths;
- `os.pathsep` for PATH-like values;
- `subprocess` with argument lists and `shell=False`;
- bounded subprocess calls;
- atomic file operations compatible with all supported platforms;
- no hard-coded `/home`, `/tmp`, Bash, GNU `timeout`, `grep`, `sed` or `awk` in
  required runtime logic.

Platform-specific host integration must live behind explicit adapters.

## Non-negotiable truth conditions

The system is not ready if any of these are true:

- SCRIBE existence decides project identity;
- server startup silently purges state without TENOR INIT authority;
- a relocated bundle keeps active agents or proofs from the old project;
- a repeated init in the same project purges active coordination;
- a Graphify stub is accepted as a real graph;
- SCRIBE is queried but its result is not bound to the task;
- the model has to remember the workflow from prose rather than follow stable
  `must_call` verdicts;
- two terminals can edit the same resource without lock/claim conflict;
- a host reports MCP ready while bound to another root;
- a test passes only because the source repository is already warm.

## Delivery rule

Future changes must be judged against the complete journey:

`copy bundle -> resolve -> classify -> purge if required -> adopt project ->
adopt/create memory -> prepare Graphify -> configure host -> verify MCP ->
register independent agent session -> coordinate task -> write with proof ->
record causal memory -> finish`.

Local component tests are necessary but cannot replace this end-to-end contract.
