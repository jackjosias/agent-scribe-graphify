from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import db, tenor_changeset
from _strict_cleanup import remove_tree_strict


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TenorChangesetTransactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.txt").write_bytes(b"alpha\n")
        (self.root / "src" / "b.txt").write_bytes(b"beta\n")
        (self.root / "src" / "large.ts").write_text(
            "".join(f"export const value{index} = {index};\n" for index in range(2051)),
            encoding="utf-8",
        )
        self.previous_root = os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT")
        os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(self.root)
        db.init_db(self.root)
        db.register_agent("test", "unit", "agent-a", project_root=self.root)

    def tearDown(self) -> None:
        if self.previous_root is None:
            os.environ.pop("AGENT_SCRIBE_GRAPHIFY_ROOT", None)
        else:
            os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"] = self.previous_root
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    def change(self, path: str, before: str, after: str) -> dict[str, str]:
        return {
            "path": path,
            "operation": "replace",
            "base_hash": hashlib.sha256(before.encode("utf-8")).hexdigest(),
            "content": after,
        }

    def apply(self, changes: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
        return tenor_changeset.apply_changeset(
            project_root=self.root,
            agent_id="agent-a",
            task_id="task-a",
            changes=changes,
            validators=kwargs.pop("validators", [{
                "argv": [sys.executable, "-c", "raise SystemExit(0)"],
                "timeout_seconds": 20,
            }]),
            allowed_resources=["src/a.txt", "src/b.txt", "src/new.txt", "src/link.txt", "src/large.ts"],
            **kwargs,
        )

    def test_validator_is_mandatory_for_every_mutating_changeset(self) -> None:
        result = self.apply(
            [self.change("src/a.txt", "alpha\n", "alpha-2\n")],
            validators=[],
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_VALIDATORS_REQUIRED")
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha\n")

    def test_destructive_full_replace_2051_lines_to_5_is_rejected_before_write(self) -> None:
        target = self.root / "src" / "large.ts"
        before = target.read_bytes()
        result = self.apply([{
            "path": "src/large.ts",
            "operation": "replace",
            "base_hash": hashlib.sha256(before).hexdigest(),
            "content": "one\ntwo\nthree\nfour\nfive\n",
        }])
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_DESTRUCTIVE_REPLACE_REJECTED")
        self.assertEqual(result["path"], "src/large.ts")
        self.assertEqual(result["before_lines"], 2051)
        self.assertEqual(result["after_lines"], 5)
        self.assertEqual(target.read_bytes(), before)

    def test_destructive_full_replace_requires_hash_bound_confirmation(self) -> None:
        target = self.root / "src" / "large.ts"
        before = target.read_bytes()
        content = "one\ntwo\nthree\nfour\nfive\n"
        after_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        wrong = self.apply([{
            "path": "src/large.ts",
            "operation": "replace",
            "base_hash": hashlib.sha256(before).hexdigest(),
            "content": content,
        }], confirm_full_replacements=[{
            "path": "src/large.ts",
            "base_hash": hashlib.sha256(before).hexdigest(),
            "new_hash": "0" * 64,
        }], request_id="wrong-full-replace-confirmation")
        self.assertEqual(wrong["verdict"], "TENOR_CHANGESET_DESTRUCTIVE_REPLACE_REJECTED")
        self.assertEqual(target.read_bytes(), before)

        accepted = self.apply([{
            "path": "src/large.ts",
            "operation": "replace",
            "base_hash": hashlib.sha256(before).hexdigest(),
            "content": content,
        }], confirm_full_replacements=[{
            "path": "src/large.ts",
            "base_hash": hashlib.sha256(before).hexdigest(),
            "new_hash": after_hash,
        }], request_id="correct-full-replace-confirmation")
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(target.read_text(encoding="utf-8"), content)

    def test_structured_edit_applies_multiple_unique_anchors_atomically(self) -> None:
        result = self.apply([{
            "path": "src/a.txt",
            "operation": "edit",
            "base_hash": sha256(self.root / "src" / "a.txt"),
            "edits": [
                {"old_text": "alpha", "new_text": "first", "expected_occurrences": 1},
                {"old_text": "first\n", "new_text": "first-second\n", "expected_occurrences": 1},
            ],
        }])
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "first-second\n")

    def test_structured_edit_rejects_missing_or_ambiguous_anchor_without_write(self) -> None:
        target = self.root / "src" / "a.txt"
        original = target.read_bytes()
        missing = self.apply([{
            "path": "src/a.txt",
            "operation": "edit",
            "base_hash": sha256(target),
            "edits": [{"old_text": "absent", "new_text": "value", "expected_occurrences": 1}],
        }], request_id="missing-anchor")
        self.assertEqual(missing["verdict"], "TENOR_CHANGESET_EDIT_ANCHOR_MISMATCH")
        self.assertEqual(target.read_bytes(), original)

        target.write_text("alpha alpha\n", encoding="utf-8")
        ambiguous = self.apply([{
            "path": "src/a.txt",
            "operation": "edit",
            "base_hash": sha256(target),
            "edits": [{"old_text": "alpha", "new_text": "beta", "expected_occurrences": 1}],
        }], request_id="ambiguous-anchor")
        self.assertEqual(ambiguous["verdict"], "TENOR_CHANGESET_EDIT_ANCHOR_MISMATCH")
        self.assertEqual(target.read_text(encoding="utf-8"), "alpha alpha\n")

    def test_two_files_commit_as_one_validated_changeset(self) -> None:
        result = self.apply(
            [
                self.change("src/a.txt", "alpha\n", "alpha-2\n"),
                self.change("src/b.txt", "beta\n", "beta-2\n"),
            ],
            validators=[{
                "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('src/a.txt').read_text() == 'alpha-2\\n'; assert Path('src/b.txt').read_text() == 'beta-2\\n'"],
                "timeout_seconds": 20,
            }],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_COMMITTED")
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha-2\n")
        self.assertEqual((self.root / "src" / "b.txt").read_text(encoding="utf-8"), "beta-2\n")
        self.assertEqual(len(result["files"]), 2)
        self.assertTrue(result["validators"][0]["ok"])

    def test_stale_hash_rejects_every_file_before_first_write(self) -> None:
        changes = [
            self.change("src/a.txt", "wrong\n", "alpha-2\n"),
            self.change("src/b.txt", "beta\n", "beta-2\n"),
        ]
        result = self.apply(changes)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_BASE_STALE")
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha\n")
        self.assertEqual((self.root / "src" / "b.txt").read_text(encoding="utf-8"), "beta\n")

    def test_failed_validator_rolls_back_every_file(self) -> None:
        result = self.apply(
            [
                self.change("src/a.txt", "alpha\n", "alpha-2\n"),
                self.change("src/b.txt", "beta\n", "beta-2\n"),
            ],
            validators=[{
                "argv": [sys.executable, "-c", "raise SystemExit(7)"],
                "timeout_seconds": 20,
            }],
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_VALIDATION_FAILED_ROLLED_BACK")
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha\n")
        self.assertEqual((self.root / "src" / "b.txt").read_text(encoding="utf-8"), "beta\n")

    def test_validator_output_is_streamed_into_bounded_tail_buffers(self) -> None:
        results = tenor_changeset._run_validators([{
            "argv": [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('A' * 1000000); sys.stderr.write('B' * 1000000)",
            ],
            "cwd": self.root,
            "cwd_display": ".",
            "timeout_seconds": 20,
        }])
        self.assertTrue(results[0]["ok"], results)
        self.assertEqual(len(results[0]["stdout"].encode("utf-8")), tenor_changeset.MAX_VALIDATOR_OUTPUT_BYTES)
        self.assertEqual(len(results[0]["stderr"].encode("utf-8")), tenor_changeset.MAX_VALIDATOR_OUTPUT_BYTES)
        self.assertEqual(set(results[0]["stdout"]), {"A"})
        self.assertEqual(set(results[0]["stderr"]), {"B"})

    @unittest.skipIf(os.name == "nt", "POSIX process-group liveness assertion")
    def test_validator_timeout_kills_descendant_process_group(self) -> None:
        child_pid_path = self.root / "child.pid"
        script = (
            "import pathlib,subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "pathlib.Path('child.pid').write_text(str(child.pid)); time.sleep(30)"
        )
        started = time.monotonic()
        results = tenor_changeset._run_validators([{
            "argv": [sys.executable, "-c", script],
            "cwd": self.root,
            "cwd_display": ".",
            "timeout_seconds": 1,
        }])
        duration = time.monotonic() - started
        self.assertTrue(results[0]["timed_out"], results)
        self.assertEqual(results[0]["returncode"], 124)
        self.assertLess(duration, 8.0, results)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 3
        alive = True
        while time.monotonic() < deadline:
            stat = Path(f"/proc/{child_pid}/stat")
            if not stat.exists():
                alive = False
                break
            fields = stat.read_text(encoding="utf-8").split()
            if len(fields) > 2 and fields[2] == "Z":
                alive = False
                break
            time.sleep(0.05)
        self.assertFalse(alive, f"validator descendant {child_pid} survived timeout")

    def test_validator_target_mutation_is_detected_and_rolled_back(self) -> None:
        result = self.apply(
            [self.change("src/a.txt", "alpha\n", "alpha-2\n")],
            validators=[{
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('src/a.txt').write_text('validator-drift\\n')",
                ],
                "timeout_seconds": 20,
            }],
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_VALIDATOR_MUTATION_ROLLED_BACK")
        self.assertEqual(result["drifted_resources"], ["src/a.txt"])
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha\n")

    def test_mid_commit_failure_rolls_back_already_replaced_file(self) -> None:
        result = self.apply(
            [
                self.change("src/a.txt", "alpha\n", "alpha-2\n"),
                self.change("src/b.txt", "beta\n", "beta-2\n"),
            ],
            _test_fail_after_replaces=1,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_APPLY_FAILED_ROLLED_BACK")
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha\n")
        self.assertEqual((self.root / "src" / "b.txt").read_text(encoding="utf-8"), "beta\n")

    def test_precommit_guard_failure_rolls_back_declared_files_before_commit(self) -> None:
        observed: list[str] = []

        def guard(provisional: dict[str, object]) -> dict[str, object]:
            observed.append((self.root / "src" / "a.txt").read_text(encoding="utf-8"))
            return {
                "ok": False,
                "verdict": "DIRECT_WRITE_BYPASS_DETECTED",
                "workspace_audit": {"suspects": [{"path": "README.md"}]},
            }

        result = self.apply(
            [self.change("src/a.txt", "alpha\n", "alpha-2\n")],
            precommit_guard=guard,
            request_id="precommit-guard-reject",
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_PRECOMMIT_GUARD_FAILED_ROLLED_BACK")
        self.assertEqual(observed, ["alpha-2\n"])
        self.assertEqual(result["restored"], ["src/a.txt"])
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha\n")
        with db.connect(self.root) as con:
            row = con.execute(
                f"SELECT status FROM {tenor_changeset.TRANSACTION_TABLE} WHERE changeset_id=?",
                (result["changeset_id"],),
            ).fetchone()
        self.assertEqual(row["status"], "rolled_back")

    def test_precommit_guard_runs_before_committed_receipt_is_published(self) -> None:
        statuses: list[str] = []

        def guard(provisional: dict[str, object]) -> dict[str, object]:
            with db.connect(self.root) as con:
                row = con.execute(
                    f"SELECT status FROM {tenor_changeset.TRANSACTION_TABLE} WHERE changeset_id=?",
                    (provisional["changeset_id"],),
                ).fetchone()
            statuses.append(str(row["status"]))
            return {"ok": True, "verdict": "PRECOMMIT_GUARD_CLEAN"}

        result = self.apply(
            [self.change("src/a.txt", "alpha\n", "alpha-2\n")],
            precommit_guard=guard,
            request_id="precommit-guard-pass",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(statuses, ["guarding"])
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_COMMITTED")
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha-2\n")

    def test_idempotent_retry_returns_original_receipt(self) -> None:
        changes = [self.change("src/a.txt", "alpha\n", "alpha-2\n")]
        first = self.apply(changes, request_id="request-123")
        second = self.apply(changes, request_id="request-123")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["verdict"], "TENOR_CHANGESET_ALREADY_COMMITTED")
        self.assertEqual(second["changeset_id"], first["changeset_id"])

    def test_request_id_cannot_be_reused_with_different_payload(self) -> None:
        first = self.apply([self.change("src/a.txt", "alpha\n", "alpha-2\n")], request_id="request-123")
        self.assertTrue(first["ok"], first)
        second = self.apply([self.change("src/b.txt", "beta\n", "beta-2\n")], request_id="request-123")
        self.assertFalse(second["ok"], second)
        self.assertEqual(second["verdict"], "TENOR_CHANGESET_IDEMPOTENCY_CONFLICT")

    def test_recovery_preserves_live_transaction_and_recovers_dead_owner(self) -> None:
        tenor_changeset.ensure_schema(self.root)
        now = int(time.time())
        with db.connect(self.root) as con:
            con.execute(
                f"INSERT INTO {tenor_changeset.TRANSACTION_TABLE}(changeset_id,request_id,request_fingerprint,task_id,agent_id,owner_pid,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("cs-live", "live-request", "fingerprint", "task-live", "agent-a", os.getpid(), "applying", now, now),
            )

        live = tenor_changeset.recover_incomplete(self.root)
        self.assertEqual(live["recovered"], [])
        with db.connect(self.root) as con:
            status = con.execute(
                f"SELECT status FROM {tenor_changeset.TRANSACTION_TABLE} WHERE changeset_id='cs-live'"
            ).fetchone()[0]
            self.assertEqual(status, "applying")
            con.execute(
                f"UPDATE {tenor_changeset.TRANSACTION_TABLE} SET owner_pid=? WHERE changeset_id='cs-live'",
                (2**30,),
            )

        dead = tenor_changeset.recover_incomplete(self.root)
        self.assertEqual(dead["recovered"], ["cs-live"])
        with db.connect(self.root) as con:
            status = con.execute(
                f"SELECT status FROM {tenor_changeset.TRANSACTION_TABLE} WHERE changeset_id='cs-live'"
            ).fetchone()[0]
        self.assertEqual(status, "rolled_back_recovered")

    def test_existing_lock_cannot_be_stolen_by_same_agent_and_task(self) -> None:
        tenor_changeset.ensure_schema(self.root)
        now = time.time()
        with db.connect(self.root) as con:
            con.execute(
                f"INSERT INTO {tenor_changeset.LOCK_TABLE}(lock_id,resource,agent_id,task_id,mode,created_at,expires_at,heartbeat_at) VALUES(?,?,?,?,?,?,?,?)",
                ("changeset-other-owner", "src/a.txt", "agent-a", "task-a", "exclusive", now, now + 600, now),
            )
        result = self.apply([self.change("src/a.txt", "alpha\n", "alpha-2\n")])
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_APPLY_FAILED_ROLLED_BACK")
        self.assertEqual(result["cause"], "TENOR_CHANGESET_RESOURCE_BUSY")
        self.assertEqual((self.root / "src" / "a.txt").read_text(encoding="utf-8"), "alpha\n")

    def test_runtime_database_connection_closes_on_context_exit(self) -> None:
        with db.connect(self.root) as con:
            self.assertEqual(con.execute("SELECT 1").fetchone()[0], 1)

        with self.assertRaises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")

    def test_create_and_delete_require_explicit_operations_and_confirmation(self) -> None:
        create = {
            "path": "src/new.txt",
            "operation": "create",
            "content": "new\n",
        }
        delete = {
            "path": "src/b.txt",
            "operation": "delete",
            "base_hash": sha256(self.root / "src" / "b.txt"),
        }
        denied = self.apply([create, delete])
        self.assertFalse(denied["ok"], denied)
        self.assertEqual(denied["verdict"], "TENOR_CHANGESET_DELETE_CONFIRMATION_REQUIRED")
        self.assertFalse((self.root / "src" / "new.txt").exists())
        self.assertTrue((self.root / "src" / "b.txt").exists())

        accepted = self.apply([create, delete], confirm_deletions=["src/b.txt"])
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual((self.root / "src" / "new.txt").read_text(encoding="utf-8"), "new\n")
        self.assertFalse((self.root / "src" / "b.txt").exists())

    def test_path_traversal_duplicate_and_unscoped_resources_are_rejected(self) -> None:
        traversal = self.change("../outside.txt", "", "owned\n")
        result = self.apply([traversal])
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_INVALID_PATH")

        duplicate = self.change("src/a.txt", "alpha\n", "alpha-2\n")
        result = self.apply([duplicate, dict(duplicate)])
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_DUPLICATE_RESOURCE")

        unscoped = self.change("not-allowed.txt", "", "owned\n")
        result = self.apply([unscoped])
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_RESOURCE_OUT_OF_SCOPE")

    @unittest.skipIf(os.name == "nt", "Windows symlink creation requires privileges on some runners")
    def test_symlink_target_is_rejected_without_touching_external_file(self) -> None:
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (self.root / "src" / "link.txt").symlink_to(outside)
        result = self.apply([{
            "path": "src/link.txt",
            "operation": "replace",
            "base_hash": sha256(outside),
            "content": "owned\n",
        }])
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_SYMLINK_FORBIDDEN")
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")


if __name__ == "__main__":
    unittest.main()
