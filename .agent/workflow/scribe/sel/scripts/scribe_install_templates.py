from __future__ import annotations

from textwrap import dedent

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
TENOR_TRIGGER = "TENOR INIT::[.agent/skills/init-tenor/SKILL.md]"
TENOR_SKILL_PATH = ".agent/skills/init-tenor/SKILL.md"
TENOR_RULE_PATH = ".agent/rules/tenor-init-v2.json"
DOC_SYNC_PATH = ".agent/docs/DOCUMENTATION_SYNC_POLICY.md"


def _text(value: str) -> str:
    return dedent(value).lstrip("\n")


def render_scribe_rule() -> str:
    return _text(
        f"""
        ---
        trigger: always_on
        ---

        # SCRIBE/TENOR — règle always-on V2.16

        `.agent` est la couche d'exploitation portable des agents LLM. TENOR
        décide l'ordre sûr, Graphify compresse le contexte structurel, SCRIBE
        fournit la mémoire causale et le runtime coordonne les agents actifs.

        ## Entrée canonique

        Déclencheur humain/LLM :

        ```text
        {TENOR_TRIGGER}
        ```

        Commande mécanique depuis la racine du projet :

        ```bash
        {BUNDLE_COMMAND} tenor-init --type <cli|extension|api|unknown>
        ```

        `tenor-init` est l'unique autorité publique d'installation, relocation et
        reprise. `bootstrap` est interne/legacy et ne doit pas être présenté comme
        le démarrage normal V2.16.

        ## Autorité

        Ordre obligatoire : résoudre le root, classifier l'installation, purger
        seulement l'état ancien prouvé, adopter/créer SCRIBE, vérifier Graphify,
        finaliser localement, vérifier le MCP local, prouver la visibilité host,
        prouver le root binding, bridger la session, puis seulement
        `TENOR_INIT_READY`.

        `server_entry.py --list-tools` ne prouve jamais la visibilité des tools
        dans le host LLM.

        ## Mémoire et graphe

        - Lecture agent : `{RAG_COMMAND}` ou MCP `scribe_query`.
        - Maintenance : `{BUNDLE_COMMAND}`.
        - Skill init : `{TENOR_SKILL_PATH}`.
        - Règles machine : `{TENOR_RULE_PATH}`.
        - Protocole complet : `{SEL_RELATIVE_PATH}/docs/scribe.md`.
        - Coordination : `{SEL_RELATIVE_PATH}/docs/live-coordination.md`.

        Graphify répond à « quoi, où, comment, dépendances et blast radius ».
        SCRIBE répond à « pourquoi, quelle douleur, quelle décision et que ne faut-il
        pas répéter ». Le graphe réel accepte `nodes + links`; le format historique
        supporté est `nodes + edges`.

        Une requête SCRIBE n'est pas une checkbox : les résultats doivent modifier
        le plan ou être explicitement contestés.

        ## Multi-agent et writes

        Chaque terminal obtient une session, un proof et des leases distincts. Le
        bootstrap commun est sérialisé ; `SAME_PROJECT` ne purge jamais la
        coordination active.

        Toute mutation exige `pre_action_guard`, lock, claim, action lease, hash,
        patch queue, workspace audit, libération et `finish_task`. Les writes natifs
        shell/edit/write/apply-patch hors MCP sont interdits.

        ## Documentation

        Toute évolution synchronise code, tests, surfaces canoniques, générateurs et
        PR selon `{DOC_SYNC_PATH}`. Les anciens baselines datés sont historiques.
        """
    )


def render_scribe_adapter() -> str:
    return _text(
        '''
        #!/usr/bin/env python3
        from __future__ import annotations

        import runpy
        import sys
        from pathlib import Path

        sys.dont_write_bytecode = True

        MEMORY_COMMANDS = {
            "hot", "context", "stats", "explain", "related", "query",
            "challenge", "eval", "compact", "review-hot", "promote",
            "export", "archive", "dashboard",
        }


        def main() -> int:
            root = Path(__file__).resolve().parent
            scripts_dir = root / ".agent" / "workflow" / "scribe" / "sel" / "scripts"
            if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
                print("Usage:")
                print("  scribe tenor-init [--root PATH] [--agent NAME] [--type cli|extension|api|unknown]")
                print("  scribe bootstrap [--root PATH]  # internal/legacy primitive")
                print("  scribe doctor|guard|install|clean|lock|sync|whoami|workflow|coordination")
                print("  scribe hot|context|stats|explain|related|query|challenge|eval|compact|review-hot|promote|export|archive|dashboard")
                print("  scribe graph|graphify-hooks|benchmark|worktree")
                return 0

            command = sys.argv.pop(1)
            scripts = {
                "doctor": "scribe_doctor.py",
                "guard": "scribe_guard.py",
                "install": "scribe_install.py",
                "bootstrap": "scribe_bootstrap.py",
                "tenor-init": "scribe_tenor_init_v216.py",
                "clean": "scribe_clean.py",
                "lock": "scribe_lock.py",
                "sync": "scribe_state.py",
                "whoami": "scribe_state.py",
                "workflow": "scribe_state.py",
                "coordination": "scribe_coordination.py",
                "coord": "scribe_coordination.py",
                "graph": "scribe_bundle_graph.py",
                "worktree": "scribe_worktree.py",
                "benchmark": "scribe_benchmark.py",
                "graphify-hooks": "scribe_graphify_hooks.py",
            }
            for memory_command in MEMORY_COMMANDS:
                scripts[memory_command] = "scribe_memory.py"
            script = scripts.get(command)
            if script is None:
                print(f"Unknown scribe command: {command}", file=sys.stderr)
                return 2
            if command in MEMORY_COMMANDS:
                sys.argv.insert(1, command)
            if command in {"sync", "whoami", "workflow"}:
                sys.argv.insert(1, command)
            sys.path.insert(0, str(scripts_dir))
            runpy.run_path(str(scripts_dir / script), run_name="__main__")
            return 0


        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def render_shim_helper() -> str:
    return _text(
        '''
        from __future__ import annotations

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
            if not module_path.is_file():
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
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(private_name, None)
                raise
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
    )


def render_module_shim(module_name: str, cli_modules: set[str]) -> str:
    if not module_name or any(part in module_name for part in ("/", "\\", "..")):
        raise ValueError(f"invalid module name: {module_name!r}")
    if module_name in cli_modules:
        return _text(
            f'''
            #!/usr/bin/env python3
            from __future__ import annotations

            from _bundle_shim import export_canonical, run_canonical_script

            export_canonical(globals(), "{module_name}")

            if __name__ == "__main__":
                run_canonical_script("{module_name}")
            '''
        )
    return _text(
        f'''
        from __future__ import annotations

        from _bundle_shim import export_canonical

        export_canonical(globals(), "{module_name}")
        '''
    )


def render_scripts_init() -> str:
    return '"""Compatibility shims for the canonical SCRIBE engineering bundle."""\n'


def render_agents_block() -> str:
    return _text(
        f"""
        {AGENTS_START}
        ## AGENT-SCRIBE-GRAPHIFY — V2.16 operating layer

        Canonical human/LLM trigger:

        ```text
        {TENOR_TRIGGER}
        ```

        Canonical commands:
        - Init: `{BUNDLE_COMMAND} tenor-init --type <cli|extension|api|unknown>`
        - Maintenance/write engine: `{BUNDLE_COMMAND}`
        - Causal retrieval: `{RAG_COMMAND}`
        - Always-on rule: `{SCRIBE_RULE_PATH}`
        - Skill: `{TENOR_SKILL_PATH}`
        - Machine contract: `{TENOR_RULE_PATH}`
        - Full protocol: `{SEL_RELATIVE_PATH}/docs/scribe.md`
        - Multi-agent contract: `{SEL_RELATIVE_PATH}/docs/multi-agent-installation.md`

        Rules:
        - TENOR INIT is the only public installation/relocation/recovery authority.
        - `bootstrap` is internal/legacy, never the normal V2.16 start.
        - Local MCP `--list-tools` is not host visibility proof.
        - Do not work before host tools, root binding and bridge produce `TENOR_INIT_READY`.
        - SCRIBE retrieval must influence the plan; Graphify is used for structure/blast radius.
        - Graphify supports explicit `nodes + links` and historical `nodes + edges` only.
        - Every write uses guard, lease, lock, claim, hash, patch queue, audit and finish.
        - Native direct shell/edit/write paths outside MCP are forbidden.
        - A prose-only “done” without terminal MCP proof is not completion.
        - Generated outputs stay under `.agent/state/outputs/` and out of product commits by default.
        - Documentation and generators move together under `{DOC_SYNC_PATH}`.
        {AGENTS_END}
        """
    )


def render_graphify_block() -> str:
    return _text(
        f"""
        {GRAPHIFY_START}
        # Keep the application graph focused on product code.
        .agent/
        .agents/
        .codex/
        .vscode/
        scribe-out/
        graphify-out/
        AGENT-MEMOIRE_PROJECT_STATUS.scribe
        AGENTS.md
        {GRAPHIFY_END}
        """
    )
