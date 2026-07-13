# First-Write Discovery Contract

## Purpose

A missing resource-relevant SCRIBE result is not permission to write and is not a permanent deadlock.
It creates a distinct, task-local workflow state:

```text
SCRIBE_CONTEXT_MISS_FOR_WRITE
→ exact resource scope
→ Graphify context
→ fresh file_hash
→ record_task_discovery
→ normal pre_action_guard / lock / claim / patch queue
```

This contract applies only to an active `write` or `delete` task whose exact project-relative file has no relevant historical SCRIBE result.

## Security invariants

1. A broad resource such as `(whole repo)` cannot receive a write claim. It must first be narrowed in place with `scope_task_resource`.
2. Scoping is one-way and is forbidden after any active claim, resource lock or pending patch exists.
3. The targeted SCRIBE query must run before discovery. The query text itself cannot prove relevance; relevance is evaluated from the SCRIBE result.
4. `SCRIBE_UNAVAILABLE` is not a historical miss and never enables this path.
5. Graphify must be completed when the task requires structural context.
6. Discovery is bound to `task_id`, `agent_id`, exact resource and the current `file_hash`.
7. Discovery expires with the active task and is revalidated before every mutating gate.
8. A changed file hash invalidates the receipt with `TASK_DISCOVERY_BASE_STALE`.
9. Discovery evidence is task-local runtime evidence. It does not update or seed canonical SCRIBE memory.
10. Resource lock, coordination claim, action lease, patch proposal, conflict detection, MCP `apply_patch`, workspace audit and finish-memory decision remain mandatory.
11. Direct host editing, manual patch application and artificial canonical-memory seeding remain forbidden bypasses.

## Evidence requirements

`record_task_discovery` requires:

- exact project-relative `resource`;
- fresh `base_hash` returned by `file_hash`;
- a concrete summary;
- evidence naming the target or a distinctive target token and describing inspected implementation, dependencies or blast radius;
- completed Graphify context when required.

Short, generic or resource-unrelated evidence is refused.

## Canonical memory timing

The discovery receipt is not canonical memory. Canonical SCRIBE recording remains a finish gate and may occur only after the actual result is known, such as:

- patch applied and audited;
- causal decision validated;
- regression test added;
- failure pattern or forbidden approach confirmed.

This separation prevents circular proof construction while allowing a safe first intervention on previously undocumented code.
