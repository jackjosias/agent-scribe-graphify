# Usage — TENOR V2.16

## 1. Portable installation

Copy the complete `.agent/` directory into any project root. The project may
be JavaScript, C, C++, Java, Rust, Python, Go or another stack. TENOR discovers
the project from filesystem evidence; no `package.json` or framework-specific
marker is required.

Run from the destination project root:

```text
TENOR INIT ::[— depuis la racine du workspace courant, lis comme un fichier local avec l’outil normal de lecture de fichiers — jamais avec un résolveur de skills — le chemin exact "./.agent/skills/init-tenor/SKILL.md"; n’utilise jamais "~/.agent", "~/.agents" ni aucun chemin global; applique ensuite intégralement ce fichier et continue automatiquement jusqu’à TENOR_INIT_READY, HOST_RECONNECT_REQUIRED ou un verdict FAIL_CLOSED explicite.]
```

The mechanical command is:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
```

On relocation TENOR purges only copied project-bound runtime, preserves the
destination SCRIBE memory when present, and revalidates preserved Graphify
output against the new root/fingerprint. If a bounded graph build is required,
the same TENOR INIT invocation owns it under the shared lock and continues
automatically. If host configuration changes, reconnect the host and rerun
TENOR INIT because the host itself must reload its MCP process.

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
internally. It returns `task_id`, resolved scope/resources and a decision
capsule bound to the current SCRIBE memory hash, Graphify manifest hash and
retrieval evidence. A stale capsule is refreshed by repeating the identical
`tenor_task_start`; the `task_id` is preserved.

After inspecting/designing the fix, call `tenor_apply_changeset` once with:

- the returned `task_id`;
- one `edit`, `patch`, `replace`, `create` or confirmed `delete` operation per file;
- structured `edit` entries with unique `old_text`, `new_text` and `expected_occurrences=1` for normal text changes;
- the real fresh SHA-256 base hash for every existing target (`create` derives the new-file sentinel internally);
- full-file content only for `replace`; a destructive shrink additionally requires a path/base/new-hash confirmation;
- at least one bounded validator `argv` array and timeout;
- a stable `request_id` for retry safety.

The immediate success response is `TENOR_CHANGESET_ACCEPTED`. It means the
payload is durably queued, not that files were committed. Wait the returned
`poll_after_ms`, then call `tenor_activity` until that job is `succeeded` or
`failed`. Do not call `tenor_apply_changeset` again and do not pause, cancel or
finish the task while the job is active. The terminal job `result` is the only
completion evidence.

Every path and hash is preflighted before the first write. Validation failure
or a mid-commit failure restores all files. The exact 2051-line-to-5-line
truncation class is rejected before any write unless an exact three-field
full-replacement confirmation is provided.

Success cannot close silently. TENOR evaluates the runtime record as one of
`promote`, `runtime_only`, `ask_user` or `conflict`. Validated durable source
knowledge is promoted into `AGENT-MEMOIRE_PROJECT_STATUS.scribe`; non-causal
noise stays runtime-only with a persisted reason. An unresolved choice remains
non-terminal until `tenor_task_control(action="memory_promote")` or
`tenor_task_control(action="memory_skip", summary="auditable reason...")`.

For deletion, provide the current base hash and include the exact path in
`confirm_deletions`.

## 4. Read task and task control

Start a read task with `tenor_task_start(intent="read")`, synthesize the
answer, then call `tenor_task_control(action="finish")` with its task id.
The same owner-only tool supports `pause`, `resume` and `cancel`. A failed,
uncommitted write task is cancelled directly without a no-op changeset. Never
start a replacement task to escape an error.

Use `tenor_activity` to observe every registered agent, its process presence,
task actions and redacted durable jobs. Polling also recovers dead workers and
releases queued capacity. A job failure leaves the task recoverable for a new,
corrected payload; retry exhaustion is reported explicitly.

## 5. Safety expectations

- Do not provide `agent_id` or context tokens to normal task tools; identity is
  process-bound by `tenor_init_bridge`.
- Do not create replacement identities to escape an error.
- Do not manually call internal lock/claim/patch tools.
- Do not use native edit/shell mutation as fallback.
- Do not ask the user to apply a manual patch.
- Do not use `replace` with a snippet; prefer structured `edit`.
- Do not run `graphify update .` in the product root.
- Do not claim completion without a terminal machine verdict and actual
  validator, decision-capsule and memory-admission evidence.
