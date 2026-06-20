# 🧠 agent-scribe-graphify — V2

<p align="center">
  <img src="https://img.shields.io/badge/branch-v2-blue" alt="Branch v2">
  <img src="https://img.shields.io/badge/MCP-v0.2.1-purple" alt="MCP v0.2.1">
  <img src="https://img.shields.io/badge/status-smoke%20tested-brightgreen" alt="Smoke tested">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/runtime-SQLite%20WAL-success" alt="SQLite WAL">
  <img src="https://img.shields.io/badge/portable-.agent%20only-success" alt="Portable .agent only">
</p>

> **Branche V2 — socle MCP local portable pour orchestration multi-agent.**
>
> Cette branche n'est pas le README historique de `main`. Elle documente la version `v2`, centrée sur le serveur MCP local, la coordination SQLite, la patch queue et la portabilité réelle du dossier `.agent/`.

---

## ✅ Statut V2

La V2 est validée localement par le smoke-test intégré :

```bash
python3 .agent/scripts/mcp_smoke.py
```

Résultat attendu :

```text
MCP_SMOKE_ALL_OK
```

Ce test couvre :

```text
✅ bootstrap MCP réel
✅ JSON-RPC stdio
✅ workflow nominal complet
✅ claim_resource
✅ propose_patch
✅ list_patches
✅ finish_task refusé si patch pending
✅ reject_patch
✅ release_claim
✅ finish_task OK
✅ refus ../ traversal
✅ refus chemins absolus Linux
✅ refus chemins absolus Windows C:\ et C:/
✅ refus UNC \\server\share
✅ refus symlink escape
✅ symlink interne accepté
✅ propose_patch sans claim refusé
✅ copie portable de .agent dans un projet temporaire avec espaces
✅ runtime isolé dans .agent/state/runtime
```

---

## 🎯 Objectif de la V2

La V2 transforme `.agent/` en **tour de contrôle locale multi-agent**.

Le principe :

```text
.agent/ = contrôle aérien local multi-agent
MCP     = canal mécanique commun
SCRIBE  = mémoire longue durée
Graphify = carte structurelle
SQLite  = coordination courte durée
Patch queue = sécurité multi-agent sur mêmes fichiers
TENOR INIT = porte d'entrée obligatoire
```

Le dossier `.agent/` doit pouvoir être copié dans n'importe quel projet et recalculer automatiquement son nouveau project root.

---

## 🧱 Architecture V2

```text
.agent/
├── mcp/
│   ├── server.py              # serveur MCP JSON-RPC stdio
│   ├── server_entry.py        # entrée portable indépendante du cwd
│   ├── install/               # doctor + catalogue hosts MCP
│   └── runtime/               # code source runtime MCP
│       ├── db.py              # coordination agents/claims/events
│       ├── patch_queue.py     # file_hash/propose/list/reject/confirm
│       └── state_paths.py     # chemins portables + migration state
│
├── rules/                     # règles always-on
├── scripts/
│   └── mcp_smoke.py           # smoke-test complet V2
├── skills/
│   └── init-tenor/            # porte d'entrée obligatoire
├── workflow/                  # workflows SCRIBE / Graphify / MCP
└── state/                     # état local généré, non versionné
    ├── runtime/
    │   └── coordination.sqlite
    ├── scribe-out/
    └── graphify-out/
```

Important :

```text
.agent/mcp/runtime/ = code source à conserver
.agent/state/runtime/ = état généré local
```

Ne pas confondre les deux.

---

## ⚡ Quick start

Depuis la branche `v2` :

```bash
git checkout v2
python3 .agent/scripts/mcp_smoke.py
```

Si tout va bien :

```text
MCP_SMOKE_ALL_OK
```

---

## 📦 Utiliser `.agent/` dans un autre projet

Copier uniquement `.agent/` dans le nouveau projet :

```bash
cp -a /chemin/agent-scribe-graphify/.agent /chemin/nouveau-projet/.agent
cd /chemin/nouveau-projet
python3 .agent/scripts/mcp_smoke.py
```

Le serveur doit créer son état ici :

```text
/chemin/nouveau-projet/.agent/state/runtime/coordination.sqlite
```

et non dans le dépôt d'origine.

---

## 🔌 Serveur MCP

Entrée recommandée :

```bash
python3 .agent/mcp/server_entry.py
```

Pourquoi `server_entry.py` ?

```text
✅ fonctionne même si le host MCP lance le serveur depuis un autre cwd
✅ recalcule PROJECT_ROOT à partir de l'emplacement réel de .agent/
✅ évite les chemins absolus codés en dur
✅ rend .agent portable par copie
```

Exemple d'appel direct :

```bash
python3 .agent/mcp/server_entry.py --call bootstrap --args '{"host_tool":"manual-test","model_name":"test","run_legacy_bootstrap":false}'
```

---

## 🛡️ Sécurité chemins

La V2 refuse les ressources dangereuses :

```text
../outside.txt
/etc/passwd
C:\Windows\win.ini
C:/Windows/win.ini
\\server\share\secret.txt
symlink -> /etc/passwd
symlink-dir -> /tmp
```

Les chemins acceptés doivent être project-relative et sûrs :

```text
src/app.py
README.md
.agent/rules/scribe.md
```

---

## 🔁 Workflow obligatoire agent

Un agent ne doit pas écrire directement sans coordination.

Workflow attendu :

```text
bootstrap
→ register_agent
→ before_task
→ claim_resource / before_edit
→ file_hash
→ propose_patch ou édition contrôlée
→ list_patches
→ confirm_patch_applied ou reject_patch
→ release_claim
→ finish_task
```

Règles fortes :

```text
✅ lecture libre
✅ écriture = claim compatible obligatoire
✅ même fichier sous claim étranger = édition directe interdite
✅ patch queue obligatoire en conflit
✅ base_hash obligatoire
✅ finish_task refuse si patch pending/conflit
```

---

## 🧪 Commandes de validation

Smoke complet :

```bash
python3 .agent/scripts/mcp_smoke.py
```

Vérifier que le dépôt reste propre :

```bash
git status --short
```

Tester manuellement le bootstrap :

```bash
python3 .agent/mcp/server_entry.py --call bootstrap --args '{"host_tool":"manual","model_name":"test","run_legacy_bootstrap":false}'
```

Tester un hash de fichier :

```bash
python3 .agent/mcp/server_entry.py --call file_hash --args '{"resource":"README.md"}'
```

---

## 🧭 Différence avec `main`

```text
main = README historique / bundle initial
v2   = socle MCP portable validé par smoke-test
```

Sur GitHub, le README affiché dépend de la branche sélectionnée.

Donc :

```text
branche main → README de main
branche v2   → README V2
```

---

## 📌 État actuel

```text
V2 feature-complete pour le socle MCP local portable.
Validée par smoke-test local complet.
Prête pour documentation d'installation, packaging et tests avec hosts réels.
```

---

## 📄 Licence

MIT.
