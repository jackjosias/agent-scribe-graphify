from __future__ import annotations


CANONICAL_TENOR_TRIGGER = "TENOR INIT::[.agent/skills/init-tenor/SKILL.md]"
CANONICAL_TENOR_COMMAND = ".agent/workflow/scribe/scribe tenor-init --type cli --host <host-id>"


def render_minimal_host_instructions(host_type: str = "unknown") -> str:
    host = str(host_type or "unknown").upper()
    core_block = f"""# AUTO-GUARD FOR HOST: {host}
AGENT-SCRIBE-GRAPHIFY AUTO-GUARD

Session entry contract:
1. Human/LLM trigger: `{CANONICAL_TENOR_TRIGGER}`.
2. Read the project-local `.agent/skills/init-tenor/SKILL.md` before global host instructions.
3. Mechanical command: `{CANONICAL_TENOR_COMMAND}` from the current project root.
4. If TENOR INIT returns a bounded Graphify build action, use the canonical command before host binding or MCP `graphify_project_build` after binding; never run `graphify update .` in the product root.
5. Let TENOR manage only the verified project-local MCP entry; reconnect and rerun when it reports `HOST_RECONNECT_REQUIRED`.
6. Verify the project-local MCP server, then prove that this host exposes the tools to the LLM.
7. Prove MCP root binding; local `--list-tools` or shell JSON-RPC is not host visibility proof.
8. Register/bridge the independent agent session through the actual host-bound MCP process. Until then report `HOST_MCP_UNBOUND`.
9. On `TENOR_INIT_SAME_PROJECT`, bundle repair is explicit via `scribe install --force`; only verified project-local MCP binding metadata may be managed automatically.
10. Runtime purge preserves `.agent/state/outputs/`; preserved Graphify output must still pass root/fingerprint readiness before use.

Before any code write/fix/refactor/delete/test:
1. Call `tenor_task_start` once with the objective, canonical intent, project-relative resources and their common scope.
2. TENOR runs targeted SCRIBE and Graphify retrieval internally; never reproduce the legacy lock/claim/patch choreography manually.
3. For a write/delete task, call `tenor_apply_changeset` once with every file operation, fresh base hashes and bounded validator argv arrays.
4. The changeset is all-or-nothing: every path/hash/lock is preflighted, validators run without a shell, and any failure rolls back every file.
5. For a read task, finish with `tenor_task_control(action="finish")`; use it also for explicit pause/resume/cancel.
6. Use `tenor_activity` for consolidated agent/task/current/last/next state. Never retire or replace another active agent.
7. Task tools derive agent identity from the successful MCP bridge; never invent agent_id, task_id or context_token.
8. Direct file edit fallback and direct `graphify update .` are forbidden.
9. Completion requires a terminal machine verdict and validator evidence; a prose-only `done` is not completion.
10. If the host tools are not visible, report HOST_MCP_UNBOUND and do not invent configuration.
11. Native Edit and Bash are denied in autonomous OpenCode sessions; do not replace the identity to escape a fail-closed verdict.
"""
    return (
        "<!-- agent-scribe-graphify:auto-guard:start -->\n"
        f"{core_block}"
        "<!-- agent-scribe-graphify:auto-guard:end -->"
    )


def render_opencode_instructions() -> str:
    return render_minimal_host_instructions("opencode")


def render_codex_instructions() -> str:
    return render_minimal_host_instructions("codex")


def render_gemini_instructions() -> str:
    return render_minimal_host_instructions("gemini")


def render_antigravity_instructions() -> str:
    return render_minimal_host_instructions("antigravity")
