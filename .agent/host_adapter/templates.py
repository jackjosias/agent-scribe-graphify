from __future__ import annotations


def render_minimal_host_instructions(host_type: str = "unknown") -> str:
    core_block = (
        "AGENT-SCRIBE-GRAPHIFY AUTO-GUARD\n\n"
        "Before any code write/fix/refactor/delete/test:\n"
        "1. Use local .agent MCP.\n"
        "2. Call discipline_ping at session start, after context compaction, after MCP error, or before finish.\n"
        "3. Call pre_action_guard before sensitive actions.\n"
        "4. Follow must_call until PRE_ACTION_GUARD_OK.\n"
        "5. Use action_lease_id on claim_resource/propose_patch/apply_patch/delete_resource/finish_task.\n"
        "6. Call workspace_audit before finish_task.\n"
        "7. Direct file edit fallback is forbidden.\n"
        "8. If stuck, call workflow_snapshot or stop and ask the user."
    )

    return (
        f"<!-- agent-scribe-graphify:auto-guard:start -->\n"
        f"# AUTO-GUARD FOR HOST: {str(host_type).upper()}\n"
        f"{core_block}\n"
        f"<!-- agent-scribe-graphify:auto-guard:end -->"
    )


def render_opencode_instructions() -> str:
    return render_minimal_host_instructions("opencode")


def render_codex_instructions() -> str:
    return render_minimal_host_instructions("codex")


def render_gemini_instructions() -> str:
    return render_minimal_host_instructions("gemini")


def render_antigravity_instructions() -> str:
    return render_minimal_host_instructions("antigravity")
