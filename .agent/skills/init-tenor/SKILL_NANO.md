---
name: init-tenor-nano
description: >
  Version distillée de SKILL.md pour modèles à contexte limité (<8K tokens).
  Contient uniquement la commande canonique, les 3 invariants critiques,
  le format de preuve minimal et le signal d'erreur fatal.
  Les modèles capables lisent le full SKILL.md — ce fichier est pour les petits.
---

# TENOR INIT — NANO (< 80 lignes)

> **Modèle complet** → lire `.agent/skills/init-tenor/SKILL.md`
> **Ce fichier** → pour agents < 8K tokens de contexte libre.

## 1. Commande canonique à lancer

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

Pour IDE/extension :

```bash
.agent/workflow/scribe/scribe tenor-init --type extension
```

Copier la sortie brute **sans résumé, sans reconstruction**.

## 2. Les 3 invariants critiques

| # | Invariant | Violation = |
|---|-----------|-------------|
| **I-1** | Le premier fichier lu DOIT être `.agent/skills/init-tenor/SKILL.md` | Session invalide |
| **I-2** | Preuve = sortie brute de `tenor-init`, pas une reconstruction | PROOF_FABRICATED |
| **I-3** | MCP tools visibles au LLM host ≠ server local listable | UNSAFE verdict |

## 3. Format de preuve minimal obligatoire

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 SCRIBE-CHECK TENOR V4 — MACHINE PROOF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status init          : VALID
Agent session        : <id>
Whoami proof         : OK
Proof token          : v1.<nonce>.<hmac>.<agent_b64>.<ts_b64>
Proof verify MCP     : verify_proof(token=<above>, agent_id='<id>')
Workflow ack         : ACK_OK
```

**Après init** : appeler `verify_proof` MCP avec le token ci-dessus pour briser la boucle circulaire.

## 4. Signal d'erreur fatal → STOP immédiat

| Signal | Action obligatoire |
|--------|-------------------|
| `TENOR INIT ERROR: missing .agent/skills/init-tenor/SKILL.md` | Arrêter — bundle non installé |
| `PROOF_MODULE_UNAVAILABLE` | Arrêter — `proof_signer.py` absent |
| `MCP_WRONG_ROOT` | Arrêter — rebinder le host MCP avant de continuer |
| `INIT_BLOCKED_HOST_MCP_UNBOUND` | Demander à l'utilisateur de configurer le MCP |

## 5. Après la preuve

```bash
# Vérification anti-boucle (appel MCP depuis le host) :
verify_proof(token="<proof_token>", agent_id="<agent_session>")
# Doit retourner : {"ok": true, "verdict": "PROOF_VALID"}
```

Si `ok: false` → le LLM a fabriqué le header. Relancer `tenor-init` réellement.
