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
1. Call discipline_ping after session start, context compaction, MCP error, or before finish.
2. Follow workflow_next and every must_call verdict.
3. Retrieve targeted SCRIBE and Graphify context; do not treat either as a checkbox.
4. Call pre_action_guard before sensitive actions.
5. Use resource locks, claims, patch queue and action_lease_id for every mutation.
6. Call workspace_audit before finish_task.
7. Direct file edit fallback is forbidden.
8. A prose-only `done` without finish_task and READY_FOR_NEXT_TASK is not completion.
9. If the host tools are not visible, report HOST_MCP_UNBOUND and do not invent configuration.
10. Use only machine intents `read`, `write`, `delete`; keep one stable agent_id and one active task_id until finish_task.
11. Native Edit and Bash are denied in autonomous OpenCode sessions; do not replace the identity to escape a HARD_STOP.
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
