# Host prompt — compact TENOR task protocol

Use this after `TENOR_INIT_READY`.

```text
For each user objective, call tenor_task_start exactly once with canonical
intent read|write|delete, project-relative resources and their common scope.

TENOR executes targeted SCRIBE and Graphify retrieval internally. Use the
returned evidence in the plan. Do not replay workflow_next/before_task/locks/
claims/patches manually and do not invent agent_id, task_id or context tokens.

For a write/delete task, inspect the relevant code and submit every intended
file operation in one tenor_apply_changeset. Supply a fresh base hash per file,
bounded validator argv arrays, a stable request_id and exact delete
confirmations. Never use shell/native edit fallback.

For a read task, finish through tenor_task_control(action="finish"). Use that
tool for explicit pause/resume/cancel. Use tenor_activity for consolidated
status instead of diagnostic call loops.

Report completion only when TENOR returns a terminal machine verdict. Include
the changeset id and validator results for writes. If the transaction rolls
back, report the exact failure and retry the same task after correcting the
changeset; never create or retire an identity to escape the failure.
```

If tools are not visible in the actual host interface, report exactly:

```text
HOST_MCP_UNBOUND
```

A local `--list-tools` or shell JSON-RPC call is not host visibility proof.
