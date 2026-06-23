# Host Adapter Auto-Guard (V2.13)

The **Host Adapter Auto-Guard** is a framework designed to automate host discipline, shifting the responsibility of protocol compliance away from human reminders to the agent workflow itself.

## Architecture

An adapter/launcher checks that:
1. **Preflight**: Compares the host's active tools to check if `discipline_ping`, `pre_action_guard`, and `workspace_audit` are available.
2. **Auto-Installation**: Automatically adds anti-bypass guidelines (the "auto-guard instructions") into host instruction files (e.g. `AGENTS.md` or `README.md`) between marked delimiters.
3. **Automated Ping & Guard**: Integrates automatic calls to `discipline_ping` and `pre_action_guard` to enforce state FSM lease rules.
4. **Bypass Detection Audit**: Calls `workspace_audit` to run Git audits detecting untracked filesystem edits bypassing MCP.

## Safety Levels

We classify host environments into four security categories:

* **UNSAFE**: Required MCP tools are missing, OR the host cannot execute the auto-guard protocol.
* **ACCEPTABLE**: MCP tools are present, but instructions have not been deployed, or FSM checkpoints are occasionally bypassed.
* **SAFE_CANDIDATE**: MCP tools are present, auto-guard instructions are active, and recent audits succeed. However, native file edit commands are still available to the host.
* **SAFE**: Complete sandboxing where native write/exec tools are disabled or fully wrapped, making it physically impossible to bypass MCP.

## CLI Usage

Run preflight checks:
```bash
python3 .agent/scripts/host_auto_guard.py preflight --host opencode
```

Deploy auto-guard instructions:
```bash
python3 .agent/scripts/host_auto_guard.py install-instructions --host opencode --target .
```

Verify planned actions before write/patch operations:
```bash
python3 .agent/scripts/host_auto_guard.py guard \
  --agent-id test-agent \
  --request "fix bug" \
  --intent write \
  --resource src/main.py \
  --planned-action propose_patch
```

Run workspace bypass audit:
```bash
python3 .agent/scripts/host_auto_guard.py audit --agent-id test-agent --task-id task_xyz
```
