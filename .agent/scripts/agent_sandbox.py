#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / ".agent"
RUNTIME = AGENT / "state" / "runtime"
DAEMON = AGENT / "scripts" / "mcp_daemon.py"
PROXY = AGENT / "mcp" / "server_proxy.py"


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def die(message: str, code: int = 1) -> None:
    print(dumps({"ok": False, "error": message}), file=sys.stderr)
    raise SystemExit(code)


def require(path: Path) -> None:
    if not path.is_file():
        die(f"missing required file: {path}")


def sandbox_binary() -> str:
    exe = shutil.which("bwrap")
    if not exe:
        die("bubblewrap is required for strict Linux OS isolation", 3)
    return exe


def wait_socket(sock: Path, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            die(f"MCP daemon exited early: {out}")
        if sock.exists():
            return
        time.sleep(0.05)
    die(f"MCP daemon socket not ready: {sock}")


def start_daemon(sock: Path) -> subprocess.Popen[str]:
    require(DAEMON)
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


def mcp_config(sock: Path) -> dict[str, Any]:
    return {
        "mcpServers": {
            "agent-scribe-graphify": {
                "command": sys.executable,
                "args": [str(PROXY)],
                "cwd": str(ROOT),
                "env": {"AGENT_MCP_SOCKET": str(sock)},
            }
        }
    }


def ro_flag() -> str:
    return "--" + "ro-bind"


def rw_flag() -> str:
    return "--" + "bind"


def launch_args(user_cmd: list[str], sock: Path, keep_net: bool) -> list[str]:
    require(PROXY)
    exe = sandbox_binary()
    root = str(ROOT.resolve())
    runtime = str(RUNTIME.resolve())
    args = [
        exe,
        "--die-with-parent",
        "--" + "unshare-all",
        "--share-net" if keep_net else "--" + "unshare-net",
        "--" + "proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        ro_flag(), "/usr", "/usr",
        ro_flag(), "/bin", "/bin",
        ro_flag(), "/lib", "/lib",
        ro_flag(), "/lib64", "/lib64",
        ro_flag(), root, root,
        rw_flag(), runtime, runtime,
        "--setenv", "AGENT_MCP_SOCKET", str(sock),
        "--setenv", "AGENT_SANDBOX", "readonly-project",
        "--chdir", root,
        "--",
    ]
    return args + user_cmd


def run(cmd: list[str], keep_net: bool, print_config: bool) -> int:
    if not cmd and not print_config:
        die("missing command. Use -- COMMAND or --print-mcp-config")
    sock = (RUNTIME / "mcp-daemon.sock").resolve()
    if print_config:
        print(dumps(mcp_config(sock)))
        return 0
    proc = start_daemon(sock)
    try:
        env = os.environ.copy()
        env["AGENT_MCP_SOCKET"] = str(sock)
        env["AGENT_SANDBOX"] = "readonly-project"
        return subprocess.call(launch_args(cmd, sock, keep_net), cwd=str(ROOT), env=env)
    finally:
        stop_daemon(proc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a host command with read-only project and external MCP daemon")
    parser.add_argument("--keep-net", action="store_true")
    parser.add_argument("--print-mcp-config", action="store_true")
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    ns = parser.parse_args()
    cmd = ns.cmd[1:] if ns.cmd[:1] == ["--"] else ns.cmd
    return run(cmd, keep_net=ns.keep_net, print_config=ns.print_mcp_config)


if __name__ == "__main__":
    raise SystemExit(main())
