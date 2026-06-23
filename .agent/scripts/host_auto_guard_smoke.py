#!/usr/bin/env python3
"""End-to-end smoke test for V2.13 host auto-guard.
"""

from __future__ import annotations

import json
import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Ensure parent and mcp directories are in sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPTS_DIR.parent.parent
MCP_DIR = ROOT_DIR / ".agent" / "mcp"

sys.path.insert(0, str(ROOT_DIR / ".agent"))
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(MCP_DIR))

from host_adapter.instructions import verify_instruction_installation


def fail(message: str) -> None:
    print(f"SMOKE_FAILURE: {message}")
    sys.exit(1)


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=15,
        shell=False,
    )


def run_cli(cwd: Path, *args: str) -> dict[str, Any]:
    cli_path = ROOT_DIR / ".agent" / "scripts" / "host_auto_guard.py"
    env = dict(os.environ)
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(cwd)
    env["PYTHONPATH"] = str(MCP_DIR) + os.pathsep + str(ROOT_DIR)

    proc = subprocess.run(
        [sys.executable, str(cli_path), "--json", "--workspace-root", str(cwd), *args],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        shell=False,
    )
    if proc.returncode != 0:
        fail(f"CLI command failed with code {proc.returncode}: {proc.stderr}\nSTDOUT: {proc.stdout}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw_stdout": proc.stdout}


def call_mcp(cwd: Path, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    entry_path = cwd / ".agent" / "mcp" / "server_entry.py"
    env = dict(os.environ)
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(cwd)
    env["PYTHONPATH"] = str(MCP_DIR) + os.pathsep + str(ROOT_DIR)

    proc = subprocess.run(
        [
            sys.executable,
            str(entry_path),
            "--call",
            tool_name,
            "--args",
            json.dumps(args),
        ],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        shell=False,
    )
    if proc.returncode != 0:
        fail(f"MCP tool call {tool_name} failed: {proc.stderr}")

    outer = json.loads(proc.stdout)
    if "content" in outer:
        return json.loads(outer["content"][0]["text"])
    return outer


def main() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="host-auto-guard-smoke-"))
    try:
        # 1. initialize temp git repository
        git(temp_dir, "init")
        git(temp_dir, "config", "user.email", "smoke@example.invalid")
        git(temp_dir, "config", "user.name", "Smoke Auto Guard")

        # 2. create .agent layout inside temp repo
        agent_dir = temp_dir / ".agent"
        (agent_dir / "mcp").mkdir(parents=True, exist_ok=True)
        (agent_dir / "state").mkdir(parents=True, exist_ok=True)

        entry_script = agent_dir / "mcp" / "server_entry.py"
        entry_script.write_text(
            f"#!/usr/bin/env python3\n"
            f"import os, runpy\n"
            f"runpy.run_path('{MCP_DIR}/server_ext.py', run_name='__main__')\n",
            encoding="utf-8",
        )
        entry_script.chmod(0o755)

        # Create a tracked file in Git
        tracked_file = temp_dir / "tracked.txt"
        tracked_file.write_text("initial tracked content\n", encoding="utf-8")
        git(temp_dir, "add", "tracked.txt")
        git(temp_dir, "commit", "-m", "initial tracked")

        # 3. CLI preflight
        res_pre = run_cli(temp_dir, "preflight", "--host", "opencode", "--agent-id", "smoke-agent")
        if not res_pre.get("ok") or res_pre.get("verdict") not in {"SAFE_CANDIDATE", "SAFE"}:
            fail(f"CLI preflight failed: {res_pre}")

        # 4. Install instructions
        agents_file = temp_dir / "AGENTS.md"
        res_inst = run_cli(temp_dir, "install-instructions", "--host", "opencode", "--target", str(agents_file))
        if not res_inst.get("ok"):
            fail(f"CLI install-instructions failed: {res_inst}")

        # 5. Verify idempotence
        res_inst2 = run_cli(temp_dir, "install-instructions", "--host", "opencode", "--target", str(agents_file))
        if not res_inst2.get("ok"):
            fail(f"CLI install-instructions idempotence failed: {res_inst2}")

        if not verify_instruction_installation(agents_file):
            fail("Instruction verification failed on target file.")

        # 6. register_agent
        agent_id = "smoke-agent-123"
        res_reg = call_mcp(temp_dir, "register_agent", {"agent_id": agent_id, "host_tool": "opencode"})
        if res_reg.get("verdict") != "AGENT_REGISTERED":
            fail(f"register_agent failed: {res_reg}")

        # 7. discipline_ping
        res_ping = run_cli(temp_dir, "ping", "--agent-id", agent_id, "--phase", "init")
        if res_ping.get("ok") is False:
            fail(f"CLI ping failed: {res_ping}")

        # 8. before_task
        res_bt = call_mcp(
            temp_dir,
            "before_task",
            {
                "agent_id": agent_id,
                "request": "modify tracked.txt",
                "intent": "write",
                "resource": "tracked.txt",
            },
        )
        if res_bt.get("verdict") != "BEFORE_TASK_OK":
            fail(f"before_task failed: {res_bt}")

        task_id = res_bt["task_id"]
        context_token = res_bt["context_token"]

        # 9 & 10. scribe_query & graphify_query
        res_sq = call_mcp(
            temp_dir,
            "scribe_query",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": context_token,
                "query": "modify tracked.txt",
            },
        )
        res_gq = call_mcp(
            temp_dir,
            "graphify_query",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": context_token,
                "query": "modify tracked.txt",
                "resource": "tracked.txt",
            },
        )

        # 11 & 12 & 13. guard & claim_resource
        res_guard_claim = run_cli(
            temp_dir,
            "guard",
            "--agent-id",
            agent_id,
            "--request",
            "modify tracked.txt",
            "--intent",
            "write",
            "--resource",
            "tracked.txt",
            "--planned-action",
            "claim_resource",
            "--task-id",
            task_id,
            "--context-token",
            context_token,
        )
        if res_guard_claim.get("verdict") != "PRE_ACTION_GUARD_OK":
            fail(f"Guard before claim_resource failed: {res_guard_claim}")

        lease_id_claim = res_guard_claim["action_lease"]["lease_id"]

        res_claim = call_mcp(
            temp_dir,
            "claim_resource",
            {
                "agent_id": agent_id,
                "resource": "tracked.txt",
                "task_id": task_id,
                "context_token": context_token,
                "action_lease_id": lease_id_claim,
            },
        )
        if res_claim.get("verdict") not in {"RESOURCE_CLAIMED", "CLAIM_GRANTED"}:
            fail(f"claim_resource failed: {res_claim}")

        # 14 & 15. guard & propose_patch
        res_guard_patch = run_cli(
            temp_dir,
            "guard",
            "--agent-id",
            agent_id,
            "--request",
            "modify tracked.txt",
            "--intent",
            "write",
            "--resource",
            "tracked.txt",
            "--planned-action",
            "propose_patch",
            "--task-id",
            task_id,
            "--context-token",
            context_token,
        )
        if res_guard_patch.get("verdict") != "PRE_ACTION_GUARD_OK":
            fail(f"Guard before propose_patch failed: {res_guard_patch}")

        lease_id_patch = res_guard_patch["action_lease"]["lease_id"]

        # Retrieve file hash for patch base
        res_hash = call_mcp(temp_dir, "file_hash", {"resource": "tracked.txt"})
        base_hash = res_hash["hash"]

        diff_text = (
            "--- tracked.txt\n"
            "+++ tracked.txt\n"
            "@@ -1,1 +1,2 @@\n"
            " initial tracked content\n"
            "+new modification line\n"
        )
        res_propose = call_mcp(
            temp_dir,
            "propose_patch",
            {
                "agent_id": agent_id,
                "target": "tracked.txt",
                "base_hash": base_hash,
                "diff_text": diff_text,
                "task_id": task_id,
                "context_token": context_token,
                "action_lease_id": lease_id_patch,
            },
        )
        if res_propose.get("status") != "PATCH_PROPOSED" and res_propose.get("verdict") != "PATCH_PROPOSED":
            fail(f"propose_patch failed: {res_propose}")

        patch_id = res_propose["patch_id"]

        # 16 & 17. guard & apply_patch
        res_guard_apply = run_cli(
            temp_dir,
            "guard",
            "--agent-id",
            agent_id,
            "--request",
            "modify tracked.txt",
            "--intent",
            "write",
            "--resource",
            "tracked.txt",
            "--planned-action",
            "apply_patch",
            "--task-id",
            task_id,
            "--context-token",
            context_token,
        )
        if res_guard_apply.get("verdict") != "PRE_ACTION_GUARD_OK":
            fail(f"Guard before apply_patch failed: {res_guard_apply}")

        lease_id_apply = res_guard_apply["action_lease"]["lease_id"]

        res_apply = call_mcp(
            temp_dir,
            "apply_patch",
            {
                "agent_id": agent_id,
                "patch_id": patch_id,
                "task_id": task_id,
                "context_token": context_token,
                "action_lease_id": lease_id_apply,
            },
        )
        if res_apply.get("verdict") != "PATCH_APPLIED":
            fail(f"apply_patch failed: {res_apply}")

        # 18. workspace_audit
        res_audit = run_cli(temp_dir, "audit", "--agent-id", agent_id, "--task-id", task_id, "--resource", "tracked.txt")
        # Check raw string or json response
        if res_audit.get("verdict") != "WORKSPACE_AUDIT_OK":
            fail(f"Workspace audit failed: {res_audit}")

        # 18b. release_claim
        claim_id = res_claim["claim_id"]
        res_release = call_mcp(
            temp_dir,
            "release_claim",
            {
                "agent_id": agent_id,
                "claim_id": claim_id,
                "summary": "smoke cleanup",
            },
        )
        if res_release.get("verdict") != "CLAIM_RELEASED":
            fail(f"release_claim failed: {res_release}")

        # 18c. scribe_record
        res_record = call_mcp(
            temp_dir,
            "scribe_record",
            {
                "agent_id": agent_id,
                "request": "modify tracked.txt",
                "summary": "smoke finished",
                "touched_resources": ["tracked.txt"],
                "verdict": "CLAIM_RELEASED",
                "tags": ["smoke"],
            },
        )
        if res_record.get("verdict") != "SCRIBE_RECORD_WRITTEN":
            fail(f"scribe_record failed: {res_record}")

        # 19. finish_task avec lease
        res_guard_finish = run_cli(
            temp_dir,
            "guard",
            "--agent-id",
            agent_id,
            "--request",
            "modify tracked.txt",
            "--intent",
            "write",
            "--resource",
            "tracked.txt",
            "--planned-action",
            "finish_task",
            "--task-id",
            task_id,
            "--context-token",
            context_token,
        )
        if res_guard_finish.get("verdict") != "PRE_ACTION_GUARD_OK":
            fail(f"Guard before finish_task failed: {res_guard_finish}")

        lease_id_finish = res_guard_finish["action_lease"]["lease_id"]

        res_finish = call_mcp(
            temp_dir,
            "finish_task",
            {
                "agent_id": agent_id,
                "summary": "Completed modification of tracked.txt",
                "task_id": task_id,
                "context_token": context_token,
                "action_lease_id": lease_id_finish,
            },
        )
        if res_finish.get("verdict") not in {"TASK_FINISHED", "TASK_FINISHED_OK"}:
            fail(f"finish_task failed: {res_finish}")

        # 20. verifying final clean state
        res_status = call_mcp(temp_dir, "task_status", {"task_id": task_id})
        if res_status.get("task", {}).get("status") != "finished":
            fail(f"Final task status is not 'finished': {res_status}")

        print("HOST_AUTO_GUARD_SMOKE_OK")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
