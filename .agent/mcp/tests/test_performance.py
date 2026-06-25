#!/usr/bin/env python3
"""Performance tests — latency, throughput, scalability, startup.

Modes tested:
  - STREAM (stdio subprocess): warm p50/p95/p99 for each tool
  - COLD (--call flag per invocation): process-spawn overhead
  - PIPELINE: end-to-end full write workflow elapsed
  - SCALABILITY: query latency vs DB row count (10, 100, 500 agents)
  - STARTUP: time-to-first-response for server_entry.py
  - DAEMON: Unix socket daemon latency (basic check)

Thresholds (conservative, local dev machine):
  - STREAM p95 < 1 000 ms for simple tools
  - COLD p95  < 3 000 ms
  - PIPELINE  < 10 000 ms
  - STARTUP   < 5 000 ms
  - DAEMON p95 < 1 000 ms
"""

from __future__ import annotations

import json
import os
import select
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
SERVER_ENTRY = str(MCP_DIR / "server_entry.py")
DAEMON_SCRIPT = str(MCP_DIR.parent / "scripts" / "mcp_daemon.py")

RESOURCE = "perf-tracked.txt"
AGENT_ID = "perf-agent"
TIMEOUT = 30
WARMUP_REPEAT = 3
MEASURE_REPEAT = 10
SCALABILITY_AGENTS = [10, 100, 500]

ReportItem = dict[str, Any]


# ── Helpers ──────────────────────────────────────────────────────────────────


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0, "mean": 0}
    return {
        "min": min(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
        "mean": statistics.mean(values),
    }


def _read_line(stream, timeout: float = TIMEOUT) -> str:
    r, _, _ = select.select([stream], [], [], timeout)
    if r:
        line = stream.readline()
        if line:
            return line.strip()
        raise RuntimeError("stream closed")
    raise TimeoutError(f"no response within {timeout}s")


class StreamClient:
    """Persistent stdio subprocess — warm mode."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = self.cwd
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_ENTRY],
            cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, text=True,
        )

    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        req = {
            "jsonrpc": "2.0", "id": f"perf-{tool}",
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        line = _read_line(self.proc.stdout)
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        result = resp.get("result", {})
        text = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(text)

    def close(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc = None


def cold_call(cwd: str, tool: str, **args: Any) -> dict[str, Any]:
    """Spawn a fresh process per call — cold mode."""
    env = os.environ.copy()
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = cwd
    proc = subprocess.run(
        [sys.executable, SERVER_ENTRY, "--call", tool, "--args",
         json.dumps(args, sort_keys=True)],
        cwd=cwd, env=env, text=True, capture_output=True, timeout=TIMEOUT,
    )
    output = proc.stdout if proc.returncode == 0 else proc.stderr
    payload = json.loads(output.strip())
    if isinstance(payload, dict) and "content" in payload:
        return json.loads(payload["content"][0]["text"])
    return payload


# ── Shared workspace setup ──────────────────────────────────────────────────


def make_workspace(with_graphify: bool = True) -> str:
    root = tempfile.mkdtemp(prefix="perf-")
    (Path(root) / ".agent" / "state").mkdir(parents=True, exist_ok=True)
    (Path(root) / ".agent" / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)
    if with_graphify:
        gdir = Path(root) / "graphify-out"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "graph.json").write_text('{"nodes":[],"edges":[]}')
        (gdir / "GRAPH_REPORT.md").write_text("# Report\n\nEmpty.\n")
        (gdir / "graph.html").write_text("<html><body></body></html>\n")
    (Path(root) / RESOURCE).write_text("line1\n")
    subprocess.run(["git", "init"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.email", "perf@test"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Perf Test"], cwd=root, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True)
    return root


def populate_agents(cwd: str, count: int) -> None:
    """Pre-register N agents so query scalability can be measured."""
    client = StreamClient(cwd)
    client.start()
    for i in range(count):
        client.call("register_agent", host_tool="perf", agent_id=f"perf-scalability-{i}")
    client.close()


def agent_creates_task(cwd: str, agent_id: str) -> dict[str, str]:
    """Register, before_task, scribe, graphify -> returns ctx."""
    client = StreamClient(cwd)
    client.start()
    client.call("register_agent", host_tool="perf", agent_id=agent_id)
    bt = client.call("before_task", agent_id=agent_id, request="perf test",
                     intent="fix", resource=RESOURCE)
    ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"],
           "agent_id": agent_id}
    client.call("scribe_query", agent_id=agent_id, task_id=ctx["task_id"],
                context_token=ctx["context_token"], query="perf", limit=3)
    client.call("graphify_query", agent_id=agent_id, task_id=ctx["task_id"],
                context_token=ctx["context_token"], query="perf", resource=RESOURCE)
    client.close()
    return ctx


def _full_pipeline(client: StreamClient, ctx: dict[str, str]) -> float:
    """Execute full write pipeline, return elapsed ms."""
    t0 = time.perf_counter()
    lc = client.call("pre_action_guard", agent_id=ctx["agent_id"], intent="write",
                     resource=RESOURCE, planned_action="claim_resource",
                     task_id=ctx["task_id"], context_token=ctx["context_token"])
    lid_c = lc["action_lease"]["lease_id"]
    claim = client.call("claim_resource", agent_id=ctx["agent_id"], resource=RESOURCE,
                        mode="patch_queue", ttl_seconds=600, action_lease_id=lid_c,
                        task_id=ctx["task_id"], context_token=ctx["context_token"])
    cid = claim["claim_id"]
    base = client.call("file_hash", resource=RESOURCE)["hash"]
    lp = client.call("pre_action_guard", agent_id=ctx["agent_id"], intent="write",
                     resource=RESOURCE, planned_action="propose_patch",
                     task_id=ctx["task_id"], context_token=ctx["context_token"])
    lid_p = lp["action_lease"]["lease_id"]
    prop = client.call("propose_patch", agent_id=ctx["agent_id"], target=RESOURCE,
                       base_hash=base,
                       diff_text="@@ -1,1 +1,1 @@\n-line1\n+line2\n",
                       action_lease_id=lid_p, task_id=ctx["task_id"],
                       context_token=ctx["context_token"])
    pid = prop["patch_id"]
    la = client.call("pre_action_guard", agent_id=ctx["agent_id"], intent="write",
                     resource=RESOURCE, planned_action="apply_patch",
                     task_id=ctx["task_id"], context_token=ctx["context_token"])
    lid_a = la["action_lease"]["lease_id"]
    client.call("apply_patch", agent_id=ctx["agent_id"], patch_id=pid,
                action_lease_id=lid_a, task_id=ctx["task_id"],
                context_token=ctx["context_token"])
    client.call("release_claim", agent_id=ctx["agent_id"], claim_id=cid)
    lf = client.call("pre_action_guard", agent_id=ctx["agent_id"], intent="write",
                     resource=RESOURCE, planned_action="finish_task",
                     task_id=ctx["task_id"], context_token=ctx["context_token"])
    lid_f = lf["action_lease"]["lease_id"]
    client.call("finish_task", agent_id=ctx["agent_id"],
                task_id=ctx["task_id"], context_token=ctx["context_token"],
                action_lease_id=lid_f)
    return ms(t0)


# ── Performance test suite ──────────────────────────────────────────────────


class TestPerfStartup(unittest.TestCase):
    """Measure server startup time — time-to-first-response."""

    def test_startup_latency(self) -> None:
        workspace = make_workspace()
        try:
            times: list[float] = []
            for _ in range(WARMUP_REPEAT + MEASURE_REPEAT):
                t0 = time.perf_counter()
                cold_call(workspace, "ping")
                times.append(ms(t0))
            measured = times[-MEASURE_REPEAT:]
            s = stats(measured)
            print(f"\n  STARTUP p50={s['p50']:.0f}ms  p95={s['p95']:.0f}ms  "
                  f"max={s['max']:.0f}ms")
            self.assertLessEqual(s["p95"], 5_000,
                                 f"Startup p95 {s['p95']:.0f}ms > 5000ms")
            self.assertLessEqual(s["max"], 8_000,
                                 f"Startup max {s['max']:.0f}ms > 8000ms")
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


class TestPerfStreamLatency(unittest.TestCase):
    """Measure warm latencies via persistent stdio subprocess."""

    workspace: str = ""
    client: StreamClient | None = None

    @classmethod
    def setUpClass(cls):
        cls.workspace = make_workspace()
        cls.client = StreamClient(cls.workspace)
        cls.client.start()
        cls.ctx = agent_creates_task(cls.workspace, AGENT_ID)

    @classmethod
    def tearDownClass(cls):
        if cls.client:
            cls.client.close()
        shutil.rmtree(cls.workspace, ignore_errors=True)

    def _measure(self, tool: str, n: int = MEASURE_REPEAT,
                 **kwargs) -> list[float]:
        times: list[float] = []
        for _ in range(n):
            t0 = time.perf_counter()
            self.client.call(tool, **kwargs)
            times.append(ms(t0))
        return times

    def _assert_ok(self, label: str, times: list[float],
                   p95_max: float = 1_000, max_max: float = 3_000) -> None:
        s = stats(times)
        print(f"\n  {label:<30s} p50={s['p50']:>7.0f}ms  "
              f"p95={s['p95']:>7.0f}ms  p99={s['p99']:>7.0f}ms  "
              f"max={s['max']:>7.0f}ms  (n={len(times)})")
        self.assertLessEqual(s["p95"], p95_max,
                             f"{label} p95 {s['p95']:.0f}ms > {p95_max}ms")
        self.assertLessEqual(s["max"], max_max,
                             f"{label} max {s['max']:.0f}ms > {max_max}ms")

    def test_01_ping(self):
        t = self._measure("ping")
        self._assert_ok("ping", t, p95_max=200, max_max=500)

    def test_02_session_status(self):
        t = self._measure("session_status")
        self._assert_ok("session_status", t, p95_max=200, max_max=500)

    def test_03_register_agent(self):
        t = self._measure("register_agent", agent_id=f"{AGENT_ID}-latency",
                          host_tool="perf")
        self._assert_ok("register_agent", t, p95_max=500, max_max=1_500)

    def test_04_agent_status(self):
        t = self._measure("agent_status", agent_id=AGENT_ID)
        self._assert_ok("agent_status", t, p95_max=200, max_max=500)

    def test_05_file_hash(self):
        t = self._measure("file_hash", resource=RESOURCE)
        self._assert_ok("file_hash", t, p95_max=200, max_max=500)

    def test_06_scribe_query(self):
        t = self._measure(
            "scribe_query", agent_id=AGENT_ID,
            task_id=self.ctx["task_id"], context_token=self.ctx["context_token"],
            query="perf latency test", limit=3,
        )
        self._assert_ok("scribe_query", t, p95_max=500, max_max=2_000)

    def test_07_pre_action_guard(self):
        t = self._measure(
            "pre_action_guard", agent_id=AGENT_ID, intent="write",
            resource=RESOURCE, planned_action="claim_resource",
            task_id=self.ctx["task_id"], context_token=self.ctx["context_token"],
        )
        self._assert_ok("pre_action_guard", t, p95_max=500, max_max=2_000)

    def test_08_list_tools(self):
        t = self._measure("tools/list", n=MEASURE_REPEAT)
        self._assert_ok("tools/list", t, p95_max=500, max_max=1_500)

    def test_09_workflow_next(self):
        t = self._measure(
            "workflow_next", agent_id=AGENT_ID, request="perf",
            intent="read", resource=RESOURCE, last_verdict="BEFORE_TASK_OK",
            task_id=self.ctx["task_id"], context_token=self.ctx["context_token"],
        )
        self._assert_ok("workflow_next", t, p95_max=500, max_max=2_000)


class TestPerfColdLatency(unittest.TestCase):
    """Measure cold-start latencies (new process per call)."""

    def test_cold_register_agent(self) -> None:
        workspace = make_workspace()
        try:
            times: list[float] = []
            for i in range(MEASURE_REPEAT):
                t0 = time.perf_counter()
                cold_call(workspace, "register_agent", host_tool="perf",
                          agent_id=f"perf-cold-{i}")
                times.append(ms(t0))
            s = stats(times)
            print(f"\n  cold_register_agent  p50={s['p50']:>7.0f}ms  "
                  f"p95={s['p95']:>7.0f}ms  max={s['max']:>7.0f}ms")
            self.assertLessEqual(s["p95"], 3_000,
                                 f"Cold p95 {s['p95']:.0f}ms > 3000ms")
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_cold_file_hash(self) -> None:
        workspace = make_workspace()
        try:
            cold_call(workspace, "register_agent", host_tool="perf",
                      agent_id="perf-cold-fh")
            times: list[float] = []
            for _ in range(MEASURE_REPEAT):
                t0 = time.perf_counter()
                cold_call(workspace, "file_hash", resource=RESOURCE)
                times.append(ms(t0))
            s = stats(times)
            print(f"\n  cold_file_hash  p50={s['p50']:>7.0f}ms  "
                  f"p95={s['p95']:>7.0f}ms  max={s['max']:>7.0f}ms")
            self.assertLessEqual(s["p95"], 3_000)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


class TestPerfPipeline(unittest.TestCase):
    """End-to-end full write workflow — fresh agent per iteration."""

    def test_full_write_pipeline_elapsed(self) -> None:
        workspace = make_workspace()
        try:
            times: list[float] = []
            for i in range(WARMUP_REPEAT + MEASURE_REPEAT):
                aid = f"perf-pipe-{i}"
                ctx = agent_creates_task(workspace, aid)
                ctx_s = {"task_id": ctx["task_id"],
                         "context_token": ctx["context_token"],
                         "agent_id": aid}
                client = StreamClient(workspace)
                client.start()
                client.call("register_agent", host_tool="perf", agent_id=aid)
                client.call("before_task", agent_id=aid, request="perf",
                            intent="fix", resource=RESOURCE)
                client.call("scribe_query", agent_id=aid,
                            task_id=ctx_s["task_id"],
                            context_token=ctx_s["context_token"],
                            query="perf", limit=3)
                client.call("graphify_query", agent_id=aid,
                            task_id=ctx_s["task_id"],
                            context_token=ctx_s["context_token"],
                            query="perf", resource=RESOURCE)
                elapsed = _full_pipeline(client, ctx_s)
                times.append(elapsed)
                client.close()
            measured = times[-MEASURE_REPEAT:]
            s = stats(measured)
            print(f"\n  full_write_pipeline  p50={s['p50']:>7.0f}ms  "
                  f"p95={s['p95']:>7.0f}ms  max={s['max']:>7.0f}ms  "
                  f"(n={len(measured)})")
            self.assertLessEqual(s["p95"], 10_000,
                                 f"Pipeline p95 {s['p95']:.0f}ms > 10000ms")
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


class TestPerfScalability(unittest.TestCase):
    """Measure query latency vs DB table size."""

    @classmethod
    def setUpClass(cls):
        cls.workspace = make_workspace()
        populate_agents(cls.workspace, max(SCALABILITY_AGENTS))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workspace, ignore_errors=True)

    def _measure_list_agents(self, label: str) -> dict[str, float]:
        client = StreamClient(self.workspace)
        client.start()
        times: list[float] = []
        for _ in range(MEASURE_REPEAT):
            t0 = time.perf_counter()
            client.call("list_agents")
            times.append(ms(t0))
        client.close()
        s = stats(times)
        print(f"\n  {label:<30s} p50={s['p50']:>7.0f}ms  "
              f"p95={s['p95']:>7.0f}ms  max={s['max']:>7.0f}ms")
        return s

    def test_list_agents_10(self):
        s = self._measure_list_agents("list_agents (10 agents)")
        self.assertLessEqual(s["p95"], 1_000)

    def test_list_agents_500(self):
        s = self._measure_list_agents("list_agents (500 agents)")
        self.assertLessEqual(s["p95"], 3_000,
                             f"list_agents(500) p95 {s['p95']:.0f}ms > 3000ms")


@unittest.skip("Daemon mode requires deployment in project root; run manually from project root with .agent/scripts/mcp_daemon.py")
class TestPerfDaemon(unittest.TestCase):
    """Unix socket daemon mode — basic latency (requires project-root)."""

    @classmethod
    def setUpClass(cls):
        cls.workspace = make_workspace()
        sock_dir = Path(cls.workspace) / ".agent" / "state" / "runtime"
        sock_dir.mkdir(parents=True, exist_ok=True)
        cls.sock_path = sock_dir / "perf-daemon.sock"
        cls.daemon_proc = subprocess.Popen(
            [sys.executable, DAEMON_SCRIPT, "--socket", str(cls.sock_path),
             "--env-file", ""],
            cwd=cls.workspace,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "AGENT_SCRIBE_GRAPHIFY_ROOT": cls.workspace},
        )
        _read_line(cls.daemon_proc.stdout)
        cls.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cls.sock.connect(str(cls.sock_path))
        cls.reader = cls.sock.makefile("r", encoding="utf-8", newline="\n")
        cls.writer = cls.sock.makefile("w", encoding="utf-8", newline="\n")

    @classmethod
    def tearDownClass(cls):
        cls.daemon_proc.terminate()
        cls.daemon_proc.wait(timeout=5)
        shutil.rmtree(cls.workspace, ignore_errors=True)

    def _daemon_call(self, tool: str, **args: Any) -> float:
        req = {
            "jsonrpc": "2.0", "id": f"dp-{tool}",
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        t0 = time.perf_counter()
        self.writer.write(json.dumps(req) + "\n")
        self.writer.flush()
        _read_line(self.reader)
        return ms(t0)

    def test_daemon_ping(self):
        times = [self._daemon_call("ping") for _ in range(MEASURE_REPEAT)]
        s = stats(times)
        print(f"\n  daemon_ping  p50={s['p50']:>7.0f}ms  "
              f"p95={s['p95']:>7.0f}ms  max={s['max']:>7.0f}ms")
        self.assertLessEqual(s["p95"], 200)

    def test_daemon_session_status(self):
        times = [self._daemon_call("session_status")
                 for _ in range(MEASURE_REPEAT)]
        s = stats(times)
        print(f"\n  daemon_session_status  p50={s['p50']:>7.0f}ms  "
              f"p95={s['p95']:>7.0f}ms  max={s['max']:>7.0f}ms")
        self.assertLessEqual(s["p95"], 500)

    def test_daemon_register_agent(self):
        times = []
        for i in range(MEASURE_REPEAT):
            times.append(self._daemon_call(
                "register_agent", host_tool="perf",
                agent_id=f"perf-daemon-{i}"))
        s = stats(times)
        print(f"\n  daemon_register_agent  p50={s['p50']:>7.0f}ms  "
              f"p95={s['p95']:>7.0f}ms  max={s['max']:>7.0f}ms")
        self.assertLessEqual(s["p95"], 1_000)


class TestPerfMemory(unittest.TestCase):
    """Check for memory leaks — RSS after repeated operations."""

    def test_no_memory_bloat(self) -> None:
        workspace = make_workspace()
        client = StreamClient(workspace)
        client.start()
        try:
            pid = client.proc.pid
            rss_before = self._rss(pid)

            for i in range(50):
                client.call("register_agent", host_tool="perf",
                            agent_id=f"perf-mem-{i}")

            rss_after = self._rss(pid)
            growth = rss_after - rss_before
            print(f"\n  RSS before={rss_before:.0f}MB  after={rss_after:.0f}MB  "
                  f"growth={growth:.0f}MB")
            self.assertLessEqual(growth, 50,
                                 f"RSS grew {growth:.0f}MB > 50MB — possible leak")
        finally:
            client.close()
            shutil.rmtree(workspace, ignore_errors=True)

    @staticmethod
    def _rss(pid: int) -> float:
        try:
            out = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            return int(out.stdout.strip()) / 1024.0
        except Exception:
            return 0.0


class TestPerfNoise(unittest.TestCase):
    """Measure variance (noise) — repeated identical calls."""

    def test_file_hash_variance(self) -> None:
        workspace = make_workspace()
        client = StreamClient(workspace)
        client.start()
        try:
            times: list[float] = []
            for _ in range(50):
                t0 = time.perf_counter()
                client.call("file_hash", resource=RESOURCE)
                times.append(ms(t0))
            s = stats(times)
            print(f"\n  file_hash x50  mean={s['mean']:.1f}ms  "
                  f"stdev={statistics.stdev(times):.1f}ms  "
                  f"p95={s['p95']:.0f}ms  p99={s['p99']:.0f}ms  "
                  f"max={s['max']:.0f}ms")
            self.assertLessEqual(s["p95"], 500,
                                 f"file_hash p95 {s['p95']:.0f}ms > 500ms")
            self.assertLessEqual(s["p99"], 2_000,
                                 f"file_hash p99 {s['p99']:.0f}ms > 2000ms — check GC/load")
        finally:
            client.close()
            shutil.rmtree(workspace, ignore_errors=True)


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
