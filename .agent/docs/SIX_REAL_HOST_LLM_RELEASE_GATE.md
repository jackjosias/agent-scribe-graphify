# Six real Codex hosts — release-grade gate

## Purpose

This gate proves more than six MCP subprocesses. It launches six direct native
`codex exec` processes and requires six distinct Codex threads, host PIDs, MCP
PIDs and TENOR identities on one immutable Git commit/tree.

The release verdict remains fail-closed. A local unit-test pass, a narrative
report, or six bridge messages is not sufficient.

## Mechanical boundaries

Each private host exposes only:

- `tenor_init_bridge`;
- the replay-specific `tenor_activity` rendezvous.

Three independent barriers apply before an action can have an effect:

1. the MCP proxy publishes only the two schemas and rejects every other
   `tools/call`;
2. a `PreToolUse` hook accepts only the exact proxy/tool aliases and validates
   every argument against the run, participant, session, phase and sequence;
3. the JSONL oracle rejects any unexpected event, shell execution, file
   change, MCP server, tool, argument or call cardinality.

The normal TENOR MCP transport is disabled with a complete CLI configuration
table for every private host. This avoids the Codex 0.145.0 partial-table
`invalid transport` failure while preventing a model from selecting the full
server during the replay.

## Required identity oracles

The harness requires all of the following:

- direct execution of an absolute, regular, non-symlink native ELF;
- Codex version and SHA-256 attestation before launch;
- clean Git worktree, exact commit SHA and exact tree SHA before and after;
- one persisted `state_5.sqlite` thread per host, recouped against root,
  commit, branch, origin, model and CLI version;
- one TENOR `agents` row per session, recouped against the proxy PID,
  `codex-cli` host and exact model;
- six unique threads, host PIDs, proxy PIDs and TENOR sessions;
- exactly `1 bridge + 8 activities` per host;
- two-phase `ready → observed` quorum with 48 durable activity calls;
- SQLite `quick_check=ok` and `integrity_check=ok`.

`--ephemeral` is forbidden because it removes the Codex thread row needed by
the independent SQLite oracle.

## Field artefact

A successful run publishes:

- 24 redacted sidecars: events, hooks, proxy decisions and cross-database
  identity for each host;
- `result.json`, bound to Git, harness and native Codex hashes;
- `manifest.json`, containing the size and SHA-256 of every preceding JSON
  file;
- a parent `soak-summary.json`.

Private homes, rollout files, authentication links and SQLite database files
are never copied into the artefact. The temporary private root is destroyed
after the oracles finish. Any credential-like value or private path found
during serialization fails the run.

## Canonical validation isolation

The canonical suite is destructive only to its own disposable runtime
fixtures by design. It must run in an isolated checkout or CI workspace and
fails closed when `AGENT_TENOR_JOB_WORKER=1`. A live changeset may use only
bounded, non-destructive targeted validators. Run the complete suite before
the changeset on the immutable candidate and again after commit in a fresh
clone, never against the SQLite authority that owns the active transaction.

## Operator command

Use an immutable clean checkout and pass the native ELF directly:

```bash
python3 .agent/scripts/six_real_host_llm_replay.py \
  --root "$PWD" \
  --codex-bin /absolute/path/to/native/codex \
  --expected-codex-sha256 "<sha256>" \
  --model gpt-5.6-terra \
  --auth-mode auth-link \
  --auth-source /absolute/path/to/auth.json \
  --allow-auth-link \
  --output-dir /absolute/path/to/redacted-evidence
```

The two auth-link flags are intentionally separate consent gates. The harness
links the existing file into each short-lived private home; it never prints or
copies its contents.

For a bounded soak, add `--soak-runs N`, with `2 ≤ N ≤ 24`. Each iteration
creates new homes, threads, sessions, rendezvous database and evidence
directory.

## Verdict hierarchy

```text
UNIT_ORACLES=PASS
VALIDATION_SUITE_OK
SIX_REAL_HOST_LLM_REPLAY_OK
CI_GITHUB_CREDENTIALÉE=PASS
SOAK_LONGUE_DURÉE=PASS
```

`PRODUCTION_GRADE=OUI` is permitted only after all five lines are observed on
the same immutable commit/tree and the CI/soak artefacts are retained. Until
then:

```text
PRODUCTION_GRADE=NON_PROUVÉ
```
