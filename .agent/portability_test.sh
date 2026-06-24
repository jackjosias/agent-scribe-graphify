#!/usr/bin/env bash
# =============================================================================
# portability_test.sh — Test de portabilité cross-host du bundle .agent/
#
# USAGE :
#   bash .agent/portability_test.sh [PROJECT_ROOT]
#
# Sortie finale : PORTABLE_OK ou PORTABLE_FAIL + raison
#
# Ce script vérifie, dans n'importe quel projet cible après `cp -r .agent/`,
# que :
#   1. scribe répond (CLI bootstrap idempotent)
#   2. les paths relatifs résolvent (scribe-out/ et graphify-out/ à la racine)
#   3. .graphifyignore est présent
#   4. les required_surfaces existent ou peuvent être créées
#   5. le MCP local server est démarrable
#   6. proof_signer.py est importable
#   7. scribe-rag gate est vert (8/8)
#   8. scribe doctor retourne 0 error
#
# Codes de sortie :
#   0  PORTABLE_OK — tout vert
#   1  PORTABLE_FAIL — au moins un check échoue
#   2  USAGE_ERROR — arguments invalides
# =============================================================================
set -euo pipefail

# ─── Couleurs ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ─── Compteurs ───────────────────────────────────────────────────────────────
PASS=0
FAIL=0
WARN=0
FAILURES=()

# ─── Helpers ─────────────────────────────────────────────────────────────────
pass() {
    PASS=$((PASS + 1))
    printf "${GREEN}  ✅ PASS${RESET} %s\n" "$1"
}

fail() {
    FAIL=$((FAIL + 1))
    FAILURES+=("$1")
    printf "${RED}  ❌ FAIL${RESET} %s\n" "$1"
    if [ -n "${2:-}" ]; then
        printf "      %b\n" "${YELLOW}↳ ${2}${RESET}"
    fi
}

warn() {
    WARN=$((WARN + 1))
    printf "${YELLOW}  ⚠  WARN${RESET} %s\n" "$1"
    if [ -n "${2:-}" ]; then
        printf "      %b\n" "${YELLOW}↳ ${2}${RESET}"
    fi
}

section() {
    printf "\n${CYAN}${BOLD}── %s ──${RESET}\n" "$1"
}

# ─── Résolution du projet cible ──────────────────────────────────────────────
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    echo "Usage: bash .agent/portability_test.sh [PROJECT_ROOT]"
    echo "  PROJECT_ROOT  chemin vers le projet à tester (défaut: répertoire courant)"
    exit 0
fi

TARGET_ROOT="${1:-$(pwd)}"

# Résoudre le chemin absolu (compatible macOS et Linux)
if command -v realpath &>/dev/null; then
    TARGET_ROOT="$(realpath "$TARGET_ROOT")"
elif command -v python3 &>/dev/null; then
    TARGET_ROOT="$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$TARGET_ROOT")"
fi

AGENT_DIR="$TARGET_ROOT/.agent"
SCRIBE="$AGENT_DIR/workflow/scribe/scribe"
SCRIBE_RAG="$AGENT_DIR/workflow/scribe/scribe-rag"
MCP_ENTRY="$AGENT_DIR/mcp/server_entry.py"
PROOF_SIGNER="$AGENT_DIR/workflow/scribe/sel/scripts/proof_signer.py"

printf "\n${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${BOLD}🔬 PORTABILITY TEST — agent-scribe-graphify${RESET}\n"
printf "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "  Project root : %s\n" "$TARGET_ROOT"
printf "  Agent dir    : %s\n" "$AGENT_DIR"
printf "  Timestamp    : %s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"

# ─── CHECK 0: Préconditions minimales ────────────────────────────────────────
section "CHECK 0 — Préconditions"

if [ ! -d "$TARGET_ROOT" ]; then
    fail "TARGET_ROOT existe" "répertoire absent : $TARGET_ROOT"
    printf "\n${RED}${BOLD}PORTABLE_FAIL — répertoire cible introuvable.${RESET}\n"
    exit 1
else
    pass "TARGET_ROOT existe"
fi

if [ ! -d "$AGENT_DIR" ]; then
    fail ".agent/ présent" ".agent/ absent dans $TARGET_ROOT — copier le bundle d'abord"
    printf "\n${RED}${BOLD}PORTABLE_FAIL — bundle .agent/ absent.${RESET}\n"
    exit 1
else
    pass ".agent/ présent"
fi

if [ ! -f "$SCRIBE" ]; then
    fail "scribe CLI présent" "$SCRIBE introuvable"
elif [ ! -x "$SCRIBE" ]; then
    warn "scribe CLI exécutable" "$SCRIBE existe mais n'est pas exécutable — chmod +x recommandé"
else
    pass "scribe CLI présent et exécutable"
fi

if [ ! -f "$SCRIBE_RAG" ]; then
    fail "scribe-rag CLI présent" "$SCRIBE_RAG introuvable"
elif [ ! -x "$SCRIBE_RAG" ]; then
    warn "scribe-rag CLI exécutable" "$SCRIBE_RAG existe mais n'est pas exécutable"
else
    pass "scribe-rag CLI présent et exécutable"
fi

# ─── CHECK 1: scribe répond ───────────────────────────────────────────────────
section "CHECK 1 — scribe bootstrap (idempotent)"

if [ -f "$SCRIBE" ]; then
    BOOTSTRAP_OUT="$(cd "$TARGET_ROOT" && "$SCRIBE" bootstrap 2>&1)" || true
    BOOTSTRAP_RC=$?
    # Only fail if the scribe CLI itself returned non-zero
    if [ $BOOTSTRAP_RC -ne 0 ]; then
        fail "scribe bootstrap répond" "rc=$BOOTSTRAP_RC output=$(echo "$BOOTSTRAP_OUT" | head -5)"
    else
        pass "scribe bootstrap répond (rc=$BOOTSTRAP_RC)"
    fi
else
    fail "scribe bootstrap répond" "scribe CLI absent — skip"
fi

# ─── CHECK 2: Paths relatifs résolvent ───────────────────────────────────────
section "CHECK 2 — Paths canoniques (scribe-out/ et graphify-out/ à la racine)"

if [ -d "$TARGET_ROOT/scribe-out" ]; then
    pass "scribe-out/ à la racine"
else
    fail "scribe-out/ à la racine" "absent — lancer: $SCRIBE bootstrap"
fi

if [ -d "$TARGET_ROOT/graphify-out" ]; then
    pass "graphify-out/ à la racine"
else
    warn "graphify-out/ à la racine" "absent — créé par graphify; pas bloquant si graphify pas encore lancé"
fi

# Vérifier l'ABSENCE des chemins violant le contrat
if [ -d "$AGENT_DIR/state/scribe-out" ]; then
    fail ".agent/state/scribe-out/ absent (violation contrat)" ".agent/state/scribe-out/ existe encore — lancer la migration state_paths.py"
else
    pass ".agent/state/scribe-out/ absent (contrat respecté)"
fi

if [ -d "$AGENT_DIR/state/graphify-out" ]; then
    fail ".agent/state/graphify-out/ absent (violation contrat)" ".agent/state/graphify-out/ existe encore — lancer la migration state_paths.py"
else
    pass ".agent/state/graphify-out/ absent (contrat respecté)"
fi

# ─── CHECK 3: .graphifyignore présent ────────────────────────────────────────
section "CHECK 3 — .graphifyignore"

if [ -f "$TARGET_ROOT/.graphifyignore" ]; then
    pass ".graphifyignore présent"
else
    fail ".graphifyignore présent" "absent — lancer: $SCRIBE bootstrap"
fi

# ─── CHECK 4: Required surfaces ──────────────────────────────────────────────
section "CHECK 4 — Required surfaces (tenor-init-v2.json)"

TENOR_JSON="$AGENT_DIR/rules/tenor-init-v2.json"
if [ -f "$TENOR_JSON" ] && command -v python3 &>/dev/null; then
    TENOR_JSON_PATH="$TENOR_JSON"
    SURFACES="$(python3 - 2>/dev/null <<PYEOF
import json, sys
try:
    d = json.loads(open("$TENOR_JSON_PATH").read())
    surfaces = d.get("required_surfaces", [])
    print("\n".join(surfaces))
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
PYEOF
    )" || SURFACES=""
    if [ -z "$SURFACES" ]; then
        warn "required_surfaces lisibles" "python3 n'a pas pu parser tenor-init-v2.json"
    else
        while IFS= read -r surface; do
            [ -z "$surface" ] && continue
            # Strip trailing slash
            surface_clean="${surface%/}"
            # Special case: surfaces like ".scribe" are glob patterns
            # matching any file ending in that extension (e.g. AGENT-MEMOIRE_PROJECT_STATUS.scribe)
            if [[ "$surface_clean" == .* ]] && [[ "$surface_clean" != */* ]]; then
                # It's a root-level dotfile or extension pattern — use glob
                ext="${surface_clean#.}"
                found_glob=0
                for f in "$TARGET_ROOT/"*."$ext" "$TARGET_ROOT/$surface_clean"; do
                    [ -e "$f" ] && { found_glob=1; break; }
                done
                if [ "$found_glob" -eq 1 ]; then
                    pass "required_surface: $surface (glob *.${ext})"
                else
                    fail "required_surface: $surface" "aucun fichier *.$ext trouvé dans $TARGET_ROOT — lancer: bootstrap"
                fi
            else
                surface_path="$TARGET_ROOT/$surface_clean"
                if [ -e "$surface_path" ] || [ -d "$surface_path" ]; then
                    pass "required_surface: $surface"
                else
                    fail "required_surface: $surface" "absent dans $TARGET_ROOT"
                fi
            fi
        done <<< "$SURFACES"
    fi
else
    warn "required_surfaces" "tenor-init-v2.json absent ou python3 indisponible — skip"
fi

# ─── CHECK 5: MCP local server démarrable ────────────────────────────────────
section "CHECK 5 — MCP local server (--list-tools)"

if [ -f "$MCP_ENTRY" ] && command -v python3 &>/dev/null; then
    MCP_OUT="$(cd "$TARGET_ROOT" && python3 "$MCP_ENTRY" --list-tools 2>&1)" || true
    MCP_RC=$?
    if [ $MCP_RC -eq 0 ] && echo "$MCP_OUT" | grep -q "workflow_next"; then
        TOOL_COUNT="$(echo "$MCP_OUT" | grep -c "\"name\"" 2>/dev/null || echo "?")"
        pass "MCP --list-tools répond ($TOOL_COUNT outils détectés)"
    else
        fail "MCP --list-tools répond" "rc=$MCP_RC — output=$(echo "$MCP_OUT" | head -5)"
    fi
else
    fail "MCP entry présent" "$MCP_ENTRY introuvable ou python3 absent"
fi

# Vérifier que verify_proof est bien enregistré
if [ -f "$MCP_ENTRY" ] && command -v python3 &>/dev/null; then
    if python3 "$MCP_ENTRY" --list-tools 2>/dev/null | grep -q "verify_proof"; then
        pass "MCP: verify_proof registré"
    else
        fail "MCP: verify_proof registré" "verify_proof absent de --list-tools"
    fi
fi

# ─── CHECK 6: proof_signer.py importable ────────────────────────────────────
section "CHECK 6 — proof_signer.py"

if [ -f "$PROOF_SIGNER" ] && command -v python3 &>/dev/null; then
    IMPORT_OUT="$(python3 - <<PYEOF 2>&1
import sys
sys.path.insert(0, "$(dirname "$PROOF_SIGNER")")
try:
    from proof_signer import issue_proof, verify_proof, purge_expired_proofs
    print("OK")
except Exception as e:
    print(f"FAIL: {e}")
PYEOF
)"
    if echo "$IMPORT_OUT" | grep -q "^OK$"; then
        pass "proof_signer.py importable (issue_proof, verify_proof, purge_expired_proofs)"
    else
        fail "proof_signer.py importable" "$IMPORT_OUT"
    fi
else
    fail "proof_signer.py présent" "$PROOF_SIGNER introuvable ou python3 absent"
fi

# ─── CHECK 7: scribe-rag gate (8/8) ─────────────────────────────────────────
section "CHECK 7 — scribe-rag gate (doit être 8/8)"

if [ -f "$SCRIBE_RAG" ]; then
    GATE_OUT="$(cd "$TARGET_ROOT" && "$SCRIBE_RAG" gate 2>&1)" || true
    GATE_RC=$?
    if echo "$GATE_OUT" | grep -qE "8/8|GATE_OK|all.*pass"; then
        pass "scribe-rag gate 8/8"
    elif echo "$GATE_OUT" | grep -qE "([0-7])/8|GATE_FAIL"; then
        SCORE="$(echo "$GATE_OUT" | grep -oE '[0-7]/8' | head -1)"
        fail "scribe-rag gate 8/8" "score=$SCORE — lancer scribe-rag build puis gate à nouveau"
    else
        warn "scribe-rag gate" "sortie inattendue rc=$GATE_RC — $(echo "$GATE_OUT" | head -3)"
    fi
else
    fail "scribe-rag gate" "scribe-rag CLI absent"
fi

# ─── CHECK 8: scribe doctor ──────────────────────────────────────────────────
section "CHECK 8 — scribe doctor (0 error)"

if [ -f "$SCRIBE" ]; then
    DOCTOR_OUT="$(cd "$TARGET_ROOT" && "$SCRIBE" doctor 2>&1)" || true
    DOCTOR_RC=$?
    # Count lines starting with error codes (E001, E002, etc.) or bare 'error:'
    ERROR_COUNT="$(echo "$DOCTOR_OUT" | awk '/^[[:space:]]*(E[0-9]+|error[: ])/{ count++ } END { print (count+0) }')"
    WARN_COUNT="$(echo "$DOCTOR_OUT" | awk '/^[[:space:]]*(W[0-9]+|warning[: ])/{ count++ } END { print (count+0) }')"
    # Also check the summary line "X error(s)" as fallback
    SUMMARY_ERRORS="$(echo "$DOCTOR_OUT" | grep -oE '[0-9]+ error' | awk '{s+=$1} END{print (s+0)}')"
    TOTAL_ERRORS=$(( ERROR_COUNT + SUMMARY_ERRORS ))
    if [ "$TOTAL_ERRORS" -eq 0 ]; then
        pass "scribe doctor 0 error ($WARN_COUNT warnings)"
    else
        fail "scribe doctor 0 error" "$TOTAL_ERRORS erreur(s) trouvée(s) — lancer: $SCRIBE doctor --suggest-fix"
    fi
else
    fail "scribe doctor" "scribe CLI absent"
fi

# ─── Rapport final ────────────────────────────────────────────────────────────
printf "\n${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${BOLD}RÉSULTAT FINAL${RESET}\n"
printf "  ✅ PASS  : %d\n" "$PASS"
printf "  ❌ FAIL  : %d\n" "$FAIL"
printf "  ⚠  WARN  : %d\n" "$WARN"

if [ "${#FAILURES[@]}" -gt 0 ]; then
    printf "\n${RED}Échecs détectés :${RESET}\n"
    for failure in "${FAILURES[@]}"; do
        printf "  ${RED}❌${RESET} %s\n" "$failure"
    done
fi

printf "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"

if [ "$FAIL" -eq 0 ]; then
    printf "${GREEN}${BOLD}PORTABLE_OK${RESET} — le bundle est portable et fonctionnel dans ce projet.\n\n"
    exit 0
else
    printf "${RED}${BOLD}PORTABLE_FAIL${RESET} — %d check(s) échoué(s). Corriger avant de déclarer le bundle portable.\n\n" "$FAIL"
    exit 1
fi
