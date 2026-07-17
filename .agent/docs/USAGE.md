# Usage — TENOR V2.16

## 1. Portable installation

Copy the complete `.agent/` directory into any project root. The project may
be JavaScript, C, C++, Java, Rust, Python, Go or another stack. TENOR discovers
the project from filesystem evidence; no `package.json` or framework-specific
marker is required.

Run from the destination project root:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

The mechanical command is:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
```

On relocation TENOR purges only copied project-bound runtime, preserves the
destination SCRIBE memory when present, and revalidates preserved Graphify
output against the new root/fingerprint. If a bounded graph build is required,
run the exact reported command and rerun TENOR INIT. If host configuration
changes, reconnect the host and rerun TENOR INIT.

Do not start product work before `TENOR_INIT_READY`.

## 2. Public MCP surface

Bootstrap tools:

```text
file_hash
tenor_init_bridge
portability_check
graphify_required_check
graphify_project_build
```

Normal task tools:

```text
tenor_task_start
tenor_apply_changeset
tenor_activity
tenor_task_control
```

The old fine-grained tools remain internal compatibility primitives and are
not listed to host models.

## 3. Write task

First call:

```json
{
  "name": "tenor_task_start",
  "arguments": {
    "objective": "Fix the signpost editor and validate the affected flow",
    "intent": "write",
    "resources": [
      "components/technical-analysis/hooks/useDrawingManager.ts",
      "components/technical-analysis/TechnicalAnalysis.tsx"
    ],
    "scope": "components/technical-analysis"
  }
}
```

TENOR creates one task and performs targeted SCRIBE and Graphify retrieval
internally. It returns `task_id`, resolved scope/resources and the next action.

After inspecting/designing the fix, call `tenor_apply_changeset` once with:

- the returned `task_id`;
- one `patch`, `replace`, `create` or confirmed `delete` operation per file;
- the real fresh SHA-256 base hash for every target;
- the full content or unified diff for each operation;
- bounded validator `argv` arrays and timeouts;
- a stable `request_id` for retry safety.

Every path and hash is preflighted before the first write. Validation failure
or a mid-commit failure restores all files. Success records SCRIBE runtime
evidence and closes the task.

For deletion, provide the current base hash and include the exact path in
`confirm_deletions`.

## 4. Read task and task control

Start a read task with `tenor_task_start(intent="read")`, synthesize the
answer, then call `tenor_task_control(action="finish")` with its task id.
The same owner-only tool supports `pause`, `resume` and `cancel`.

Use `tenor_activity` to observe every registered agent and its process
presence, current task, last task, current action, last action and next action.

## 5. Safety expectations

- Do not provide `agent_id` or context tokens to normal task tools; identity is
  process-bound by `tenor_init_bridge`.
- Do not create replacement identities to escape an error.
- Do not manually call internal lock/claim/patch tools.
- Do not use native edit/shell mutation as fallback.
- Do not run `graphify update .` in the product root.
- Do not claim completion without a terminal machine verdict and actual
  validator result.
