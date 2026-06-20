# 🧠 agent-scribe-graphify — V2

<p align="center">
  <img src="https://img.shields.io/badge/branch-v2-blue" alt="Branch v2">
  <img src="https://img.shields.io/badge/MCP-v0.2.3-purple" alt="MCP v0.2.3">
  <img src="https://img.shields.io/badge/status-smoke%20tested-brightgreen" alt="Smoke tested">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/write--gate-apply__patch-success" alt="MCP write gate">
</p>

> **Branche V2 — socle MCP local portable avec workflow mécanique `workflow_next` et write gate `apply_patch`.**

---

## ✅ Statut V2

Validation locale :

```bash
python3 .agent/scripts/mcp_smoke.py
```

Résultat attendu :

```text
MCP_SMOKE_ALL_OK
```

Le smoke-test couvre maintenant le workflow mécanique complet, y compris :

```text
bootstrap
workflow_next
before_task
claim_resource
before_edit refusé pour écriture directe
file_hash
propose_patch
apply_patch
release_claim
finish_task
portabilité .agent
sécurité chemins et symlinks
```

---

## 🎯 Principe V2

```text
.agent/       = contrôle aérien local multi-agent
MCP           = canal mécanique commun
workflow_next = chef d'orchestre obligatoire
apply_patch   = write gate MCP obligatoire
SQLite WAL    = coordination courte durée
Patch queue   = sécurité multi-agent sur mêmes fichiers
```

Règle centrale :

```text
Le LLM hôte ne décide pas seul la prochaine étape.
Il appelle workflow_next.
Il exécute must_call.
Il rappelle workflow_next.
Toute écriture acceptée passe par apply_patch.
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
├── scripts/
│   └── mcp_smoke.py
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

`server_entry.py` recalcule le project root depuis l'emplacement réel de `.agent/`.

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
→ apply_patch
→ workflow_next
→ release_claim
→ workflow_next
→ finish_task
```

---

## 🛡️ Write gate

Les écritures directes du host sont refusées par `before_edit`.

Le chemin accepté par `.agent` V2.3 est :

```text
workflow_next → file_hash → propose_patch → apply_patch
```

Limite honnête : une sandbox OS reste nécessaire pour empêcher physiquement un processus externe qui possède déjà les droits d'écriture du système. Le write gate rend le protocole `.agent` MCP-only, mais ne remplace pas une isolation au niveau OS.

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
v2   = socle MCP portable + workflow_next + apply_patch write gate
```

---

## 📌 État actuel

```text
V2 feature-complete pour le socle MCP local portable.
Workflow parent workflow_next implémenté.
Write gate apply_patch implémenté.
Validée par smoke-test local complet.
Prête pour tests avec hosts réels.
```

---

## 📄 Licence

MIT.
