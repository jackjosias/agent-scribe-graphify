# agent-scribe-graphify MCP — V2.16 public surface

The MCP runtime exposes nine tools to a host model.

Bootstrap/init tools:

- `file_hash`
- `tenor_init_bridge`
- `portability_check`
- `graphify_required_check`
- `graphify_project_build`

Normal task tools:

- `tenor_task_start`
- `tenor_apply_changeset`
- `tenor_activity`
- `tenor_task_control`

## Normal write flow

1. `tenor_task_start(objective, intent, resources, scope)` creates or resumes
   one task for the process-bound agent and performs targeted SCRIBE and
   Graphify retrieval internally.
2. The host inspects the relevant code and prepares all intended file changes.
3. `tenor_apply_changeset(task_id, changes, validators, request_id)` performs
   path/scope/hash/lock preflight for every file before the first write,
   applies the complete set, runs validator argv arrays without a shell, then
   commits all files or rolls all files back. It records the runtime SCRIBE
   receipt and closes the task on success.

The changeset supports `patch`, `replace`, `create` and confirmed `delete`
operations. It rejects absolute paths, traversal, symlinks, duplicate targets,
stale hashes, out-of-scope paths and unconfirmed deletions. A stable
`request_id` makes retry safe and detects conflicting reuse.

## Read and control flow

Use `tenor_task_control(action="finish")` to close a read task. The same tool
supports owner-only pause, resume and cancel. `tenor_activity` returns a
consolidated snapshot containing agents, presence, tasks, current action, last
action and next action.

## Identity and compatibility

After `tenor_init_bridge`, task identity is bound to that MCP server process.
Normal task tools do not accept caller-supplied `agent_id` or context tokens.
One process cannot control another process's task.

The older fine-grained tools remain registered internally so existing runtime
tests and compatibility adapters can call them, but `tools/list` does not
advertise them to host models. They are implementation primitives, not a
workflow for a small model to orchestrate.

## Local diagnostics

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

This proves only that the project-local server starts. It does not prove host
visibility, root binding or a bridged task identity.
