# SCRIBE Engineering Local Causal Retrieval

Purpose: package SCRIBE/TENOR local causal retrieval assets so they stay reusable across projects.

This folder is the importable home for agent rules, doctor scripts, workflow docs,
engineering notes, command specs, retrieval design, evaluation suites, benchmarks, and future
implementation plans around SCRIBE query/challenge/explain tooling.

Current stable baseline (2026-06-01): SEL `81 OK`, RAG `25 OK`, gate/eval `8/8`,
doctor `0 error` with only cosmetic legacy `W009`, `os.getpid()` presence/lock
ownership fixed, TTL claims active, expired/no-TTL claims stale, and backup
`~/backups/agent-scribe-stable-20260601.tar.gz` created. Do not keep expanding
this bundle unless a real SCRIBE bug or test failure appears; return to product work.

Layout:
- `docs/`: canonical agent and workflow documents; historical protocols stay under `docs/archive/`.
- `docs/multi-agent-installation.md`: rootless install and async-agent operating procedure for host projects.
- `scripts/`: executable implementation modules used by the bundle-local `scribe` command.
- `adapters/`: policy note only; generated legacy root adapters are not stored in the bundle.
- `templates/`: generated-file policy references.
- `patches/`: upstreamable/local patches for external tooling issues that can recur after reinstall.
- `tests/`: focused unit tests for the local SCRIBE store and memory commands.

Boundaries:
- Do not store causal project memory here; use `AGENT-MEMOIRE_PROJECT_STATUS.scribe`.
- Do not store generated structural graph output inside this bundle; bundle graph output belongs under `.agent/state/outputs/scribe-out/bundle-graph/scribe/`.
- The host application graph lives at `.agent/state/outputs/graphify-out/`; root `graphify-out/` is legacy-only and must be migrated or cleaned after CLI execution.
- Version only the host application and deliberate project memory by default:
  `AGENT-MEMOIRE_PROJECT_STATUS.scribe` can be shared when the team wants causal
  memory in Git, while `.agent/state/outputs/graphify-out/` and `.agent/state/outputs/scribe-out/` are generated local
  state and should stay out of commits and pushes. Version `.agent/` only when
  intentionally maintaining the agent tooling itself.
- Active SCRIBE workflow rules live in `docs/scribe.md`.
- Host-agent always-on summary rules live at `.agent/rules/scribe.md` in the host project; that short rule must point back here instead of duplicating the protocol.
- Multi-agent installation and handoff rules live in `docs/multi-agent-installation.md`.
- Friction-aware fast paths live in `docs/friction-policy.md`; use them to avoid paying the full protocol cost for read-only or narrow low-risk tasks.
- Graphify hook hardening lives in `docs/AGENTS.md`; after any Graphify upgrade or reinstall, verify the installed templates still consume stdin before returning hook decisions.
- The Graphify upstream PR diff is preserved at `patches/graphify-upstream-hook-stdin.patch`; use it when a direct PR cannot be opened from the current machine.
- Keep experiments reproducible and guarded by the bundle-local `scribe doctor` when they touch SCRIBE data.
- Do not require root `./scribe` or root `scripts/` for normal operation. They are generated only by `<SCRIBE> install --with-root-adapters`.
- Do not store generated root adapters inside the bundle. `scripts/scribe_install_templates.py` is the single source for legacy adapter generation.
- Do not put generated caches in the bundle; the installer skips `__pycache__`,
  `.pytest_cache`, `.mypy_cache`, `.pyc`, and `.pyo` artifacts.

Current CLI baseline:
From a host project root, the canonical command path is `.agent/workflow/scribe/scribe`.
The examples below use `<SCRIBE>` for that path.

- `<SCRIBE> bootstrap [--root PATH]`
  - Idempotently prepares a copied `.agent` bundle inside a project: managed `AGENTS.md`, `.graphifyignore`, initial SCRIBE template when absent, `.agent/state/outputs/scribe-out/`, doctor, and `state.json`.
- `<SCRIBE> clean --dry-run|--apply [--graphify] [--agent-cache]`
  - Removes generated `.agent/state/outputs/scribe-out/` noise such as historical doctor reports, validation exports, dashboard screenshots, and commit plans. With `--graphify`, prunes Graphify AST caches with LRU retention; with `--agent-cache`, removes portable `.agent` bytecode caches.
- `<SCRIBE> doctor [SCRIBE_PATH] [--output REPORT] [--suggest-fix]`
  - Default report: `.agent/state/outputs/scribe-out/scribe-doctor-report.md`
- `<SCRIBE> guard [SCRIBE_PATH] -- <command> [args...]`
  - Default reports: `.agent/state/outputs/scribe-out/scribe-doctor-before-report.md` and `.agent/state/outputs/scribe-out/scribe-doctor-after-report.md`
- `<SCRIBE> install [TARGET_PATH] [--force] [--dry-run] [--with-root-adapters]`
  - Installs this bundle into another project rootlessly by default. `AGENTS.md` and `.graphifyignore` managed blocks are updated; root `scribe` and `scripts/` are generated only with `--with-root-adapters`.
- `<SCRIBE> lock acquire|release|status`
  - Coordinates SCRIBE mutations with a local JSON lock at `.agent/state/outputs/scribe-out/locks/scribe.lock`; mutating commands refuse writes without an active lock. `release` verifies agent/surface before stale cleanup; use `SCRIBE_OWNER_PID` or `--owner-pid` when the lock represents a long-lived owner process.
- `<SCRIBE> sync --agent NAME --type extension|cli|api|unknown [--repair --session JOURNAL-ID]`
  - Compares `.agent/state/outputs/scribe-out/state.json` with the real SCRIBE hash; `--repair` rebuilds the state file atomically after a legitimate write or manual recovery.
- `<SCRIBE> whoami`
  - Shows the last SCRIBE writer recorded in `.agent/state/outputs/scribe-out/state.json` without changing writer ownership.
- `<SCRIBE> hot [--limit N] [--topic TEXT]`
  - Prints a short hot-memory slice for immediate agent grounding; default output is recency-ranked and intentionally capped.
- `<SCRIBE> context [--mode quick|standard] [--topic TEXT] [--format text|json]`
  - Prints a low-friction context pack: quick skips full doctor, standard includes doctor summary and active debts; JSON mode is stable for agents.
- `<SCRIBE> stats`
  - Prints SCRIBE health, tier counts, entity counts, and causal/evidence/consultation/journal edge counts.
- `<SCRIBE> explain <ID>`
  - Explains one SCRIBE entity and its causal neighborhood.
- `<SCRIBE> related <ID>`
  - Shows incoming and outgoing causal neighbors for one SCRIBE entity.
- `<SCRIBE> query "<text>" [--limit N]`
  - Searches causal SCRIBE memory locally. It does not call Graphify by default.
- `<SCRIBE> challenge "<plan>" [--limit N]`
  - Challenges a plan against scars, vaccins, patterns, ghosts, hypotheses, and debts.
- `<SCRIBE> eval [--query TEXT --expect ID[,ID...]] [--format text|json]`
  - Measures local retrieval quality for query, hot-topic, and context surfaces with top-1/top-3/top-5 metrics.
- `<SCRIBE> compact [--apply]`
  - Reports safe tier-registry compaction opportunities; with `--apply`, rewrites only the root `tiers` registry after doctor validation.
- `<SCRIBE> review-hot [--target N] [--warm-overflow N] [--apply]`
  - Reviews hot-tier pressure and can demote overflow entries to warm/cold after doctor validation.
- `<SCRIBE> promote <ID> --tier hot|warm|cold [--dry-run]`
  - Moves one SCRIBE entity across memory tiers with a targeted text patch and post-write rollback on doctor errors.
- `<SCRIBE> export --format json [--output PATH] [--include-values]`
  - Exports indexed causal memory as deterministic JSON for external tools, dashboards, or future retrieval pipelines.
- `<SCRIBE> archive [--apply] [--output AGENT-MEMOIRE_ARCHIVE.yaml]`
  - Dry-runs cold-entry archival by default; with `--apply`, writes archive YAML and prunes archived blocks from the active SCRIBE after doctor validation.
- `<SCRIBE> dashboard [--output .agent/state/outputs/scribe-out/scribe-dashboard.html]`
  - Generates a static HTML dashboard plus JSON data file from the same indexed payload used by `<SCRIBE> export`, including retrieval-quality data.
- `<SCRIBE> dashboard --serve [--host 127.0.0.1] [--port 8765] [--poll-interval-ms 2000]`
  - Serves a lightweight local live view that hashes the SCRIBE file, disables HTTP caching, and reloads the dashboard when memory changes without adding external dependencies or a long-running app stack.
- `<SCRIBE> graph [--build] [--query TEXT] [--budget N]`
  - Builds or queries a separate Graphify graph under `.agent/state/outputs/scribe-out/bundle-graph/` without leaving root legacy `graphify-out/` or polluting this bundle.
- `<SCRIBE> graphify-hooks [--apply] [--template PATH] [--trusted-hooks PATH]`
  - Checks and reapplies the stdin-consuming Graphify hook patch after any Graphify reinstall or upgrade; also simulates Codex and Gemini hook commands.
- `<SCRIBE> benchmark [--entities 1000,10000] [--queries N] [--json]`
  - Runs synthetic local retrieval/load benchmarks for SCRIBE memory scale checks.
- `<SCRIBE> worktree [--strict]`
  - Classifies Git worktree state into tracked changes, untracked source candidates, and generated noise.
