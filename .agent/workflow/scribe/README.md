# SCRIBE Portable Workflow Bundle

`.agent/workflow/scribe/` is the single portable SCRIBE workflow root.

## Stabilized Baseline 2026-06-01

This bundle is stable. Do not keep improving SCRIBE infrastructure unless a real
SCRIBE bug, red test, or documentation drift is observed.

- SEL tests: `81 OK`.
- RAG tests: `25 OK`.
- `scribe-rag gate`: green at `8/8`.
- `scribe doctor --suggest-fix`: `0 error`; `W009` legacy pre-V3.2 is cosmetic.
- Identity/presence: unique session IDs, `os.getpid()` PIDs, stale PID cleanup.
- Coordination: claims have `ttl_seconds` and `expires_at`; expired or legacy no-TTL claims are stale.
- Lock release: validates agent/surface before stale cleanup; use `SCRIBE_OWNER_PID` or `--owner-pid` for long-lived ownership.
- Causal ratio last measured around `17.5%`; target is `35%`, improved only by real future SCAR/GHOST evidence.
- Pain capture reflex: do not chase the ratio, but every resolved bug after >2 attempts, regression, costly rollback, or broken browser/visual smoke must become a SCAR with `cause_racine`, `resolution`, and `test_binding`, then be retrieved before similar work.
- Reference backup: `~/backups/agent-scribe-stable-20260601.tar.gz`.

Final operating rule: STOP `.agent`. Return to product work; use SCRIBE only as
memory and guardrail until a real SCRIBE defect appears.

## Canonical Commands

When the full `.agent/` directory has been copied into a project, the first host-agent prompt should be:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

That prompt must make the agent read the project skill first, not global OpenCode/Codex/Gemini configs. The skill then runs the deterministic init proof. If operating manually after copying only the workflow directory, run:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

`tenor-init` runs bootstrap internally, records agent presence, acknowledges workflow, queries SCRIBE through `scribe-rag`, and prints `SCRIBE-CHECK TENOR V4 — MACHINE PROOF`. For very old bundles without `tenor-init`, fall back to `scribe bootstrap` plus `scribe-rag context`.

Choose the smallest safe tier from `sel/docs/friction-policy.md`:

```bash
# NANO: < 30 min, one file, no shared surface
.agent/workflow/scribe/scribe-rag context

# STANDARD: significant implementation
.agent/workflow/scribe/scribe-rag build
.agent/workflow/scribe/scribe-rag context
.agent/workflow/scribe/scribe-rag challenge "<plan>"

# CRITICAL or SCRIBE/shared-surface mutation
.agent/workflow/scribe/scribe workflow read --agent <name> --type <extension|cli|api|unknown>
.agent/workflow/scribe/scribe workflow check --agent <name>
.agent/workflow/scribe/scribe-rag preflight --tier CRITICAL --strict "<plan>"
```

Run the gate for bundle changes:

```bash
.agent/workflow/scribe/scribe-rag gate
```

## Layout

- `scribe`: maintenance, `tenor-init`, bootstrap, doctor, lock, sync, graph, worktree, and SCRIBE writes.
- `scribe-rag`: canonical read/retrieval interface for agents.
- `sel/`: internal SCRIBE engineering local causal retrieval engine.
- `rag/`: BM25 retrieval layer that calls the local SEL engine.
- `sel/docs/friction-policy.md`: tier selector, including NANO.
- `sel/docs/live-coordination.md`: canonical agent-pool live coordination workflow.

## Retrieval Policy

BM25 is the canonical scribe-rag mode while it retrieves the right SCRIBE
memories. Hybrid embeddings are tested only after recall-loss evidence: eval
below `7/8`, a query missing a known relevant SCRIBE entry, challenge missing a
directly related SCAR/VAC/GHOST, or repeated off-topic results for normal project
wording. A local `all-MiniLM-L6-v2` model or installed `sentence-transformers`
package is not enough reason to switch.

## Canonical Surface Sync

After any SCRIBE workflow evolution, update and verify the canonical surfaces as
one set: `AGENTS.md`, `.agent/rules/scribe.md`,
`.agent/skills/init-tenor/SKILL.md`, `.agent/workflow/scribe/README.md`,
`.agent/workflow/scribe/rag/README.md`,
`.agent/workflow/scribe/sel/docs/AGENTS.md`,
`.agent/workflow/scribe/sel/docs/friction-policy.md`,
`.agent/workflow/scribe/sel/docs/scribe.md`, and
`AGENT-MEMOIRE_PROJECT_STATUS.scribe`. Archive `.old` files are historical and
non-canonical.

## AutoDream Post-Implementation

Agents cannot detect real user idle time. After a real implementation has been
delivered and locally validated, the agent may ask whether the user wants to run
AutoDream. The executable command is:

```bash
.agent/workflow/scribe/scribe-rag autodream --read-only
```

AutoDream is a bounded read-only review: it digests current diff surfaces,
compacts session context from the existing RAG index, detects contradictions
across docs/SCRIBE rules, and proposes causal memory candidates. It proves that
protected memory/runtime files stayed unchanged. It must not edit source, mutate
SCRIBE, touch generated artifacts, start background daemons, or commit. Any write
discovered by AutoDream becomes a separate user-approved task with workflow ack,
doctor, lock, sync, and focused validation.

No legacy sibling workflow directory is part of the portable bundle. New projects
should copy this directory as one unit and use the root commands above. A sibling
`.agent/workflow/multi-agent/` directory is non-canonical; migrate its content
under `.agent/workflow/scribe/`.

## CI / Pre-Commit Gate

```bash
.agent/workflow/scribe/scribe-rag gate
```

The gate exits non-zero unless the SCRIBE-RAG protocol eval is fully green and at
least `8/8`.

Portable pre-commit hook:

```bash
.agent/workflow/scribe/hooks/pre-commit
```

A repository may symlink or copy that file to `.git/hooks/pre-commit`; CI can
call the same script directly. This repo also ships `.github/workflows/scribe-rag-gate.yml`,
which runs `.agent/workflow/scribe/scribe-rag gate` on push and pull request.

## Multi-Agent Agent-Pool Startup

```bash
.agent/workflow/scribe/scribe whoami --type cli --surface idle
.agent/workflow/scribe/scribe workflow read --agent <session-id> --type cli
.agent/workflow/scribe/scribe workflow status
.agent/workflow/scribe/scribe coordination status
```

Use `workflow status --required ... --strict` only when a human explicitly
imposes a named gate. Otherwise the pool is dynamic: each terminal starts idle,
then claims semantic work such as `indicator:X` when a concrete task arrives.

`scribe lock acquire` refuses SCRIBE writes or named shared-surface locks when
the requesting agent has no fresh workflow ack for the current docs digest.
