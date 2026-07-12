# Host MCP Integration Guides — V2.16

This directory contains host-specific integration guides. It is not an OpenCode-only manual and no configuration may be copied blindly from one host to another.

## Canonical session entry

Human/LLM trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical local command:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown>
```

Host integration starts only after the local installation is ready and Graphify is valid.

## Universal host order

```text
1. detect the real host
2. read exactly that host's guide
3. prefer workspace/project-local configuration
4. restart or reconnect the host when required
5. prove MCP tools visible to the LLM
6. prove MCP root binding
7. call tenor_init_bridge
8. obtain TENOR_INIT_READY
9. run one complete MCP micro-write
10. test direct-write bypass behavior
```

The standard STDIO command for a project-local `.agent` server is:

```bash
python3 .agent/mcp/server_entry.py
```

A successful local command:

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

proves only `MCP_LOCAL_SERVER_READY`. It does **not** prove that the host UI exposes the tools to the model.

## Minimum tool surface

Each host must expose at least:

```text
workflow_next
before_task
discipline_ping
scribe_query
graphify_query
pre_action_guard
resource_lock_claim
resource_lock_release
claim_resource
file_hash
propose_patch
apply_patch
delete_resource
workspace_audit
scribe_record
finish_task
tenor_init_bridge
portability_check
graphify_required_check
```

## Root binding

MCP visibility alone is insufficient. The host and MCP must hash the same stable sentinel in the current project.

Mismatch verdict:

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

A global config pointing to another checkout, an old copied `.agent`, or the source repository itself is forbidden.

## Configuration policy

- Prefer project/workspace-local configuration when the host supports it.
- Do not edit global/user configuration without explicit permission.
- Do not remove unrelated MCP servers such as Chrome DevTools.
- Do not invent a config filename, schema or restart behavior.
- Record whether the host requires restart, reconnect or new conversation.
- Treat every host guide's `UNKNOWN` fields as real open gates, not assumptions.

## Direct Tool Neutralization

Before classifying a host as safe, verify its native mutation paths:

```text
shell / bash / terminal
write_file / edit / apply_patch
redirections > and >>
tee
sed -i / perl -pi
rm / mv / cp
host-native file APIs
```

Required behavior is either strict denial/approval gating or reliable detection by workspace audit/tripwire.

If an untracked direct mutation appears without the expected MCP receipts:

```text
DIRECT_WRITE_BYPASS_DETECTED
```

Stop the task and report the affected files.

## Host verdicts

- `UNSAFE` — MCP absent/non-visible, wrong root, or uncontrolled direct writes.
- `ACCEPTABLE` — MCP visible and root-bound, but direct shell/edit paths remain accessible.
- `SAFE_CANDIDATE` — MCP visible/root-bound and native writes are denied or strict-ask, but full terrain proof is incomplete.
- `SAFE` — MCP visible/root-bound, complete micro-write proven, and no uncontrolled project mutation path remains.
- `UNKNOWN` — not tested or insufficient evidence.

Until host visibility and root binding are proved, report:

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

## Guides

- `AGENT_MCP_INSTALL_MATRIX.md`
- `OPENAI_CODEX_MCP.md`
- `OPENCODE_MCP.md`
- `CLAUDE_CODE_MCP.md`
- `VSCODE_COPILOT_MCP.md`
- `CLINE_MCP.md`
- `KCODE_MCP.md`
- `ROO_CODE_MCP.md`
- `WINDSURF_MCP.md`
- `COMMAND_CODE_CLI.md`
- `CURSOR_MCP.md`
- `GEMINI_CLI_MCP.md`

Every guide must end with a terrain verdict table covering tool visibility, root binding, native write paths, bridge, micro-write and bypass test.

## Documentation maintenance

When host architecture changes, update the active guide, this index, host templates, tests, `.agent/rules/tenor-init-v2.json` and the PR body according to `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
