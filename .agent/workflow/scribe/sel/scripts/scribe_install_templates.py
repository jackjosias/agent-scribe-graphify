from __future__ import annotations


AGENTS_START = "<!-- SCRIBE-PORTABLE-WORKFLOW:START -->"
AGENTS_END = "<!-- SCRIBE-PORTABLE-WORKFLOW:END -->"
GRAPHIFY_START = "# SCRIBE-PORTABLE-WORKFLOW:START"
GRAPHIFY_END = "# SCRIBE-PORTABLE-WORKFLOW:END"
LEGACY_LOCAL_AGENTS_START = "<!-- SCRIBE-ENGINEERING-LOCAL-CAUSAL-RETRIEVAL:START -->"
LEGACY_LOCAL_AGENTS_END = "<!-- SCRIBE-ENGINEERING-LOCAL-CAUSAL-RETRIEVAL:END -->"
LEGACY_RAG_AGENTS_START = "<!-- SCRIBE-ENGINEERING-RAG:START -->"
LEGACY_RAG_AGENTS_END = "<!-- SCRIBE-ENGINEERING-RAG:END -->"
LEGACY_LOCAL_GRAPHIFY_START = "# SCRIBE-ENGINEERING-LOCAL-CAUSAL-RETRIEVAL:START"
LEGACY_LOCAL_GRAPHIFY_END = "# SCRIBE-ENGINEERING-LOCAL-CAUSAL-RETRIEVAL:END"
LEGACY_RAG_GRAPHIFY_START = "# SCRIBE-ENGINEERING-RAG:START"
LEGACY_RAG_GRAPHIFY_END = "# SCRIBE-ENGINEERING-RAG:END"
LEGACY_AGENTS_START = LEGACY_RAG_AGENTS_START
LEGACY_AGENTS_END = LEGACY_RAG_AGENTS_END
LEGACY_GRAPHIFY_START = LEGACY_RAG_GRAPHIFY_START
LEGACY_GRAPHIFY_END = LEGACY_RAG_GRAPHIFY_END
LEGACY_AGENTS_MARKERS = (
    (LEGACY_LOCAL_AGENTS_START, LEGACY_LOCAL_AGENTS_END),
    (LEGACY_RAG_AGENTS_START, LEGACY_RAG_AGENTS_END),
)
LEGACY_GRAPHIFY_MARKERS = (
    (LEGACY_LOCAL_GRAPHIFY_START, LEGACY_LOCAL_GRAPHIFY_END),
    (LEGACY_RAG_GRAPHIFY_START, LEGACY_RAG_GRAPHIFY_END),
)
PORTABLE_RELATIVE_PATH = ".agent/workflow/scribe"
SEL_RELATIVE_PATH = f"{PORTABLE_RELATIVE_PATH}/sel"
RAG_RELATIVE_PATH = f"{PORTABLE_RELATIVE_PATH}/rag"
BUNDLE_RELATIVE_PATH = PORTABLE_RELATIVE_PATH
BUNDLE_COMMAND = f"{PORTABLE_RELATIVE_PATH}/scribe"
RAG_COMMAND = f"{PORTABLE_RELATIVE_PATH}/scribe-rag"
SCRIBE_RULE_PATH = ".agent/rules/scribe.md"


def render_scribe_rule() -> str:
    return f"""---
trigger: always_on
---

# SCRIBE — REGLE ALWAYS-ON

Ce fichier est une regle courte pour les LLM hotes. Il ne remplace pas le
protocole complet.

## Source canonique

- Racine portable unique: `{BUNDLE_RELATIVE_PATH}/`
- CLI maintenance interne: `{BUNDLE_COMMAND}`
- CLI lecture agent: `{RAG_COMMAND}`
- Protocole complet: `{SEL_RELATIVE_PATH}/docs/scribe.md`
- Regles locales: `{SEL_RELATIVE_PATH}/docs/AGENTS.md`
- Politique de friction: `{SEL_RELATIVE_PATH}/docs/friction-policy.md`
- Installation multi-agent: `{SEL_RELATIVE_PATH}/docs/multi-agent-installation.md`
- Coordination live: `{SEL_RELATIVE_PATH}/docs/live-coordination.md`
- Skill init: `.agent/skills/init-tenor/SKILL.md`

Tout chemin SCRIBE hors de `{BUNDLE_RELATIVE_PATH}/` est non canonique. Ne pas
creer de dossier de compatibilite visible; corriger les anciens appels vers les
commandes ci-dessus.

## Etat stabilise 2026-06-01

Le bundle est stable et ne doit plus etre perfectionne hors bug reel:

- SEL: tests verts (`81 OK` apres les tests lock ownership).
- RAG: tests verts (`25 OK`).
- Gate/eval: `{RAG_COMMAND} gate` vert a `8/8`.
- Doctor: `0 error`; `W009` legacy pre-V3.2 reste cosmetique.
- Identite/presence: `os.getpid()` utilise, stale PID nettoye, IDs/PIDs simultanes distincts.
- Coordination: claims avec `ttl_seconds` et `expires_at`; claims expires ou sans TTL nettoyes.
- Lock: `release` verifie agent/surface avant de nettoyer un stale lock; utiliser `SCRIBE_OWNER_PID` ou `--owner-pid` pour representer un processus proprietaire long-vivant.
- Backup de reference: `~/backups/agent-scribe-stable-20260601.tar.gz`.
- Ratio causal mesure: environ `17.5%`, cible `35%`; ne jamais creer de SCAR/GHOST/PAT cosmetique pour gonfler ce ratio.
- Reflexe douleur reelle: bug resolu en plus de 2 tentatives, regression, rollback couteux ou smoke visuel casse => SCAR immediat avec `cause_racine`, `resolution` et `test_binding`; avant une tache proche, interroger `scribe-rag query/explain/challenge` pour exploiter ces cicatrices.

Instruction operationnelle: STOP `.agent`. Revenir au vrai projet. Ne rouvrir
le chantier SCRIBE que pour un bug SCRIBE observe, un test rouge, ou une derive
documentaire concrete.

## Reflexe de demarrage par tier

Depuis la racine du projet:

```bash
{BUNDLE_COMMAND} bootstrap
```

`bootstrap` est idempotent et initialise seulement ce qui manque:
`AGENT-MEMOIRE_PROJECT_STATUS.scribe`, `.agent/state/outputs/scribe-out/`, `state.json`,
`.graphifyignore` et le bloc gere de `AGENTS.md`. Il ne lance aucun daemon.

Mode NANO, correction < 30 min, 1 fichier, sans surface partagee:

```bash
{RAG_COMMAND} context
```

Mode STANDARD, changement significatif:

```bash
{RAG_COMMAND} build
{RAG_COMMAND} context
{RAG_COMMAND} challenge "<plan>"
```

Mode CRITICAL ou mutation SCRIBE/surface partagee:

```bash
{BUNDLE_COMMAND} workflow read --agent <name> --type <extension|cli|api|unknown>
{BUNDLE_COMMAND} workflow check --agent <name>
{BUNDLE_COMMAND} sync --agent <name> --type <extension|cli|api|unknown>
{RAG_COMMAND} preflight --tier CRITICAL --strict "<objectif ou plan de session>"
```

`workflow read` calcule le SHA du workflow canonique et enregistre l'ack dans
`.agent/state/outputs/scribe-out/workflow-acks.json`. `workflow check` doit etre vert avant toute
mutation SCRIBE ou surface partagee.

En mode 4 terminaux, ne pas imposer de noms fixes. Chaque terminal demarre en
presence `idle`, obtient son ID via `scribe whoami`, lit `coordination status`,
puis prend un `coordination claim` seulement quand une tache concrete arrive.
`workflow status` affiche le pool reel; `--required ... --strict` sert uniquement
si une liste nommee est imposee.

```bash
{BUNDLE_COMMAND} whoami --type cli --surface idle
{BUNDLE_COMMAND} coordination status
{BUNDLE_COMMAND} workflow status
```

Preuve minimale attendue dans chaque reponse de travail non triviale:
`SCRIBE_RAG_PROOF: preflight <tier> | eval X/8 | challenge <VERDICT>`.
Si `preflight` signale `HYBRID_REQUIRED`, reconstruire avec
`scribe-rag build --with-embeddings --force` ou justifier explicitement le
maintien BM25.

## Dashboard SCRIBE

```bash
{BUNDLE_COMMAND} dashboard
{BUNDLE_COMMAND} dashboard --serve --host 127.0.0.1 --port 8765
```

`dashboard` genere un HTML statique dans `.agent/state/outputs/scribe-out/`. `dashboard --serve`
lance a la demande un serveur HTTP local leger (`ThreadingHTTPServer`) pour vue
live/reload; ce processus s'arrete avec Ctrl+C et n'est jamais demarre par
`bootstrap`.

## Reflexe avant mutation SCRIBE

Avant toute commande qui modifie la memoire:

```bash
{BUNDLE_COMMAND} workflow check --agent <name>
{BUNDLE_COMMAND} doctor --suggest-fix
{BUNDLE_COMMAND} lock acquire --agent <name> --type <extension|cli|api|unknown> --session <JOURNAL-ID>
```

Apres validation:

```bash
{BUNDLE_COMMAND} doctor --suggest-fix
{BUNDLE_COMMAND} sync --repair --agent <name> --type <extension|cli|api|unknown> --session <JOURNAL-ID>
{BUNDLE_COMMAND} lock release --agent <name>
```

`lock acquire` refuse un agent sans ack workflow frais. Utiliser `--surface <nom>`
pour reserver une surface partagee non-SCRIBE avec le meme garde-fou. `lock release`
ne supprime plus le stale lock d'un autre agent: il verifie agent et surface avant
nettoyage.

Les commandes SEL read-only de maintenance (`explain`, `related`, `stats`, `doctor`)
ne doivent pas etre bloquees par le lock. Pour le retrieval agent, ne pas appeler
`scribe context` ni `scribe query` directement; utiliser `scribe-rag preflight`, puis
`scribe-rag query/explain/challenge` selon le besoin.

## Separation Graphify / SCRIBE

- Graphify = structure du code: quoi, ou, comment.
- SCRIBE = causalite: pourquoi, douleur, decision, cicatrice.

Ne pas ecrire dans SCRIBE ce que Graphify peut deduire du code. Ecrire un
SCAR ou un GHOST seulement si l'information evitera une vraie souffrance au
prochain agent.

## Hygiene Git / push

- Scope par defaut: le code produit du projet hote.
- `AGENT-MEMOIRE_PROJECT_STATUS.scribe`: a versionner seulement si l'equipe
  veut partager la memoire causale entre agents et humains.
- `.agent/state/outputs/graphify-out/`: ne pas versionner par defaut; c'est un graphe genere,
  reconstructible avec `graphify update .`.
- `.agent/state/outputs/scribe-out/`: ne pas versionner par defaut; c'est de l'etat runtime local
  (index, locks, rapports, dashboards, exports, sync metadata).
- `.agent/`: a versionner seulement quand l'equipe maintient volontairement
  l'outillage agentique; sinon le garder hors des commits produit.

Avant livraison, utiliser `{BUNDLE_COMMAND} worktree` pour separer source
reelle, memoire causale validee, tooling volontaire et bruit genere. Quand le bundle
SCRIBE/RAG change, lancer aussi `{RAG_COMMAND} gate`; le gate doit rester a 8/8
pour CI/pre-commit.

## Intention finale obligatoire

Avant de fermer une vraie session de coding, poser cette question:

> "Qu'est-ce qui fera souffrir le prochain LLM si je ne le documente pas ?"

Si la reponse est une douleur concrete, la graver en SCAR ou GHOST. Sinon, le
JOURNAL suffit.
"""

def render_scribe_adapter() -> str:
    return '#!/usr/bin/env python3\nfrom __future__ import annotations\n\nimport runpy\nimport sys\nfrom pathlib import Path\n\n\nsys.dont_write_bytecode = True\n\n\nMEMORY_COMMANDS = {\n    "hot",\n    "context",\n    "stats",\n    "explain",\n    "related",\n    "query",\n    "challenge",\n    "eval",\n    "compact",\n    "review-hot",\n    "promote",\n    "export",\n    "archive",\n    "dashboard",\n}\n\n\ndef main() -> int:\n    root = Path(__file__).resolve().parent\n    scripts_dir = root / ".agent" / "workflow" / "scribe" / "sel" / "scripts"\n    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:\n        print("Usage:")\n        print("  ./scribe doctor [SCRIBE_PATH] [--output REPORT] [--suggest-fix]")\n        print("  ./scribe guard [SCRIBE_PATH] -- <command> [args...]")\n        print("  ./scribe install [TARGET_PATH] [--force] [--dry-run]")\n        print("  ./scribe bootstrap [--root PATH]")\n        print("  ./scribe tenor-init [--root PATH] [--agent NAME] [--type cli|extension|api|unknown]")\n        print("  ./scribe clean --dry-run|--apply [--graphify] [--agent-cache]")\n        print("  ./scribe lock acquire|release|status")\n        print("  ./scribe sync|whoami")\n        print("  ./scribe workflow read|check|status")\n        print("  ./scribe hot|context|stats|explain|related|query|challenge|eval|compact|review-hot|promote|export|archive|dashboard")\n        print("  ./scribe graph [--build] [--query TEXT] [--budget N]")\n        print("  ./scribe graphify-hooks [--apply] [--template PATH] [--trusted-hooks PATH]")\n        print("  ./scribe benchmark [--entities 1000,10000] [--queries N] [--json]")\n        print("  ./scribe worktree [--strict]")\n        return 0\n\n    command = sys.argv.pop(1)\n    scripts = {\n        "doctor": "scribe_doctor.py",\n        "guard": "scribe_guard.py",\n        "install": "scribe_install.py",\n        "bootstrap": "scribe_bootstrap.py",\n        "tenor-init": "scribe_tenor_init.py",\n        "clean": "scribe_clean.py",\n        "lock": "scribe_lock.py",\n        "sync": "scribe_state.py",\n        "whoami": "scribe_state.py",\n        "workflow": "scribe_state.py",\n        "graph": "scribe_bundle_graph.py",\n        "worktree": "scribe_worktree.py",\n        "benchmark": "scribe_benchmark.py",\n        "graphify-hooks": "scribe_graphify_hooks.py",\n    }\n    for memory_command in MEMORY_COMMANDS:\n        scripts[memory_command] = "scribe_memory.py"\n    script = scripts.get(command)\n    if script is None:\n        print(f"Unknown scribe command: {command}", file=sys.stderr)\n        print(\n            "Available commands: doctor, guard, install, bootstrap, tenor-init, clean, hot, context, stats, explain, related, query, "\n            "challenge, eval, compact, review-hot, promote, export, archive, dashboard, lock, sync, whoami, workflow, graph, graphify-hooks, benchmark, worktree",\n            file=sys.stderr,\n        )\n        return 2\n\n    if command in MEMORY_COMMANDS:\n        sys.argv.insert(1, command)\n    if command in {"sync", "whoami", "workflow"}:\n        sys.argv.insert(1, command)\n    sys.path.insert(0, str(scripts_dir))\n    runpy.run_path(str(scripts_dir / script), run_name="__main__")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'

def render_shim_helper() -> str:
    return '''from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SCRIPTS_DIR = ROOT / ".agent" / "workflow" / "scribe" / "sel" / "scripts"


def ensure_canonical_path() -> None:
    scripts_path = str(CANONICAL_SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)


def load_canonical_module(module_name: str) -> ModuleType:
    ensure_canonical_path()
    module_path = CANONICAL_SCRIPTS_DIR / f"{module_name}.py"
    if not module_path.exists():
        raise ModuleNotFoundError(f"Cannot find SCRIBE bundle module: {module_path}")

    private_name = f"_scribe_bundle_{module_name}"
    cached = sys.modules.get(private_name)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(private_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load SCRIBE bundle module: {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[private_name] = module
    spec.loader.exec_module(module)
    return module


def export_canonical(namespace: dict[str, Any], module_name: str) -> None:
    module = load_canonical_module(module_name)
    public_names = [name for name in vars(module) if not name.startswith("_")]
    namespace["__doc__"] = module.__doc__
    namespace["__all__"] = public_names
    for name in public_names:
        namespace[name] = getattr(module, name)


def run_canonical_script(module_name: str) -> None:
    ensure_canonical_path()
    runpy.run_path(str(CANONICAL_SCRIPTS_DIR / f"{module_name}.py"), run_name="__main__")
'''


def render_module_shim(module_name: str, cli_modules: set[str]) -> str:
    if module_name in cli_modules:
        return f'''#!/usr/bin/env python3
from __future__ import annotations

from _bundle_shim import export_canonical, run_canonical_script


export_canonical(globals(), "{module_name}")


if __name__ == "__main__":
    run_canonical_script("{module_name}")
'''
    return f'''from __future__ import annotations

from _bundle_shim import export_canonical


export_canonical(globals(), "{module_name}")
'''


def render_scripts_init() -> str:
    return '"""Compatibility shims for the canonical SCRIBE engineering bundle."""\n'


def render_agents_block() -> str:
    return f"""{AGENTS_START}
## SCRIBE/TENOR Local Causal Retrieval Bundle

Bundle root: `{BUNDLE_RELATIVE_PATH}/`

Canonical commands:
- Maintenance/write engine: `{BUNDLE_COMMAND}`
- Agent read interface: `{RAG_COMMAND}`
- Local rules: `{SEL_RELATIVE_PATH}/docs/AGENTS.md`
- Always-on summary: `{SCRIBE_RULE_PATH}`
- Full protocol: `{SEL_RELATIVE_PATH}/docs/scribe.md`
- Multi-agent install: `{SEL_RELATIVE_PATH}/docs/multi-agent-installation.md`
- Friction policy: `{SEL_RELATIVE_PATH}/docs/friction-policy.md`

Current stable baseline (2026-06-01): SEL `81 OK`, RAG `25 OK`, gate/eval
`8/8`, doctor `0 error` with only cosmetic `W009`. STOP `.agent`: use SCRIBE as
memory and guardrail, then return to product work unless a real SCRIBE bug appears.

## PRÉFLIGHT (copier-coller direct)

Mode NANO (< 30 min, 1 file):

```bash
{RAG_COMMAND} context
```

Mode STANDARD (> 30 min):

```bash
{RAG_COMMAND} build
{RAG_COMMAND} context
{RAG_COMMAND} challenge "<plan>"
```

Mode CRITICAL or SCRIBE/shared-surface mutation:

```bash
{BUNDLE_COMMAND} workflow read --agent <name> --type <extension|cli|api|unknown>
{BUNDLE_COMMAND} workflow check --agent <name>
{RAG_COMMAND} preflight --tier CRITICAL --strict "<plan>"
```

## Rules

- If the user sends `TENOR INIT::[.agent/skills/init-tenor/SKILL.md]`, read that exact project file first, before global OpenCode/Codex/Gemini configs, Graphify, README, or SCRIBE. Then run `{BUNDLE_COMMAND} tenor-init --type <extension|cli|api|unknown>` and paste its MACHINE PROOF. If `tenor-init` is unavailable, fall back to `{BUNDLE_COMMAND} bootstrap`; bootstrap remains mandatory and idempotent for every TENOR INIT.
- Never read `AGENT-MEMOIRE_PROJECT_STATUS.scribe` directly during init; use `{RAG_COMMAND} context` and `{RAG_COMMAND} query`.
- Do not claim YAML validity, session counts, SCAR counts, debts, or hot entries without showing real command output.
- Use `scribe-rag` for retrieval: `preflight`, `context`, `query`, `explain`, `challenge`, `eval`, `gate`, `whoami`.
- Do not use SEL direct retrieval (`scribe context`, `scribe query`, `scribe explain`) for normal agent work.
- Read `.agent/state/outputs/graphify-out/GRAPH_REPORT.md` before architecture or codebase work when it exists.
- If SCRIBE memory or shared surfaces are mutated, run workflow ack/check, doctor, lock acquire, sync, and lock release through `{BUNDLE_COMMAND}`.
- Default commit/push scope is the host product source; keep `.agent/state/outputs/graphify-out/` and `.agent/state/outputs/scribe-out/` out of commits by default; version `.agent/` only when intentionally maintaining agent tooling.
- Use `{RAG_COMMAND} gate` for bundle changes; it must stay green at 8/8.
- Real pain capture is mandatory: bug >2 attempts, regression, costly rollback, or broken browser/visual smoke => SCAR with `cause_racine`, `resolution`, `test_binding`; retrieve related scars with `{RAG_COMMAND} query/explain/challenge` before adjacent work.
{AGENTS_END}
"""

def render_graphify_block() -> str:
    return f"""{GRAPHIFY_START}
# Keep the canonical root graph focused on application code.
# SCRIBE/TENOR tooling is causal/process infrastructure, not app architecture.
.agent/
.codex/
.vscode/
scribe-out/
AGENT-MEMOIRE_PROJECT_STATUS.scribe
AGENTS.md
{GRAPHIFY_END}
"""
