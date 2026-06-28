from __future__ import annotations

import hashlib
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag_interface import DEFAULT_SCRIBE, PROJECT_ROOT, RAG_INDEX_PATH


PROTECTED_RELATIVE_PATHS = (
    "AGENT-MEMOIRE_PROJECT_STATUS.scribe",
    ".agent/state/outputs/scribe-out/rag-index.json",
    ".agent/state/outputs/scribe-out/scribe-doctor-report.md",
    ".agent/state/outputs/scribe-out/state.json",
    ".agent/state/outputs/scribe-out/workflow-acks.json",
    ".agent/state/outputs/scribe-out/locks/scribe.lock",
    ".agent/state/outputs/scribe-out/coordination/events.jsonl",
)

SURFACE_PREFIXES = (
    ("agent_rag", ".agent/workflow/scribe/rag/"),
    ("agent_sel", ".agent/workflow/scribe/sel/"),
    ("agent_rules", ".agent/rules/"),
    ("agent_skills", ".agent/skills/"),
    ("agent_docs", ".agent/"),
    ("scribe_memory", "AGENT-MEMOIRE_PROJECT_STATUS.scribe"),
    ("runtime_state", ".agent/state/outputs/scribe-out/"),
    ("graph_state", ".agent/state/outputs/graphify-out/"),
    ("docs", "docs/"),
    ("tests", "tests/"),
    ("source", "src/"),
    ("components", "components/"),
    ("config", "config/"),
)


class AutoDreamCancelled(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class ReadOnlyViolation(RuntimeError):
    pass


@dataclass
class AutoDreamBudget:
    max_files: int = 500
    max_diff_bytes: int = 240_000
    max_read_bytes: int = 12_000_000
    timeout_ms: int = 5_000
    max_output_lines: int = 180
    max_candidates: int = 8
    started_at: float = field(default_factory=time.monotonic)
    bytes_read: int = 0
    files_seen: int = 0
    truncated: bool = False

    def check_time(self) -> None:
        elapsed_ms = int((time.monotonic() - self.started_at) * 1000)
        if elapsed_ms > self.timeout_ms:
            raise BudgetExceeded(f"timeout exceeded: {elapsed_ms}ms > {self.timeout_ms}ms")

    def charge_bytes(self, amount: int, label: str) -> None:
        self.bytes_read += max(0, amount)
        if self.bytes_read > self.max_read_bytes:
            raise BudgetExceeded(f"read budget exceeded after {label}: {self.bytes_read} > {self.max_read_bytes}")

    def charge_file(self, label: str) -> None:
        self.files_seen += 1
        if self.files_seen > self.max_files:
            raise BudgetExceeded(f"file budget exceeded after {label}: {self.files_seen} > {self.max_files}")

    def remaining_timeout_seconds(self) -> float:
        elapsed_ms = int((time.monotonic() - self.started_at) * 1000)
        remaining_ms = max(1, self.timeout_ms - elapsed_ms)
        return max(0.001, remaining_ms / 1000)


@dataclass(frozen=True)
class AutoDreamConfig:
    project_root: Path = PROJECT_ROOT
    scribe_path: Path = DEFAULT_SCRIBE
    index_path: Path = RAG_INDEX_PATH
    read_only: bool = True
    cancel_file: Path | None = None
    max_files: int = 500
    max_diff_bytes: int = 240_000
    max_read_bytes: int = 12_000_000
    timeout_ms: int = 5_000
    max_output_lines: int = 180
    max_candidates: int = 8
    protected_paths: tuple[str, ...] = PROTECTED_RELATIVE_PATHS


_CANCELLED = False


def _handle_cancel_signal(_signum: int, _frame: Any) -> None:
    global _CANCELLED
    _CANCELLED = True


def install_cancel_handlers() -> None:
    signal.signal(signal.SIGINT, _handle_cancel_signal)
    signal.signal(signal.SIGTERM, _handle_cancel_signal)


def check_cancel(config: AutoDreamConfig, budget: AutoDreamBudget) -> None:
    budget.check_time()
    if _CANCELLED:
        raise AutoDreamCancelled("received SIGINT/SIGTERM")
    if config.cancel_file and config.cancel_file.exists():
        raise AutoDreamCancelled(f"cancel file present: {config.cancel_file}")


def file_fingerprint(path: Path, budget: AutoDreamBudget) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "sha256": "", "size": 0, "mtime_ns": 0}
    budget.charge_file(str(path))
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            budget.charge_bytes(len(chunk), str(path))
            digest.update(chunk)
            budget.check_time()
    stat = path.stat()
    return {"exists": True, "sha256": digest.hexdigest(), "size": size, "mtime_ns": stat.st_mtime_ns}


class ReadOnlyGuard:
    def __init__(self, root: Path, relative_paths: tuple[str, ...], budget: AutoDreamBudget) -> None:
        self.root = root
        self.relative_paths = relative_paths
        self.budget = budget
        self.before: dict[str, dict[str, Any]] = {}

    def snapshot_before(self) -> None:
        self.before = {rel: file_fingerprint(self.root / rel, self.budget) for rel in self.relative_paths}

    def verify_after(self) -> dict[str, Any]:
        after = {rel: file_fingerprint(self.root / rel, self.budget) for rel in self.relative_paths}
        changed = [rel for rel, before in self.before.items() if before != after.get(rel)]
        if changed:
            raise ReadOnlyViolation("protected files changed during AutoDream: " + ", ".join(changed))
        return {"protected_paths": len(self.relative_paths), "unchanged": True, "changed_paths": []}


def read_text_limited(path: Path, budget: AutoDreamBudget, *, limit: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    budget.charge_file(str(path))
    raw = path.read_bytes()[: limit + 1]
    budget.charge_bytes(len(raw), str(path))
    truncated = len(raw) > limit
    return raw[:limit].decode("utf-8", errors="replace"), truncated


def run_git(root: Path, args: list[str], budget: AutoDreamBudget) -> tuple[int, str, str]:
    budget.check_time()
    result = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=budget.remaining_timeout_seconds(),
        check=False,
    )
    output_bytes = len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8"))
    budget.charge_bytes(output_bytes, "git output")
    return result.returncode, result.stdout, result.stderr


def surface_for_path(path: str) -> str:
    normalized = path.strip()
    for surface, prefix in SURFACE_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix):
            return surface
    suffix = Path(normalized).suffix
    if suffix in {".md", ".mdx", ".txt", ".rst"}:
        return "docs"
    if suffix in {".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx", ".py"} and "test" in normalized.lower():
        return "tests"
    return "other"


def parse_status_paths(status_text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw_line in status_text.splitlines():
        if len(raw_line) < 4:
            continue
        status = raw_line[:2].strip() or raw_line[:2]
        path = raw_line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append({"status": status, "path": path, "surface": surface_for_path(path)})
    return rows
