# Host Discipline Protocol

This protocol is mandatory for LLM hosts using `agent-scribe-graphify`.
It exists because context memory is not a reliable control plane.

## Before Any Code Write, Fix, Refactor, Delete, Or Test

1. Call `discipline_ping` when the session may have drifted, after context compaction, after an MCP error, or before finishing a task.
2. Call `pre_action_guard` with `agent_id`, `request`, `intent`, `resource`, `task_id`, `context_token`, and `planned_action`.
3. Execute the returned `must_call` until `pre_action_guard` returns `PRE_ACTION_GUARD_OK` and `ACTION_LEASE_ISSUED`.
4. Call the sensitive MCP tool with the returned `action_lease_id`.
5. Call `workspace_audit` before `finish_task`.
6. Call `finish_task` only after the audit returns `WORKSPACE_AUDIT_OK`.

## Sensitive Tools

These tools require a fresh action lease:

- `claim_resource`
- `propose_patch`
- `apply_patch`
- `delete_resource`
- `finish_task`

A lease is bound to `agent_id`, `task_id`, `resource`, `intent`, and `action` where applicable.
A lease is short-lived and single-use. Reuse, expiration, wrong agent, wrong task, wrong resource, or wrong action must fail.

## Direct Write Rule

Direct file edit fallback is never acceptable.
If the MCP workflow feels stuck, call `workflow_snapshot`, call `discipline_ping`, or stop and ask the user.
Do not bypass the MCP.

## Audit Rule

`workspace_audit` is read-only. It must never reset, delete, clean, or restore files.
If a tracked file changed without a matching MCP apply trace, the verdict is `DIRECT_WRITE_BYPASS_DETECTED` and finishing the task is forbidden.
If Git status is unavailable, the verdict is degraded and finishing remains forbidden.
