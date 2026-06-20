#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as server  # type: ignore  # noqa: E402


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def fail(message: str, code: int = 1) -> None:
    print(dumps({"ok": False, "error": message}), file=sys.stderr)
    raise SystemExit(code)


def handle_client(conn: socket.socket) -> None:
    with conn:
        reader = conn.makefile("r", encoding="utf-8", newline="\n")
        writer = conn.makefile("w", encoding="utf-8", newline="\n")
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                res = server.handle(req)
            except Exception as exc:
                res = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            writer.write(json.dumps(res, ensure_ascii=False) + "\n")
            writer.flush()


def write_env_file(path: Path, socket_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"AGENT_MCP_SOCKET={socket_path}\n", encoding="utf-8")


def bind_unix_socket(listener: socket.socket, socket_path: Path) -> None:
    try:
        listener.bind(str(socket_path))
        return
    except OSError as exc:
        if "AF_UNIX path too long" not in str(exc):
            raise

    cwd = os.getcwd()
    try:
        os.chdir(socket_path.parent)
        listener.bind(socket_path.name)
    finally:
        os.chdir(cwd)


def serve(socket_path: Path, env_file: Path | None = None) -> int:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    stop = False

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True
        try:
            listener.close()
        except Exception:
            pass

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bind_unix_socket(listener, socket_path)
    os.chmod(socket_path, 0o600)
    listener.listen(64)
    listener.settimeout(0.5)

    if env_file:
        write_env_file(env_file, socket_path)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print(dumps({"ok": True, "verdict": "MCP_DAEMON_READY", "socket": str(socket_path), "root": str(ROOT)}), flush=True)

    try:
        while not stop:
            try:
                conn, _addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            handle_client(conn)
    finally:
        try:
            listener.close()
        finally:
            if socket_path.exists():
                socket_path.unlink()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-scribe-graphify MCP daemon outside OS sandbox")
    parser.add_argument("--socket", default=str(ROOT / ".agent" / "state" / "runtime" / "mcp-daemon.sock"))
    parser.add_argument("--env-file", default=str(ROOT / ".agent" / "state" / "runtime" / "mcp-daemon.env"))
    ns = parser.parse_args()

    socket_path = Path(ns.socket).resolve()
    env_file = Path(ns.env_file).resolve() if ns.env_file else None
    if not str(socket_path).startswith(str((ROOT / ".agent" / "state" / "runtime").resolve())):
        fail("socket must live inside .agent/state/runtime")
    return serve(socket_path, env_file)


if __name__ == "__main__":
    raise SystemExit(main())
