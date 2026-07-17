from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
AGENT_DIR = ROOT / ".agent"
for path in (MCP_DIR, AGENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from host_adapter import host_config
from runtime import db, graphify_readiness, installation_state
from _strict_cleanup import remove_tree_strict
from unittest.mock import patch


class V216TerrainEnforcementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="v216-terrain-enforcement-")
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(ROOT / ".agent" / "mcp", self.root / ".agent" / "mcp")
        shutil.copytree(ROOT / ".agent" / "host_adapter", self.root / ".agent" / "host_adapter")
        (self.root / "README.md").write_text("terrain\n", encoding="utf-8")
        (self.root / "one.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "two.py").write_text("value = 2\n", encoding="utf-8")
        self.entry = self.root / ".agent" / "mcp" / "server_entry.py"

        subprocess.run(["git", "init"], cwd=self.root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "terrain@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Terrain"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "README.md", "one.py", "two.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.root, capture_output=True, check=True)

        prepared = installation_state.ensure_fresh_installation_state(self.root)
        self.assertTrue(prepared["ok"], prepared)
        self.assertTrue(installation_state.finalize_installation_state(self.root)["ok"])
        self.assertTrue(graphify_readiness.write_smoke_fixture(self.root)["ok"])
        configured = host_config.configure_host(self.root, explicit="opencode")
        self.assertTrue(configured["ok"], configured)
        config = host_config._load_jsonc((self.root / "opencode.jsonc").read_text(encoding="utf-8"))
        self.host_env = {
            **os.environ,
            **config["mcp"][host_config.SERVER_NAME]["environment"],
            "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self.root),
            graphify_readiness.FIXTURE_ENV: "1",
        }
        self.unbound_env = {
            **os.environ,
            "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self.root),
            graphify_readiness.FIXTURE_ENV: "1",
        }
        registered = self.call(
            "register_agent", env=self.unbound_env, agent_id="terrain-agent", host_tool="opencode"
        )
        self.assertEqual(registered["verdict"], "AGENT_REGISTERED", registered)

    def tearDown(self) -> None:
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    def call(self, tool: str, *, env: dict[str, str] | None = None, **args: object) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, str(self.entry), "--call", tool, "--args", json.dumps(args)],
            cwd=self.root,
            env=env or self.host_env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        raw = json.loads(proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout))
        if "content" in raw:
            return json.loads(raw["content"][0]["text"])
        return raw

    def insert_exclusive_lock(self, *, agent_id: str, task_id: str, resource: str = "one.py") -> None:
        database = self.root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        now = time.time()
        with closing(sqlite3.connect(database)) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS resource_exclusive_locks("
                    "lock_id TEXT PRIMARY KEY,resource TEXT NOT NULL UNIQUE,agent_id TEXT NOT NULL,"
                    "task_id TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'exclusive',created_at REAL NOT NULL,"
                    "expires_at REAL NOT NULL,heartbeat_at REAL NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO resource_exclusive_locks("
                    "lock_id,resource,agent_id,task_id,mode,created_at,expires_at,heartbeat_at"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    (f"lock-{uuid.uuid4().hex}", resource, agent_id, task_id, "exclusive", now, now + 300, now),
                )

    def test_descriptive_intent_is_rejected_before_task_creation(self) -> None:
        result = self.call(
            "before_task",
            agent_id="terrain-agent",
            request="fix signpost",
            intent="fix signpost inline editor creation flow",
            resource="one.py",
        )
        self.assertEqual(result["verdict"], "TASK_INTENT_ENUM_REQUIRED", result)
        self.assertEqual(result["allowed_intents"], ["delete", "read", "write"], result)

    def test_bound_host_cannot_register_replacement_identity(self) -> None:
        result = self.call("register_agent", agent_id="replacement-agent", host_tool="opencode")
        self.assertEqual(result["verdict"], "AGENT_REGISTRATION_REQUIRES_TENOR_INIT", result)

    def test_unknown_identity_routes_directly_to_tenor_init_in_bound_host(self) -> None:
        result = self.call(
            "before_task", agent_id="unknown-agent", request="edit one", intent="write", resource="one.py"
        )
        self.assertEqual(result["verdict"], "TENOR_INIT_REQUIRED", result)
        self.assertEqual(result["after_success_must_call"]["tool"], "tenor_init_bridge", result)

    def test_bound_host_cannot_retire_identity_with_active_task(self) -> None:
        before = self.call(
            "before_task", agent_id="terrain-agent", request="edit one", intent="write", resource="one.py"
        )
        self.assertEqual(before["verdict"], "BEFORE_TASK_OK", before)
        result = self.call("retire_agent", agent_id="terrain-agent", reason="escape hard stop")
        self.assertEqual(result["verdict"], "AGENT_RETIRE_REFUSED_ACTIVE_OWNERSHIP", result)
        self.assertGreaterEqual(result["blockers"]["active_tasks"], 1, result)

    def test_bound_host_cannot_retire_identity_with_current_exclusive_lock(self) -> None:
        before = self.call(
            "before_task", agent_id="terrain-agent", request="edit one", intent="write", resource="one.py"
        )
        self.assertEqual(before["verdict"], "BEFORE_TASK_OK", before)
        self.insert_exclusive_lock(agent_id="terrain-agent", task_id=before["task_id"])
        result = self.call("retire_agent", agent_id="terrain-agent", reason="escape active lock")
        self.assertEqual(result["verdict"], "AGENT_RETIRE_REFUSED_ACTIVE_OWNERSHIP", result)
        self.assertGreaterEqual(result["blockers"]["active_resource_locks"], 1, result)

    def test_graphify_build_refuses_active_current_exclusive_lock(self) -> None:
        before = self.call(
            "before_task", agent_id="terrain-agent", request="edit one", intent="write", resource="one.py"
        )
        self.assertEqual(before["verdict"], "BEFORE_TASK_OK", before)
        self.insert_exclusive_lock(agent_id="terrain-agent", task_id=before["task_id"])
        result = self.call("graphify_project_build", timeout_seconds=1)
        self.assertEqual(result["verdict"], "GRAPHIFY_BUILD_ACTIVE_OWNERSHIP", result)
        self.assertGreaterEqual(result["ownership"]["active_resource_locks"], 1, result)

    def test_one_bound_identity_has_only_one_active_task(self) -> None:
        first = self.call(
            "before_task", agent_id="terrain-agent", request="edit one", intent="write", resource="one.py"
        )
        self.assertEqual(first["verdict"], "BEFORE_TASK_OK", first)
        second = self.call(
            "before_task", agent_id="terrain-agent", request="inspect two", intent="read", resource="two.py"
        )
        self.assertEqual(second["verdict"], "ACTIVE_TASK_EXISTS", second)
        self.assertEqual(second["must_call"]["args"]["task_id"], first["task_id"], second)

    def test_fixed_bug_record_requires_mcp_patch_receipt(self) -> None:
        before = self.call(
            "before_task", agent_id="terrain-agent", request="edit one", intent="write", resource="one.py"
        )
        self.assertEqual(before["verdict"], "BEFORE_TASK_OK", before)
        result = self.call(
            "scribe_record",
            agent_id="terrain-agent",
            task_id=before["task_id"],
            context_token=before["context_token"],
            request="edit one",
            summary="claimed fixed without MCP write",
            touched_resources=["one.py"],
            verdict="FIXED",
            record_type="bugfix",
        )
        self.assertEqual(result["verdict"], "SCRIBE_RECORD_MCP_PATCH_RECEIPT_REQUIRED", result)

    def test_exact_task_can_rescope_only_after_authorized_patch_receipt(self) -> None:
        before = self.call(
            "before_task", agent_id="terrain-agent", request="edit two files", intent="write", resource="one.py"
        )
        blocked = self.call(
            "scope_task_resource",
            agent_id="terrain-agent",
            task_id=before["task_id"],
            context_token=before["context_token"],
            resource="two.py",
        )
        self.assertEqual(blocked.get("verdict"), "TASK_RESOURCE_RESCOPE_PATCH_REQUIRED", blocked)

        script = (
            "from runtime import direct_fs_tripwire; "
            "direct_fs_tripwire.record_authorized_mutation("
            f"{before['task_id']!r}, 'terrain-agent', 'one.py', 'apply_patch', "
            "patch_id='patch-terrain', before_hash='before', after_hash='after')"
        )
        recorded = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.root,
            env={**self.unbound_env, "PYTHONPATH": str(self.root / ".agent" / "mcp")},
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        resumed = self.call(
            "scope_task_resource",
            agent_id="terrain-agent",
            task_id=before["task_id"],
            context_token=before["context_token"],
            resource="two.py",
        )
        self.assertEqual(resumed["verdict"], "TASK_RESOURCE_SCOPED", resumed)
        self.assertEqual(resumed["previous_resource"], "one.py", resumed)

    def test_machine_schema_advertises_only_canonical_intents(self) -> None:
        listed = subprocess.run(
            [sys.executable, str(self.entry), "--list-tools"],
            cwd=self.root,
            env=self.host_env,
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        tools = json.loads(listed.stdout)["tools"]
        names = {item["name"] for item in tools}
        self.assertEqual(
            names,
            {
                "file_hash", "tenor_init_bridge", "portability_check",
                "graphify_required_check", "graphify_project_build",
                "tenor_task_start", "tenor_apply_changeset", "tenor_activity",
                "tenor_task_control",
            },
        )
        task_start = next(item for item in tools if item["name"] == "tenor_task_start")
        self.assertEqual(task_start["inputSchema"]["properties"]["intent"]["enum"], ["read", "write", "delete"])

    def test_runtime_database_integrity_failure_is_fail_closed(self) -> None:
        class CorruptCursor:
            def __iter__(self):
                return iter((("freelist corruption",),))

            def fetchone(self) -> tuple[str]:
                return ("freelist corruption",)

        class CorruptConnection:
            def execute(self, _statement: str) -> CorruptCursor:
                return CorruptCursor()

        @contextmanager
        def corrupt_connect(_project_root: Path | None = None):
            yield CorruptConnection()

        database = self.root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        db._init_db_checked.clear()
        with (
            patch.object(db, "_run_migrations", return_value=[]),
            patch.object(db, "paths", return_value={"db": database}),
            patch.object(db, "connect", side_effect=corrupt_connect),
        ):
            with self.assertRaisesRegex(db.CoordinationError, "SQLITE_INTEGRITY_CHECK_FAILED"):
                db.init_db(self.root)


if __name__ == "__main__":
    unittest.main()
