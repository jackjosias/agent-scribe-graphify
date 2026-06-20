#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".agent" / "state" / "runtime"
DAEMON = ROOT / ".agent" / "scripts" / "mcp_daemon.py"
PROXY = ROOT / ".agent" / "mcp" / "server_proxy.py"
SANDBOX = ROOT / ".agent" / "scripts" / "agent_sandbox.py"

COMPILE_TARGETS = [
    DAEMON,
    PROXY,
    SANDBOX,
    ROOT / ".agent" / "mcp" / "server_ext.py",
    ROOT / ".agent" / "mcp" / "runtime" / "delete_ops.py",
]

REQUIRED_TOOLS = {"workflow_next", "apply_patch", "delete_resource"}


def fail(message: str) -> None:
    raise SystemExit(f"SANDBOX_SMOKE_FAIL: {message}")


def compile_targets() -> None:
    for path in COMPILE_TARGETS:
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(path)], cwd=str(ROOT), text=True, capture_output=True)
        if proc.returncode != 0:
            fail(f"py_compile failed for {path}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")


def wait_socket(sock: Path, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            fail(f"daemon exited before socket was ready: {out}")
        if sock.exists():
            return
        time.sleep(0.05)
    fail(f"daemon socket not ready: {sock}")


def start_daemon(sock: Path) -> subprocess.Popen[str]:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if sock.exists():
        sock.unlink()
    proc = subprocess.Popen(
        [sys.executable, str(DAEMON), "--socket", str(sock)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    wait_socket(sock, proc)
    return proc


def stop_daemon(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def smoke_proxy_tools(sock: Path) -> None:
    env = os.environ.copy()
    env["AGENT_MCP_SOCKET"] = str(sock)
    proc = subprocess.run([sys.executable, str(PROXY), "--list-tools"], cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        fail(f"server_proxy --list-tools failed\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    missing = sorted(tool for tool in REQUIRED_TOOLS if tool not in proc.stdout)
    if missing:
        fail(f"server_proxy missing tools {missing}: {proc.stdout}")


def smoke_linux_sandbox() -> None:
    if not shutil.which("bwrap"):
        print("SANDBOX_STRICT_LINUX_SKIPPED: bubblewrap not available")
        return
    proc = subprocess.run(
        [sys.executable, str(SANDBOX), "--", sys.executable, "-c", 'print("SANDBOX_HOST_OK")'],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode == 0 and "SANDBOX_HOST_OK" in proc.stdout:
        return
    runtime_blocked = "Operation not permitted" in proc.stderr or "No permissions" in proc.stderr
    if runtime_blocked:
        print("SANDBOX_STRICT_LINUX_SKIPPED: bubblewrap present but blocked by kernel/runtime permissions")
        return
    fail(f"agent_sandbox strict smoke failed\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")


def main() -> int:
    compile_targets()
    sock = (RUNTIME / f"sandbox-smoke-{os.getpid()}.sock").resolve()
    proc = start_daemon(sock)
    try:
        smoke_proxy_tools(sock)
    finally:
        stop_daemon(proc)
    smoke_linux_sandbox()
    print("SANDBOX_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
