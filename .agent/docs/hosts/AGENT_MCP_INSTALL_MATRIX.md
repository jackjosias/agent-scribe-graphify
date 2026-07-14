# Agent MCP Install Matrix — V2.16

## Canonical entry

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Local command:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

STDIO MCP command after local readiness:

```bash
python3 .agent/mcp/server_entry.py
```

Local `--list-tools` is not host visibility proof.

## Universal gates

Every host must prove, in order:

```text
local TENOR installation ready
real Graphify bound
verified project-local host configuration loaded
tools visible to the host LLM
host process binding receipt/config hash valid
correct root binding
tenor_init_bridge OK
TENOR_INIT_READY
complete MCP micro-write
direct-write bypass refused or detected
```

## Capability matrix

`YES` means supported in principle or documented by the host; it does not mean terrain-proven for this project. `UNKNOWN` remains an open gate.

| Host | Local MCP configurable | Automatic project config | Tools visible terrain | Root binding terrain | Bridge terrain | Micro-write terrain | Direct-write audit | Current `.agent` verdict | Guide |
|---|---|---|---|---|---|---|---|---|---|
| OpenCode | YES | VERIFIED | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `OPENCODE_MCP.md` |
| Codex CLI | YES | VERIFIED | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `OPENAI_CODEX_MCP.md` |
| Claude Code | YES | VERIFIED | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `CLAUDE_CODE_MCP.md` |
| Cursor | YES | GUIDE_ONLY | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `CURSOR_MCP.md` |
| Gemini CLI | UNKNOWN | GUIDE_ONLY | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `GEMINI_CLI_MCP.md` |
| VS Code / Copilot | YES | GUIDE_ONLY | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `VSCODE_COPILOT_MCP.md` |
| Cline | YES | GUIDE_ONLY | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `CLINE_MCP.md` |
| Roo Code | YES | GUIDE_ONLY | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `ROO_CODE_MCP.md` |
| Kilo Code | UNKNOWN | GUIDE_ONLY | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `KCODE_MCP.md` |
| Windsurf | UNKNOWN | GUIDE_ONLY | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `WINDSURF_MCP.md` |
| Unknown host | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | `UNKNOWN` | `README.md` |

## Verdict rules

- `UNSAFE` — MCP unavailable/non-visible, wrong root, or uncontrolled direct mutation.
- `ACCEPTABLE` — tools visible/root-bound and workflow usable, but native write paths remain accessible.
- `SAFE_CANDIDATE` — complete MCP path works and native writes are strict-deny/ask, but full sandbox/bypass evidence is incomplete.
- `SAFE` — complete host proof, correct root, bridge, micro-write and no uncontrolled mutation path.
- `UNKNOWN` — missing terrain evidence.

No host may be promoted by documentation assumption.

## Minimum terrain script per host

1. Start with the canonical TENOR trigger.
2. Complete local init and real Graphify binding.
3. Run TENOR with the exact host id; accept automatic configuration only for a verified host.
4. Restart/reconnect the host when required.
5. Capture evidence that the LLM sees the required MCP tools.
6. Hash one sentinel from host and MCP to prove the root.
7. Call `tenor_init_bridge` through the actual host-bound MCP process and require `TENOR_INIT_BRIDGE_OK` with `MCP_BRIDGE_ONLY` scope.
8. Execute one complete MCP micro-write.
9. Attempt a native direct write and prove denial/approval gate or `DIRECT_WRITE_BYPASS_DETECTED`.
10. Update this matrix and the host guide with actual evidence.

## Maintenance

Follow `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`. When a host guide changes, update this matrix, the active guide, host templates, tests, machine rules and PR body in the same lot.
