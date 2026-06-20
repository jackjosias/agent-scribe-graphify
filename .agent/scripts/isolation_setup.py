#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Plan:
    os_name: str
    status: str
    commands: tuple[tuple[str, ...], ...]
    advantages: tuple[str, ...]
    drawbacks: tuple[str, ...]
    notes: tuple[str, ...]


def linux_plan() -> Plan:
    if shutil.which("apt"):
        commands = (("sudo", "apt", "update"), ("sudo", "apt", "install", "-y", "bubblewrap"))
    elif shutil.which("dnf"):
        commands = (("sudo", "dnf", "install", "-y", "bubblewrap"),)
    elif shutil.which("pacman"):
        commands = (("sudo", "pacman", "-S", "--needed", "bubblewrap"),)
    else:
        commands = tuple()
    return Plan(
        os_name="linux",
        status="strict-supported",
        commands=commands,
        advantages=("fast local isolation", "project can be mounted read-only for the host", "MCP daemon can stay outside and keep controlled write access"),
        drawbacks=("requires bubblewrap", "some distributions disable unprivileged namespaces", "GUI hosts may need extra bindings"),
        notes=("Use .agent/scripts/agent_sandbox.py -- <host command> after installation.",),
    )


def macos_plan() -> Plan:
    commands = (("brew", "install", "colima", "docker"), ("colima", "start"))
    return Plan(
        os_name="macos",
        status="container-vm-recommended",
        commands=commands,
        advantages=("works on macOS with Docker-compatible containers", "read-only bind mounts are supported by Docker", "good fallback because sandbox-exec is deprecated"),
        drawbacks=("heavier than Linux bubblewrap", "requires Homebrew and a VM runtime", "MCP proxy/socket mapping needs host-specific setup"),
        notes=("Built-in sandbox-exec exists but is deprecated; prefer a container VM for strict host isolation.",),
    )


def windows_plan() -> Plan:
    commands = (("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "Start-Process PowerShell -Verb RunAs -ArgumentList 'Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All'"),)
    return Plan(
        os_name="windows",
        status="windows-sandbox-recommended",
        commands=commands,
        advantages=("official disposable Windows environment", "supports .wsb configuration", "host folders can be mapped read-only"),
        drawbacks=("requires supported Windows edition and virtualization", "usually requires restart after enabling", "MCP proxy integration needs a generated .wsb file"),
        notes=("Use a .wsb file with read-only mapped project folder for strict isolation.",),
    )


def detect_plan() -> Plan:
    name = platform.system().lower()
    if name == "linux":
        return linux_plan()
    if name == "darwin":
        return macos_plan()
    if name == "windows":
        return windows_plan()
    return Plan(name, "unsupported", tuple(), tuple(), tuple(), ("No strict local isolation recipe is defined for this OS.",))


def print_plan(plan: Plan) -> None:
    print(f"OS: {plan.os_name}")
    print(f"Status: {plan.status}")
    print("\nAdvantages:")
    for item in plan.advantages:
        print(f"  + {item}")
    print("\nDrawbacks:")
    for item in plan.drawbacks:
        print(f"  - {item}")
    print("\nCommands:")
    if not plan.commands:
        print("  <none>")
    for cmd in plan.commands:
        print("  " + " ".join(cmd))
    print("\nNotes:")
    for item in plan.notes:
        print(f"  * {item}")


def run_commands(commands: Sequence[Sequence[str]], assume_yes: bool) -> int:
    if not commands:
        print("No install command available for this system.")
        return 2
    if not assume_yes:
        answer = input("Run these commands now? Type YES to continue: ").strip()
        if answer != "YES":
            print("Cancelled by user.")
            return 1
    for cmd in commands:
        print("$ " + " ".join(cmd))
        result = subprocess.run(list(cmd))
        if result.returncode != 0:
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or install optional OS isolation dependency for .agent")
    parser.add_argument("--install", action="store_true", help="run the detected install commands after explicit confirmation")
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    args = parser.parse_args()
    plan = detect_plan()
    print_plan(plan)
    if args.install:
        return run_commands(plan.commands, assume_yes=args.yes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
