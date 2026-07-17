from __future__ import annotations


CANONICAL_TENOR_TRIGGER = "TENOR INIT::[.agent/skills/init-tenor/SKILL.md]"
CANONICAL_TENOR_COMMAND = ".agent/workflow/scribe/scribe tenor-init --type cli --host {host_id}"

_HOST_IDS = {
    "opencode": "opencode",
    "codex": "codex-cli",
    "codex-cli": "codex-cli",
    "gemini": "gemini-cli",
    "gemini-cli": "gemini-cli",
    "antigravity": "antigravity",
}


def canonical_tenor_command(host_type: str) -> str:
    normalized = str(host_type or "unknown").strip().lower().replace("_", "-")
    host_id = _HOST_IDS.get(normalized, normalized if normalized and normalized != "unknown" else "auto")
    return CANONICAL_TENOR_COMMAND.format(host_id=host_id)


def render_minimal_host_instructions(host_type: str = "unknown") -> str:
    host = str(host_type or "unknown").upper()
    command = canonical_tenor_command(host_type)
    core_block = f"""# AUTO-GUARD FOR HOST: {host}
AGENT-SCRIBE-GRAPHIFY AUTO-GUARD

Session entry contract:
1. Human/LLM trigger: `{CANONICAL_TENOR_TRIGGER}`.
2. Read the project-local `.agent/skills/init-tenor/SKILL.md` before global host instructions.
3. Mechanical command for this host: `{command}` from the current project root. Never substitute `--host auto` when this host id is known.
4. If TENOR INIT returns a bounded Graphify build action, use the canonical command before host binding or MCP `graphify_project_build` after binding; never run `graphify update .` in the product root.
5. Let TENOR manage only the verified project-local MCP entry; reconnect and rerun when it reports `HOST_RECONNECT_REQUIRED`.
6. A local success is explicitly non-terminal. On `TENOR_INIT_TERMINAL=false`, immediately call the host-visible `tenor_init_bridge` with the emitted session id; do not summarize, ask the user, wait, or stop between the CLI and bridge.
7. The bridge verifies host visibility, project-local process binding, config hash, resolved root, one-time proof and independent session in one call. Local `--list-tools` or shell JSON-RPC is not host proof.
8. Only `TENOR_INIT_READY`, `HOST_RECONNECT_REQUIRED`, or an explicit fail-closed verdict may end the init turn. Prose after `SCRIBE BOOTSTRAP` is never completion.
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
