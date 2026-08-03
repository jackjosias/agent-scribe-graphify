from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
REPO_ROOT = MCP_DIR.parents[1]
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_runtime


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class FakeInstaller:
    def __init__(self, wheel_bytes: bytes) -> None:
        self.wheel_bytes = wheel_bytes
        self.download_calls = 0
        self.install_calls = 0
        self.probe_calls = 0
        self._guard = threading.Lock()

    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, env
        if "download" in command:
            destination = Path(command[command.index("--dest") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            policy = graphify_runtime.load_runtime_policy(self.root)
            (destination / policy["wheel"]["filename"]).write_bytes(self.wheel_bytes)
            with self._guard:
                self.download_calls += 1
            return subprocess.CompletedProcess(command, 0, stdout="downloaded\n")

        if "install" in command:
            target = Path(command[command.index("--target") + 1])
            (target / "graphify").mkdir(parents=True, exist_ok=True)
            (target / "graphify" / "__init__.py").write_text(
                "__version__ = '0.9.26'\n",
                encoding="utf-8",
            )
            metadata = target / "graphifyy-0.9.26.dist-info"
            metadata.mkdir(parents=True, exist_ok=True)
            (metadata / "METADATA").write_text(
                "Metadata-Version: 2.4\nName: graphifyy\nVersion: 0.9.26\n",
                encoding="utf-8",
            )
            with self._guard:
                self.install_calls += 1
            return subprocess.CompletedProcess(command, 0, stdout="installed\n")

        if "--tenor-runtime-probe" in command:
            with self._guard:
                self.probe_calls += 1
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "package": "graphifyy",
                        "version": "0.9.26",
                        "module": "graphify",
                    }
                )
                + "\n",
            )
        raise AssertionError(f"unexpected command: {command}")


class GraphifyLocalRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="graphify-local-runtime-")
        self.root = Path(self.tmp.name) / "project"
        policy_source = (
            REPO_ROOT
            / ".agent"
            / "mcp"
            / "runtime"
            / "graphify_runtime_policy.json"
        )
        policy_target = (
            self.root
            / ".agent"
            / "mcp"
            / "runtime"
            / "graphify_runtime_policy.json"
        )
        policy_target.parent.mkdir(parents=True)
        shutil.copy2(policy_source, policy_target)
        graphify_runtime.clear_runtime_resolution_cache()

    def tearDown(self) -> None:
        graphify_runtime.clear_runtime_resolution_cache()
        self.tmp.cleanup()

    def policy_with_fake_wheel(self, wheel_bytes: bytes) -> None:
        path = graphify_runtime.runtime_policy_path(self.root)
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy["wheel"]["sha256"] = _sha256(wheel_bytes)
        path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_policy_pins_official_graphify_wheel(self) -> None:
        policy = graphify_runtime.load_runtime_policy(self.root)
        self.assertEqual(policy["package"], "graphifyy")
        self.assertEqual(policy["version"], "0.9.26")
        self.assertEqual(policy["module"], "graphify")
        self.assertEqual(
            policy["wheel"]["sha256"],
            "2184c5891b71f6b9cea127eb0e92fdd33ab8ee5c254c99312227fc6c5af3ada5",
        )
        self.assertTrue(policy["constraints"])

    def test_runtime_location_is_project_local_and_platform_scoped(self) -> None:
        location = graphify_runtime.runtime_install_dir(self.root)
        expected_state = (self.root / ".agent" / "state").resolve()
        self.assertTrue(location.is_relative_to(expected_state))
        self.assertIn(graphify_runtime.runtime_platform_key(), location.parts)
        self.assertNotIn(str(Path.home()), str(location))

    def test_pip_environment_rejects_host_index_and_python_path_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PIP_EXTRA_INDEX_URL": "https://attacker.invalid/simple",
                "pip_find_links": "/attacker/wheels",
                "PYTHONPATH": "/attacker/modules",
            },
            clear=False,
        ):
            environment = graphify_runtime._sanitized_environment()

        self.assertEqual(environment["PIP_CONFIG_FILE"], os.devnull)
        self.assertNotIn("PIP_EXTRA_INDEX_URL", environment)
        self.assertNotIn("pip_find_links", environment)
        self.assertNotIn("PYTHONPATH", environment)

    def test_verified_install_is_atomic_and_idempotent(self) -> None:
        wheel = b"deterministic test wheel"
        self.policy_with_fake_wheel(wheel)
        installer = FakeInstaller(wheel)
        installer.root = self.root

        first = graphify_runtime.ensure_graphify_runtime(
            self.root,
            runner=installer,
            allow_external=False,
        )
        second = graphify_runtime.ensure_graphify_runtime(
            self.root,
            runner=installer,
            allow_external=False,
        )

        self.assertTrue(first["ok"], first)
        self.assertEqual(first["verdict"], "GRAPHIFY_LOCAL_RUNTIME_INSTALLED")
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["verdict"], "GRAPHIFY_LOCAL_RUNTIME_READY")
        self.assertEqual(installer.download_calls, 1)
        self.assertEqual(installer.install_calls, 1)
        self.assertGreaterEqual(installer.probe_calls, 2)
        self.assertEqual(first["command"][0], sys.executable)
        self.assertIn("-I", first["command"])
        launcher = first["runtime_dir"] / graphify_runtime.LAUNCHER_FILENAME
        launcher_text = launcher.read_text(encoding="utf-8")
        self.assertIn("Path(__file__).resolve().parent", launcher_text)
        self.assertNotIn(".installing-", launcher_text)
        self.assertFalse(any(".installing-" in path.name for path in first["runtime_dir"].parent.iterdir()))

    def test_wrong_wheel_hash_fails_before_install(self) -> None:
        installer = FakeInstaller(b"tampered wheel")
        installer.root = self.root
        result = graphify_runtime.ensure_graphify_runtime(
            self.root,
            runner=installer,
            allow_external=False,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "GRAPHIFY_RUNTIME_WHEEL_HASH_MISMATCH")
        self.assertEqual(installer.install_calls, 0)
        self.assertFalse(graphify_runtime.runtime_install_dir(self.root).exists())

    def test_concurrent_installers_converge_to_one_runtime(self) -> None:
        wheel = b"single-flight wheel"
        self.policy_with_fake_wheel(wheel)
        installer = FakeInstaller(wheel)
        installer.root = self.root

        def install(_: int) -> dict[str, object]:
            return graphify_runtime.ensure_graphify_runtime(
                self.root,
                runner=installer,
                allow_external=False,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(install, range(8)))

        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(installer.download_calls, 1)
        self.assertEqual(installer.install_calls, 1)
        self.assertEqual(
            {result["verdict"] for result in results},
            {"GRAPHIFY_LOCAL_RUNTIME_INSTALLED", "GRAPHIFY_LOCAL_RUNTIME_READY"},
        )

    def test_runtime_tamper_is_detected_after_cache_reset(self) -> None:
        wheel = b"runtime integrity wheel"
        self.policy_with_fake_wheel(wheel)
        installer = FakeInstaller(wheel)
        installer.root = self.root
        installed = graphify_runtime.ensure_graphify_runtime(
            self.root,
            runner=installer,
            allow_external=False,
        )
        self.assertTrue(installed["ok"], installed)
        target = installed["runtime_dir"] / "site" / "graphify" / "__init__.py"
        target.write_text("__version__ = 'tampered'\n", encoding="utf-8")
        graphify_runtime.clear_runtime_resolution_cache()

        result = graphify_runtime.inspect_graphify_runtime(
            self.root,
            runner=installer,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "GRAPHIFY_LOCAL_RUNTIME_INTEGRITY_FAILED")

    def test_failed_install_does_not_publish_partial_runtime(self) -> None:
        wheel = b"failing install wheel"
        self.policy_with_fake_wheel(wheel)
        installer = FakeInstaller(wheel)
        installer.root = self.root
        original = installer.__call__

        def fail_install(
            command: list[str],
            *,
            cwd: Path,
            timeout: float,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            if "install" in command:
                return subprocess.CompletedProcess(command, 9, stdout="dependency failure\n")
            return original(command, cwd=cwd, timeout=timeout, env=env)

        result = graphify_runtime.ensure_graphify_runtime(
            self.root,
            runner=fail_install,
            allow_external=False,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], "GRAPHIFY_RUNTIME_DEPENDENCY_INSTALL_FAILED")
        self.assertFalse(graphify_runtime.runtime_install_dir(self.root).exists())
        failed = list(
            graphify_runtime.runtime_install_dir(self.root).parent.glob(".failed-*")
        )
        self.assertEqual(len(failed), 1)

    def test_cold_resolution_does_not_touch_tracked_bundle_files(self) -> None:
        wheel = b"tracked surface wheel"
        self.policy_with_fake_wheel(wheel)
        installer = FakeInstaller(wheel)
        installer.root = self.root
        before = {
            path.relative_to(self.root): path.read_bytes()
            for path in (self.root / ".agent" / "mcp").rglob("*")
            if path.is_file()
        }
        result = graphify_runtime.ensure_graphify_runtime(
            self.root,
            runner=installer,
            allow_external=False,
        )
        self.assertTrue(result["ok"], result)
        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in (self.root / ".agent" / "mcp").rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
