#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / ".agent" / "mcp"
ENTRY = MCP_DIR / "server_entry.py"
DEFAULT_REPEAT = 5
DEFAULT_TIMEOUT_SECONDS = 60


@dataclass
class Sample:
    tool_name: str
    elapsed_ms: float
    mode: str = "cold"


@dataclass
class PerfReport:
    samples: list[Sample] = field(default_factory=list)

    def add(self, tool_name: str, elapsed_ms: float, mode: str = "cold") -> None:
        if not tool_name:
            raise ValueError("tool_name is required")
        if elapsed_ms < 0:
            raise ValueError("elapsed_ms must be non-negative")
        self.samples.append(Sample(tool_name=tool_name, elapsed_ms=elapsed_ms, mode=mode))

    def by_tool(self, mode: str = "") -> dict[str, list[float]]:
        grouped: dict[str, list[float]] = {}
        for s in self.samples:
            if mode and s.mode != mode:
                continue
            grouped.setdefault(s.tool_name, []).append(s.elapsed_ms)
        return grouped

    def rows(self, mode: str = "") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for tool_name, values in sorted(self.by_tool(mode).items()):
            ordered = sorted(values)
            rows.append({
                "tool_name": tool_name,
                "count": len(ordered),
                "min_ms": ordered[0],
                "p50_ms": percentile(ordered, 50),
                "p95_ms": percentile(ordered, 95),
                "max_ms": ordered[-1],
                "total_ms": sum(ordered),
            })
        return rows

    def top_by(self, column: str, mode: str = "", limit: int = 5) -> list[dict[str, Any]]:
        all_rows = self.rows(mode)
        col_key = column if column.endswith("_ms") else f"{column}_ms"
        sorted_rows = sorted(all_rows, key=lambda r: r.get(col_key, 0), reverse=True)
        return sorted_rows[:limit]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("values are required")
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def fail(message: str) -> None:
    raise SystemExit(f"MCP_PERF_SMOKE_FAIL: {message}")


def copy_mcp_workspace() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temp = tempfile.TemporaryDirectory(prefix="agent-mcp-perf-")
    project = Path(temp.name) / "project"
    project.mkdir(parents=True)
    shutil.copytree(MCP_DIR, project / ".agent" / "mcp")
    (project / "README.md").write_text("mcp perf smoke\n", encoding="utf-8")
    perf_dir = project / "perf-fixtures"
    perf_dir.mkdir()
    for index in range(5):
        (perf_dir / f"file-{index}.txt").write_text(f"file {index}\n", encoding="utf-8")
    return temp, project


def parse_tool_output(raw_output: str, tool_name: str) -> dict[str, Any]:
    try:
        outer = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        fail(f"{tool_name} returned non-json: {raw_output!r}")
    if "content" in outer:
        try:
            return json.loads(outer["content"][0]["text"])
        except (KeyError, json.JSONDecodeError):
            return outer
    return outer


def call_tool_cold(entry: Path, cwd: Path, report: PerfReport, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(entry), "--call", tool_name, "--args", json.dumps(args, sort_keys=True)],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    output = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
    payload = parse_tool_output(output, tool_name)
    report.add(tool_name, elapsed_ms, mode="cold")
    return payload


def require_verdict(payload: dict[str, Any], expected: set[str], label: str) -> None:
    v = str(payload.get("verdict") or payload.get("status") or "")
    if v not in expected:
        fail(f"{label}: expected {sorted(expected)}, got {payload}")


def measure_cold(project: Path, repeat: int, agent_id: str, task_id: str, context_token: str) -> PerfReport:
    report = PerfReport()
    entry = project / ".agent" / "mcp" / "server_entry.py"
    for _ in range(repeat):
        require_verdict(
            call_tool_cold(entry, project, report, "session_status", {}),
            {"SESSION_STATUS"}, "session_status",
        )
        require_verdict(
            call_tool_cold(entry, project, report, "list_agents", {}),
            {"AGENTS_LISTED"}, "list_agents",
        )
        require_verdict(
            call_tool_cold(entry, project, report, "agent_status", {"agent_id": agent_id}),
            {"AGENT_STATUS"}, "agent_status",
        )
        require_verdict(
            call_tool_cold(entry, project, report, "list_tasks", {"agent_id": agent_id}),
            {"TASKS_LISTED"}, "list_tasks",
        )
        require_verdict(
            call_tool_cold(entry, project, report, "task_status", {"task_id": task_id}),
            {"TASK_STATUS"}, "task_status",
        )
        require_verdict(
            call_tool_cold(entry, project, report, "wait_for_tasks", {"task_ids": [task_id]}),
            {"TASKS_WAITING"}, "wait_for_tasks",
        )
        require_verdict(
            call_tool_cold(entry, project, report, "file_hash", {"resource": "README.md"}),
            {"FILE_HASH"}, "file_hash",
        )
        for idx in range(5):
            require_verdict(
                call_tool_cold(entry, project, report, "file_hash", {"resource": f"perf-fixtures/file-{idx}.txt"}),
                {"FILE_HASH"}, f"file_hash-{idx}",
            )
        require_verdict(
            call_tool_cold(entry, project, report, "workflow_next", {
                "agent_id": agent_id,
                "request": "measure latency",
                "intent": "read",
                "resource": "README.md",
                "task_id": task_id,
                "context_token": context_token,
                "last_verdict": "BEFORE_TASK_OK",
            }),
            {"NEXT_ACTION_REQUIRED"}, "workflow_next",
        )

        batch_resources = ["README.md"] + [f"perf-fixtures/file-{idx}.txt" for idx in range(5)]
        batch_result = call_tool_cold(entry, project, report, "batch_file_hash", {
            "resources": batch_resources,
            "max_workers": 4,
        })
        require_verdict(batch_result, {"BATCH_FILE_HASH"}, "batch_file_hash")

        snapshot_result = call_tool_cold(entry, project, report, "workflow_snapshot", {
            "agent_id": agent_id,
            "resource": "README.md",
        })
        if snapshot_result.get("verdict") not in {"WORKFLOW_SNAPSHOT", "INPUT_REQUIRED"}:
            fail(f"workflow_snapshot unexpected: {snapshot_result}")
    return report


class StdinStdioServer:
    def __init__(self, entry: Path, cwd: Path) -> None:
        self.entry = entry
        self.cwd = cwd
        self.proc: subprocess.Popen[str] | None = None
        self._lock = time.time()

    def __enter__(self) -> StdinStdioServer:
        self.proc = subprocess.Popen(
            [sys.executable, str(self.entry)],
            cwd=str(self.cwd),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return self

    def __exit__(self, *args: Any) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()

    def call(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self.proc or not self.proc.stdin:
            fail("stdio server not running")
        req = {
            "jsonrpc": "2.0",
            "id": f"perf-{int(time.time() * 1000)}",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        }
        line = json.dumps(req, ensure_ascii=False, sort_keys=True)
        started = time.perf_counter()
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        resp_line = self.proc.stdout.readline()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if not resp_line:
            fail(f"stdio server closed on {tool_name}")
        payload = parse_tool_output(resp_line.strip(), tool_name)
        if "result" in payload:
            inner = payload["result"]
            if "content" in inner:
                return json.loads(inner["content"][0]["text"])
            return inner
        if "error" in payload:
            return {"ok": False, "error": payload["error"].get("message", str(payload["error"]))}
        return payload


def measure_stdio(project: Path, repeat: int, agent_id: str, task_id: str, context_token: str) -> PerfReport:
    report = PerfReport()
    entry = project / ".agent" / "mcp" / "server_entry.py"
    with StdinStdioServer(entry, project) as server:
        for _ in range(repeat):
            for tool_name, tool_args in [
                ("session_status", {}),
                ("list_agents", {}),
                ("agent_status", {"agent_id": agent_id}),
                ("list_tasks", {"agent_id": agent_id}),
                ("task_status", {"task_id": task_id}),
                ("wait_for_tasks", {"task_ids": [task_id]}),
                ("file_hash", {"resource": "README.md"}),
                *[(f"file_hash", {"resource": f"perf-fixtures/file-{idx}.txt"}) for idx in range(5)],
                ("workflow_next", {
                    "agent_id": agent_id,
                    "request": "measure latency",
                    "intent": "read",
                    "resource": "README.md",
                    "task_id": task_id,
                    "context_token": context_token,
                    "last_verdict": "BEFORE_TASK_OK",
                }),
                ("batch_file_hash", {
                    "resources": ["README.md"] + [f"perf-fixtures/file-{idx}.txt" for idx in range(5)],
                    "max_workers": 4,
                }),
                ("workflow_snapshot", {
                    "agent_id": agent_id,
                    "resource": "README.md",
                }),
            ]:
                started = time.perf_counter()
                result = server.call(tool_name, tool_args)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                report.add(tool_name, elapsed_ms, mode="stdio")
                require_verdict(result, expected_verdicts(tool_name), tool_name)
    return report


def expected_verdicts(tool_name: str) -> set[str]:
    mapping: dict[str, set[str]] = {
        "session_status": {"SESSION_STATUS"},
        "list_agents": {"AGENTS_LISTED"},
        "agent_status": {"AGENT_STATUS"},
        "list_tasks": {"TASKS_LISTED"},
        "task_status": {"TASK_STATUS"},
        "wait_for_tasks": {"TASKS_WAITING"},
        "file_hash": {"FILE_HASH"},
        "workflow_next": {"NEXT_ACTION_REQUIRED", "INPUT_REQUIRED", "SCRIBE_CONTEXT_REQUIRED"},
        "batch_file_hash": {"BATCH_FILE_HASH"},
        "workflow_snapshot": {"WORKFLOW_SNAPSHOT", "INPUT_REQUIRED"},
    }
    return mapping.get(tool_name, {"OK"})


def run_scenario(project: Path, repeat: int) -> tuple[PerfReport, str, str, str]:
    if repeat < 1 or repeat > 100:
        fail("repeat must be between 1 and 100")
    entry = project / ".agent" / "mcp" / "server_entry.py"

    boot = call_tool_cold(entry, project, PerfReport(), "bootstrap", {
        "host_tool": "mcp-perf", "model_name": "perf", "run_legacy_bootstrap": False,
    })
    require_verdict(boot, {"BOOT_OK_MCP"}, "bootstrap")
    agent_id = str(boot["agent"]["agent_id"])

    before = call_tool_cold(entry, project, PerfReport(), "before_task", {
        "agent_id": agent_id,
        "request": "measure read-only MCP latency",
        "intent": "read",
        "resource": "README.md",
    })
    require_verdict(before, {"BEFORE_TASK_OK"}, "before_task")
    task_id = str(before["task_id"])
    context_token = str(before["context_token"])

    return agent_id, task_id, context_token


def print_table(rows: list[dict[str, Any]], label: str = "") -> None:
    if label:
        print(f"\n--- {label} ---")
    print(f"{'tool_name':<30} {'count':>5} {'min_ms':>8} {'p50_ms':>8} {'p95_ms':>8} {'max_ms':>8} {'total_ms':>10}")
    for r in rows:
        print(f"{r['tool_name']:<30} {r['count']:>5} {r['min_ms']:>8.3f} {r['p50_ms']:>8.3f} {r['p95_ms']:>8.3f} {r['max_ms']:>8.3f} {r['total_ms']:>10.3f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure MCP tool latency without mutating live project state.")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT, help="Measurement loops, 1..100.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of table.")
    parser.add_argument("--mode", choices=["cold", "stdio", "both"], default="both",
                        help="Measurement mode: cold (new process per call), stdio (persistent JSON-RPC), both.")
    args = parser.parse_args()

    temp, project = copy_mcp_workspace()
    try:
        agent_id, task_id, context_token = run_scenario(project, args.repeat)
        report = PerfReport()

        if args.mode in ("cold", "both"):
            cold_report = measure_cold(project, args.repeat, agent_id, task_id, context_token)
            report.samples.extend(cold_report.samples)

        if args.mode in ("stdio", "both"):
            stdio_report = measure_stdio(project, args.repeat, agent_id, task_id, context_token)
            report.samples.extend(stdio_report.samples)

        output: dict[str, Any] = {
            "repeat": args.repeat,
            "mode": args.mode,
        }
        if args.mode in ("cold", "both"):
            output["cold"] = report.rows(mode="cold")
        if args.mode in ("stdio", "both"):
            output["stdio"] = report.rows(mode="stdio")

        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            if args.mode in ("cold", "both"):
                print_table(output.get("cold", []), "COLD MODE (new process per call)")
            if args.mode in ("stdio", "both"):
                print_table(output.get("stdio", []), "STDIO MODE (persistent JSON-RPC)")
            if args.mode == "both":
                cold_rows = output.get("cold", [])
                stdio_rows = output.get("stdio", [])
                if cold_rows or stdio_rows:
                    print("\n--- MODE COMPARISON (top 5 by p95_ms) ---")
                    all_compared: list[dict[str, Any]] = []
                    tool_names = sorted({r["tool_name"] for r in cold_rows + stdio_rows})
                    for tn in tool_names:
                        cold_r = next((r for r in cold_rows if r["tool_name"] == tn), None)
                        stdio_r = next((r for r in stdio_rows if r["tool_name"] == tn), None)
                        all_compared.append({
                            "tool_name": tn,
                            "cold_p50": cold_r["p50_ms"] if cold_r else 0,
                            "stdio_p50": stdio_r["p50_ms"] if stdio_r else 0,
                            "cold_p95": cold_r["p95_ms"] if cold_r else 0,
                            "stdio_p95": stdio_r["p95_ms"] if stdio_r else 0,
                            "cold_total": cold_r["total_ms"] if cold_r else 0,
                            "stdio_total": stdio_r["total_ms"] if stdio_r else 0,
                        })
                    sorted_compared = sorted(all_compared, key=lambda r: r.get("cold_p95", 0), reverse=True)
                    print(f"{'tool_name':<25} {'cold_p50':>9} {'stdio_p50':>10} {'cold_p95':>9} {'stdio_p95':>10} {'cold_total':>11} {'stdio_total':>12}")
                    for r in sorted_compared[:5]:
                        print(f"{r['tool_name']:<25} {r['cold_p50']:>9.3f} {r['stdio_p50']:>10.3f} {r['cold_p95']:>9.3f} {r['stdio_p95']:>10.3f} {r['cold_total']:>11.3f} {r['stdio_total']:>12.3f}")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    finally:
        temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
