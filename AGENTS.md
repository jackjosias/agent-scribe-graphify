<!-- SCRIBE-PORTABLE-WORKFLOW:START -->
## SCRIBE/TENOR Local Causal Retrieval Bundle

Bundle root: `.agent/workflow/scribe/`

Canonical commands:
- Maintenance/write engine: `.agent/workflow/scribe/scribe`
- Agent read interface: `.agent/workflow/scribe/scribe-rag`
- Local rules: `.agent/workflow/scribe/sel/docs/AGENTS.md`
- Always-on summary: `.agent/rules/scribe.md`
- Full protocol: `.agent/workflow/scribe/sel/docs/scribe.md`
- Multi-agent install: `.agent/workflow/scribe/sel/docs/multi-agent-installation.md`
- Friction policy: `.agent/workflow/scribe/sel/docs/friction-policy.md`

Current stable baseline (2026-06-01): SEL `81 OK`, RAG `25 OK`, gate/eval
`8/8`, doctor `0 error` with only cosmetic `W009`. STOP `.agent`: use SCRIBE as
memory and guardrail, then return to product work unless a real SCRIBE bug appears.

## PRÉFLIGHT (copier-coller direct)

Mode NANO (< 30 min, 1 file):

```bash
.agent/workflow/scribe/scribe-rag context
```

Mode STANDARD (> 30 min):

```bash
.agent/workflow/scribe/scribe-rag build
.agent/workflow/scribe/scribe-rag context
.agent/workflow/scribe/scribe-rag challenge "<plan>"
```

Mode CRITICAL or SCRIBE/shared-surface mutation:

```bash
.agent/workflow/scribe/scribe workflow read --agent <name> --type <extension|cli|api|unknown>
.agent/workflow/scribe/scribe workflow check --agent <name>
.agent/workflow/scribe/scribe-rag preflight --tier CRITICAL --strict "<plan>"
```

## Rules

- If the user sends `TENOR INIT::[.agent/skills/init-tenor/SKILL.md]`, read that exact project file first, before global OpenCode/Codex/Gemini configs, Graphify, README, or SCRIBE. Then run `.agent/workflow/scribe/scribe tenor-init --type <extension|cli|api|unknown>` and paste its MACHINE PROOF. If `tenor-init` is unavailable, fall back to `.agent/workflow/scribe/scribe bootstrap`; bootstrap remains mandatory and idempotent for every TENOR INIT.
- Never read `AGENT-MEMOIRE_PROJECT_STATUS.scribe` directly during init; use `.agent/workflow/scribe/scribe-rag context` and `.agent/workflow/scribe/scribe-rag query`.
- Do not claim YAML validity, session counts, SCAR counts, debts, or hot entries without showing real command output.
- Use `scribe-rag` for retrieval: `preflight`, `context`, `query`, `explain`, `challenge`, `eval`, `gate`, `whoami`.
- Do not use SEL direct retrieval (`scribe context`, `scribe query`, `scribe explain`) for normal agent work.
- Read `graphify-out/GRAPH_REPORT.md` before architecture or codebase work when it exists.
- If SCRIBE memory or shared surfaces are mutated, run workflow ack/check, doctor, lock acquire, sync, and lock release through `.agent/workflow/scribe/scribe`.
- Default commit/push scope is the host product source; keep `graphify-out/` and `scribe-out/` out of commits by default; version `.agent/` only when intentionally maintaining agent tooling.
- Use `.agent/workflow/scribe/scribe-rag gate` for bundle changes; it must stay green at 8/8.
- Real pain capture is mandatory: bug >2 attempts, regression, costly rollback, or broken browser/visual smoke => SCAR with `cause_racine`, `resolution`, `test_binding`; retrieve related scars with `.agent/workflow/scribe/scribe-rag query/explain/challenge` before adjacent work.
<!-- SCRIBE-PORTABLE-WORKFLOW:END -->
