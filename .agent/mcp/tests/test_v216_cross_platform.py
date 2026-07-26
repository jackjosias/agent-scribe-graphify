from __future__ import annotations

"""Portable V2.16 tests including first-write runtime and machine-contract regressions."""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]


def _export_test_cases(path: Path, module_name: str) -> None:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load portable test module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dir(module):
        value = getattr(module, name)
        if isinstance(value, type) and issubclass(value, unittest.TestCase):
            globals()[name] = value


_export_test_cases(
    HERE / "_test_v216_runtime_policy_impl.py",
    "_test_v216_runtime_policy_impl",
)
_export_test_cases(
    HERE / "test_first_write_discovery.py",
    "_test_first_write_discovery_portable",
)
_export_test_cases(
    HERE / "test_first_write_machine_contract.py",
    "_test_first_write_machine_contract_portable",
)


class PortablePythonLauncherTest(unittest.TestCase):
    def test_python_launchers_delegate_through_current_interpreter(self) -> None:
        for relative in (
            ".agent/workflow/scribe/scribe",
            ".agent/workflow/scribe/scribe-rag",
        ):
            with self.subTest(relative=relative):
                completed = subprocess.run(
                    [sys.executable, str(ROOT / relative), "--help"],
                    cwd=str(ROOT),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr + completed.stdout,
                )


if __name__ == "__main__":
    unittest.main()
