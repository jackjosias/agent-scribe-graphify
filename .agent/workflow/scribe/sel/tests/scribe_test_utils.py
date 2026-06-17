from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


SEL_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = SEL_ROOT.parent
SCRIPTS_DIR = SEL_ROOT / "scripts"
sys.dont_write_bytecode = True

SCRIBE_FIXTURE = """schema_version: "TENOR_SCRIBE_v1"
tiers:
  hot: ["VAC-100", "PAT-100"]
  warm: ["DEBT-100"]
  cold: []
vaccins:
  - id: "VAC-100"
    tier: "hot"
    status: "ACTIVE"
    titre: "Portable bundle command"
    l0_abstract: "Bundle-local command paths protect portability across projects and agents."
    virus: "Assuming root adapters exist makes agents edit or execute the wrong surface."
    antidote: "Use the bundle-local command path; generate root adapters only by explicit opt-in."
    liens_causaux:
      renforce: ["PAT-100"]
patterns:
  - id: "PAT-100"
    tier: "hot"
    status: "ACTIVE"
    titre: "Local retrieval"
    l0_abstract: "SCRIBE query uses local causal memory."
    liens_causaux:
      source: "JOURNAL-100"
debts:
  - id: "DEBT-100"
    tier: "warm"
    status: "ACTIVE"
    severite: "HIGH"
    titre: "Shared limiter"
    l0_abstract: "Replace process-local limiter before scaling."
    plan_remboursement: "Use Redis."
journal:
  - id: "JOURNAL-100"
    date: "2026-05-23"
    mode: "STANDARD"
    hot_entries_consulted: ["VAC-100", "PAT-100"]
    l0_abstract: "Fixture session entry."
    pourquoi: "Exercise causal links."
metrics:
  sessions_total: 10
"""


def ensure_scripts_path() -> None:
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)


def load_script_module(module_name: str) -> ModuleType:
    ensure_scripts_path()
    module_path = SCRIPTS_DIR / f"{module_name}.py"
    private_name = f"_scribe_test_{module_name}"
    spec = importlib.util.spec_from_file_location(private_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load SCRIBE script module: {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[private_name] = module
    spec.loader.exec_module(module)
    return module


def write_fixture(directory: Path) -> Path:
    path = directory / "test.scribe"
    path.write_text(SCRIBE_FIXTURE, encoding="utf-8")
    return path
