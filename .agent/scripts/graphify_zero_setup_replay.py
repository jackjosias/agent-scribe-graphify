#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


INIT_SESSION = re.compile(r"^TENOR_INIT_AGENT_SESSION=(.+)$", re.MULTILINE)
RUNTIME_READY = "TENOR_GRAPHIFY_RUNTIME_READY source=project_local"
GRAPH_READY = "TENOR_GRAPHIFY_READY"


class ReplayFailure(RuntimeError):
    pass


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        raise ReplayFailure(
            f"timeout after {timeout}s: {command!r}\n{output[-20_000:]}"
        ) from exc


def require(
    condition: object,
    message: str,
    *,
    output: str = "",
) -> None:
    if condition:
        return
    suffix = f"\n{output[-20_000:]}" if output else ""
    raise ReplayFailure(f"{message}{suffix}")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _remove_readonly(function: Any, path: str, _exc: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)
    except OSError:
        pass


def copy_complete_agent(source: Path, destination: Path) -> None:
    source_agent = source / ".agent"
    require(source_agent.is_dir(), f"source .agent is missing: {source_agent}")

    def ignored(directory: str, names: list[str]) -> set[str]:
        current = Path(directory)
        result = set(
            shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "*.pyo",
                ".pytest_cache",
                ".mypy_cache",
            )(directory, names)
        )
        if current.resolve() == source_agent.resolve():
            result.add("state")
        return result

    shutil.copytree(source_agent, destination / ".agent", ignore=ignored)


def git(project: Path, *arguments: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return run(
        ["git", *arguments],
        cwd=project,
        env=dict(os.environ),
        timeout=timeout,
    )


def create_project(root: Path) -> None:
    write(
        root / "pyproject.toml",
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        'name = "tenor-zero-setup-fixture"\n'
        'version = "1.0.0"\n'
        'requires-python = ">=3.10"\n',
    )
    write(
        root / "src" / "zero_setup_fixture" / "__init__.py",
        '"""Fixture package for the TENOR Graphify zero-setup replay."""\n\n'
        "from .service import add_one\n\n"
        '__all__ = ["add_one"]\n',
    )
    write(
        root / "src" / "zero_setup_fixture" / "service.py",
        "def add_one(value: int) -> int:\n"
        '    """Return the successor of ``value``."""\n\n'
        "    return value + 1\n",
    )
    write(
        root / "tests" / "test_service.py",
        "from zero_setup_fixture import add_one\n\n\n"
        "def test_add_one() -> None:\n"
        "    assert add_one(41) == 42\n",
    )
    write(
        root / "README.md",
        "# TENOR zero-setup fixture\n\n"
        "A fresh application project used to prove project-local Graphify provisioning.\n",
    )
    initialized = git(root, "init")
    require(initialized.returncode == 0, "git init failed", output=initialized.stdout)
    for key, value in (
        ("user.email", "zero-setup@example.invalid"),
        ("user.name", "TENOR Zero Setup Replay"),
    ):
        configured = git(root, "config", key, value)
        require(configured.returncode == 0, f"git config {key} failed", output=configured.stdout)
    added = git(root, "add", "-A")
    require(added.returncode == 0, "initial git add failed", output=added.stdout)
    committed = git(root, "commit", "-m", "initial application fixture")
    require(committed.returncode == 0, "initial git commit failed", output=committed.stdout)


def parse_bridge(raw: str) -> dict[str, Any]:
    try:
        outer = json.loads(raw)
        return json.loads(outer["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReplayFailure(f"invalid bridge payload: {exc}\n{raw[-20_000:]}") from exc


def replay(source: Path, *, keep: bool, require_no_global: bool) -> dict[str, Any]:
    temporary = Path(tempfile.mkdtemp(prefix="tenor-graphify-zero-setup-"))
    project = temporary / "project"
    try:
        project.mkdir()
        create_project(project)
        copy_complete_agent(source, project)
        global_graphify = shutil.which("graphify")
        if require_no_global:
            require(
                global_graphify is None,
                f"global Graphify unexpectedly present: {global_graphify}",
            )

        environment = dict(os.environ)
        environment.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
                "TENOR_GRAPHIFY_REQUIRE_LOCAL": "1",
            }
        )
        init_command = [
            sys.executable,
            ".agent/workflow/scribe/scribe",
            "tenor-init",
            "--type",
            "cli",
            "--host",
            "codex-cli",
        ]
        first = run(init_command, cwd=project, env=environment, timeout=240)
        require(
            first.returncode == 76,
            f"first TENOR INIT expected reconnect exit 76, got {first.returncode}",
            output=first.stdout,
        )
        require(RUNTIME_READY in first.stdout, "project-local runtime marker missing", output=first.stdout)
        require(GRAPH_READY in first.stdout, "real Graphify graph marker missing", output=first.stdout)
        require("HOST_RECONNECT_REQUIRED" in first.stdout, "host reconnect verdict missing", output=first.stdout)

        graph_path = (
            project
            / ".agent"
            / "state"
            / "outputs"
            / "graphify-out"
            / "graph.json"
        )
        ready_path = graph_path.with_name("GRAPHIFY_READY.json")
        require(graph_path.is_file(), "canonical graph.json missing")
        require(ready_path.is_file(), "GRAPHIFY_READY.json missing")
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        edges = graph.get("links", graph.get("edges"))
        require(isinstance(graph.get("nodes"), list) and graph["nodes"], "real graph has no nodes")
        require(isinstance(edges, list), "real graph has no supported edge collection")
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        require(ready.get("project_root") == str(project.resolve()), "graph root binding mismatch")
        require(ready.get("kind") == "real", "Graphify manifest is not a real graph")

        runtime_manifests = list(
            (
                project
                / ".agent"
                / "state"
                / "runtime"
                / "toolchains"
                / "graphify"
            ).glob("*/*/TENOR_GRAPHIFY_RUNTIME.json")
        )
        require(len(runtime_manifests) == 1, "expected exactly one local runtime manifest")
        runtime_manifest = json.loads(runtime_manifests[0].read_text(encoding="utf-8"))
        require(runtime_manifest.get("version") == "0.9.26", "runtime version mismatch")
        require(
            runtime_manifest.get("wheel_sha256")
            == "2184c5891b71f6b9cea127eb0e92fdd33ab8ee5c254c99312227fc6c5af3ada5",
            "runtime wheel digest mismatch",
        )
        ignored_runtime = git(
            project,
            "check-ignore",
            "-q",
            str(runtime_manifests[0].relative_to(project)),
        )
        require(
            ignored_runtime.returncode == 0,
            "project-local Graphify runtime is not ignored by the copied .agent bundle",
            output=ignored_runtime.stdout,
        )

        baseline_add = git(project, "add", "-A")
        require(baseline_add.returncode == 0, "baseline git add failed", output=baseline_add.stdout)
        baseline_commit = git(project, "commit", "-m", "baseline after first TENOR INIT")
        require(
            baseline_commit.returncode == 0,
            "baseline commit after first init failed",
            output=baseline_commit.stdout,
        )
        baseline_tree = git(project, "write-tree")
        require(baseline_tree.returncode == 0, "baseline tree read failed", output=baseline_tree.stdout)

        second = run(init_command, cwd=project, env=environment, timeout=120)
        require(
            second.returncode == 0,
            f"second TENOR INIT expected exit 0, got {second.returncode}",
            output=second.stdout,
        )
        require(
            "TENOR_INIT_NEXT_TOOL=tenor_init_bridge" in second.stdout,
            "second INIT did not expose the bridge continuation contract",
            output=second.stdout,
        )
        match = INIT_SESSION.search(second.stdout)
        require(match is not None, "second INIT agent session missing", output=second.stdout)
        agent_session = match.group(1).strip()
        after_tree = git(project, "write-tree")
        require(after_tree.returncode == 0, "post-init tree read failed", output=after_tree.stdout)
        require(
            after_tree.stdout.strip() == baseline_tree.stdout.strip(),
            "second INIT changed the staged/tracked tree",
        )
        clean = git(project, "status", "--porcelain")
        require(clean.returncode == 0, "git status failed", output=clean.stdout)
        require(not clean.stdout.strip(), "second INIT changed tracked files", output=clean.stdout)

        binding_path = project / ".agent" / "state" / "install" / "host-binding.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        bridge_environment = {
            **environment,
            "AGENT_MCP_HOST": "codex-cli",
            "AGENT_MCP_BINDING_ID": str(binding["binding_id"]),
            "AGENT_SCRIBE_GRAPHIFY_ROOT": ".",
        }
        bridge = run(
            [
                sys.executable,
                ".agent/mcp/server_entry.py",
                "--call",
                "tenor_init_bridge",
                "--args",
                json.dumps(
                    {
                        "agent_session_id": agent_session,
                        "host_tool": "codex-cli",
                        "model_name": "graphify-zero-setup-replay",
                    }
                ),
            ],
            cwd=project,
            env=bridge_environment,
            timeout=60,
        )
        require(bridge.returncode == 0, "bridge process failed", output=bridge.stdout)
        bridge_payload = parse_bridge(bridge.stdout)
        require(
            bridge_payload.get("ok")
            and bridge_payload.get("verdict") == "TENOR_INIT_READY",
            "bridge did not reach TENOR_INIT_READY",
            output=json.dumps(bridge_payload, indent=2, sort_keys=True),
        )
        require(
            bridge_payload.get("ready_scope") == "HOST_PROCESS_ROOT_AND_SESSION",
            "bridge ready scope mismatch",
        )
        clean_after_bridge = git(project, "status", "--porcelain")
        require(
            clean_after_bridge.returncode == 0 and not clean_after_bridge.stdout.strip(),
            "bridge changed tracked files",
            output=clean_after_bridge.stdout,
        )

        result = {
            "ok": True,
            "verdict": "GRAPHIFY_ZERO_SETUP_REPLAY_OK",
            "project": str(project),
            "global_graphify": global_graphify,
            "runtime_version": runtime_manifest["version"],
            "runtime_wheel_sha256": runtime_manifest["wheel_sha256"],
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(edges),
            "graph_sources": ready.get("source_file_count"),
            "first_init_exit": first.returncode,
            "second_init_exit": second.returncode,
            "second_init_tracked_tree_unchanged": True,
            "bridge_verdict": bridge_payload["verdict"],
            "bridge_scope": bridge_payload["ready_scope"],
        }
        if keep:
            result["kept_at"] = str(temporary)
        return result
    finally:
        if not keep:
            shutil.rmtree(temporary, onerror=_remove_readonly)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay canonical TENOR INIT from a raw .agent copy without global Graphify."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root containing the candidate .agent bundle",
    )
    parser.add_argument("--keep", action="store_true", help="preserve the temporary replay project")
    parser.add_argument(
        "--allow-global",
        action="store_true",
        help="do not fail when a global graphify executable exists; local runtime is still required",
    )
    arguments = parser.parse_args()
    try:
        result = replay(
            arguments.source.resolve(),
            keep=arguments.keep,
            require_no_global=not arguments.allow_global,
        )
    except ReplayFailure as exc:
        print(f"GRAPHIFY_ZERO_SETUP_REPLAY_FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    print("GRAPHIFY_ZERO_SETUP_REPLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
