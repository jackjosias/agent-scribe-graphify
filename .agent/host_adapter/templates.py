from __future__ import annotations


CANONICAL_TENOR_TRIGGER = "TENOR INIT::[.agent/skills/init-tenor/SKILL.md]"
CANONICAL_TENOR_COMMAND = ".agent/workflow/scribe/scribe tenor-init --type cli"


def render_minimal_host_instructions(host_type: str = "unknown") -> str:
    host = str(host_type or "unknown").upper()
    core_block = f"""# AUTO-GUARD FOR HOST: {host}
AGENT-SCRIBE-GRAPHIFY AUTO-GUARD

Session entry contract:
1. Human/LLM trigger: `{CANONICAL_TENOR_TRIGGER}`.
2. Read the project-local `.agent/skills/init-tenor/SKILL.md` before global host instructions.
3. Mechanical command: `{CANONICAL_TENOR_COMMAND}` from the current project root.
4. If TENOR INIT returns a bounded Graphify build action, execute only that action and rerun TENOR INIT.
5. Verify the project-local MCP server, then prove that this host exposes the tools to the LLM.
6. Prove MCP root binding; local `--list-tools` alone is not host visibility proof.
7. Register/bridge the independent agent session. Until then report `HOST_MCP_UNBOUND`.
8. On `TENOR_INIT_SAME_PROJECT`, tracked configuration/documentation files are read-only; bundle repair is explicit via `scribe install --force`.
9. Runtime purge preserves `.agent/state/outputs/`; preserved Graphify output must still pass root/fingerprint readiness before use.

Before any code write/fix/refactor/delete/test:
1. Call discipline_ping after session start, context compaction, MCP error, or before finish.
2. Follow workflow_next and every must_call verdict.
3. Retrieve targeted SCRIBE and Graphify context; do not treat either as a checkbox.
4. Call pre_action_guard before sensitive actions.
5. Use resource locks, claims, patch queue and action_lease_id for every mutation.
6. Call workspace_audit before finish_task.
7. Direct file edit fallback is forbidden.
8. A prose-only `done` without finish_task and READY_FOR_NEXT_TASK is not completion.
9. If the host tools are not visible, report HOST_MCP_UNBOUND and do not invent configuration.
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
