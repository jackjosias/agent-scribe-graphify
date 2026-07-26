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
TENOR_TRIGGER = 'TENOR INIT ::[— depuis la racine du workspace courant, lis comme un fichier local avec l’outil normal de lecture de fichiers — jamais avec un résolveur de skills — le chemin exact "./.agent/skills/init-tenor/SKILL.md"; n’utilise jamais "~/.agent", "~/.agents" ni aucun chemin global; applique ensuite intégralement ce fichier et continue automatiquement jusqu’à TENOR_INIT_READY, HOST_RECONNECT_REQUIRED ou un verdict FAIL_CLOSED explicite.]'
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

        # SCRIBE/TENOR — RÈGLE ALWAYS-ON V2.16

        Ce fichier est la règle courte destinée aux LLM hôtes. Il ne remplace pas le skill d'initialisation ni le document d'autorité.

        ## Démarrage canonique

        Déclencheur humain/LLM :

        ```text
        {TENOR_TRIGGER}
        ```

        Commande mécanique depuis la racine du projet :

        ```bash
        {BUNDLE_COMMAND} tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
        ```

        Sous Windows :

        ```powershell
        py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
        ```

        `tenor-init` est l'unique autorité publique d'installation, de relocation et de reprise. `bootstrap` est une primitive interne/legacy et ne doit jamais remplacer TENOR INIT dans un bundle V2.16.

        ## Autorité et ordre

        TENOR INIT doit :

        1. résoudre le root courant ;
        2. classifier l'installation avant SCRIBE ;
        3. purger uniquement l'état copié lié à un ancien root quand la relocation est prouvée ;
        4. adopter ou créer la mémoire SCRIBE de destination ;
        5. provisionner si nécessaire le runtime Graphify project-local épinglé et vérifié, puis exécuter le build borné ;
        6. finaliser le manifest local ;
        7. détecter et configurer uniquement le host project-local vérifié ;
        8. exiger une reconnexion puis une nouvelle init si la configuration change ;
        9. vérifier le serveur MCP local ;
        10. vérifier la visibilité réelle des tools dans le host ;
        11. prouver le root binding ;
        12. bridger la session indépendante avec la preuve serveur one-shot ;
        13. produire `TENOR_INIT_READY`.

        Tant que les tools ne sont pas visibles dans le host réel, le verdict maximal est :

        ```text
        LOCAL_INIT_READY_HOST_MCP_UNBOUND
        ```

        `python3 .agent/mcp/server_entry.py --list-tools` ou un JSON-RPC lancé depuis un shell ne prouve jamais la visibilité host.

        ## Graphify et SCRIBE

        - Graphify = structure : quoi, où, comment, dépendances, centralité, communautés, blast radius.
        - SCRIBE = causalité : pourquoi, douleur, décision, régression, SCAR, GHOST, dette, `ne_pas_reproposer`.
        - Les outputs Graphify canoniques vivent sous `.agent/state/outputs/graphify-out/`.
        - L'absence d'une commande `graphify` globale n'est pas un prérequis utilisateur : TENOR installe atomiquement `graphifyy==0.9.26` sous `.agent/state/runtime/toolchains/`, après vérification SHA-256, puis contrôle son intégrité.
        - Le graphe réel peut exposer `nodes + links` ; le format historique supporté est `nodes + edges`.
        - Un graphe manquant, vide à tort, stub, wrong-root, stale ou contradictoire bloque les writes.
        - Les agents lisent la mémoire via `{RAG_COMMAND}` ou MCP `scribe_query`, jamais en parcourant directement le fichier `.scribe`.
        - Une requête mémoire doit modifier le plan ou produire une contradiction explicitement auditée.
        - Protocole complet : `{SEL_RELATIVE_PATH}/docs/scribe.md`.

        ## Workflow par tâche

        Avant toute mutation produit :

        ```text
        tenor_task_start(objective, intent, resources, scope)
          -> SCRIBE cible + Graphify cible, executes en interne
          -> capsule decisionnelle liee aux preuves et ressources
        tenor_apply_changeset(task_id, changes[], validators[])
          -> preflight complet + locks ordonnes + commit atomique ou rollback total
          -> validateurs obligatoires + admission memoire + cloture terminale
        ```

        Les mutations texte utilisent `operation=edit` avec des ancres exactes. `replace` signifie le fichier complet et une reduction destructive exige la confirmation chemin/hash avant/hash apres. `create` derive le sentinel nouveau fichier en interne. Une erreur recuperable reste dans le meme task id ; une tache non commitee peut etre annulee sans changeset factice. Aucun fallback ne demande a l'utilisateur d'appliquer un patch manuel.

        Chaque tache terminee persiste exactement un verdict memoire : promotion canonique, runtime-only motive, decision utilisateur requise ou conflit. Une capsule stale apres une ecriture concurrente est rafraichie par le meme `tenor_task_start`, sans tache ni identite de remplacement.

        Les writes directs via shell, redirection, `tee`, `sed -i`, `cp`, `mv`, `rm`, outil natif edit/write/apply-patch ou équivalent sont interdits hors MCP.

        L'API normale de tâche contient seulement `tenor_task_start`,
        `tenor_apply_changeset`, `tenor_activity` et `tenor_task_control`. Les anciens
        outils fins restent internes pour compatibilité ; le LLM hôte ne doit jamais
        reconstruire manuellement leur chorégraphie.

        ## Multi-agent

        - Chaque terminal exécute son propre TENOR INIT et reçoit une session distincte.
        - Le bootstrap partagé est sérialisé.
        - `TENOR_INIT_SAME_PROJECT` ne purge jamais la coordination active.
        - Les agents partagent runtime SQLite, SCRIBE et Graphify.
        - L'identité est liée au processus MCP après le bridge ; les appels de tâche n'acceptent ni `agent_id` ni token fourni par le LLM.
        - Un agent ne peut ni retirer ni contrôler la tâche d'un autre agent.
        - Un heartbeat daemon et un TTL roulant maintiennent l'activité réelle sans masquer un processus mort.
        - Toute clôture laisse zéro transaction ou lock en attente.

        ## Mémoire causale

        Avant de fermer une vraie session, poser :

        > Qu'est-ce qui fera souffrir le prochain LLM si je ne le documente pas ?

        Une douleur, cause racine, régression ou approche rejetée durable devient SCAR/GHOST/PAT selon le cas. Une activité sans valeur causale ne doit pas être promue artificiellement.

        ## Hygiène et documentation

        - Les outputs runtime et Graphify générés restent hors des commits produit par défaut.
        - `.agent/` n'est versionné que lors d'une maintenance intentionnelle de l'outillage.
        - Toute évolution d'architecture doit synchroniser les surfaces listées dans `{DOC_SYNC_PATH}` et leurs générateurs.
        - Les anciens baselines datés, fichiers `.old` et exemples pré-V2.16 sont historiques, jamais normatifs.

        ## Invariant SAME_PROJECT (V2.16.1)

        Sur `TENOR_INIT_SAME_PROJECT`, `bootstrap_project()` n'appelle jamais l'installateur forcé et ne réécrit aucun fichier suivi du bundle. La dérive est signalée en warning ; la réparation reste explicite (`scribe install --force`). TENOR peut uniquement gérer l'entrée MCP project-local vérifiée et son reçu de binding. `NEW_INSTALLATION` / `RELOCATED_PROJECT` / `LEGACY_INSTALLATION` conservent l'installation du bundle.

        ## Invariant purge/migration sans perte (V2.16.2)

        Une purge de runtime conserve `.agent/state/outputs/` byte-for-byte. Elle réinitialise seulement les états projet-liés (runtime, proofs, locks, sessions, agents, redteam, backups et manifest). Les outputs Graphify préservés doivent encore réussir la validation root/fingerprint avant readiness. Lors d'un conflit de migration, la destination canonique gagne et la donnée legacy est placée sous `_legacy_migrated/`.
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
                print("  scribe tenor-init [--root PATH] [--agent NAME] [--type cli|extension|api|unknown] [--host HOST|auto]")
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
        ## AGENT-SCRIBE-GRAPHIFY — V2.16 CANONICAL OPERATING CONTRACT

        ### Canonical session entry

        Human/LLM trigger:

        ```text
        {TENOR_TRIGGER}
        ```

        Mechanical command from the current project root:

        ```bash
        {BUNDLE_COMMAND} tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
        ```

        The project-local `{TENOR_SKILL_PATH}` and `{TENOR_RULE_PATH}` are authoritative. `bootstrap` is an internal/legacy primitive, not the public V2.16 installation authority.

        ### Authority order

        ```text
        resolve root
        classify installation
        purge only old project-bound runtime when relocation is proven
        preserve canonical outputs and quarantine legacy conflicts
        adopt/create target SCRIBE
        provision/verify the pinned project-local Graphify runtime when required
        build and bind Graphify
        finalize local installation
        detect/configure the verified project-local host
        reconnect and rerun if host configuration changed
        verify local MCP
        verify tools visible in the real host
        prove MCP root binding
        bridge the independent session
        TENOR_INIT_READY
        ```

        ### Hard rules

        - Never start product work before `TENOR_INIT_READY`.
        - `server_entry.py --list-tools` and shell JSON-RPC prove only local MCP readiness, never host visibility.
        - Never read `AGENT-MEMOIRE_PROJECT_STATUS.scribe` directly for normal agent retrieval; use `{RAG_COMMAND}` or MCP `scribe_query`.
        - SCRIBE results must change the plan or be explicitly challenged; retrieval is not a checkbox.
        - Use Graphify before architecture or broad code changes; prefer targeted structure/blast-radius queries over mass file reads.
        - A missing global `graphify` command is not a user prerequisite. TENOR provisions the pinned and SHA-256-verified project-local `graphifyy==0.9.26` runtime under `.agent/state/runtime/toolchains/`, then verifies its integrity before every cold-process use.
        - The public task surface is exactly `tenor_task_start`, `tenor_apply_changeset`, `tenor_activity`, `tenor_task_control`; bootstrap retains the five bounded init tools.
        - `tenor_task_start` performs targeted SCRIBE and Graphify retrieval server-side and returns a hash-bound decision capsule. The host model must not replay the legacy internal choreography.
        - Every mutation is submitted as one atomic multi-file `tenor_apply_changeset` with exact structured edits, fresh hashes and mandatory bounded validator argv arrays; TENOR owns locks, conditional non-destructive rollback, SCRIBE admission and closure.
        - `TENOR_CHANGESET_ACCEPTED` and `GRAPHIFY_BUILD_ACCEPTED` are durable non-terminal acknowledgements. Poll `tenor_activity` or `graphify_required_check`; only the terminal job result proves commit, rollback or build completion.
        - Long validators and Graphify builds run in bounded isolated workers so the MCP stdio loop remains available. Never resubmit an active job or control its task concurrently.
        - A worker is authoritative only while its SQLite lease is live and its exact `(job_id, worker_instance_id, fence_token)` matches. PIDs are diagnostic only; recovery transfers a monotone fence and an older worker may not heartbeat, publish, rollback or release locks.
        - Rollback preflights every current hash and restores only bytes still equal to the changeset's declared `new_hash`. A later writer or validator drift produces `TENOR_CHANGESET_ROLLBACK_CONFLICT` and its evidence is preserved.
        - Graphify freshness uses `relative-path-size-content-sha256-v2`; `mtime` never changes graph identity. A rebuild is single-flight, waits for active changesets and fences new writers until the content-bound graph is current.
        - Canonical SCRIBE promotion publishes the entry, deterministic digest proof, tripwire receipt and task-context flag transactionally. Every promoted summary is copied to `l0_abstract` and must be semantically retrievable.
        - `replace` means complete file content. Destructive shrink requires path/base/new-hash confirmation; fragments and manual-patch fallback are forbidden.
        - Every completed task has an explicit memory-admission verdict. Durable validated source knowledge is promoted; non-causal noise is retained runtime-only with a reason.
        - Native shell/edit/write/apply-patch paths outside MCP are forbidden for project mutation.
        - A prose-only “done” without a terminal machine verdict, decision capsule, validator evidence and memory admission is not completion.
        - Each terminal uses its own process-bound identity and server-side one-time proof. Task calls never accept caller-supplied `agent_id` or context tokens.
        - `TENOR_INIT_SAME_PROJECT` never repairs the bundle; only the verified project-local MCP entry and binding receipt may be managed automatically.
        - A complete raw copy of `.agent/` is a mandatory supported installation path on Linux, macOS and Windows; relocation is classified from the current root and manifest.
        - Runtime purge preserves `.agent/state/outputs/`; canonical output wins and conflicting legacy output is quarantined under `_legacy_migrated/`.
        - Preserved Graphify output is never trusted automatically; root/fingerprint readiness must pass again.
        - Graphify supports explicit `nodes + links` and historical `nodes + edges`; missing, stale, wrong-root, stub or contradictory graphs are rejected.
        - Graphify runtime installation is atomic, platform-scoped and single-flight. A policy, wheel-hash, dependency, probe or integrity mismatch fails closed and never publishes a partial runtime.
        - Default commit/push scope is the host product source; `.agent/` changes require intentional tooling maintenance.
        - Always keep `.agent/state/outputs/graphify-out/` and `.agent/state/outputs/scribe-out/` out of commits by default.
        - Documentation and generators move together under `{DOC_SYNC_PATH}`.

        ### Canonical surfaces

        - `{TENOR_SKILL_PATH}`
        - `{TENOR_RULE_PATH}`
        - `{SCRIBE_RULE_PATH}`
        - `.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md`
        - `{DOC_SYNC_PATH}`
        - `{PORTABLE_RELATIVE_PATH}/README.md`
        - `{SEL_RELATIVE_PATH}/docs/scribe.md`
        - `{SEL_RELATIVE_PATH}/docs/multi-agent-installation.md`
        - `.agent/docs/hosts/README.md`

        Historical `.old` files and dated baselines are not authoritative.
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
