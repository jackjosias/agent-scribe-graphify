from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path


def make_tree_writable(root: Path | str) -> None:
    """Clear read-only bits without following symlinks outside the sandbox."""

    path = Path(root)
    if not path.exists() and not path.is_symlink():
        return
    for current, directories, files in os.walk(path, topdown=False, followlinks=False):
        for name in (*files, *directories):
            candidate = Path(current) / name
            if candidate.is_symlink():
                continue
            try:
                mode = candidate.stat().st_mode
                candidate.chmod(mode | stat.S_IWUSR | stat.S_IRUSR)
            except FileNotFoundError:
                continue
    if not path.is_symlink():
        try:
            mode = path.stat().st_mode
            path.chmod(mode | stat.S_IWUSR | stat.S_IRUSR)
        except FileNotFoundError:
            pass


def remove_tree_strict(root: Path | str, *, timeout_seconds: float = 10.0) -> None:
    """Remove a test sandbox with bounded Windows sharing-violation retries."""

    path = Path(root)
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    delay = 0.025
    last_error: OSError | None = None
    while path.exists() or path.is_symlink():
        make_tree_writable(path)
        try:
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path)
            last_error = None
            break
        except FileNotFoundError:
            last_error = None
            break
        except OSError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"STRICT_TEST_CLEANUP_FAILED path={path} error={exc}"
                ) from exc
            time.sleep(delay)
            delay = min(0.5, delay * 2)
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"STRICT_TEST_CLEANUP_INCOMPLETE path={path}") from last_error
