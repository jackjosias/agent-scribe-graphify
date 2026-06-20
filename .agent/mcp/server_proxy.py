#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOCKET = ROOT / ".agent" / "state" / "runtime" / "mcp-daemon.sock"


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def socket_path() -> Path:
    return Path(os.environ.get("AGENT_MCP_SOCKET", str(DEFAULT_SOCKET))).resolve()


def connect_unix_socket(conn: socket.socket, sock: Path) -> None:
    try:
        conn.connect(str(sock))
        return
    except OSError as exc:
        if "AF_UNIX path too long" not in str(exc):
            raise

    cwd = os.getcwd()
    try:
        os.chdir(sock.parent)
        conn.connect(sock.name)
    finally:
        os.chdir(cwd)


def rpc(req: dict[str, Any]) -> dict[str, Any]:
    sock = socket_path()
    if not sock.exists():
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "result": {
                "isError": True,
                "content": [{
                    "type": "text",
                    "text": dumps({"ok": False, "code": "MCP_DAEMON_UNAVAILABLE", "error": f"MCP daemon socket not found: {sock}"}),
                }],
            },
        }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        connect_unix_socket(conn, sock)
        reader = conn.makefile("r", encoding="utf-8", newline="\n")
        writer = conn.makefile("w", encoding="utf-8", newline="\n")
        writer.write(json.dumps(req, ensure_ascii=False) + "\n")
        writer.flush()
        line = reader.readline()
        if not line:
            raise RuntimeError("empty response from MCP daemon")
        return json.loads(line)


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})


def list_tools() -> dict[str, Any]:
    return rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


def run_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            print(json.dumps(rpc(req), ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(exc)}}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="stdio MCP proxy for sandboxed hosts")
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--call")
    parser.add_argument("--args", default="{}")
    ns = parser.parse_args()

    if ns.list_tools:
        print(dumps(list_tools()))
        return 0
    if ns.call:
        try:
            args = json.loads(ns.args)
        except json.JSONDecodeError as exc:
            print(dumps({"ok": False, "error": f"invalid json args: {exc}"}), file=sys.stderr)
            return 2
        print(dumps(call_tool(ns.call, args)))
        return 0
    run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
