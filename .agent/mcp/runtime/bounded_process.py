from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Sequence


_ACTIVE_PROCESSES: set[subprocess.Popen[bytes]] = set()
_ACTIVE_PROCESSES_LOCK = threading.RLock()


@dataclass(frozen=True)
class BoundedProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


class _TailBuffer:
    def __init__(self, maximum_bytes: int) -> None:
        if maximum_bytes < 1:
            raise ValueError("maximum_bytes must be positive")
        self.maximum_bytes = maximum_bytes
        self._data = bytearray()

    def append(self, chunk: bytes) -> None:
        if len(chunk) >= self.maximum_bytes:
            self._data[:] = chunk[-self.maximum_bytes:]
            return
        overflow = len(self._data) + len(chunk) - self.maximum_bytes
        if overflow > 0:
            del self._data[:overflow]
        self._data.extend(chunk)

    def text(self) -> str:
        return bytes(self._data).decode("utf-8", errors="replace")


def _drain(stream: BinaryIO, target: _TailBuffer) -> None:
    try:
        while True:
            try:
                chunk = stream.read(64 * 1024)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            target.append(chunk)
    finally:
        with contextlib.suppress(OSError):
            stream.close()


def _close_stream(stream: BinaryIO) -> None:
    with contextlib.suppress(OSError, ValueError):
        stream.close()


def terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate the process group created by run_bounded, then force-kill it."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                shell=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            process.terminate()
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        process.kill()
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


def terminate_all_bounded_processes() -> None:
    with _ACTIVE_PROCESSES_LOCK:
        processes = tuple(_ACTIVE_PROCESSES)
    for process in processes:
        terminate_process_tree(process)


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path | str,
    timeout_seconds: float,
    output_limit_bytes: int,
    merge_stderr: bool = False,
    env: dict[str, str] | None = None,
) -> BoundedProcessResult:
    """Run an argv without a shell, bound output memory, and kill descendants on timeout."""

    command = tuple(str(value) for value in argv)
    if not command or any(not value for value in command):
        raise ValueError("argv must contain non-empty strings")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    stdout_tail = _TailBuffer(output_limit_bytes)
    stderr_tail = _TailBuffer(output_limit_bytes)
    process_kwargs: dict[str, Any] = {
        "cwd": str(Path(cwd)),
        "env": env,
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        "close_fds": True,
    }
    if os.name == "nt":
        process_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        process_kwargs["start_new_session"] = True

    started = time.monotonic()
    process = subprocess.Popen(command, **process_kwargs)
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.add(process)
    readers: list[threading.Thread] = []
    assert process.stdout is not None
    readers.append(threading.Thread(target=_drain, args=(process.stdout, stdout_tail), daemon=True))
    if not merge_stderr:
        assert process.stderr is not None
        readers.append(threading.Thread(target=_drain, args=(process.stderr, stderr_tail), daemon=True))
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process_tree(process)
            returncode = 124
    finally:
        if process.poll() is None:
            terminate_process_tree(process)
        # Normal readers reach EOF immediately. A descendant can retain a pipe
        # write handle after the direct child exits on Windows, so close only
        # the exceptional streams and do it asynchronously: BufferedReader
        # close may otherwise wait on the concurrent read lock.
        for reader in readers:
            reader.join(timeout=0.5)
        if any(reader.is_alive() for reader in readers):
            streams = (process.stdout, None if merge_stderr else process.stderr)
            for stream in streams:
                if stream is not None:
                    threading.Thread(
                        target=_close_stream,
                        args=(stream,),
                        daemon=True,
                    ).start()
            for reader in readers:
                if reader.is_alive():
                    reader.join(timeout=4.5)
        with _ACTIVE_PROCESSES_LOCK:
            _ACTIVE_PROCESSES.discard(process)
        if any(reader.is_alive() for reader in readers):
            raise RuntimeError("bounded process output reader did not terminate")

    return BoundedProcessResult(
        argv=command,
        returncode=returncode,
        stdout=stdout_tail.text(),
        stderr="" if merge_stderr else stderr_tail.text(),
        timed_out=timed_out,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
