from __future__ import annotations

from pathlib import Path

from scribe_test_utils import SCRIBE_FIXTURE


def write_smoke_eval_fixture(directory: Path) -> Path:
    content = """schema_version: "TENOR_SCRIBE_v1"
tiers:
  hot: ["GHOST-002", "GHOST-003"]
  warm: ["PAT-023", "PAT-024", "VAC-001"]
  cold: []
vaccins:
  - id: "VAC-001"
    tier: "warm"
    status: "ACTIVE"
    titre: "Old legacy Next lint"
    l0_abstract: "After Next lint became legacy, use the ESLint CLI instead of next lint."
    contexte: "old legacy next lint migration"
patterns:
  - id: "PAT-023"
    tier: "warm"
    status: "ACTIVE"
    titre: "Clean generated memory noise"
    l0_abstract: "Clean generated scribe-out and Graphify cache noise before delivery."
    pourquoi: "Generated reports make agents inspect stale artifacts before delivery."
    contexte: "portable bootstrap clean generated noise"
  - id: "PAT-024"
    tier: "warm"
    status: "ACTIVE"
    titre: "Dual Graphify output separation"
    l0_abstract: "graphify-out is the application graph; scribe-out/bundle-graph is the SCRIBE bundle graph."
    pourquoi: "Application and bundle Graphify outputs answer different questions and must not be merged."
    contexte: "graphify application bundle graph confusion"
ghosts:
  - id: "GHOST-002"
    tier: "hot"
    status: "ACTIVE"
    titre: "Lock multi-agent local choisi"
    l0_abstract: "Local lock protects multi agent writes."
    contexte: "lock multi agent stale sync repair writer"
  - id: "GHOST-003"
    tier: "hot"
    status: "ACTIVE"
    titre: "state.json shared writer state"
    l0_abstract: "state.json tracks stale sync repair and the last real writer."
    contexte: "lock multi agent stale sync repair writer"
journal: []
metrics:
  sessions_total: 1
"""
    path = directory / "smoke-eval.scribe"
    path.write_text(content, encoding="utf-8")
    return path

def write_cold_fixture(directory: Path) -> Path:
    cold_pattern = """  - id: "PAT-200"
    tier: "cold"
    status: "ACTIVE"
    titre: "Old experiment"
    l0_abstract: "Old cold memory ready for archive."
"""
    content = SCRIBE_FIXTURE.replace('  cold: []', '  cold: ["PAT-200"]').replace("debts:\n", cold_pattern + "debts:\n")
    path = directory / "cold.scribe"
    path.write_text(content, encoding="utf-8")
    return path


def write_many_hot_fixture(directory: Path) -> Path:
    extra_ids = [f"PAT-{number}" for number in range(101, 110)]
    hot_ids = ["VAC-100", "PAT-100", *extra_ids]
    hot_line = "  hot: [" + ", ".join(f'"{entity_id}"' for entity_id in hot_ids) + "]"
    extra_patterns = "".join(
        f"""  - id: "{entity_id}"
    tier: "hot"
    status: "ACTIVE"
    titre: "Hot context {entity_id}"
    l0_abstract: "Synthetic hot memory for context limiting."
"""
        for entity_id in extra_ids
    )
    content = SCRIBE_FIXTURE.replace('  hot: ["VAC-100", "PAT-100"]', hot_line).replace("debts:\n", extra_patterns + "debts:\n")
    path = directory / "many-hot.scribe"
    path.write_text(content, encoding="utf-8")
    return path


def write_ranked_hot_fixture(directory: Path) -> Path:
    hot_ids = ["VAC-100", "PAT-100", "PAT-301", "PAT-302"]
    hot_line = "  hot: [" + ", ".join(f'"{entity_id}"' for entity_id in hot_ids) + "]"
    extra_patterns = """  - id: "PAT-301"
    tier: "hot"
    status: "ACTIVE"
    date: "2026-05-20"
    titre: "Socket abuse guard"
    l0_abstract: "Socket abuse payload validation memory."
    liens_causaux:
      source: "JOURNAL-301"
  - id: "PAT-302"
    tier: "hot"
    status: "ACTIVE"
    date: "2026-05-24"
    titre: "Recent context pack"
    l0_abstract: "Recent context memory without socket terms."
    liens_causaux:
      source: "JOURNAL-302"
"""
    content = SCRIBE_FIXTURE.replace('  hot: ["VAC-100", "PAT-100"]', hot_line).replace("debts:\n", extra_patterns + "debts:\n")
    path = directory / "ranked-hot.scribe"
    path.write_text(content, encoding="utf-8")
    return path



def write_abstract_retrieval_fixture(directory: Path) -> Path:
    content = SCRIBE_FIXTURE.replace(
        "debts:\n",
        """  - id: \"PAT-401\"
    tier: \"hot\"
    status: \"ACTIVE\"
    titre: \"Friction-aware tiny-task workflow\"
    l0_abstract: \"Avoid wasting agent attention and runtime ceremony on low-risk small fixes.\"
    pourquoi: \"The protocol must reduce overhead for quick work while preserving safety for critical work.\"
    contexte: \"Agentic workflow friction, token budget, and small task latency.\"
debts:\n""",
    ).replace('  hot: ["VAC-100", "PAT-100"]', '  hot: ["VAC-100", "PAT-100", "PAT-401"]')
    path = directory / "abstract-retrieval.scribe"
    path.write_text(content, encoding="utf-8")
    return path


def write_warm_pattern_audit_fixture(directory: Path) -> Path:
    journals = "".join(
        f"""  - id: "JOURNAL-{number:03d}"
    date: "2026-05-{(number % 24) + 1:02d}"
    l0_abstract: "Audit fixture session {number}."
"""
        for number in range(1, 26)
    )
    content = f"""schema_version: "TENOR_SCRIBE_v1"
tiers:
  hot: []
  warm: ["PAT-250"]
  cold: []
patterns:
  - id: "PAT-250"
    tier: "warm"
    status: "ACTIVE"
    scope: "project"
    titre: "Unproven warm rule"
    l0_abstract: "Warm pattern that must be audited after twenty sessions without a SCAR or GHOST source."
    evidence:
      type: "OBSERVED"
      source: "JOURNAL-001"
      observable: "Created from a journal only."
    liens_causaux:
      source: "JOURNAL-001"
journal:
{journals}metrics:
  sessions_total: 25
"""
    path = directory / "warm-pattern-audit.scribe"
    path.write_text(content, encoding="utf-8")
    return path
