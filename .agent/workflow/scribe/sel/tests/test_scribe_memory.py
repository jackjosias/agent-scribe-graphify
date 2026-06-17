from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from scribe_memory_fixtures import (
    write_abstract_retrieval_fixture,
    write_cold_fixture,
    write_many_hot_fixture,
    write_ranked_hot_fixture,
    write_smoke_eval_fixture,
    write_warm_pattern_audit_fixture,
)
from scribe_test_utils import BUNDLE_ROOT, load_script_module, write_fixture


scribe_memory = load_script_module("scribe_memory")
scribe_main = getattr(scribe_memory, "main")
scribe_lock = load_script_module("scribe_lock")
acquire_lock = getattr(scribe_lock, "acquire_lock")
release_lock = getattr(scribe_lock, "release_lock")

scribe_state = load_script_module("scribe_state")
state_path_for_scribe = getattr(scribe_state, "state_path_for_scribe")

scribe_index = load_script_module("scribe_index")
quick_index_version = getattr(scribe_index, "INDEX_VERSION")
quick_recommendation_policy_version = getattr(scribe_index, "RECOMMENDATION_POLICY_VERSION")

scribe_doctor_lib = load_script_module("scribe_doctor_lib")
run_doctor = getattr(scribe_doctor_lib, "run_doctor")


class ScribeMemoryCommandTests(unittest.TestCase):
    @contextlib.contextmanager
    def temporary_scribe_lock(self, root: Path):
        old_lock_path = os.environ.get("SCRIBE_LOCK_PATH")
        os.environ["SCRIBE_LOCK_PATH"] = str(root / "locks" / "scribe.lock")
        ok, message = acquire_lock("unit-test", "JOURNAL-100")
        self.assertTrue(ok, message)
        try:
            yield
        finally:
            release_lock("unit-test")
            if old_lock_path is None:
                os.environ.pop("SCRIBE_LOCK_PATH", None)
            else:
                os.environ["SCRIBE_LOCK_PATH"] = old_lock_path

    @contextlib.contextmanager
    def isolated_empty_lock_path(self, root: Path):
        old_lock_path = os.environ.get("SCRIBE_LOCK_PATH")
        os.environ["SCRIBE_LOCK_PATH"] = str(root / "locks" / "missing.lock")
        try:
            yield
        finally:
            if old_lock_path is None:
                os.environ.pop("SCRIBE_LOCK_PATH", None)
            else:
                os.environ["SCRIBE_LOCK_PATH"] = old_lock_path

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        old_argv = sys.argv[:]
        sys.argv = ["scribe", *args]
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = scribe_main()
        finally:
            sys.argv = old_argv
        return code, stdout.getvalue(), stderr.getvalue()

    def test_stats_reports_health_and_graph_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(Path(tmp))
            code, output, error = self.run_cli("stats", "--scribe", str(path))

        self.assertEqual(code, 0, error)
        self.assertIn("SCRIBE STATS", output)
        self.assertIn("doctor: 0 error(s)", output)
        self.assertIn("edges.total: 4", output)
        self.assertIn("edges.causal: 1", output)
        self.assertIn("edges.consultation: 2", output)
        self.assertIn("edges.journal: 1", output)

    def test_hot_defaults_to_short_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_many_hot_fixture(Path(tmp))
            code, output, error = self.run_cli("hot", "--scribe", str(path))

        self.assertEqual(code, 0, error)
        self.assertIn("SCRIBE HOT: 8/11", output)
        self.assertIn("PAT-106 [patterns]", output)
        self.assertNotIn("PAT-107 [patterns]", output)

    def test_hot_defaults_to_recent_signal_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_ranked_hot_fixture(Path(tmp))
            code, output, error = self.run_cli("hot", "--scribe", str(path), "--limit", "1")

        self.assertEqual(code, 0, error)
        self.assertIn("PAT-302 [patterns]", output)
        self.assertNotIn("PAT-301 [patterns]", output)

    def test_hot_topic_ranks_relevant_memory_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_ranked_hot_fixture(Path(tmp))
            code, output, error = self.run_cli("hot", "--topic", "socket abuse", "--scribe", str(path), "--limit", "1")

        self.assertEqual(code, 0, error)
        self.assertIn("topic=socket abuse", output)
        self.assertIn("PAT-301 [patterns]", output)
        self.assertNotIn("PAT-302 [patterns]", output)

    def test_query_matches_abstract_friction_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_abstract_retrieval_fixture(Path(tmp))
            code, output, error = self.run_cli("query", "perte de temps agentique sur petites taches", "--scribe", str(path), "--limit", "1")

        self.assertEqual(code, 0, error)
        self.assertIn("PAT-401 [patterns]", output)

    def test_review_hot_reports_pressure_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_many_hot_fixture(Path(tmp))
            before = path.read_text(encoding="utf-8")
            code, output, error = self.run_cli("review-hot", "--scribe", str(path), "--target", "4")
            after = path.read_text(encoding="utf-8")

        self.assertEqual(code, 0, error)
        self.assertIn("SCRIBE HOT REVIEW", output)
        self.assertIn("demote_to_warm:", output)
        self.assertIn("verdict: pressure detected", output)
        self.assertEqual(before, after)

    def test_review_hot_apply_demotes_overflow_to_warm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_many_hot_fixture(root)
            with self.temporary_scribe_lock(root):
                code, output, error = self.run_cli("review-hot", "--scribe", str(path), "--target", "4", "--apply")
            updated = path.read_text(encoding="utf-8")

        self.assertEqual(code, 0, error)
        self.assertIn("verdict: review-hot applied", output)
        self.assertIn('    - "PAT-108"', updated)
        self.assertIn('    tier: "warm"\n    status: "ACTIVE"\n    titre: "Hot context PAT-108"', updated)

    def test_review_hot_apply_moves_overflow_to_cold_after_warm_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_many_hot_fixture(root)
            with self.temporary_scribe_lock(root):
                code, output, error = self.run_cli(
                    "review-hot",
                    "--scribe",
                    str(path),
                    "--target",
                    "4",
                    "--warm-overflow",
                    "2",
                    "--apply",
                )
            updated = path.read_text(encoding="utf-8")

        self.assertEqual(code, 0, error)
        self.assertIn("demote_to_cold:", output)
        self.assertIn('    tier: "cold"\n    status: "ACTIVE"\n    titre: "Hot context PAT-108"', updated)

    def test_context_quick_is_compact_and_skips_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_many_hot_fixture(Path(tmp))
            code, output, error = self.run_cli("context", "--mode", "quick", "--topic", "recherche causla", "--scribe", str(path))

        self.assertEqual(code, 0, error)
        self.assertIn("SCRIBE CONTEXT [quick]", output)
        self.assertIn("doctor: skipped for quick mode", output)
        self.assertIn("hot_by_topic: 5/11", output)
        self.assertIn("topic: recherche causla", output)

    def test_context_quick_creates_and_reuses_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_many_hot_fixture(root)
            index_path = root / "scribe-out" / "scribe-index.json"
            first_code, first_output, first_error = self.run_cli("context", "--mode", "quick", "--scribe", str(path))
            first_mtime = index_path.stat().st_mtime_ns
            second_code, second_output, second_error = self.run_cli("context", "--mode", "quick", "--scribe", str(path))
            second_mtime = index_path.stat().st_mtime_ns

        self.assertEqual(first_code, 0, first_error)
        self.assertEqual(second_code, 0, second_error)
        self.assertIn("(rebuilt)", first_output)
        self.assertIn("(fresh)", second_output)
        self.assertEqual(first_mtime, second_mtime)

    def test_context_quick_uses_fresh_index_without_yaml_parse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "broken.scribe"
            path.write_text("schema_version: [", encoding="utf-8")
            source = path.read_bytes()
            index_path = root / "scribe-out" / "scribe-index.json"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(
                json.dumps(
                    {
                        "version": quick_index_version,
                        "recommendation_policy_version": quick_recommendation_policy_version,
                        "source": str(path),
                        "source_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
                        "source_mtime_ns": path.stat().st_mtime_ns,
                        "source_line_count": 1,
                        "hot_entities": [
                            {
                                "id": "PAT-INDEX",
                                "collection": "patterns",
                                "path": "patterns[0]",
                                "position": 0,
                                "tier": "hot",
                                "status": "ACTIVE",
                                "title": "Indexed hot memory",
                                "abstract": "Served from the quick index.",
                                "date": "2026-05-24",
                                "source_number": 1,
                                "links": [],
                                "offset": {"line": 1, "char": 0},
                                "search_text": "indexed quick path",
                            }
                        ],
                        "active_debts": [],
                        "id_to_offset": {},
                    }
                ),
                encoding="utf-8",
            )
            code, output, error = self.run_cli("context", "--mode", "quick", "--scribe", str(path))

        self.assertEqual(code, 0, error)
        self.assertIn("(fresh)", output)
        self.assertIn("PAT-INDEX [patterns]", output)

    def test_context_standard_includes_doctor_and_debts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(Path(tmp))
            code, output, error = self.run_cli("context", "--mode", "standard", "--topic", "limiter", "--scribe", str(path))

        self.assertEqual(code, 0, error)
        self.assertIn("SCRIBE CONTEXT [standard]", output)
        self.assertIn("doctor: 0 error(s)", output)
        self.assertIn("active_debts: 1/1", output)
        self.assertIn("DEBT-100 [debts]", output)

    def test_context_json_is_agent_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(Path(tmp))
            code, output, error = self.run_cli(
                "context",
                "--mode",
                "quick",
                "--topic",
                "portable bundle",
                "--format",
                "json",
                "--scribe",
                str(path),
            )

        self.assertEqual(code, 0, error)
        payload = json.loads(output)
        self.assertEqual(payload["mode"], "quick")
        self.assertFalse(payload["doctor"]["included"])
        self.assertEqual(payload["hot_label"], "hot_by_topic")
        self.assertIn("VAC-100", {entity["id"] for entity in payload["hot"]})

    def test_query_uses_local_scribe_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(Path(tmp))
            code, output, error = self.run_cli("query", "portable bundle", "--scribe", str(path), "--limit", "2")

        self.assertEqual(code, 0, error)
        self.assertIn("SCRIBE QUERY: portable bundle", output)
        self.assertIn("index:", output)
        self.assertIn("VAC-100 [vaccins]", output)

    def test_query_uses_fresh_complete_index_without_yaml_parse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "broken.scribe"
            path.write_text("schema_version: [", encoding="utf-8")
            source = path.read_bytes()
            index_path = root / "scribe-out" / "scribe-index.json"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(
                json.dumps(
                    {
                        "version": quick_index_version,
                        "recommendation_policy_version": quick_recommendation_policy_version,
                        "complete": True,
                        "source": str(path),
                        "source_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
                        "source_mtime_ns": path.stat().st_mtime_ns,
                        "source_line_count": 1,
                        "summary": {"entities": 1, "ids": 1, "doctor_errors": 0, "doctor_warnings": 0, "edges": {"total": 0}, "index_version": quick_index_version, "index_complete": True},
                        "entities": [
                            {
                                "id": "PAT-INDEX",
                                "collection": "patterns",
                                "path": "patterns[0]",
                                "position": 0,
                                "tier": "warm",
                                "status": "ACTIVE",
                                "title": "Indexed complete memory",
                                "abstract": "Served from the complete index.",
                                "search_text": "portable bundle indexed complete memory",
                            }
                        ],
                        "hot_entities": [],
                        "active_debts": [],
                        "id_to_offset": {},
                    }
                ),
                encoding="utf-8",
            )
            code, output, error = self.run_cli("query", "portable bundle", "--scribe", str(path), "--limit", "2")

        self.assertEqual(code, 0, error)
        self.assertIn("(fresh)", output)
        self.assertIn("PAT-INDEX [patterns]", output)

    def test_doctor_warns_for_stale_warm_pattern_without_scar_or_ghost_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_warm_pattern_audit_fixture(root)
            report_path = root / "doctor.md"
            code = run_doctor(path, report_path, suggest_fix=True)
            report = report_path.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn("W014", report)
        self.assertIn("PAT-250", report)

    def test_challenge_warns_on_relevant_high_debt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(Path(tmp))
            code, output, error = self.run_cli(
                "challenge",
                "scale process-local limiter before using redis",
                "--scribe",
                str(path),
                "--limit",
                "3",
            )

        self.assertEqual(code, 0, error)
        self.assertIn("verdict: WARN", output)
        self.assertIn("DEBT-100 [debts]", output)

    def test_eval_scores_query_hot_and_context_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(Path(tmp))
            code, output, error = self.run_cli(
                "eval",
                "--query",
                "portable bundle",
                "--expect",
                "VAC-100",
                "--format",
                "json",
                "--scribe",
                str(path),
            )

        self.assertEqual(code, 0, error)
        payload = json.loads(output)
        self.assertEqual(payload["summary"]["failed_cases"], 0)
        self.assertEqual(payload["summary"]["by_surface"]["query"]["passed"], 1)
        self.assertIn("VAC-100", payload["cases"][0]["surfaces"]["context"]["retrieved_ids"])

    def test_eval_smoke_uses_fixed_real_cases_on_query_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_smoke_eval_fixture(Path(tmp))
            code, output, error = self.run_cli("eval", "--smoke", "--format", "json", "--scribe", str(path))

        self.assertEqual(code, 0, error)
        payload = json.loads(output)
        self.assertEqual(payload["surfaces"], ["query"])
        self.assertEqual(payload["summary"]["cases"], 4)
        self.assertEqual(payload["summary"]["failed_cases"], 0)

    def test_export_outputs_deterministic_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(Path(tmp))
            code, output, error = self.run_cli("export", "--scribe", str(path), "--format", "json")

        self.assertEqual(code, 0, error)
        payload = json.loads(output)
        self.assertEqual(payload["summary"]["entities"], 4)
        self.assertEqual(payload["tiers"]["hot"], ["VAC-100", "PAT-100"])
        self.assertIn("retrieval_quality", payload)
        self.assertIn("VAC-100", {entity["id"] for entity in payload["entities"]})

    def test_promote_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_fixture(Path(tmp))
            before = path.read_text(encoding="utf-8")
            code, output, error = self.run_cli(
                "promote",
                "DEBT-100",
                "--tier",
                "hot",
                "--scribe",
                str(path),
                "--dry-run",
            )
            after = path.read_text(encoding="utf-8")

        self.assertEqual(code, 0, error)
        self.assertIn("dry-run: True", output)
        self.assertEqual(before, after)

    def test_promote_applies_targeted_tier_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_fixture(root)
            with self.temporary_scribe_lock(root):
                code, output, error = self.run_cli("promote", "DEBT-100", "--tier", "hot", "--scribe", str(path))
            updated = path.read_text(encoding="utf-8")
            state = json.loads(state_path_for_scribe(path).read_text(encoding="utf-8"))

        self.assertEqual(code, 0, error)
        self.assertIn("verdict: promote applied", output)
        self.assertIn('    - "DEBT-100"', updated)
        self.assertIn('    tier: "hot"\n    status: "ACTIVE"\n    severite: "HIGH"', updated)
        self.assertEqual(state["write_kind"], "tier_rebalance")
        self.assertEqual(state["changed_ids"], ["DEBT-100", "tiers"])

    def test_promote_apply_refuses_without_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_empty_lock_path(Path(tmp)):
            path = write_fixture(Path(tmp))
            before = path.read_text(encoding="utf-8")
            code, output, error = self.run_cli("promote", "DEBT-100", "--tier", "hot", "--scribe", str(path))
            after = path.read_text(encoding="utf-8")

        self.assertEqual(code, 2, output)
        self.assertIn("mutation refused", error)
        self.assertEqual(before, after)

    def test_compact_apply_removes_duplicate_and_orphan_tier_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_fixture(root)
            messy = path.read_text(encoding="utf-8").replace(
                '  hot: ["VAC-100", "PAT-100"]',
                '  hot: ["VAC-100", "VAC-100", "MISSING", "PAT-100"]',
            )
            path.write_text(messy, encoding="utf-8")
            with self.temporary_scribe_lock(root):
                code, output, error = self.run_cli("compact", "--scribe", str(path), "--apply")
            updated = path.read_text(encoding="utf-8")

        self.assertEqual(code, 0, error)
        self.assertIn("duplicate refs removed: 1", output)
        self.assertIn("orphan refs removed: 1", output)
        self.assertIn("verdict: compact applied", output)
        self.assertNotIn("MISSING", updated)
        self.assertEqual(updated.count('"VAC-100"'), 3)

    def test_archive_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_cold_fixture(root)
            archive_path = root / "archive.yaml"
            before = path.read_text(encoding="utf-8")
            code, output, error = self.run_cli("archive", "--scribe", str(path), "--output", str(archive_path))
            after = path.read_text(encoding="utf-8")

        self.assertEqual(code, 0, error)
        self.assertIn("candidates: 1", output)
        self.assertIn("verdict: dry-run", output)
        self.assertEqual(before, after)
        self.assertFalse(archive_path.exists())

    def test_archive_apply_writes_archive_and_prunes_active_scribe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_cold_fixture(root)
            archive_path = root / "archive.yaml"
            with self.temporary_scribe_lock(root):
                code, output, error = self.run_cli("archive", "--scribe", str(path), "--output", str(archive_path), "--apply")
            active = path.read_text(encoding="utf-8")
            archived = archive_path.read_text(encoding="utf-8")

        self.assertEqual(code, 0, error)
        self.assertIn("verdict: archived 1 entrie(s)", output)
        self.assertNotIn("PAT-200", active)
        self.assertIn('schema_version: "TENOR_SCRIBE_ARCHIVE_v1"', archived)
        self.assertIn('id: "PAT-200"', archived)

    def test_dashboard_writes_static_html_and_json_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_fixture(root)
            html_path = root / "dashboard.html"
            data_path = root / "dashboard.json"
            code, output, error = self.run_cli(
                "dashboard",
                "--scribe",
                str(path),
                "--output",
                str(html_path),
                "--data-output",
                str(data_path),
            )
            html = html_path.read_text(encoding="utf-8")
            data = json.loads(data_path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0, error)
        self.assertIn("SCRIBE DASHBOARD", output)
        self.assertIn("Tableau de bord SCRIBE", html)
        self.assertIn("Explorateur mémoire", html)
        self.assertIn("Réinitialiser", html)
        self.assertIn('<html lang="fr" data-theme="dark">', html)
        self.assertIn('data-theme-option="dark" aria-pressed="true"', html)
        self.assertIn('data-theme-option="light" aria-pressed="false"', html)
        self.assertIn("scribe-dashboard-theme", html)
        self.assertIn('data-filter="query"', html)
        self.assertIn("data-entity-card", html)
        self.assertIn('data-particle-field', html)
        self.assertIn("initParticleField", html)
        self.assertIn('data-chart="edges"', html)
        self.assertIn('data-chart="tiers"', html)
        self.assertIn('data-chart="collections"', html)
        self.assertIn('data-chart="risk"', html)
        self.assertIn('data-chart="statuses"', html)
        self.assertIn('data-chart="quality"', html)
        self.assertIn("echarts.init", html)
        self.assertIn("Actions recommandées", html)
        self.assertIn("Qualité", html)
        self.assertIn("VAC-100", html)
        self.assertNotIn("https://", html)
        self.assertEqual(data["summary"]["entities"], 4)
        self.assertTrue(data["summary"]["index_complete"])
        self.assertIn("retrieval_quality", data)
        self.assertIn("recommendations", data)
        self.assertIn("source_sha256", data)
        self.assertNotIn("/api/scribe-state", html)

    def test_dashboard_live_reload_script_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_fixture(root)
            dashboard_module = load_script_module("scribe_memory_dashboard")
            render_dashboard = getattr(dashboard_module, "render_dashboard")
            scribe_file_state = getattr(dashboard_module, "scribe_file_state")
            payload = {
                "source": str(path),
                "schema_version": "test",
                "summary": {"entities": 0, "doctor_errors": 0, "doctor_warnings": 0, "edges": {}},
                "tiers": {},
                "entities": [],
            }
            payload.update(scribe_file_state(path))
            static_html = render_dashboard(payload)
            live_html = render_dashboard(payload, live_poll_interval_ms=500)

        self.assertNotIn("/api/scribe-state", static_html)
        self.assertNotIn("/api/scribe-events", static_html)
        self.assertIn("/api/scribe-events", live_html)
        self.assertIn("/api/scribe-state", live_html)
        self.assertIn("EventSource", live_html)
        self.assertIn("pollIntervalMs = 1000", live_html)
        self.assertIn("fallbackIntervalMs = Math.max(30000, pollIntervalMs * 10)", live_html)
        self.assertIn(payload["source_sha256"], live_html)

    def test_worktree_classifies_generated_noise(self) -> None:
        worktree = load_script_module("scribe_worktree")
        item = getattr(worktree, "StatusItem")
        classify = getattr(worktree, "classify")
        tracked, source, generated, other = classify([
            item(" M", "README.md"),
            item("??", "scribe-out/report.md"),
            item("??", ".agent/workflow/scribe/sel/scripts/tool.py"),
            item("??", ".agent/workflow/scribe/sel/templates/root.graphifyignore"),
            item("??", ".agent/workflow/scribe/sel/docs/archive/scribe.v3.1.md.old"),
        ])

        self.assertEqual([entry.path for entry in tracked], ["README.md"])
        self.assertEqual(
            [entry.path for entry in source],
            [
                ".agent/workflow/scribe/sel/scripts/tool.py",
                ".agent/workflow/scribe/sel/templates/root.graphifyignore",
                ".agent/workflow/scribe/sel/docs/archive/scribe.v3.1.md.old",
            ],
        )
        self.assertEqual([entry.path for entry in generated], ["scribe-out/report.md"])
        self.assertEqual(other, [])

    def test_worktree_detects_surface_violations(self) -> None:
        worktree = load_script_module("scribe_worktree")
        item = getattr(worktree, "StatusItem")
        check_surface_violations = getattr(worktree, "check_surface_violations")

        violations = check_surface_violations(
            "auth",
            "agent-a",
            [
                item(" M", "src/auth/login.ts"),
                item(" M", "src/websocket/server.ts"),
                item(" M", "frontend/src/App.tsx"),
                item("??", "scribe-out/report.md"),
            ],
        )

        self.assertEqual(
            violations,
            [
                {"file": "src/websocket/server.ts", "belongs_to": "websocket", "claimed_by": "agent-a"},
                {"file": "frontend/src/App.tsx", "belongs_to": "frontend", "claimed_by": "agent-a"},
            ],
        )

    def test_worktree_allows_claimed_surface_changes(self) -> None:
        worktree = load_script_module("scribe_worktree")
        item = getattr(worktree, "StatusItem")
        check_surface_violations = getattr(worktree, "check_surface_violations")

        violations = check_surface_violations(
            "tests",
            "agent-tests",
            [
                item(" M", "backend/tests/auth.test.ts"),
                item("??", "src/session.spec.ts"),
            ],
        )

        self.assertEqual(violations, [])

    def test_bundle_identity_matches_adapter_paths(self) -> None:
        install = load_script_module("scribe_install")
        templates = load_script_module("scribe_install_templates")
        bundle_graph = load_script_module("scribe_bundle_graph")

        expected_name = "scribe"
        expected_path = f".agent/workflow/{expected_name}"
        adapter = getattr(templates, "render_scribe_adapter")()
        shim = getattr(templates, "render_shim_helper")()
        agents_block = getattr(templates, "render_agents_block")()
        graph_dir = getattr(bundle_graph, "BUNDLE_GRAPH_DIR")

        self.assertEqual(BUNDLE_ROOT.name, expected_name)
        self.assertTrue((BUNDLE_ROOT / "scribe").is_file())
        self.assertTrue((BUNDLE_ROOT / "scribe-rag").is_file())
        self.assertTrue((BUNDLE_ROOT / "sel" / "scripts").is_dir())
        self.assertTrue((BUNDLE_ROOT / "rag" / "scripts").is_dir())
        self.assertTrue((BUNDLE_ROOT / "sel" / "docs" / "multi-agent-installation.md").is_file())
        self.assertTrue((BUNDLE_ROOT / "sel" / "adapters" / "README.md").is_file())
        self.assertFalse((BUNDLE_ROOT / "sel" / "adapters" / "root" / "scripts").exists())
        self.assertEqual(str(getattr(install, "BUNDLE_RELATIVE_PATH")), expected_path)
        self.assertIn("multi-agent-installation.md", agents_block)
        self.assertIn("Default commit/push scope is the host product source", agents_block)
        self.assertIn("keep `graphify-out/` and `scribe-out/` out of commits", agents_block)
        self.assertIn(".agent/rules/scribe.md", agents_block)
        self.assertIn("docs/scribe.md", getattr(templates, "render_scribe_rule")())
        self.assertIn(expected_name, adapter)
        self.assertIn(expected_name, shim)
        self.assertIn("scribe-out", graph_dir.parts)
        self.assertIn("bundle-graph", graph_dir.parts)
        self.assertEqual(graph_dir.name, expected_name)
        self.assertNotIn("scribe-engineering-rag", adapter + shim)
        self.assertNotIn("scribe-engineering-local-causal-retrieval", adapter + shim)

    def test_install_is_rootless_by_default(self) -> None:
        install = load_script_module("scribe_install")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "host-project"
            installer = install.Installer(
                BUNDLE_ROOT,
                target,
                force=False,
                dry_run=True,
                with_root_adapters=False,
            )
            installer.plan()

        self.assertEqual(installer.conflicts, [])
        self.assertNotIn("write scribe", installer.actions)
        self.assertFalse(any(action.startswith("write scripts/") for action in installer.actions))
        self.assertTrue(any(action.startswith("copy scribe ") for action in installer.actions))

    def test_install_generates_root_adapters_only_when_requested(self) -> None:
        install = load_script_module("scribe_install")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "host-project"
            installer = install.Installer(
                BUNDLE_ROOT,
                target,
                force=False,
                dry_run=True,
                with_root_adapters=True,
            )
            installer.plan()

        self.assertEqual(installer.conflicts, [])
        self.assertIn("write scribe", installer.actions)
        self.assertTrue(any(action.startswith("write scripts/") for action in installer.actions))


if __name__ == "__main__":
    unittest.main()
