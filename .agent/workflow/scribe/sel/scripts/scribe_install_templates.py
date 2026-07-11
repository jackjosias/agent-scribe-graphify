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


def _text(value: str) -> str:
    return dedent(value).lstrip("\n")


def render_scribe_rule() -> str:
    return _text(
        f"""
        ---
        trigger: always_on
        ---

        # SCRIBE/TENOR — règle always-on

        `.agent` est la couche d'exploitation portable des agents LLM. TENOR
        décide l'ordre sûr, Graphify compresse le contexte structurel, SCRIBE
        fournit la mémoire causale, et le runtime coordonne les agents actifs.

        ## Entrée canonique

        Depuis la racine du projet, toute session commence par :

        ```bash
        {BUNDLE_COMMAND} tenor-init --type <cli|extension|api|unknown>
        ```

        `tenor-init` est l'unique autorité d'installation. Un serveur MCP qui
        retourne `TENOR_INIT_REQUIRED` ne doit pas être contourné : exécuter
        l'action indiquée, puis reconnecter le host si nécessaire.

        ## Mémoire et graphe

        - Lecture agent : `{RAG_COMMAND}`.
        - Maintenance : `{BUNDLE_COMMAND}`.
        - Protocole complet : `{SEL_RELATIVE_PATH}/docs/scribe.md`.
        - Coordination : `{SEL_RELATIVE_PATH}/docs/live-coordination.md`.
        - Skill d'init : `.agent/skills/init-tenor/SKILL.md`.

        Graphify répond à « quoi, où, comment et quel blast radius ? » sans
        obliger le LLM à lire une masse de fichiers. SCRIBE répond à « pourquoi,
        quelle douleur a déjà été payée, que ne faut-il pas répéter ? ».

        Une requête SCRIBE n'est pas une checkbox : les SCAR, GHOST,
        `ne_pas_reproposer`, décisions et tests retrouvés doivent modifier le plan
        ou produire une contradiction explicitement auditée.

        ## Multi-agent

        Plusieurs terminaux peuvent lancer TENOR INIT dans le même projet. Le
        bootstrap partagé est sérialisé, puis chaque terminal reçoit une session
        distincte. `SAME_PROJECT` ne doit jamais purger la coordination active.
        Claims, resource locks et leases sont obligatoires avant write.

        ## Preuve de fin

        Une tâche non triviale n'est terminée que si les verdicts MCP terminaux
        sont présents, le workspace est audité, les claims sont libérés et la
        mémoire canonique est promue ou explicitement ignorée avec une raison
        précise et auditable.
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
                print("  scribe bootstrap [--root PATH]")
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
        ## SCRIBE/TENOR portable operating layer

        Canonical commands:
        - Init: `{BUNDLE_COMMAND} tenor-init --type <cli|extension|api|unknown>`
        - Maintenance/write engine: `{BUNDLE_COMMAND}`
        - Causal retrieval: `{RAG_COMMAND}`
        - Always-on rule: `{SCRIBE_RULE_PATH}`
        - Full protocol: `{SEL_RELATIVE_PATH}/docs/scribe.md`
        - Multi-agent contract: `{SEL_RELATIVE_PATH}/docs/multi-agent-installation.md`

        Rules:
        - TENOR INIT is the only installation/relocation authority.
        - Never treat MCP `--list-tools` as proof that the host model sees tools.
        - Never treat SCRIBE query execution as proof that its result influenced the task.
        - Read Graphify context before architectural or broad code changes.
        - Use resource locks, claims, leases and patch queue for writes.
        - A prose-only “done” without terminal MCP proof is not completion.
        - Keep generated outputs under `.agent/state/outputs/` and out of product commits.
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
