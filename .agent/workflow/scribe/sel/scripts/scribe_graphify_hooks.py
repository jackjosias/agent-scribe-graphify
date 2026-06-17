#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


CODEX_SAFE_COMMAND = 'python3 -c "import sys; sys.stdin.read()" # graphify hook'
GEMINI_SAFE_COMMAND = "python3 -c 'import sys, json; sys.stdin.read(); print(json.dumps({\"decision\":\"allow\"}))' # graphify hook"
LEGACY_GEMINI_TEMPLATE = 'r"""echo \'{"decision":"allow"}\' # graphify hook"""'
SAFE_GEMINI_TEMPLATE = f'r"""{GEMINI_SAFE_COMMAND}"""'
LEGACY_GEMINI_COMMANDS = (
    'echo \'{"decision":"allow"}\' # graphify hook',
    ': graphify hook; echo \'{"decision":"allow"}\'',
    ': graphify hook; printf "%s\\n" "{\\"decision\\":\\"allow\\"}"',
)
BAD_SCHEMA_FIELDS = ("additionalContext", "hookSpecificOutput")


@dataclass(frozen=True)
class Finding:
    severity: str
    target: str
    message: str


def default_template_paths() -> list[Path]:
    candidates: list[Path] = []
    try:
        import graphify.__main__ as graphify_main  # type: ignore

        main_file = getattr(graphify_main, "__file__", None)
        if main_file:
            candidates.append(Path(main_file))
    except Exception:
        pass

    home = Path.home()
    for root in (
        home / ".local" / "lib",
        home / ".local" / "share" / "pipx" / "venvs" / "graphifyy" / "lib",
    ):
        if not root.exists():
            continue
        candidates.extend(root.glob("python*/site-packages/graphify/__main__.py"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved not in seen and resolved.exists():
            seen.add(resolved)
            unique.append(resolved)
    return unique


def check_template(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    if not path.exists():
        return [Finding("ERROR", str(path), "template file is missing")]
    text = path.read_text(encoding="utf-8")
    for field in BAD_SCHEMA_FIELDS:
        if field in text:
            findings.append(Finding("ERROR", str(path), f"contains unsupported hook field {field}"))
    if LEGACY_GEMINI_TEMPLATE in text or any(command in text for command in LEGACY_GEMINI_COMMANDS):
        findings.append(Finding("ERROR", str(path), "contains legacy Gemini hook command that can exit before reading stdin"))
    if GEMINI_SAFE_COMMAND not in text:
        findings.append(Finding("WARNING", str(path), "safe Gemini stdin-consuming template was not found"))
    if text.count("sys.stdin.read()") < 2:
        findings.append(Finding("WARNING", str(path), "expected stdin-consuming Codex and Gemini hook templates were not both found"))
    return findings


def patch_template(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = text.replace(LEGACY_GEMINI_TEMPLATE, SAFE_GEMINI_TEMPLATE)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def check_codex_hooks(path: Path, simulate: bool) -> list[Finding]:
    if not path.exists():
        return [Finding("INFO", str(path), "Codex hooks file is absent")]
    try:
        data = load_json(path)
    except json.JSONDecodeError as exc:
        return [Finding("ERROR", str(path), f"invalid JSON: {exc}")]
    findings: list[Finding] = []
    commands = []
    if isinstance(data, dict):
        for entry in data.get("hooks", {}).get("PreToolUse", []):
            if not isinstance(entry, dict):
                continue
            for hook in entry.get("hooks", []):
                if isinstance(hook, dict) and "graphify hook" in str(hook.get("command", "")):
                    commands.append(str(hook.get("command", "")))
    if not commands:
        return [Finding("WARNING", str(path), "no graphify Codex PreToolUse hook found")]
    for command in commands:
        if "sys.stdin.read()" not in command:
            findings.append(Finding("ERROR", str(path), "Codex graphify hook does not consume stdin"))
        if simulate:
            findings.extend(simulate_command(path, command, expect_json=False))
    return findings


def check_trusted_hooks(path: Path, simulate: bool) -> list[Finding]:
    if not path.exists():
        return [Finding("INFO", str(path), "Gemini trusted hooks file is absent")]
    try:
        data = load_json(path)
    except json.JSONDecodeError as exc:
        return [Finding("ERROR", str(path), f"invalid JSON: {exc}")]
    if not isinstance(data, dict):
        return [Finding("ERROR", str(path), "trusted hooks JSON must be an object")]
    findings: list[Finding] = []
    graphify_commands = []
    for commands in data.values():
        if not isinstance(commands, list):
            continue
        for command in commands:
            if isinstance(command, str) and "graphify hook" in command:
                graphify_commands.append(command)
    if not graphify_commands:
        return [Finding("INFO", str(path), "no Gemini graphify trusted hook found")]
    for command in graphify_commands:
        if "sys.stdin.read()" not in command:
            findings.append(Finding("ERROR", str(path), "Gemini graphify hook does not consume stdin"))
        if any(legacy in command for legacy in LEGACY_GEMINI_COMMANDS):
            findings.append(Finding("ERROR", str(path), "Gemini graphify hook uses legacy echo/printf allow command"))
        if simulate:
            findings.extend(simulate_command(path, command, expect_json=True))
    return findings


def patch_trusted_hooks(path: Path) -> bool:
    if not path.exists():
        return False
    data = load_json(path)
    if not isinstance(data, dict):
        return False
    changed = False
    for project, commands in list(data.items()):
        if not isinstance(commands, list):
            continue
        fixed = []
        for command in commands:
            if isinstance(command, str) and "graphify hook" in command and "sys.stdin.read()" not in command:
                fixed.append(GEMINI_SAFE_COMMAND)
                changed = True
            else:
                fixed.append(command)
        data[project] = fixed
    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changed


def simulate_command(path: Path, command: str, expect_json: bool) -> list[Finding]:
    result = subprocess.run(
        command,
        input=b'{"tool_input":{"command":"rg x"},"hook":"BeforeToolUse"}',
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        check=False,
    )
    findings: list[Finding] = []
    if result.returncode != 0:
        findings.append(Finding("ERROR", str(path), f"hook simulation failed with exit {result.returncode}"))
    stderr = result.stderr.decode(errors="replace").strip()
    if stderr:
        findings.append(Finding("ERROR", str(path), f"hook simulation wrote stderr: {stderr}"))
    stdout = result.stdout.decode(errors="replace").strip()
    if expect_json:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            findings.append(Finding("ERROR", str(path), "Gemini hook simulation did not return JSON"))
        else:
            if payload.get("decision") != "allow":
                findings.append(Finding("ERROR", str(path), "Gemini hook simulation did not return decision=allow"))
    elif stdout:
        findings.append(Finding("ERROR", str(path), f"Codex hook simulation must stay silent, got stdout: {stdout}"))
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scribe graphify-hooks",
        description="Check and repair Graphify hook templates that must consume stdin before returning.",
    )
    parser.add_argument("--apply", action="store_true", help="Patch known vulnerable local Graphify hook templates and Gemini trusted hooks.")
    parser.add_argument("--template", action="append", default=[], help="Graphify __main__.py template path to check; may be repeated.")
    parser.add_argument("--codex-hooks", default=".codex/hooks.json", help="Codex hooks JSON path to simulate/check.")
    parser.add_argument("--trusted-hooks", default=str(Path.home() / ".gemini" / "trusted_hooks.json"), help="Gemini trusted_hooks.json path to simulate/check.")
    parser.add_argument("--no-default-templates", action="store_true", help="Use only --template paths instead of auto-discovered Graphify installations.")
    parser.add_argument("--no-simulate", action="store_true", help="Skip executing hook simulation commands.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    templates = [Path(item).expanduser() for item in args.template]
    if not args.no_default_templates:
        templates.extend(default_template_paths())
    unique_templates: list[Path] = []
    seen: set[Path] = set()
    for path in templates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_templates.append(resolved)

    patched: list[str] = []
    if args.apply:
        for path in unique_templates:
            if path.exists() and patch_template(path):
                patched.append(str(path))
        trusted_path = Path(args.trusted_hooks).expanduser()
        if patch_trusted_hooks(trusted_path):
            patched.append(str(trusted_path))

    findings: list[Finding] = []
    if not unique_templates:
        findings.append(Finding("WARNING", "graphify templates", "no Graphify __main__.py templates discovered"))
    for path in unique_templates:
        findings.extend(check_template(path))
    simulate = not args.no_simulate
    findings.extend(check_codex_hooks(Path(args.codex_hooks).expanduser(), simulate))
    findings.extend(check_trusted_hooks(Path(args.trusted_hooks).expanduser(), simulate))

    print("SCRIBE GRAPHIFY HOOKS")
    print(f"  templates_checked: {len(unique_templates)}")
    print(f"  patched: {len(patched)}")
    for path in patched:
        print(f"    - {path}")
    for finding in findings:
        print(f"  {finding.severity}: {finding.target}: {finding.message}")

    errors = [finding for finding in findings if finding.severity == "ERROR"]
    if errors:
        print("  verdict: FAIL")
        return 1
    print("  verdict: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
