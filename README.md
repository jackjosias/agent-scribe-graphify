# 🧠 agent-scribe-graphify — V2

<p align="center">
  <img src="https://img.shields.io/badge/branch-v2-blue" alt="Branch v2">
  <img src="https://img.shields.io/badge/MCP-v0.2.2-purple" alt="MCP v0.2.2">
  <img src="https://img.shields.io/badge/status-smoke%20tested-brightgreen" alt="Smoke tested">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/runtime-SQLite%20WAL-success" alt="SQLite WAL">
  <img src="https://img.shields.io/badge/workflow-mechanical-success" alt="Mechanical workflow">
</p>

> **Branche V2 — socle MCP local portable avec workflow mécanique `workflow_next`.**
>
> Cette branche documente la version `v2`, centrée sur le serveur MCP local, la coordination SQLite, la patch queue, la portabilité réelle du dossier `.agent/` et la réduction de confiance envers les longues instructions textuelles données au LLM.

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
✅ workflow_next mécanique
✅ before_task
✅ claim_resource
✅ file_hash
✅ propose_patch
✅ list_patches
✅ finish_task refusé si patch pending
✅ reject_patch
✅ release_claim
✅ finish_task OK
✅ chemins dangereux refusés
✅ symlink escape refusé
✅ symlink interne accepté
✅ propose_patch sans claim refusé
✅ copie portable de .agent dans un projet temporaire avec espaces
✅ runtime isolé dans .agent/state/runtime
```

---

## 🎯 Principe V2

```text
.agent/       = contrôle aérien local multi-agent
MCP           = canal mécanique commun
workflow_next = chef d'orchestre obligatoire
SCRIBE        = mémoire longue durée
Graphify      = carte structurelle
SQLite WAL    = coordination courte durée
Patch queue   = sécurité multi-agent sur mêmes fichiers
TENOR INIT    = porte d'entrée obligatoire
```

Règle centrale :

```text
Le LLM hôte ne décide pas seul la prochaine étape.
Il appelle workflow_next.
Il exécute must_call.
Il rappelle workflow_next.
```

---

## 🧱 Architecture V2

```text
.agent/
├── docs/
│   ├── USAGE.md
│   └── HOST_PROMPT.md
├── mcp/
│   ├── server.py
│   ├── server_entry.py
│   ├── install/
│   └── runtime/
│       ├── db.py
│       ├── patch_queue.py
│       └── state_paths.py
├── rules/
├── scripts/
│   └── mcp_smoke.py
├── skills/
├── workflow/
└── state/
    ├── runtime/
    ├── scribe-out/
    └── graphify-out/
```

Important :

```text
.agent/mcp/runtime/   = code source à conserver
.agent/state/runtime/ = état généré local
```

---

## ⚡ Quick start

```bash
git checkout v2
git pull origin v2
python3 .agent/scripts/mcp_smoke.py
```

---

## 🔌 Entrée MCP recommandée

```bash
python3 .agent/mcp/server_entry.py
```

`server_entry.py` recalcule le project root depuis l'emplacement réel de `.agent/`. Il rend la copie portable fiable même si le host MCP lance le serveur depuis un autre dossier courant.

---

## 🧭 Tool parent obligatoire

Le tool central est :

```text
workflow_next
```

Exemple :

```bash
python3 .agent/mcp/server_entry.py --call workflow_next --args '{"request":"modifier README.md","intent":"write","resource":"README.md","host_tool":"manual","model_name":"test"}'
```

La réponse contient :

```text
must_call.tool
must_call.args
forbidden
reason
```

Le LLM hôte doit exécuter uniquement l'étape retournée, puis rappeler `workflow_next`.

---

## 🔁 Workflow mécanique attendu

```text
workflow_next
→ bootstrap
→ workflow_next
→ before_task
→ workflow_next
→ claim_resource
→ workflow_next
→ file_hash
→ workflow_next
→ propose_patch
→ workflow_next
→ list_patches si patch pending
→ reject_patch ou confirm_patch_applied
→ workflow_next
→ release_claim
→ workflow_next
→ finish_task
```

---

## 🛡️ Sécurité

La V2 refuse les ressources qui sortent du projet : traversal, chemins absolus système, chemins absolus Windows/UNC et symlinks qui pointent hors projet.

Règles d'écriture :

```text
✅ écriture = claim obligatoire
✅ patch = base_hash obligatoire
✅ finish_task interdit avec patch pending/conflict
✅ direct edit interdit sous claim patch_queue
```

---

## 📦 Utiliser `.agent/` dans un autre projet

```bash
cp -a /chemin/agent-scribe-graphify/.agent /chemin/nouveau-projet/.agent
cd /chemin/nouveau-projet
python3 .agent/scripts/mcp_smoke.py
```

---

## 📚 Docs utiles

```text
.agent/docs/USAGE.md       = guide humain court
.agent/docs/HOST_PROMPT.md = prompt court à donner au LLM hôte
```

---

## 🧭 Différence avec `main`

```text
main = README historique / bundle initial
v2   = socle MCP portable + workflow_next mécanique + smoke-test complet
```

---

## 📌 État actuel

```text
V2 feature-complete pour le socle MCP local portable.
Workflow parent workflow_next implémenté.
Validée par smoke-test local complet.
Prête pour tests avec hosts réels.
```

---

## 📄 Licence

MIT.
