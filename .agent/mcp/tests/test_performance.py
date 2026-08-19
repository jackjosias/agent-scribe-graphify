#!/usr/bin/env python3
"""Measured latency, concurrency, resource and startup regressions for public MCP tools."""

from __future__ import annotations

import json
import gc
import os
import select
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
ROOT = MCP_DIR.parents[1]
SERVER_ENTRY = str(MCP_DIR / "server_entry.py")
RESOURCE = "perf-tracked.txt"
TIMEOUT_SECONDS = 30
MEASURE_REPEAT = 20

if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_readiness, installation_state, patch_queue, tenor_jobs
from _strict_cleanup import remove_tree_strict


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def measurements(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
        "mean": statistics.mean(values),
    }


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def read_line(stream: Any, timeout: float = TIMEOUT_SECONDS) -> str:
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        raise TimeoutError(f"no MCP response within {timeout}s")
    line = stream.readline()
    if not line:
        raise RuntimeError("MCP stream closed")
    return line.strip()


def process_env(workspace: str) -> dict[str, str]:
    return {
        **os.environ,
        "AGENT_SCRIBE_GRAPHIFY_ROOT": workspace,
        graphify_readiness.FIXTURE_ENV: "1",
    }


class StreamClient:
    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.process: subprocess.Popen[str] | None = None
        self.sequence = 0

    def start(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, SERVER_ENTRY],
            cwd=self.cwd,
            env=process_env(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("MCP client is not started")
        self.sequence += 1
        if tool == "tools/list":
            request = {
                "jsonrpc": "2.0",
                "id": f"perf-{self.sequence}",
                "method": "tools/list",
                "params": {},
            }
        else:
            request = {
                "jsonrpc": "2.0",
                "id": f"perf-{self.sequence}",
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        self.process.stdin.write(json.dumps(request, sort_keys=True) + "\n")
        self.process.stdin.flush()
        try:
            response = json.loads(read_line(self.process.stdout))
        except Exception as exc:
            returncode = self.process.poll()
            diagnostic = ""
            if returncode is not None and self.process.stderr is not None:
                diagnostic = self.process.stderr.read()[-4000:]
            raise RuntimeError(
                f"MCP call failed: tool={tool} returncode={returncode} stderr={diagnostic!r}"
            ) from exc
        if "error" in response:
            raise RuntimeError(f"MCP error for {tool}: {response['error']}")
        result = response.get("result", {})
        if tool == "tools/list":
            return result
        blocks = result.get("content") or []
        if not blocks or "text" not in blocks[0]:
            raise RuntimeError(f"MCP response has no text payload: {response}")
        return json.loads(blocks[0]["text"])

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def cold_call(workspace: str, tool: str, **arguments: Any) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            SERVER_ENTRY,
            "--call",
            tool,
            "--args",
            json.dumps(arguments, sort_keys=True),
        ],
        cwd=workspace,
        env=process_env(workspace),
        text=True,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"cold MCP call failed: tool={tool} returncode={completed.returncode} "
            f"stderr={completed.stderr[-4000:]!r}"
        )
    payload = json.loads(completed.stdout.strip())
    if "content" in payload:
        return json.loads(payload["content"][0]["text"])
    return payload


def make_workspace() -> str:
    root = Path(tempfile.mkdtemp(prefix="perf-mcp-"))
    agent = root / ".agent"
    agent.mkdir()
    target = agent / "mcp"
    try:
        target.symlink_to(MCP_DIR, target_is_directory=True)
    except OSError:
        shutil.copytree(MCP_DIR, target)
    (root / RESOURCE).write_text("line1\n", encoding="utf-8")
    (root / "README.md").write_text("performance fixture\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "perf@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "MCP Performance"], cwd=root, check=True)
    subprocess.run(["git", "add", RESOURCE, "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, capture_output=True, check=True)
    prepared = installation_state.ensure_fresh_installation_state(root)
    if not prepared.get("ok"):
        raise RuntimeError(f"installation fixture failed: {prepared}")
    finalized = installation_state.finalize_installation_state(root)
    if not finalized.get("ok"):
        raise RuntimeError(f"installation fixture finalization failed: {finalized}")
    ready = graphify_readiness.write_smoke_fixture(root)
    if not ready.get("ok"):
        raise RuntimeError(f"Graphify fixture failed: {ready}")
    return str(root)


def remove_workspace(workspace: str) -> None:
    remove_tree_strict(workspace)


class PerformanceAssertions(unittest.TestCase):
    def assert_latency(
        self,
        label: str,
        values: list[float],
        *,
        p95_limit: float,
        max_limit: float,
    ) -> None:
        result = measurements(values)
        print(
            f"\n  {label:<32} p50={result['p50']:7.1f}ms "
            f"p95={result['p95']:7.1f}ms p99={result['p99']:7.1f}ms "
            f"max={result['max']:7.1f}ms n={len(values)}"
        )
        self.assertLessEqual(result["p95"], p95_limit, result)
        self.assertLessEqual(result["max"], max_limit, result)


class TestPublicWarmLatency(PerformanceAssertions):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = make_workspace()
        cls.client = StreamClient(cls.workspace)
        cls.client.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        remove_workspace(cls.workspace)

    def measure(self, tool: str, **arguments: Any) -> list[float]:
        payload = self.client.call(tool, **arguments)
        self.assertIsInstance(payload, dict)
        durations: list[float] = []
        for _ in range(MEASURE_REPEAT):
            started = time.perf_counter()
            payload = self.client.call(tool, **arguments)
            durations.append(elapsed_ms(started))
            self.assertIsInstance(payload, dict)
        return durations

    def test_file_hash_latency(self) -> None:
        durations = self.measure("file_hash", resource=RESOURCE)
        self.assert_latency("warm file_hash", durations, p95_limit=250, max_limit=750)

    def test_graphify_check_latency(self) -> None:
        durations = self.measure("graphify_required_check", workspace_root=self.workspace)
        self.assert_latency("warm graphify_required_check", durations, p95_limit=500, max_limit=1500)

    def test_graphify_ready_build_latency(self) -> None:
        durations = self.measure("graphify_project_build", timeout_seconds=180)
        self.assert_latency("warm graphify_project_build", durations, p95_limit=500, max_limit=1500)

    def test_portability_latency(self) -> None:
        durations = self.measure("portability_check", workspace_root=self.workspace)
        self.assert_latency("warm portability_check", durations, p95_limit=750, max_limit=2000)

    def test_tools_list_is_public_contract(self) -> None:
        result = self.client.call("tools/list")
        names = [item["name"] for item in result["tools"]]
        self.assertEqual(len(names), 9, names)
        self.assertIn("tenor_apply_changeset", names)
        self.assertIn("graphify_project_build", names)


class TestColdStartupLatency(PerformanceAssertions):
    def test_cold_public_calls_are_real_successes(self) -> None:
        workspace = make_workspace()
        try:
            durations: list[float] = []
            for _ in range(MEASURE_REPEAT):
                started = time.perf_counter()
                payload = cold_call(workspace, "file_hash", resource=RESOURCE)
                durations.append(elapsed_ms(started))
                self.assertEqual(payload.get("verdict"), "FILE_HASH", payload)
                self.assertTrue(payload.get("ok"), payload)
            self.assert_latency("cold file_hash", durations, p95_limit=3000, max_limit=5000)
        finally:
            remove_workspace(workspace)


class TestConcurrentPublicCalls(PerformanceAssertions):
    def test_64_parallel_cold_clients_share_one_workspace(self) -> None:
        workspace = make_workspace()
        try:
            def one_call(_: int) -> tuple[float, dict[str, Any]]:
                started = time.perf_counter()
                payload = cold_call(workspace, "file_hash", resource=RESOURCE)
                return elapsed_ms(started), payload

            with ThreadPoolExecutor(max_workers=16) as executor:
                results = list(executor.map(one_call, range(64)))
            durations = [duration for duration, _ in results]
            for _, payload in results:
                self.assertEqual(payload.get("verdict"), "FILE_HASH", payload)
                self.assertTrue(payload.get("ok"), payload)
            self.assert_latency("64 parallel cold file_hash", durations, p95_limit=8000, max_limit=12000)
        finally:
            remove_workspace(workspace)


class TestJobQueueLatency(PerformanceAssertions):
    def test_500_durable_job_submissions_remain_bounded(self) -> None:
        workspace = make_workspace()
        root = Path(workspace)
        try:
            durations: list[float] = []
            for index in range(500):
                started = time.perf_counter()
                accepted = tenor_jobs.submit_job(
                    root,
                    kind="changeset",
                    agent_id="perf-agent",
                    task_id=f"perf-task-{index}",
                    request_id=f"perf-request-{index}",
                    payload={"task_id": f"perf-task-{index}", "changes": [], "validators": []},
                    max_runtime_seconds=60,
                    auto_launch=False,
                )
                durations.append(elapsed_ms(started))
                self.assertTrue(accepted.get("ok"), accepted)
            self.assert_latency("500 durable job submissions", durations, p95_limit=100, max_limit=500)
            snapshot = tenor_jobs.job_snapshot(root, limit=500)
            self.assertEqual(snapshot["count"], 500)
            self.assertEqual(snapshot["active"], 500)
        finally:
            remove_workspace(workspace)


class TestLongLivedProcessResources(unittest.TestCase):
    def test_1000_warm_calls_do_not_leak_unbounded_rss(self) -> None:
        workspace = make_workspace()
        client = StreamClient(workspace)
        client.start()
        try:
            assert client.process is not None
            warm = client.call("file_hash", resource=RESOURCE)
            self.assertEqual(warm.get("verdict"), "FILE_HASH", warm)
            before = self.rss_megabytes(client.process.pid)
            if before <= 0.0:
                old_cwd = Path.cwd()
                os.chdir(workspace)
                try:
                    tracemalloc.start()
                    gc.collect()
                    baseline, _ = tracemalloc.get_traced_memory()
                    for _ in range(1000):
                        patch_queue.file_hash(RESOURCE)
                    gc.collect()
                    current, peak = tracemalloc.get_traced_memory()
                    growth = (current - baseline) / (1024 * 1024)
                    print(
                        f"\n  1000 in-process file hashes retained={growth:.2f}MB "
                        f"peak={peak / (1024 * 1024):.2f}MB (RSS unavailable)"
                    )
                    self.assertLessEqual(growth, 10.0)
                finally:
                    tracemalloc.stop()
                    os.chdir(old_cwd)
                return
            for _ in range(1000):
                payload = client.call("file_hash", resource=RESOURCE)
                self.assertEqual(payload.get("verdict"), "FILE_HASH", payload)
            after = self.rss_megabytes(client.process.pid)
            growth = after - before
            print(f"\n  1000 warm calls RSS before={before:.1f}MB after={after:.1f}MB growth={growth:.1f}MB")
            self.assertLessEqual(growth, 30.0)
        finally:
            client.close()
            remove_workspace(workspace)

    @staticmethod
    def rss_megabytes(pid: int) -> float:
        status = Path(f"/proc/{pid}/status")
        if status.is_file():
            for line in status.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        value = completed.stdout.strip()
        return int(value) / 1024.0 if value else 0.0


if __name__ == "__main__":
    unittest.main(verbosity=2)
