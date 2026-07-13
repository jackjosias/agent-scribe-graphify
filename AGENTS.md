<!-- SCRIBE-PORTABLE-WORKFLOW:START -->
## AGENT-SCRIBE-GRAPHIFY — V2.16 CANONICAL OPERATING CONTRACT

### Canonical session entry

Human/LLM trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical command from the current project root:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown>
```

The project-local `.agent/skills/init-tenor/SKILL.md` and `.agent/rules/tenor-init-v2.json` are authoritative. `bootstrap` is an internal/legacy primitive, not the public V2.16 installation authority.

### Authority order

```text
resolve root
classify installation
purge only old project-bound state when relocation is proven
adopt/create target SCRIBE
verify/build and bind Graphify
finalize local installation
verify local MCP
verify tools visible in the real host
prove MCP root binding
bridge the independent session
TENOR_INIT_READY
```

### Hard rules

- Never start product work before `TENOR_INIT_READY`.
- `server_entry.py --list-tools` proves only local MCP readiness, never host visibility.
- Never read `AGENT-MEMOIRE_PROJECT_STATUS.scribe` directly for normal agent retrieval; use `.agent/workflow/scribe/scribe-rag` or MCP `scribe_query`.
- SCRIBE results must change the plan or be explicitly challenged; retrieval is not a checkbox.
- Use Graphify before architecture or broad code changes; prefer targeted structure/blast-radius queries over mass file reads.
- Every mutation requires `pre_action_guard`, an action lease, resource lock/claim, file hash, patch queue, `workspace_audit`, release and `finish_task`.
- Native shell/edit/write/apply-patch paths outside MCP are forbidden for project mutation.
- A prose-only “done” without `finish_task` and `READY_FOR_NEXT_TASK` is not completion.
- Each terminal uses its own `agent_id`, proof token and lease. Agents share runtime, SCRIBE and Graphify, never identity or ownership credentials.
- A relocation may purge only copied `.agent/state/` bound to the old root. It must preserve the target's canonical SCRIBE memory.
- Graphify readiness accepts explicit supported edge fields (`edges` or real NetworkX node-link `links`) and rejects missing, stale, wrong-root, stub or contradictory graphs.

### Canonical surfaces

- `.agent/skills/init-tenor/SKILL.md`
- `.agent/rules/tenor-init-v2.json`
- `.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md`
- `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`
- `.agent/workflow/scribe/README.md`
- `.agent/workflow/scribe/sel/docs/scribe.md`
- `.agent/workflow/scribe/sel/docs/multi-agent-installation.md`
- `.agent/docs/hosts/README.md`

When the architecture or workflow changes, update these surfaces and their generators in the same change. Historical `.old` files and old dated baselines are not authoritative.
<!-- SCRIBE-PORTABLE-WORKFLOW:END -->
- Invariant terrain V2.16.1 : sur `TENOR_INIT_SAME_PROJECT`, l'init de session est strictement en lecture seule des fichiers suivis (`AGENTS.md`, `.agent/rules/scribe.md`, `.graphifyignore`, `.agent/.gitignore`) ; l'installateur forcé n'est jamais appelé et la réparation du bundle reste explicite (`scribe install --force`).
