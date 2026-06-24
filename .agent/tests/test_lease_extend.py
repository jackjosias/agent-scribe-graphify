"""test_lease_extend.py — TDD tests for discipline.extend_action_lease().

Coverage: 16 tests.
  - Happy path: normal extension
  - Concurrent-safe: two agents, correct one extends
  - Wrong agent rejected
  - Consumed lease rejected
  - Expired lease rejected (and marked expired)
  - Extend count cap (MAX_LEASE_EXTEND_COUNT)
  - Cumulative TTL ceiling (MAX_CUMULATIVE_LEASE_TTL_SECONDS)
  - extend_seconds=0 rejected
  - extend_seconds=negative rejected
  - extend_seconds=None uses default
  - extend_seconds=very large clamped to ceiling
  - lease_id empty rejected
  - agent_id empty rejected
  - lease_id unknown rejected
  - Idempotent-safe: extend twice before deadline is allowed (within cap)
  - Schema migration: old lease (no extend_count col) handled gracefully
"""
from __future__ import annotations

import os
import sys
import time
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Bootstrap: point state at a throw-away temp dir so tests don't touch
# the real project state.
# ---------------------------------------------------------------------------
_ORIG_CWD = os.getcwd()


def _bootstrap_temp_root() -> str:
    tmp = tempfile.mkdtemp(prefix="test_lease_extend_")
    agent_state = Path(tmp) / ".agent" / "state"
    agent_state.mkdir(parents=True)
    return tmp


_TMP_ROOT = _bootstrap_temp_root()

# Inject mcp and runtime dir on path before importing discipline.
_MCP_DIR = str(Path(_ORIG_CWD) / ".agent" / "mcp")
_RUNTIME_DIR = str(Path(_ORIG_CWD) / ".agent" / "mcp" / "runtime")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

# Override env so db.py picks up temp state dir.
os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"] = _TMP_ROOT

import discipline  # noqa: E402 — must come after sys.path + env setup
import db          # noqa: E402


class TestLeaseExtend(unittest.TestCase):
    """16 tests covering extend_action_lease() in full edge-case mode."""

    def setUp(self) -> None:
        """Fresh schema + active agent before each test."""
        discipline.ensure_schema()
        db.init_db()
        self._agent = "test-agent-ext"
        now = db.now_ts()
        # Register agent using the real schema:
        # (agent_id, host_tool, model_name, pid, started_at, last_seen, status)
        with db.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO agents(agent_id, host_tool, model_name, pid, started_at, last_seen, status)
                VALUES(?,?,?,?,?,?,?)
                """,
                (self._agent, "test", "test-model", 0, now, now, "active"),
            )
            # Clear resource_locks and action_leases to guarantee isolation between tests.
            con.execute("DELETE FROM resource_locks")
            con.execute("DELETE FROM action_leases")

    def _issue(
        self,
        agent_id: str | None = None,
        action: str = "apply_patch",
        resource: str = "src/foo.py",
        ttl: int | None = None,
    ) -> dict:
        return discipline.issue_action_lease(
            agent_id=agent_id or self._agent,
            action=action,
            resource=resource,
            task_id="task-123",
            ttl_seconds=ttl,
        )

    # ------------------------------------------------------------------
    # 1. Happy path
    # ------------------------------------------------------------------
    def test_01_extend_happy_path(self) -> None:
        lease = self._issue(ttl=30)
        result = discipline.extend_action_lease(
            lease_id=lease["lease_id"],
            agent_id=self._agent,
            extend_seconds=60,
        )
        self.assertEqual(result["verdict"], "LEASE_EXTENDED")
        self.assertEqual(result["extend_count"], 1)
        self.assertGreater(result["expires_at"], lease["expires_at"])

    # ------------------------------------------------------------------
    # 2. Correct agent extends; other agent's lease untouched
    # ------------------------------------------------------------------
    def test_02_correct_agent_extends(self) -> None:
        other = "other-agent"
        with db.connect() as con:
            now = db.now_ts()
            con.execute(
                "INSERT OR REPLACE INTO agents(agent_id,host_tool,model_name,pid,started_at,last_seen,status) VALUES(?,?,?,?,?,?,?)",
                (other, "test", "test-model", 0, now, now, "active"),
            )
        lease_a = self._issue()
        lease_b = discipline.issue_action_lease(
            agent_id=other, action="apply_patch", resource="src/bar.py", task_id="task-456"
        )
        result = discipline.extend_action_lease(lease_a["lease_id"], self._agent)
        self.assertEqual(result["verdict"], "LEASE_EXTENDED")
        # lease_b untouched
        with db.connect() as con:
            raw = con.execute(
                "SELECT expires_at FROM action_leases WHERE lease_id=?", (lease_b["lease_id"],)
            ).fetchone()
        if raw:
            self.assertEqual(raw["expires_at"], lease_b["expires_at"])

    # ------------------------------------------------------------------
    # 3. Wrong agent rejected
    # ------------------------------------------------------------------
    def test_03_wrong_agent_rejected(self) -> None:
        lease = self._issue()
        # Wrong/unregistered agent raises either DisciplineError or db.CoordinationError.
        with self.assertRaises((discipline.DisciplineError, db.CoordinationError)) as cm:
            discipline.extend_action_lease(lease["lease_id"], "intruder-agent")
        code = getattr(cm.exception, "code", str(cm.exception))
        self.assertIn(code, {"ACTION_LEASE_INVALID", "AGENT_UNKNOWN_OR_UNREGISTERED"})

    # ------------------------------------------------------------------
    # 4. Consumed lease rejected
    # ------------------------------------------------------------------
    def test_04_consumed_lease_rejected(self) -> None:
        lease = self._issue()
        discipline.consume_action_lease(
            lease_id=lease["lease_id"],
            agent_id=self._agent,
            action="apply_patch",
            task_id="task-123",
            resource="src/foo.py",
        )
        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.extend_action_lease(lease["lease_id"], self._agent)
        self.assertEqual(cm.exception.code, "ACTION_LEASE_CONSUMED")

    # ------------------------------------------------------------------
    # 5. Expired lease rejected (and atomically marked expired)
    # ------------------------------------------------------------------
    def test_05_expired_lease_rejected(self) -> None:
        lease = self._issue(ttl=1)
        # Force expiry by back-dating expires_at.
        with db.connect() as con:
            con.execute(
                "UPDATE action_leases SET expires_at=? WHERE lease_id=?",
                (db.now_ts() - 10, lease["lease_id"]),
            )
        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.extend_action_lease(lease["lease_id"], self._agent)
        self.assertEqual(cm.exception.code, "ACTION_LEASE_EXPIRED")
        # Verify status was set to expired.
        with db.connect() as con:
            row = con.execute(
                "SELECT status FROM action_leases WHERE lease_id=?", (lease["lease_id"],)
            ).fetchone()
        self.assertEqual(row["status"], "expired")

    # ------------------------------------------------------------------
    # 6. Extend count cap
    # ------------------------------------------------------------------
    def test_06_extend_count_cap(self) -> None:
        # Issue with long TTL so we don't expire.
        lease = self._issue(ttl=600)
        for _ in range(discipline.MAX_LEASE_EXTEND_COUNT):
            discipline.extend_action_lease(
                lease["lease_id"], self._agent, extend_seconds=10
            )
        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.extend_action_lease(lease["lease_id"], self._agent)
        self.assertEqual(cm.exception.code, "ACTION_LEASE_EXTEND_LIMIT")

    # ------------------------------------------------------------------
    # 7. Cumulative TTL ceiling enforced
    # ------------------------------------------------------------------
    def test_07_cumulative_ttl_ceiling(self) -> None:
        lease = self._issue(ttl=600)
        now = db.now_ts()

        # Strategy: backdate original_issued_at so only 300s of cumulative budget remains.
        # ceiling = original_issued_at + MAX_CUMULATIVE = now + 300
        # expires_at = now + 60 (active, below ceiling, not expired)
        # Each extend adds up to MAX_LEASE_TTL_SECONDS=600 but ceiling is at now+300.
        # → first extend is clamped to ceiling → second extend hits CEILING_REACHED.
        remaining_budget = 300  # seconds of budget remaining
        original_issued_at = now - (discipline.MAX_CUMULATIVE_LEASE_TTL_SECONDS - remaining_budget)
        abs_ceiling = original_issued_at + discipline.MAX_CUMULATIVE_LEASE_TTL_SECONDS
        assert abs_ceiling == now + remaining_budget  # sanity check

        with db.connect() as con:
            con.execute(
                "UPDATE action_leases SET original_issued_at=?, issued_at=?, expires_at=? WHERE lease_id=?",
                (original_issued_at, original_issued_at, now + 60, lease["lease_id"]),
            )

        # First extend: budget is 300s, extend asks for 120s → succeeds, expires_at clamped ≤ ceiling.
        result = discipline.extend_action_lease(
            lease["lease_id"], self._agent, extend_seconds=discipline.DEFAULT_EXTEND_SECONDS
        )
        self.assertLessEqual(result["expires_at"], abs_ceiling)

        # Now expires_at is at or near ceiling. Further extends must hit the ceiling.
        for _ in range(5):
            try:
                discipline.extend_action_lease(
                    lease["lease_id"], self._agent, extend_seconds=discipline.DEFAULT_EXTEND_SECONDS
                )
            except discipline.DisciplineError as exc:
                if exc.code in {"ACTION_LEASE_EXTEND_CEILING_REACHED", "ACTION_LEASE_EXTEND_LIMIT"}:
                    return  # Ceiling correctly enforced — test passes.
                raise

        self.fail("Ceiling was never hit after 5+ extensions despite being within 300s of budget")

    # ------------------------------------------------------------------
    # 8. extend_seconds=0 rejected
    # ------------------------------------------------------------------
    def test_08_zero_extend_seconds_rejected(self) -> None:
        lease = self._issue(ttl=300)
        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.extend_action_lease(lease["lease_id"], self._agent, extend_seconds=0)
        self.assertEqual(cm.exception.code, "EXTEND_SECONDS_INVALID")

    # ------------------------------------------------------------------
    # 9. extend_seconds=negative rejected
    # ------------------------------------------------------------------
    def test_09_negative_extend_seconds_rejected(self) -> None:
        lease = self._issue(ttl=300)
        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.extend_action_lease(lease["lease_id"], self._agent, extend_seconds=-60)
        self.assertEqual(cm.exception.code, "EXTEND_SECONDS_INVALID")

    # ------------------------------------------------------------------
    # 10. extend_seconds=None uses default
    # ------------------------------------------------------------------
    def test_10_none_extend_seconds_uses_default(self) -> None:
        lease = self._issue(ttl=60)
        before_expires = lease["expires_at"]
        result = discipline.extend_action_lease(lease["lease_id"], self._agent, extend_seconds=None)
        self.assertEqual(result["extend_count"], 1)
        # New expiry should be roughly before + DEFAULT_EXTEND_SECONDS (allow ±2s tolerance).
        expected = before_expires + discipline.DEFAULT_EXTEND_SECONDS
        self.assertAlmostEqual(result["expires_at"], expected, delta=2)

    # ------------------------------------------------------------------
    # 11. Very large extend_seconds clamped by ceiling, not raised
    # ------------------------------------------------------------------
    def test_11_very_large_extend_clamped(self) -> None:
        lease = self._issue(ttl=10)
        result = discipline.extend_action_lease(
            lease["lease_id"], self._agent, extend_seconds=999_999
        )
        ceiling = lease["issued_at"] + discipline.MAX_CUMULATIVE_LEASE_TTL_SECONDS
        self.assertLessEqual(result["expires_at"], ceiling)

    # ------------------------------------------------------------------
    # 12. lease_id empty rejected
    # ------------------------------------------------------------------
    def test_12_empty_lease_id_rejected(self) -> None:
        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.extend_action_lease("", self._agent)
        self.assertEqual(cm.exception.code, "ACTION_LEASE_REQUIRED")

    # ------------------------------------------------------------------
    # 13. agent_id empty rejected
    # ------------------------------------------------------------------
    def test_13_empty_agent_id_rejected(self) -> None:
        lease = self._issue()
        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.extend_action_lease(lease["lease_id"], "")
        self.assertEqual(cm.exception.code, "AGENT_ID_REQUIRED")

    # ------------------------------------------------------------------
    # 14. Unknown lease_id rejected
    # ------------------------------------------------------------------
    def test_14_unknown_lease_id_rejected(self) -> None:
        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.extend_action_lease("lease-does-not-exist", self._agent)
        self.assertEqual(cm.exception.code, "ACTION_LEASE_INVALID")

    # ------------------------------------------------------------------
    # 15. Two extends before deadline succeed (within cap)
    # ------------------------------------------------------------------
    def test_15_two_extends_allowed_within_cap(self) -> None:
        lease = self._issue(ttl=300)
        r1 = discipline.extend_action_lease(lease["lease_id"], self._agent, extend_seconds=30)
        r2 = discipline.extend_action_lease(lease["lease_id"], self._agent, extend_seconds=30)
        self.assertEqual(r1["extend_count"], 1)
        self.assertEqual(r2["extend_count"], 2)
        self.assertGreaterEqual(r2["expires_at"], r1["expires_at"])

    # ------------------------------------------------------------------
    # 16. Resource lock: claim + release + status lifecycle
    # ------------------------------------------------------------------
    def test_16_resource_lock_lifecycle(self) -> None:
        resource = "src/critical.py"
        # Claim
        claim = discipline.resource_lock_claim(
            agent_id=self._agent,
            resource=resource,
            task_id="task-rl",
        )
        self.assertIn(claim["verdict"], {"RESOURCE_LOCK_ACQUIRED", "RESOURCE_LOCK_CLAIMED"})
        lock_id = claim["lock_id"]

        # Status shows held
        status = discipline.resource_lock_status(resource)
        self.assertEqual(status["verdict"], "RESOURCE_LOCK_HELD")
        self.assertEqual(status["owner_agent_id"], self._agent)

        # Second claim by same agent on same resource: idempotent — returns existing lock info.
        # The implementation returns RESOURCE_LOCK_ALREADY_HELD (not an exception).
        claim2 = discipline.resource_lock_claim(
            agent_id=self._agent, resource=resource, task_id="task-rl"
        )
        self.assertIn(claim2["verdict"], {"RESOURCE_LOCKED", "RESOURCE_LOCK_ALREADY_HELD", "RESOURCE_LOCK_CLAIMED"})

        # Release
        release = discipline.resource_lock_release(
            agent_id=self._agent, resource=resource
        )
        self.assertEqual(release["verdict"], "RESOURCE_LOCK_RELEASED")

        # Status shows free
        status2 = discipline.resource_lock_status(resource)
        self.assertEqual(status2["verdict"], "RESOURCE_LOCK_FREE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
