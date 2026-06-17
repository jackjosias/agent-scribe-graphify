# Multi-Agent Installation Procedure

Purpose: install and operate the SCRIBE/TENOR local causal retrieval bundle in a host project so several coding agents can work asynchronously without creating competing roots, stale memory, or hidden regressions. Live session coordination is canonical in `docs/live-coordination.md`; do not create `.agent/workflow/multi-agent/`.

Stable baseline as of 2026-06-01: SEL `81 OK`, RAG `25 OK`, gate/eval `8/8`,
doctor `0 error` plus cosmetic `W009`, PID identity fixed, TTL claims active,
lock release owner-safe, and backup `~/backups/agent-scribe-stable-20260601.tar.gz`.
The installation goal is now reuse, not adding features.

## Command Contract

From a host project root:

```bash
SCRIBE=.agent/workflow/scribe/scribe
SCRIBE_RAG=.agent/workflow/scribe/scribe-rag
```

Rules:
- The bundle-local command above is the canonical CLI.
- Root `./scribe` and root `scripts/` are not required for normal operation.
- Generate root adapters only for a legacy project that explicitly needs them.
- Do not store generated root adapters inside the bundle; the installer templates are the source of truth.
- Generated doctor reports and bundle graphs belong under `scribe-out/`, never at repository root and never inside the bundle.
- Structural app facts belong to Graphify; causal decisions belong to `AGENT-MEMOIRE_PROJECT_STATUS.scribe`.

## Rootless Install

Use this path for new projects and for multi-agent coding sessions:

```bash
$SCRIBE bootstrap
$SCRIBE workflow read --agent "<agent-name>" --type "<extension|cli|api|unknown>"
$SCRIBE workflow check --agent "<agent-name>"
$SCRIBE_RAG context
```

Expected result:
- `AGENTS.md` has a managed SCRIBE block pointing to the bundle-local command.
- `.graphifyignore` excludes `.agent/`, `scribe-out/`, and SCRIBE memory from the app graph.
- `AGENT-MEMOIRE_PROJECT_STATUS.scribe` exists; if missing, it is created from the master template.
- `scribe-out/state.json` exists and records the real bootstrap writer.
- No root `scribe` file is created.
- No root `scripts/` directory is created.
- The active command remains `.agent/workflow/scribe/scribe`.

## Legacy Adapter Install

Use this only when an existing tool or editor cannot call the bundle-local path:

```bash
$SCRIBE install . --with-root-adapters --dry-run
$SCRIBE install . --with-root-adapters
```

Rules:
- Treat generated root adapters as compatibility bridges only.
- Do not edit root adapters by hand.
- Keep canonical code changes inside the bundle.
- Do not copy generated adapters back into `.agent/workflow/scribe/sel/adapters/`.
- If root adapters are no longer needed, remove them as a deliberate migration and rerun the rootless install.

## Agent Bootstrap

Every agent that joins the project should run this sequence before editing:

```bash
$SCRIBE bootstrap
$SCRIBE workflow read --agent "<agent-name>" --type "<extension|cli|api|unknown>"
$SCRIBE workflow check --agent "<agent-name>"
sed -n '1,220p' graphify-out/GRAPH_REPORT.md
$SCRIBE sync --agent "<agent-name>" --type "<extension|cli|api|unknown>"
$SCRIBE whoami --agent "<agent-name>" --type "<extension|cli|api|unknown>" --surface idle
$SCRIBE coordination status
$SCRIBE_RAG context
$SCRIBE worktree
```

Then the agent chooses the smallest safe workflow tier from `docs/friction-policy.md`.
Use NANO (`$SCRIBE_RAG context`) for a one-file task under 30 minutes; reserve
CRITICAL preflight for auth/data/public API, SCRIBE mutation, shared surfaces, or destructive work.

For SCRIBE memory edits:

```bash
$SCRIBE workflow check --agent "<agent-name>"
$SCRIBE lock acquire --agent "<agent-name>" --type "<extension|cli|api|unknown>" --session "JOURNAL-XXX"
$SCRIBE sync --agent "<agent-name>" --type "<extension|cli|api|unknown>"
$SCRIBE doctor --suggest-fix
# edit AGENT-MEMOIRE_PROJECT_STATUS.scribe incrementally
$SCRIBE doctor --suggest-fix
$SCRIBE sync --repair --agent "<agent-name>" --type "<extension|cli|api|unknown>" --session "JOURNAL-XXX" --changed-id "JOURNAL-XXX" --write-kind "memory_append"
$SCRIBE lock release --agent "<agent-name>"
```

For long-lived ownership, set `SCRIBE_OWNER_PID` or pass `--owner-pid`; release
now validates agent/surface before stale cleanup.

## Async Coordination

When several LLM agents work on the same repository:
- Agents start as an idle pool; they only claim work after receiving a concrete task.
- Work is claimed semantically, for example `indicator:X`, not only by filename.
- Shared files are allowed across different semantic claims when the agent re-reads current files and rebases before delivery.
- The same semantic claim, same exact function, deletion, rename, or global refactor requires explicit coordination.
- Broad surfaces remain exclusive only when they are actually claimed as broad surfaces.
- Each active agent must have a fresh `$SCRIBE workflow read` ack before SCRIBE writes or shared-surface locks.
- `$SCRIBE workflow status` without `--required` shows the current acked agent pool; use `--required ... --strict` only when a human explicitly imposes a named gate.
- SCRIBE memory is a locked surface: mutating commands and manual YAML edits require `$SCRIBE lock acquire` first.
- Application code, SCRIBE bundle code, SCRIBE memory, and generated reports are separate surfaces.
- Agents must run `$SCRIBE coordination status` before writing and `$SCRIBE coordination finish` when their semantic claim is delivered.
- No agent reverts files it did not intentionally change.
- Before changing shared behavior, run `$SCRIBE_RAG preflight --tier CRITICAL --strict "<plan>"`; do not use CRITICAL for routine NANO/QUICK tasks.
- Before delivery, run `$SCRIBE worktree` and report tracked changes, untracked source candidates, generated noise, and other untracked files.
- If the task changes bundle architecture, run `$SCRIBE graph --build`.
- If the task changes retrieval ranking, tiers, or scoring, run `$SCRIBE_RAG eval --force` before and after the change.

## Acceptance Checklist

A portable installation is accepted only when all checks pass:

```bash
test -f .agent/workflow/scribe/scribe
test -f .agent/workflow/scribe/scribe-rag
test ! -e scribe
test ! -e scripts
$SCRIBE install . --dry-run
$SCRIBE clean --dry-run
$SCRIBE doctor --suggest-fix
$SCRIBE workflow read --agent codex --type cli
$SCRIBE workflow check --agent codex
$SCRIBE_RAG preflight --tier STANDARD "installation acceptance"
$SCRIBE_RAG eval --force
$SCRIBE_RAG gate
git diff --check
```

Expected acceptance:
- install dry-run reports `unchanged AGENTS.md` and `unchanged .graphifyignore`;
- doctor reports zero errors;
- scribe-rag preflight runs, prints proof, eval remains green, and gate passes locally plus through `.github/workflows/scribe-rag-gate.yml`;
- no Python `__pycache__` or `.pyc` artifacts remain inside the bundle.

## Recovery

If a project drifts:
1. Run `$SCRIBE_RAG query "bundle identity drift"`.
2. Verify whether root `scribe` or root `scripts/` exist.
3. If they exist unintentionally, move their reusable contents under the bundle or remove generated adapters deliberately.
4. Rerun `$SCRIBE install .`.
5. Rerun the acceptance checklist.

If Graphify hook errors reappear, inspect `docs/AGENTS.md` under "Graphify hook compatibility" before reinstalling Graphify hooks.
