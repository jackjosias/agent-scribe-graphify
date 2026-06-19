#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
INSTALL_DIR = THIS_FILE.parent
AGENT_ROOT = THIS_FILE.parents[2]
PROJECT_ROOT = AGENT_ROOT.parent
CATALOG_PATH = INSTALL_DIR / "host_catalog.json"


def load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def server_command() -> dict:
    server_path = AGENT_ROOT / "mcp" / "server_entry.py"
    return {"name": "agent-scribe-graphify", "command": sys.executable or "python3", "args": [str(server_path)], "transport": "stdio", "cwd": str(PROJECT_ROOT)}


def generic_snippet() -> dict:
    cmd = server_command()
    return {"mcpServers": {cmd["name"]: {"command": cmd["command"], "args": cmd["args"], "transport": cmd["transport"]}}}


def main() -> int:
    parser = argparse.ArgumentParser(description="Assistant d'installation MCP agent-scribe-graphify")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--host")
    parser.add_argument("--doctor", action="store_true")
    args = parser.parse_args()
    catalog = load_catalog()
    if args.doctor:
        server = AGENT_ROOT / "mcp" / "server_entry.py"
        print(json.dumps({"ok": server.is_file(), "project_root": str(PROJECT_ROOT), "server": server_command(), "catalog": str(CATALOG_PATH)}, ensure_ascii=False, indent=2))
        return 0 if server.is_file() else 1
    if args.list:
        for name, recipe in sorted(catalog["hosts"].items()):
            print(f"- {name}: {recipe['kind']} / {recipe['config_scope']}")
        return 0
    if args.host:
        recipe = catalog["hosts"].get(args.host)
        if not recipe:
            print(json.dumps({"ok": False, "error": "unknown host", "known_hosts": sorted(catalog["hosts"])}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps({"ok": True, "host": args.host, "recipe": recipe, "server": server_command(), "generic_json_snippet": generic_snippet(), "manual_confirmation_required": True}, ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
