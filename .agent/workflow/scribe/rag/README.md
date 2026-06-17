# scribe-rag — Retrieval Layer V1

## Role

scribe-rag is the only memory read interface for agents. Its index rebuild path
uses SEL native Python modules for low-latency export, while the canonical SEL CLI
remains available for compatibility, doctor, and maintenance commands. Agents must not read
`AGENT-MEMOIRE_PROJECT_STATUS.scribe` directly. `preflight` is the host-model
proof command: it emits whoami, context, eval, and challenge in one compact gate.

SEL remains the internal guard/write engine for bootstrap, doctor, lock, sync,
state, export, archive, graph maintenance, and SCRIBE writes.

## Stabilized Baseline 2026-06-01

- RAG tests: `25 OK`.
- Protocol eval/gate: `8/8`.
- Forced BM25 gate target: rebuild and eval should stay near single-digit seconds on the project SCRIBE; investigate if it regresses toward the old 18-26s CLI-export path.
- BM25 remains canonical while eval stays `>= 7/8`.
- Hybrid remains a fallback, not the default, and needs concrete recall-loss evidence before activation.
- The current operational instruction is to stop improving SCRIBE and return to product work unless a real SCRIBE defect appears.

## Modes

- `BM25`: default, zero external dependency, portable.
- `Hybrid`: enabled by `--with-embeddings` when `sentence-transformers` is installed.

Hybrid is recommended only after recall-loss evidence: `scribe-rag eval --force`
drops below `7/8`, a query misses a known relevant SCRIBE entry, or
`challenge "<plan>"` fails to surface a directly related SCAR/VAC/GHOST. The
mere presence of `sentence-transformers` or the `all-MiniLM-L6-v2` model is not
a reason to leave BM25.

## Commands

```bash
.agent/workflow/scribe/scribe-rag preflight [--tier STANDARD] [--strict] ["plan"]
.agent/workflow/scribe/scribe-rag build [--with-embeddings] [--force]
.agent/workflow/scribe/scribe-rag context
.agent/workflow/scribe/scribe-rag query "<text>"
.agent/workflow/scribe/scribe-rag explain <ID>
.agent/workflow/scribe/scribe-rag challenge "<plan>"
.agent/workflow/scribe/scribe-rag eval [--force]
.agent/workflow/scribe/scribe-rag gate [--min-passed 8]
.agent/workflow/scribe/scribe-rag autodream --read-only [--format text|json]
.agent/workflow/scribe/scribe-rag doctor
.agent/workflow/scribe/scribe-rag whoami
```

## Workflow Tiers

```bash
# NANO: < 30 min, one file, no shared surface
.agent/workflow/scribe/scribe-rag context

# STANDARD: significant implementation
.agent/workflow/scribe/scribe-rag build
.agent/workflow/scribe/scribe-rag context
.agent/workflow/scribe/scribe-rag challenge "<plan>"

# CRITICAL: auth/data/public API, SCRIBE mutation, or shared surface
.agent/workflow/scribe/scribe-rag preflight --tier CRITICAL --strict "<plan>"
```

## Local State

`scribe-rag whoami` reports the last SCRIBE writer, last session, lock status,
lock owner/surface when locked, index mode, and the eval command to run. It is
read-only.

## Canonical Decisions

- `PAT-GRAPH-001`: Graphify handles structure while SCRIBE handles causal memory.
- `PAT-GIT-001`: generated Graphify/SCRIBE runtime state stays out of product commits by default.
- `PAT-SCRIBE-RAG-001`: host agents prove memory usage through `scribe-rag preflight`.
- `GHOST-SCRIBE-RAG-SEL-DIRECT-001`: SEL direct retrieval is rejected for host agents.

Benchmark decision data:

- Preflight output: one proof surface for whoami, context, eval, and challenge.
- Query output: scribe-rag BM25 `12` lines vs SEL `34` lines.
- Protocol eval: Graphify/SCRIBE separation, artifact boundary, scribe-rag interface, and SEL-direct rejection.
- Hybrid rejected as default because its latency, dependency risk, and model footprint do not justify the marginal gain while BM25 retrieves the right memory.
- BM25 index rebuild uses a native index export contract rather than the full SEL export payload; compatibility tests compare the native contract with the CLI export entity and tier contract.
- A hybrid index must not satisfy a default BM25 request; default commands rebuild BM25 instead of silently loading the embedding model.

## Gate CI / Pre-Commit

Use this command in a pre-commit hook or CI job:

```bash
.agent/workflow/scribe/scribe-rag gate
```

It rebuilds the compact index, runs the protocol eval, and exits non-zero unless
all checks pass and the score is at least `8/8`. The portable hook
`.agent/workflow/scribe/hooks/pre-commit` calls this command directly.

## AutoDream Policy

AutoDream is not an automatic idle daemon. The host agent must suggest it only
after a completed implementation, because it cannot know real user idle time.
The executable runner is `scribe-rag autodream --read-only`. It emits a bounded
text or JSON report with diff surfaces, stale context cleanup, contradiction
findings, candidate SCAR/PAT/GHOST/JOURNAL memories, and `read_only_proof`.
Defaults are finite: 500 files, 12 MB read budget, 240 KB diff-summary budget,
5s timeout, 180 output lines. `--cancel-file <path>` aborts safely. The runner
does not rebuild indexes, run doctor, acquire locks, write SCRIBE, modify code,
change generated outputs, install dependencies, start background services, or
commit. If the user approves a candidate write, run it as a separate guarded
SCRIBE mutation.

## Canonical Surface Sync

When SCRIBE/RAG workflow behavior changes, propagate the same operational fact
to every canonical surface before delivery: `AGENTS.md`,
`.agent/rules/scribe.md`, `.agent/skills/init-tenor/SKILL.md`,
`.agent/workflow/scribe/README.md`, `.agent/workflow/scribe/rag/README.md`,
`.agent/workflow/scribe/sel/docs/AGENTS.md`,
`.agent/workflow/scribe/sel/docs/friction-policy.md`,
`.agent/workflow/scribe/sel/docs/scribe.md`, and
`AGENT-MEMOIRE_PROJECT_STATUS.scribe`. Do not update archive `.old` files for
current operating rules.

## Hybrid Signal

```bash
.agent/workflow/scribe/scribe-rag eval --force
# if result is < 7/8, or a known relevant SCRIBE entry is missed by BM25:
pip install sentence-transformers --break-system-packages
.agent/workflow/scribe/scribe-rag build --with-embeddings --force
```

After that build, only commands explicitly run with `--with-embeddings` should use the hybrid index. Keep BM25 when
the proof is only "a model exists"; switch only when retrieval quality actually
fails.
