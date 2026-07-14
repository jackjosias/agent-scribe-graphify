#!/usr/bin/env python3
"""
proof_signer.py — SCRIBE/TENOR proof signing and verification.

PURPOSE
-------
Break the circular trust loop: a LLM can fabricate a SCRIBE-CHECK V4 header
without ever running tenor-init. The only robust defence is to make the proof
non-falsifiable — a HMAC token generated server-side at init time, stored in
the MCP runtime DB, and later verifiable by the LLM through a dedicated MCP
tool `verify_proof`.

DESIGN
------
• Nonce is 32 random bytes (256 bits), unique per init call.
• HMAC-SHA256 over  (nonce_hex || "|" || agent_id || "|" || utc_iso_timestamp).
• Key material = SCRIBE_PROOF_SECRET env var, or a per-project key derived from
  the SHA256 of the AGENT-MEMOIRE_PROJECT_STATUS.scribe absolute path + a fixed
  pepper.  This makes the key project-bound without requiring secrets management.
• The signed proof token is a compact string:
    v1.<nonce_hex>.<hmac_hex>.<agent_id_b64url>.<ts_b64url>
• Only the nonce, signature and session metadata are stored atomically in
  scribe-out/proof_store.json. The full bearer token is never persisted or
  printed by TENOR. TTL: 24 h (configurable).
• verify_proof(token) checks:
    1. Format is well-formed (v1 prefix, 5 parts).
    2. HMAC matches (constant-time compare).
    3. Token is present in the proof_store (it was actually issued).
    4. TTL not exceeded.
    5. agent_id in token matches caller-supplied agent_id.

EDGE CASES
----------
• Concurrent writers: an owned inter-process lock serializes the transactional
  read/modify/fsync/atomic-replace sequence.
• Corrupt store: fails closed and is preserved for diagnosis; issuance never
  overwrites it as though it were an empty store.
• Missing secret env: falls back to project-bound derived key deterministically.
• Clock skew: TTL is checked with UTC only; no locale dependency.
• Replay: nonce is one-time; used proofs are not removed (audit trail) but
  `is_fresh` flag is set to False after the first successful verify call.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from scribe_output_paths import scribe_out_dir
from typing import Any

_MCP_ROOT = Path(__file__).resolve().parents[4] / "mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))
from runtime.owned_file_lock import OwnedFileLockTimeout, owned_file_lock  # noqa: E402

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
_TOKEN_VERSION = "v1"
_DEFAULT_TTL_SECONDS = 86_400  # 24 h
_PEPPER = b"SCRIBE_TENOR_PROOF_V1_PEPPER_2026"
_PROOF_STORE_FILENAME = "proof_store.json"
_ENV_SECRET = "SCRIBE_PROOF_SECRET"


# ─────────────────────────────────────────────────────────────────────────────
# Key derivation — project-bound, no secrets management needed
# ─────────────────────────────────────────────────────────────────────────────

def _derive_project_key(project_root: Path) -> bytes:
    """
    Derive a stable per-project HMAC key without requiring external secrets.

    Precondition  : project_root must be a resolved absolute Path.
    Postcondition : returns 32 bytes deterministic for (project_root, _PEPPER).
    Invariant     : same project_root always produces the same key on the same machine.

    NOTE: This is NOT a secret key in the cryptographic sense — it is a
    project-specific binding that prevents cross-project token reuse.
    True secrecy requires setting SCRIBE_PROOF_SECRET in the environment.
    """
    raw = str(project_root).encode("utf-8") + _PEPPER
    return hashlib.sha256(raw).digest()


def _load_key(project_root: Path) -> bytes:
    """
    Load HMAC key from env or derive project-bound key.

    Edge cases:
    - Env var present but empty → fall through to derived key.
    - Env var present with valid hex → use it.
    - Env var present but invalid hex → log warning, fall through.
    """
    raw_env = os.environ.get(_ENV_SECRET, "").strip()
    if raw_env:
        try:
            key = bytes.fromhex(raw_env)
            if len(key) >= 16:
                return key
            logger.warning(
                "SCRIBE_PROOF_SECRET is shorter than 16 bytes (%d); falling back to project-derived key.",
                len(key),
            )
        except ValueError:
            logger.warning(
                "SCRIBE_PROOF_SECRET is not valid hex; falling back to project-derived key."
            )
    return _derive_project_key(project_root)


# ─────────────────────────────────────────────────────────────────────────────
# Proof store — atomic read/write of proof_store.json
# ─────────────────────────────────────────────────────────────────────────────

def _store_path(project_root: Path) -> Path:
    return scribe_out_dir(project_root) / _PROOF_STORE_FILENAME


def _store_lock_path(project_root: Path) -> Path:
    path = _store_path(project_root)
    return path.with_name(f".{path.name}.lock")


class ProofStoreCorrupt(RuntimeError):
    """The proof store exists but cannot be trusted or updated safely."""


def _load_store(project_root: Path) -> dict[str, Any]:
    """Load the proof store; missing is empty, corruption fails closed."""
    path = _store_path(project_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProofStoreCorrupt(f"proof_store.json is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ProofStoreCorrupt("proof_store.json root must be an object")
    return value


def _save_store_unlocked(project_root: Path, store: dict[str, Any]) -> None:
    """
    Atomic write via temp file + rename to prevent partial writes.

    Precondition  : store is JSON-serialisable.
    Postcondition : proof_store.json is fully written or unchanged (on error).
    """
    path = _store_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(store, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".proof_store_tmp_", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        delay = 0.01
        for attempt in range(10):
            try:
                os.replace(tmp_name, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.25)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to write proof_store.json: %s", exc)
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Token generation
# ─────────────────────────────────────────────────────────────────────────────

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> str:
    # Restore padding
    padded = s + "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8")


def _compute_hmac(key: bytes, nonce_hex: str, agent_id: str, ts: str) -> str:
    """
    HMAC-SHA256 over a canonical message string.

    Message: "<nonce_hex>|<agent_id>|<ts>"
    All fields are ASCII/UTF-8; pipe is the separator (not present in nonce hex or ISO ts).
    agent_id may contain arbitrary chars — encode as b64url before use in message
    to avoid injection via a crafted agent_id containing "|".
    """
    agent_b64 = _b64url(agent_id)
    message = f"{nonce_hex}|{agent_b64}|{ts}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def issue_proof(
    project_root: Path,
    agent_id: str,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> str:
    """
    Generate and store a signed proof token for agent_id.

    Returns: proof token string  v1.<nonce_hex>.<hmac_hex>.<agent_id_b64url>.<ts_b64url>

    Precondition  : project_root is resolved, agent_id is non-empty.
    Postcondition : token is stored in proof_store.json; calling verify_proof(token) returns True.
    Invariant     : each call generates a fresh nonce — tokens are not reused.

    Raises:
        ValueError  : if agent_id is empty.
        OSError     : if proof_store.json cannot be written.
        ProofStoreCorrupt: if an existing store cannot be parsed safely.
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("agent_id must be non-empty to issue a proof token.")

    key = _load_key(project_root)
    nonce = secrets.token_hex(32)          # 256 bits of entropy
    ts = _utc_iso()
    sig = _compute_hmac(key, nonce, agent_id, ts)
    agent_b64 = _b64url(agent_id)
    ts_b64 = _b64url(ts)
    token = f"{_TOKEN_VERSION}.{nonce}.{sig}.{agent_b64}.{ts_b64}"

    with owned_file_lock(_store_lock_path(project_root), purpose="proof_issue"):
        store = _load_store(project_root)
        store[nonce] = {
            "schema": "server_proof_v2",
            "agent_id": agent_id,
            "issued_at": ts,
            "expires_at_epoch": time.time() + ttl_seconds,
            "is_fresh": True,
            "signature": sig,
            "token_prefix": token[:40],  # audit only; the full token is never persisted
        }
        _save_store_unlocked(project_root, store)
    logger.info("Proof issued for agent_id=%s nonce=%s...", agent_id, nonce[:8])
    return token


# ─────────────────────────────────────────────────────────────────────────────
# Token verification
# ─────────────────────────────────────────────────────────────────────────────

class ProofVerificationError(Exception):
    """Raised when a proof token fails verification with a structured reason."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def verify_proof(
    project_root: Path,
    token: str,
    expected_agent_id: str,
    *,
    mark_consumed: bool = True,
) -> dict[str, Any]:
    """
    Verify a proof token and return a structured result dict.

    Args:
        project_root    : resolved project root.
        token           : the token string from the LLM's SCRIBE-CHECK output.
        expected_agent_id: agent_id the caller claims; must match token's agent_id.
        mark_consumed   : if True, sets is_fresh=False in the store after first valid verify.

    Returns dict:
        {
          "ok": bool,
          "verdict": str,           # PROOF_VALID | PROOF_INVALID_* | PROOF_EXPIRED | PROOF_CONSUMED
          "agent_id": str,          # from token (only when ok)
          "issued_at": str,         # ISO timestamp (only when ok)
          "detail": str,            # human explanation
        }

    Never raises — all exceptions are caught and returned as PROOF_INVALID_INTERNAL.

    Precondition  : token and expected_agent_id are non-empty strings.
    Postcondition : returns dict with "ok" key always set.
    Invariant     : constant-time HMAC compare prevents timing side-channels.
    """
    def _fail(code: str, detail: str = "") -> dict[str, Any]:
        return {"ok": False, "verdict": code, "detail": detail}

    # ── 1. Guard: inputs
    if not token or not expected_agent_id:
        return _fail("PROOF_INVALID_MISSING_INPUT", "token and expected_agent_id are required.")

    # ── 2. Parse token structure
    parts = token.split(".")
    if len(parts) != 5:
        return _fail("PROOF_INVALID_FORMAT", f"Expected 5 parts, got {len(parts)}.")
    version, nonce_hex, hmac_hex, agent_b64, ts_b64 = parts

    if version != _TOKEN_VERSION:
        return _fail("PROOF_INVALID_VERSION", f"Unsupported token version: {version!r}.")

    # ── 3. Decode fields (may raise on corrupt b64)
    try:
        token_agent_id = _b64url_decode(agent_b64)
        token_ts = _b64url_decode(ts_b64)
    except Exception as exc:  # noqa: BLE001
        return _fail("PROOF_INVALID_ENCODING", str(exc))

    # ── 4. Agent ID match
    if token_agent_id != expected_agent_id:
        return _fail(
            "PROOF_INVALID_AGENT_MISMATCH",
            f"Token agent_id does not match expected_agent_id.",
        )

    # ── 5. HMAC verification — constant-time to prevent timing oracle
    try:
        key = _load_key(project_root)
        expected_sig = _compute_hmac(key, nonce_hex, token_agent_id, token_ts)
        if not hmac.compare_digest(expected_sig, hmac_hex):
            return _fail("PROOF_INVALID_SIGNATURE", "HMAC mismatch — token was not issued by this server.")
    except Exception as exc:  # noqa: BLE001
        return _fail("PROOF_INVALID_INTERNAL", f"Key/HMAC error: {exc}")

    def inspect_entry(store: dict[str, Any]) -> dict[str, Any] | None:
        entry = store.get(nonce_hex)
        if not isinstance(entry, dict):
            return _fail("PROOF_INVALID_NOT_IN_STORE", "Nonce not found — token was never issued by this server.")
        try:
            expires_at = float(entry.get("expires_at_epoch") or 0)
        except (TypeError, ValueError):
            return _fail("PROOF_STORE_CORRUPT", "Stored proof expiration is invalid.")
        if time.time() > expires_at:
            return _fail("PROOF_EXPIRED", f"Token expired at {expires_at}.")
        if not entry.get("is_fresh", True):
            return _fail("PROOF_CONSUMED", "Token was already verified; replay detected.")
        return None

    if mark_consumed:
        try:
            with owned_file_lock(_store_lock_path(project_root), purpose="proof_consume"):
                store = _load_store(project_root)
                failure = inspect_entry(store)
                if failure is not None:
                    return failure
                store[nonce_hex]["is_fresh"] = False
                store[nonce_hex]["consumed_at_epoch"] = time.time()
                _save_store_unlocked(project_root, store)
        except ProofStoreCorrupt as exc:
            return _fail("PROOF_STORE_CORRUPT", str(exc))
        except (OSError, OwnedFileLockTimeout) as exc:
            return _fail("PROOF_STORE_WRITE_FAILED", str(exc))
    else:
        try:
            store = _load_store(project_root)
        except ProofStoreCorrupt as exc:
            return _fail("PROOF_STORE_CORRUPT", str(exc))
        failure = inspect_entry(store)
        if failure is not None:
            return failure

    logger.info("Proof verified OK for agent_id=%s nonce=%s...", token_agent_id, nonce_hex[:8])
    return {
        "ok": True,
        "verdict": "PROOF_VALID",
        "agent_id": token_agent_id,
        "issued_at": token_ts,
        "detail": "Proof token is authentic, bound to this project, and within TTL.",
    }


def consume_agent_proof(project_root: Path, expected_agent_id: str) -> dict[str, Any]:
    """Consume the newest server-side proof without exposing a bearer token."""

    if not expected_agent_id or not expected_agent_id.strip():
        return {"ok": False, "verdict": "PROOF_INVALID_MISSING_INPUT", "detail": "expected_agent_id is required"}
    try:
        with owned_file_lock(_store_lock_path(project_root), purpose="proof_consume_server_side"):
            store = _load_store(project_root)
            now = time.time()
            candidates: list[tuple[str, dict[str, Any]]] = []
            for nonce, raw_entry in store.items():
                if not isinstance(raw_entry, dict):
                    continue
                if raw_entry.get("schema") != "server_proof_v2":
                    continue
                if raw_entry.get("agent_id") != expected_agent_id or not raw_entry.get("is_fresh", False):
                    continue
                try:
                    expires_at = float(raw_entry.get("expires_at_epoch") or 0)
                except (TypeError, ValueError):
                    continue
                if expires_at <= now:
                    continue
                issued_at = str(raw_entry.get("issued_at") or "")
                signature = str(raw_entry.get("signature") or "")
                expected = _compute_hmac(_load_key(project_root), nonce, expected_agent_id, issued_at)
                if hmac.compare_digest(expected, signature):
                    candidates.append((nonce, raw_entry))
            if not candidates:
                return {"ok": False, "verdict": "PROOF_NOT_AVAILABLE", "detail": "no fresh server-side proof for agent"}
            candidates.sort(key=lambda item: str(item[1].get("issued_at") or ""), reverse=True)
            nonce, entry = candidates[0]
            entry["is_fresh"] = False
            entry["consumed_at_epoch"] = now
            entry["consumption_mode"] = "host_bound_server_side"
            for stale_nonce, stale_entry in candidates[1:]:
                stale_entry["is_fresh"] = False
                stale_entry["superseded_by"] = nonce
                stale_entry["consumed_at_epoch"] = now
                store[stale_nonce] = stale_entry
            store[nonce] = entry
            _save_store_unlocked(project_root, store)
            return {
                "ok": True,
                "verdict": "PROOF_VALID",
                "agent_id": expected_agent_id,
                "issued_at": entry.get("issued_at", ""),
                "consumption_mode": "host_bound_server_side",
            }
    except ProofStoreCorrupt as exc:
        return {"ok": False, "verdict": "PROOF_STORE_CORRUPT", "detail": str(exc)}
    except (OSError, OwnedFileLockTimeout) as exc:
        return {"ok": False, "verdict": "PROOF_STORE_WRITE_FAILED", "detail": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: purge expired entries (housekeeping)
# ─────────────────────────────────────────────────────────────────────────────

def purge_expired_proofs(project_root: Path) -> int:
    """
    Remove expired entries from the proof store.

    Returns: number of entries removed.
    Idempotent — safe to call at any time.
    """
    with owned_file_lock(_store_lock_path(project_root), purpose="proof_purge"):
        store = _load_store(project_root)
        now = time.time()
        expired_nonces: list[str] = []
        for nonce, entry in store.items():
            if not isinstance(entry, dict):
                expired_nonces.append(nonce)
                continue
            try:
                expires_at = float(entry.get("expires_at_epoch") or 0)
            except (TypeError, ValueError):
                expired_nonces.append(nonce)
                continue
            if expires_at < now:
                expired_nonces.append(nonce)
        if not expired_nonces:
            return 0
        for nonce in expired_nonces:
            del store[nonce]
        _save_store_unlocked(project_root, store)
    logger.info("Purged %d expired proof(s) from store.", len(expired_nonces))
    return len(expired_nonces)


# ─────────────────────────────────────────────────────────────────────────────
# CLI shim (used by scribe tenor-init and tests)
# ─────────────────────────────────────────────────────────────────────────────

def cli_main() -> int:
    """
    CLI entry point:
        proof_signer issue   <project_root> <agent_id>
        proof_signer verify  <project_root> <agent_id> <token>
        proof_signer purge   <project_root>

    Exits 0 on success, non-zero on failure.
    Output: JSON line to stdout.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="proof_signer", description="SCRIBE TENOR proof signing CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue", help="Issue a proof token")
    p_issue.add_argument("project_root")
    p_issue.add_argument("agent_id")
    p_issue.add_argument("--ttl", type=int, default=_DEFAULT_TTL_SECONDS)

    p_verify = sub.add_parser("verify", help="Verify a proof token")
    p_verify.add_argument("project_root")
    p_verify.add_argument("agent_id")
    p_verify.add_argument("token")
    p_verify.add_argument("--no-consume", action="store_true")

    p_purge = sub.add_parser("purge", help="Purge expired proof tokens")
    p_purge.add_argument("project_root")

    args = parser.parse_args()
    root = Path(args.project_root).resolve()

    try:
        if args.cmd == "issue":
            token = issue_proof(root, args.agent_id, ttl_seconds=args.ttl)
            print(json.dumps({"ok": True, "token": token}))
            return 0

        if args.cmd == "verify":
            result = verify_proof(root, args.token, args.agent_id, mark_consumed=not args.no_consume)
            print(json.dumps(result))
            return 0 if result["ok"] else 1

        if args.cmd == "purge":
            removed = purge_expired_proofs(root)
            print(json.dumps({"ok": True, "removed": removed}))
            return 0

    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "verdict": "PROOF_INTERNAL_ERROR", "detail": str(exc)}))
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
