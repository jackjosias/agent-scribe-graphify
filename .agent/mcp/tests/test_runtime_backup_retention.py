from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import runtime_backup_retention as retention
from runtime.validation_lock import validation_runtime_lock


def make_project(root: Path) -> None:
    (root / ".agent" / "mcp").mkdir(parents=True)
    (root / ".agent" / "mcp" / "server_entry.py").write_text("# marker\n", encoding="utf-8")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")


def make_backup(path: Path, index: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"backup-{index}\n", encoding="utf-8")
    ts = time.time() + index
    os.utime(path, (ts, ts))
    return path


class RuntimeBackupRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        make_project(self.root)
        self.runtime = self.root / ".agent" / "state" / "runtime"
        self.runtime.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_01_dry_run_reports_backup_count_and_total_bytes(self) -> None:
        make_backup(self.runtime / "coordination.backup-20260627T000001Z.sqlite", 1)
        report = retention.runtime_backup_report(self.root)
        self.assertEqual(report["backup_count"], 1)
        self.assertGreater(report["total_bytes"], 0)

    def test_02_dry_run_does_not_move_or_delete_files(self) -> None:
        backup = make_backup(self.runtime / "coordination.backup-20260627T000001Z.sqlite", 1)
        retention.organize_runtime_backups(self.root, apply=False)
        self.assertTrue(backup.exists())

    def test_03_organize_apply_moves_top_level_backups(self) -> None:
        backup = make_backup(self.runtime / "coordination.backup-20260627T000001Z.sqlite", 1)
        result = retention.organize_runtime_backups(self.root, apply=True)
        self.assertEqual(result["verdict"], retention.RUNTIME_BACKUP_ORGANIZED)
        self.assertFalse(backup.exists())
        self.assertTrue((self.runtime / "backups" / backup.name).exists())

    def test_04_cleanup_apply_keeps_newest_n_backups(self) -> None:
        self._make_nested_backups(5)
        result = retention.cleanup_runtime_backups(self.root, keep_last=2, apply=True)
        self.assertEqual(result["delete_count"], 3)
        remaining = sorted((self.runtime / "backups").glob("coordination.backup-*.sqlite"))
        self.assertEqual(len(remaining), 2)
        self.assertTrue(all(path.name.endswith(("000003Z.sqlite", "000004Z.sqlite")) for path in remaining))

    def test_05_cleanup_deletes_older_backups_only_under_runtime_backups(self) -> None:
        top = make_backup(self.runtime / "coordination.backup-20260627T000099Z.sqlite", 99)
        self._make_nested_backups(3)
        retention.cleanup_runtime_backups(self.root, keep_last=1, apply=True)
        self.assertTrue(top.exists())
        self.assertEqual(len(list((self.runtime / "backups").glob("coordination.backup-*.sqlite"))), 1)

    def test_06_cleanup_refuses_if_validation_smoke_lock_active(self) -> None:
        self._make_nested_backups(2)
        with validation_runtime_lock(self.root, timeout_seconds=1):
            result = retention.cleanup_runtime_backups(self.root, keep_last=1, apply=True)
        self.assertEqual(result["verdict"], retention.VALIDATION_RUNTIME_BUSY)
        self.assertEqual(len(list((self.runtime / "backups").glob("coordination.backup-*.sqlite"))), 2)

    def test_07_cleanup_never_deletes_coordination_sqlite(self) -> None:
        db_file = self.runtime / "coordination.sqlite"
        db_file.write_text("db\n", encoding="utf-8")
        self._make_nested_backups(2)
        retention.cleanup_runtime_backups(self.root, keep_last=1, apply=True)
        self.assertTrue(db_file.exists())

    def test_08_cleanup_never_deletes_wal_or_shm(self) -> None:
        wal = self.runtime / "coordination.sqlite-wal"
        shm = self.runtime / "coordination.sqlite-shm"
        wal.write_text("wal\n", encoding="utf-8")
        shm.write_text("shm\n", encoding="utf-8")
        self._make_nested_backups(2)
        retention.cleanup_runtime_backups(self.root, keep_last=1, apply=True)
        self.assertTrue(wal.exists())
        self.assertTrue(shm.exists())

    def test_09_cleanup_refuses_if_target_path_escapes_runtime_dir(self) -> None:
        outside = Path(self.tmp.name) / "outside.sqlite"
        outside.write_text("outside\n", encoding="utf-8")
        backups = self.runtime / "backups"
        backups.mkdir()
        (backups / "coordination.backup-20260627T000001Z.sqlite").symlink_to(outside)
        result = retention.cleanup_runtime_backups(self.root, keep_last=1, apply=True)
        self.assertEqual(result["verdict"], retention.RUNTIME_BACKUP_RETENTION_REFUSED)

    def test_10_report_includes_oldest_and_newest_backup(self) -> None:
        self._make_nested_backups(2)
        report = retention.runtime_backup_report(self.root)
        self.assertIsNotNone(report["oldest_backup"])
        self.assertIsNotNone(report["newest_backup"])

    def test_11_invalid_keep_last_rejected(self) -> None:
        with self.assertRaises(ValueError):
            retention.cleanup_runtime_backups(self.root, keep_last=0, apply=False)

    def test_12_cli_dry_run_exits_zero(self) -> None:
        proc = self._run_cli("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])

    def test_13_cli_apply_organize_works_in_temp_project(self) -> None:
        backup = make_backup(self.runtime / "coordination.backup-20260627T000001Z.sqlite", 1)
        proc = self._run_cli("--apply", "--organize")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(backup.exists())
        self.assertTrue((self.runtime / "backups" / backup.name).exists())

    def test_14_cli_apply_cleanup_keep_last_works_in_temp_project(self) -> None:
        self._make_nested_backups(4)
        proc = self._run_cli("--apply", "--cleanup", "--keep-last", "2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(list((self.runtime / "backups").glob("coordination.backup-*.sqlite"))), 2)

    def test_15_json_output_is_parseable(self) -> None:
        proc = self._run_cli("--dry-run")
        data = json.loads(proc.stdout)
        self.assertIn("verdict", data)

    def test_16_cleanup_refuses_with_active_agents(self) -> None:
        db_file = self.runtime / "coordination.sqlite"
        con = sqlite3.connect(db_file)
        try:
            con.execute("CREATE TABLE agents(agent_id TEXT, status TEXT)")
            con.execute("INSERT INTO agents(agent_id,status) VALUES('active-agent','active')")
            con.commit()
        finally:
            con.close()
        self._make_nested_backups(2)
        result = retention.cleanup_runtime_backups(self.root, keep_last=1, apply=True)
        self.assertEqual(result["verdict"], retention.RUNTIME_ACTIVE_AGENTS_BUSY)


    def test_17_organize_apply_refuses_if_validation_lock_active(self) -> None:
        backup = make_backup(self.runtime / "coordination.backup-20260627T000777Z.sqlite", 777)
        with validation_runtime_lock(self.root, timeout_seconds=1):
            result = retention.organize_runtime_backups(self.root, apply=True)
        self.assertEqual(result["verdict"], retention.VALIDATION_RUNTIME_BUSY)
        self.assertTrue(backup.exists())

    def test_18_organize_apply_refuses_with_active_agents(self) -> None:
        backup = make_backup(self.runtime / "coordination.backup-20260627T000778Z.sqlite", 778)
        db_file = self.runtime / "coordination.sqlite"
        con = sqlite3.connect(db_file)
        try:
            con.execute("CREATE TABLE agents(agent_id TEXT, status TEXT)")
            con.execute("INSERT INTO agents(agent_id,status) VALUES('active-agent','active')")
            con.commit()
        finally:
            con.close()
        result = retention.organize_runtime_backups(self.root, apply=True)
        self.assertEqual(result["verdict"], retention.RUNTIME_ACTIVE_AGENTS_BUSY)
        self.assertTrue(backup.exists())

    def test_19_organize_dry_run_reports_even_when_validation_lock_active(self) -> None:
        backup = make_backup(self.runtime / "coordination.backup-20260627T000779Z.sqlite", 779)
        with validation_runtime_lock(self.root, timeout_seconds=1):
            result = retention.organize_runtime_backups(self.root, apply=False)
        self.assertEqual(result["verdict"], retention.RUNTIME_BACKUP_DRY_RUN)
        self.assertEqual(result["would_move_count"], 1)
        self.assertTrue(backup.exists())

    def _make_nested_backups(self, count: int) -> None:
        for i in range(count):
            make_backup(self.runtime / "backups" / f"coordination.backup-20260627T00000{i}Z.sqlite", i)

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        script = Path(__file__).resolve().parents[2] / "scripts" / "runtime_backup_doctor.py"
        return subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), *args],
            text=True,
            capture_output=True,
            timeout=15,
        )


if __name__ == "__main__":
    unittest.main()
