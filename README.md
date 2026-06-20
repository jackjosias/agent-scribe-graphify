# 🧠 agent-scribe-graphify — V2

<p align="center">
  <img src="https://img.shields.io/badge/branch-v2-blue" alt="Branch v2">
  <img src="https://img.shields.io/badge/MCP-v0.2.6-purple" alt="MCP v0.2.4">
  <img src="https://img.shields.io/badge/status-smoke%20tested-brightgreen" alt="Smoke tested">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/write--gate-apply__patch-success" alt="MCP write gate">
</p>

> **Branche V2 — socle MCP local portable avec workflow mécanique `workflow_next`, write gate `apply_patch` et delete gate `delete_resource`, proactive context gates and `scribe_record`.**

---

## ✅ Statut V2

Validation locale :

```bash
python3 .agent/scripts/mcp_smoke.py
python3 .agent/scripts/enforcement_redteam_smoke.py
python3 .agent/scripts/sandbox_smoke.py
```

Résultat attendu :

```text
MCP_SMOKE_ALL_OK
```

Audit enforcement V2.7 :

```bash
python3 .agent/scripts/enforcement_redteam_smoke.py
python3 .agent/scripts/enforcement_redteam_smoke.py --strict-context
```

Le mode normal est un audit non bloquant. `--strict-context` échoue si le bypass contexte est encore ouvert.

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
delete_resource visible
sandbox/proxy smoke optionnel
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
delete_resource = delete gate MCP avec confirmation exacte
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
Toute suppression acceptée passe par delete_resource avec confirmation utilisateur exacte.
```

---

## 🧱 Architecture V2

```text
.agent/
├── docs/
│   ├── USAGE.md
│   ├── HOST_PROMPT.md
│   ├── OS_ISOLATION.md
│   └── SANDBOX.md
├── mcp/
│   ├── server.py
│   ├── server_entry.py
│   ├── install/
│   └── runtime/
│       ├── db.py
│       ├── patch_queue.py
│       └── state_paths.py
├── scripts/
│   ├── mcp_smoke.py
│   └── sandbox_smoke.py
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
python3 .agent/scripts/sandbox_smoke.py
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
→ scribe_query
→ workflow_next
→ graphify_query si code/architecture
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
→ scribe_record
→ workflow_next
→ finish_task
```

---

## 🛡️ Write gate

Les écritures directes du host sont refusées par `before_edit`.

Le chemin accepté par `.agent` V2.5 est :

```text
workflow_next → before_task → scribe_query → graphify_query si code → claim_resource → file_hash → propose_patch → apply_patch
```

Limite honnête : une sandbox OS reste nécessaire pour empêcher physiquement un processus externe qui possède déjà les droits d'écriture du système. Le write gate rend le protocole `.agent` MCP-only, mais ne remplace pas une isolation au niveau OS.

## 🧨 Delete gate

Les suppressions directes du host sont interdites. Le chemin accepté par `.agent` V2.5 est :

```text
workflow_next → before_task → scribe_query → graphify_query si code → claim_resource → file_hash → delete_resource → release_claim → scribe_record → finish_task
```

## 🧠 Context gates et gravure mémoire

Depuis V2.5, `workflow_next` impose aussi le contexte avant action :

```text
before_task → targeted_scribe_query → targeted_graphify_query si code/architecture → action
```

SCRIBE n'est jamais lu entièrement par défaut : `workflow_next` construit une requête RAG ciblée avec demande, intention, ressource et termes de cicatrice quand nécessaire. Graphify est interrogé pour impact/structure/blast-radius, pas en lecture brute totale. Après une écriture, suppression, correction, refactor, test, cicatrice, dette, conflit ou décision importante, `workflow_next` impose `scribe_record` typé avant `finish_task`. `scribe_record` écrit uniquement sous `.agent/state/scribe-out/records/`.

`delete_resource` exige une confirmation exacte fournie par l'utilisateur :

```text
DELETE chemin/relatif/du/fichier
```

## 🧱 Isolation OS optionnelle

Le MCP normal fonctionne sans sandbox. Pour lancer un host avec projet en lecture seule et MCP hors sandbox, voir :

```text
.agent/docs/OS_ISOLATION.md
.agent/docs/SANDBOX.md
```

Smoke du chemin proxy/daemon :

```bash
python3 .agent/scripts/sandbox_smoke.py
```

---

## 📦 Utiliser `.agent/` dans un autre projet

```bash
cp -a /chemin/agent-scribe-graphify/.agent /chemin/nouveau-projet/.agent
cd /chemin/nouveau-projet
python3 .agent/scripts/mcp_smoke.py
python3 .agent/scripts/sandbox_smoke.py
```

---

## 📚 Docs utiles

```text
.agent/docs/USAGE.md       = guide humain court
.agent/docs/HOST_PROMPT.md = prompt court à donner au LLM hôte
.agent/docs/OS_ISOLATION.md = stratégie Linux/macOS/Windows
.agent/docs/SANDBOX.md     = wrapper Linux bubblewrap + proxy MCP
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
Delete gate delete_resource implémenté.
Isolation OS optionnelle documentée.
Validée par smoke-test local complet.
Prête pour tests avec hosts réels.
```

---

## 📄 Licence

MIT.
