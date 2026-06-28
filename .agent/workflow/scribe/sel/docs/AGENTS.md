## graphify

When the host project has a Graphify knowledge graph, it lives at `.agent/state/outputs/graphify-out/`; root `graphify-out/` is legacy-only.

Scope:
- `.agent/state/outputs/graphify-out/` is the canonical application graph for the host project.
- `.agent/state/outputs/scribe-out/bundle-graph/` is the generated graph for this SCRIBE bundle itself. It is for maintaining the tooling, not for understanding the host application.
- Do not confuse the two graphs: app work reads `.agent/state/outputs/graphify-out/`; SCRIBE tooling work uses `<SCRIBE> graph --build` and `<SCRIBE> graph --query "..."`.
- SCRIBE/TENOR tooling is excluded by `.graphifyignore` so app god-nodes stay clean.
- For app work, phrase queries with an app scope, e.g. `graphify query "APP_SCOPE <project-name> auth websocket"`.
- Canonical SEL engine CLI from a host project root is `.agent/workflow/scribe/scribe`; examples below use `<SCRIBE>` for maintenance-only commands. Agent retrieval must go through `.agent/workflow/scribe/scribe-rag`.
- Host-agent always-on summary lives at `.agent/rules/scribe.md`; the full canonical protocol remains `docs/scribe.md`.
- Do not assume root `./scribe` or root `scripts/` exist. They are optional legacy adapters generated only with `<SCRIBE> install --with-root-adapters`.
- For installation, migration, or several agents working on the same repo, read `docs/multi-agent-installation.md` and `docs/live-coordination.md` before editing.
- For SCRIBE tooling work, use `<SCRIBE> graph --build` and `<SCRIBE> graph --query "..."` for a separate bundle graph under `.agent/state/outputs/scribe-out/bundle-graph/`; do not pollute the app graph or store generated graph output inside the bundle.

## Current stable baseline

As of 2026-06-01, the bundle is stable: SEL `81 OK`, RAG `25 OK`, gate/eval
`8/8`, doctor `0 error` with only cosmetic legacy `W009`, PID identity fixed via
`os.getpid()`, TTL claims active, expired/no-TTL claims stale, and lock release
validates agent/surface ownership. Backup: `~/backups/agent-scribe-stable-20260601.tar.gz`.

Operational rule: STOP `.agent`; use SCRIBE as memory/guardrail and return to
product work. Reopen SCRIBE only for a real SCRIBE bug, red test, or concrete doc drift.
Causal ratio was measured around `17.5%` with target `35%`; improve it only from
real future bugs, decisions, and rejected alternatives.

## Versioning boundaries

Default commit/push scope is the host product source. For this bundle's host
projects:

- `AGENT-MEMOIRE_PROJECT_STATUS.scribe`: version only when the team wants shared causal memory between agents and humans.
- `.agent/state/outputs/graphify-out/`: do not version by default; it is generated structural graph output and can be rebuilt with `graphify update .`.
- `.agent/state/outputs/scribe-out/`: do not version by default; it is local runtime state such as indexes, locks, doctor reports, dashboards, exports, and sync metadata.
- `.agent/`: version only when deliberately maintaining or distributing agent tooling; keep it out of ordinary product commits.

Rationale: generated memory and runtime directories create noisy diffs, may
contain stale local state, vary by machine/agent/session, and are
reconstructible from the product source plus the SCRIBE commands. Run
`<SCRIBE> worktree` before delivery to separate source changes from generated
noise.

## PRÉFLIGHT (copier-coller direct)

Command convention from a host project root:

```bash
SCRIBE=.agent/workflow/scribe/scribe
SCRIBE_RAG=.agent/workflow/scribe/scribe-rag
```

### Mode NANO (< 30 min)

```bash
$SCRIBE_RAG context
```

Use this for a one-file correction with no shared surface. No doctor, no lock, no worktree, no sync.

### Mode STANDARD (> 30 min)

```bash
$SCRIBE_RAG build
$SCRIBE_RAG context
graphify update .   # only if application code changed
```

### Before significant implementation

```bash
$SCRIBE_RAG challenge "<plan>"
```

Verdicts:
- `STOP`: do not implement; read the block and revise the plan.
- `REVIEW`: read warnings, then decide explicitly.
- `PROCEED`: implement.

### SCRIBE write or shared surface only

```bash
$SCRIBE workflow read --agent <name> --type <extension|cli|api|unknown>
$SCRIBE workflow check --agent <name>
$SCRIBE doctor --suggest-fix
$SCRIBE lock acquire --agent <name> --type <extension|cli|api|unknown> --session <JOURNAL-ID>
```

After validation:

```bash
$SCRIBE doctor --suggest-fix
$SCRIBE sync --agent <name> --type <extension|cli|api|unknown>
$SCRIBE lock release --agent <name>
```

Do not use these for normal agent retrieval: `$SCRIBE context`, `$SCRIBE query`, `$SCRIBE explain`, or SEL direct challenge.

### Hybrid signal

BM25 is canonical while it retrieves the right SCRIBE memories. Test hybrid
embeddings only after recall-loss evidence:

- `$SCRIBE_RAG eval --force` drops below `7/8`;
- `$SCRIBE_RAG query "<question>"` misses a known relevant SCRIBE entry;
- `$SCRIBE_RAG challenge "<plan>"` misses a directly related SCAR/VAC/GHOST;
- normal project wording repeatedly returns off-topic results.

The existence of `sentence-transformers` or `all-MiniLM-L6-v2` is not a signal by
itself.

### AutoDream post-implementation

AutoDream is a user-approved read-only review suggested after a real
implementation has been delivered. It exists because agents cannot infer human
idle time. Use `$SCRIBE_RAG autodream --read-only`. The runner inspects current
diff surfaces, existing RAG context, coordination state, and docs/SCRIBE rules,
then reports contradictions, read-only proof, stale context cleanup, and memory
candidates. It must not edit files, write SCRIBE, clean generated outputs, run
daemons, or commit. Any write candidate becomes a separate task with workflow
ack, doctor, lock, sync, and validation.

### Canonical surface sync

After any SCRIBE workflow evolution, keep these surfaces at the same information
level: `AGENTS.md`, `.agent/rules/scribe.md`,
`.agent/skills/init-tenor/SKILL.md`, `.agent/workflow/scribe/README.md`,
`.agent/workflow/scribe/rag/README.md`,
`.agent/workflow/scribe/sel/docs/AGENTS.md`,
`.agent/workflow/scribe/sel/docs/friction-policy.md`,
`.agent/workflow/scribe/sel/docs/scribe.md`, and
`AGENT-MEMOIRE_PROJECT_STATUS.scribe`. Archive `.old` files are historical
snapshots, not canonical rule surfaces.

## Multi-Agent V4

- Agents start as an idle pool; a work task claims a semantic intent first, for example `indicator:X`.
- Shared files are allowed across different semantic claims, but each agent must re-read and rebase before delivery.
- Same semantic claim, same exact function, deletion, rename, or global refactor is a conflict requiring coordination.
- One exclusive broad surface per agent maximum when a broad surface is claimed.
- Agents read memory through scribe-rag; they do not read the `.scribe` file directly.
- Every active agent must run `$SCRIBE workflow read --agent <name> --type <extension|cli|api|unknown>` before SCRIBE writes or shared surfaces.
- `$SCRIBE workflow status` without `--required` shows the current acked agent pool; use `--required ... --strict` only for an explicit named gate.
- SEL lock is mandatory before SCRIBE writes and refuses missing/stale workflow ack.
- `scribe sync` is mandatory before work and after memory repair.
- `scribe worktree --strict` is mandatory before delivery in coordinated multi-agent work.
- Coordination ownership is temporary: the terminal integrating deltas owns conflict arbitration for that integration window, not a permanent named role.


Rules:
- At the start of a copied `.agent` installation, run `<SCRIBE> tenor-init --type <extension|cli|api|unknown>`; it is idempotent, runs bootstrap internally, records agent presence, acknowledges workflow, and proves SCRIBE retrieval through `scribe-rag`. If `tenor-init` is unavailable in an old bundle, run `<SCRIBE> bootstrap` as fallback.
- Before answering architecture or codebase questions, read .agent/state/outputs/graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If .agent/state/outputs/graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying app code files in this session, run `graphify update .` to keep the app graph current; after modifying this bundle, run `<SCRIBE> graph --build`.
- Use `<SCRIBE> clean --dry-run` before delivery when `.agent/state/outputs/scribe-out/` looks noisy; use `<SCRIBE> clean --apply --graphify --agent-cache` only for generated reports, screenshots, exports, reconstructible AST cache overflow, and `.agent` bytecode caches.

## Final host-LLM intention

Before closing a real coding session, ask:

> "Qu'est-ce qui fera souffrir le prochain LLM si je ne le documente pas ?"

If the answer is concrete pain, write a SCAR or GHOST. If there is no future pain, a JOURNAL entry is enough. This is the anti-drift rule that keeps SCRIBE causal instead of becoming a polished activity log.

Hard reflex: when the session resolves a real bug after more than two attempts, fixes a regression, performs a costly rollback, or repairs a broken browser/visual smoke, write a SCAR immediately with `cause_racine`, `resolution`, and `test_binding`. Before any later task in the same domain, run `$SCRIBE_RAG query` or `explain` on the related symptom and challenge the plan so the scar is actively used, not merely archived.

## Causal density dashboard warning

Dashboard causal-density warnings are informational quality signals. Do not create SCAR/GHOST/PAT entries to make the dashboard look clean. Leave the warning visible until real application pain, bug evidence, a durable decision, or a rejected alternative gives the next agent concrete causal value.

## Graphify hook compatibility

Resolved failure modes:
- 2026-05-23: `PreToolUse hook returned unsupported additionalContext` came from the Graphify hook template, not from SCRIBE or the application.
- 2026-05-24: `failed to write hook stdin: Broken pipe (os error 32)` came from a silent hook command that exited before reading the Codex JSON payload.

What happened:
- The generated `.codex/hooks.json` hook emitted `hookSpecificOutput.additionalContext`.
- The current runtime rejects `additionalContext` in `PreToolUse` hook output.
- Codex writes the PreToolUse payload to hook stdin; a command that exits immediately can trigger a broken pipe even when it returns success.
- The active project hook consumes stdin, stays silent, and keeps a `graphify hook` marker so reinstall/uninstall can identify it.
- Patch every active Graphify installation if a reinstall can recreate the bad hook.
- Also sanitize global Gemini/Antigravity trusted hook registries if they contain old Graphify commands with `additionalContext`; valid Gemini commands should return only JSON allow decisions.
- Local template patches are not enough if upstream Graphify is reinstalled later. Treat any `pipx reinstall graphifyy`, `pip install -U graphifyy`, or package-manager upgrade as a recurrence risk until the installed Graphify templates are rechecked and the stdin-consuming contract is proven again.

Rules:
- Never reintroduce `additionalContext` or `hookSpecificOutput` in project PreToolUse hooks.
- Never use a Codex PreToolUse command that exits without consuming stdin.
- If Graphify is upgraded or reinstalled, rerun `graphify codex install` and verify active hook files plus active Graphify installations for unsupported fields and legacy stdin-broken commands.
- After any Graphify reinstall or upgrade, run `<SCRIBE> graphify-hooks --apply`, then simulate both Codex and Gemini hook commands with representative JSON stdin before continuing project work; if either command exits before consuming stdin, patch the installed template and prefer upstreaming the fix.
- Ready-to-submit Graphify upstream diff: `patches/graphify-upstream-hook-stdin.patch`; legacy installed-template fallback: `patches/graphify-gemini-hook-stdin.patch`.
- A valid Codex hook command for this project is `python3 -c "import sys; sys.stdin.read()" # graphify hook`.
- A valid Gemini trusted hook command consumes stdin before returning the allow decision: `python3 -c 'import sys, json; sys.stdin.read(); print(json.dumps({"decision":"allow"}))' # graphify hook`.
- A local hook simulation must exit 0 with empty stdout/stderr when fed a representative JSON payload.

## SCRIBE doctor guard

Use `docs/friction-policy.md` to choose the smallest safe workflow tier automatically. READ_ONLY must skip doctor and SCRIBE writes; QUICK must run only focused retrieval/validation unless risk escalates. Do not run full SCRIBE ceremony for read-only answers or trivial low-risk fixes.

Every future evolution of `AGENT-MEMOIRE_PROJECT_STATUS.scribe` is guarded:

- Run `<SCRIBE> doctor --suggest-fix` before editing the SCRIBE.
- Run `<SCRIBE> sync --agent <name> --type <extension|cli|api|unknown>` before work; if it reports stale state, relire the latest SCRIBE delta before editing.
- Run `<SCRIBE> workflow check --agent <name>` before any SCRIBE mutation or shared-surface lock.
- Run `<SCRIBE> lock acquire --agent <name> --type <extension|cli|api|unknown> --session <JOURNAL-ID>` before any SCRIBE mutation, then `<SCRIBE> lock release --agent <name>` after validation; doctor and read-only commands are never blocked by the lock.
- Doctor Markdown reports are generated under `.agent/state/outputs/scribe-out/` by default; do not write doctor `.md` reports at repository root.
- If the pre-doctor reports any ERROR, stop and repair the existing memory before adding a new delta.
- Edit the SCRIBE incrementally only; never overwrite the file.
- Run `<SCRIBE> doctor --suggest-fix` immediately after editing.
- If the post-doctor reports any ERROR, fix the delta immediately or remove the faulty delta.
- For command-based mutations, prefer `<SCRIBE> guard -- <command>` so doctor wraps the command before and after.
