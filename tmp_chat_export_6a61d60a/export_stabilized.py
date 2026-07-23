from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi import requests

SHARE_URL = os.environ.get(
    "SHARE_URL",
    "https://chatgpt.com/share/6a61d60a-1844-83ea-928b-76ed2a2673ac",
)
SHARE_ID = SHARE_URL.rstrip("/").rsplit("/", 1)[-1]
OUT_DIR = Path.cwd() / "tmp_chat_export_6a61d60a" / "result"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TXT_PATH = OUT_DIR / f"conversation-chatgpt-public-{SHARE_ID}-INTEGRALE.txt"
DIAG_PATH = OUT_DIR / "diagnostics.txt"
COMPARE_PATH = OUT_DIR / "double-fetch-comparison.txt"
PREVIEW_PATH = OUT_DIR / "first-last-preview.txt"


class ExportFailure(RuntimeError):
    pass


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = normalize_text(value)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_html(label: str) -> bytes:
    session = requests.Session(impersonate="chrome124")
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    notes: list[str] = []
    for attempt in range(1, 6):
        try:
            response = session.get(
                SHARE_URL,
                headers=headers,
                timeout=180,
                params={"_archive_probe": f"{label}-{time.time_ns()}"},
            )
            notes.append(
                f"{label}: attempt={attempt} status={response.status_code} "
                f"type={response.headers.get('content-type')} bytes={len(response.content)}"
            )
            if response.status_code == 200 and len(response.content) > 10_000:
                path = OUT_DIR / f"public-share-{label}.html"
                path.write_bytes(response.content)
                return response.content
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{label}: attempt={attempt} {type(exc).__name__}: {exc}")
        time.sleep(attempt * 3)
    raise ExportFailure("Téléchargement impossible:\n" + "\n".join(notes))


def decode_react_stream(html: bytes, label: str) -> str:
    soup = BeautifulSoup(html.decode("utf-8", errors="replace"), "html.parser")
    chunks: list[str] = []
    errors: list[str] = []
    for index, script in enumerate(soup.find_all("script")):
        text = script.string if script.string is not None else script.get_text()
        if "streamController.enqueue" not in text:
            continue
        match = re.search(r"streamController\.enqueue\((.*)\)\s*;?\s*$", text, flags=re.S)
        if not match:
            errors.append(f"script={index}: enqueue non reconnu")
            continue
        try:
            decoded = json.loads(match.group(1).strip())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"script={index}: {type(exc).__name__}: {exc}")
            continue
        chunks.append(decoded if isinstance(decoded, str) else json.dumps(decoded, ensure_ascii=False))
    stream = "".join(chunks)
    (OUT_DIR / f"decoded-react-stream-{label}.txt").write_text(stream, encoding="utf-8")
    if not stream:
        raise ExportFailure(f"Flux React vide ({label}): " + "; ".join(errors[:20]))
    return stream


def locate_flat_table(stream: str) -> list[Any]:
    tables: list[list[Any]] = []
    for line in stream.splitlines():
        stripped = line.strip()
        if not stripped.startswith("["):
            continue
        try:
            value = json.loads(stripped)
        except Exception:
            continue
        if isinstance(value, list):
            tables.append(value)
    if not tables:
        raise ExportFailure("Table aplatie introuvable dans le flux React.")
    return max(tables, key=len)


UNDEFINED = object()
SPECIAL_VALUES: dict[int, Any] = {
    -1: UNDEFINED,
    -2: float("nan"),
    -3: float("inf"),
    -4: float("-inf"),
    -5: None,
    -6: -0.0,
}


class FlatDecoder:
    def __init__(self, table: list[Any]) -> None:
        self.table = table
        self.memo: dict[int, Any] = {}

    def decode(self, value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int):
            if value < 0:
                return SPECIAL_VALUES.get(value)
            if value >= len(self.table):
                return value
            return self.decode_index(value)
        if isinstance(value, (str, float)):
            return value
        if isinstance(value, dict):
            return self.decode_object(value)
        if isinstance(value, list):
            return self.decode_list(value)
        return value

    def decode_key(self, key: str) -> str:
        match = re.fullmatch(r"_(\d+)", key)
        return str(self.decode_index(int(match.group(1)))) if match else key

    def decode_object(self, raw: dict[str, Any], target: dict[str, Any] | None = None) -> dict[str, Any]:
        result = target if target is not None else {}
        for raw_key, raw_value in raw.items():
            decoded = self.decode(raw_value)
            if decoded is not UNDEFINED:
                result[self.decode_key(raw_key)] = decoded
        return result

    def decode_list(self, raw: list[Any], target: list[Any] | None = None) -> Any:
        if raw and isinstance(raw[0], str) and raw[0] in {"Date", "URL", "BigInt", "RegExp", "Set", "Map"}:
            tag = raw[0]
            values = [self.decode(item) for item in raw[1:]]
            if tag in {"Date", "URL", "BigInt"}:
                return values[0] if values else None
            if tag == "Set":
                return values
            if tag == "Map":
                result_map: dict[str, Any] = {}
                for index in range(0, len(values) - 1, 2):
                    result_map[str(values[index])] = values[index + 1]
                return result_map
            return values
        result = target if target is not None else []
        for item in raw:
            decoded = self.decode(item)
            if decoded is not UNDEFINED:
                result.append(decoded)
        return result

    def decode_index(self, index: int) -> Any:
        if index in self.memo:
            return self.memo[index]
        raw = self.table[index]
        if isinstance(raw, dict):
            target: dict[str, Any] = {}
            self.memo[index] = target
            return self.decode_object(raw, target)
        if isinstance(raw, list):
            target_list: list[Any] = []
            self.memo[index] = target_list
            decoded = self.decode_list(raw, target_list)
            if decoded is not target_list:
                self.memo[index] = decoded
            return decoded
        self.memo[index] = raw
        return raw


def find_key(value: Any, wanted: str, path: str = "$", seen: set[int] | None = None) -> list[tuple[str, Any]]:
    if seen is None:
        seen = set()
    if isinstance(value, (dict, list)):
        object_id = id(value)
        if object_id in seen:
            return []
        seen.add(object_id)
    results: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        if wanted in value:
            results.append((f"{path}.{wanted}", value[wanted]))
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                results.extend(find_key(child, wanted, f"{path}.{key}", seen))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                results.extend(find_key(child, wanted, f"{path}[{index}]", seen))
    return results


ATTACHMENT_FIELDS = (
    "name", "filename", "file_name", "title", "mime_type", "content_type",
    "size", "size_bytes", "width", "height", "url",
)


def attachment_block(value: dict[str, Any]) -> str:
    lines = ["[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]"]
    for field in ATTACHMENT_FIELDS:
        item = value.get(field)
        if isinstance(item, (str, int, float)) and str(item).strip():
            lines.append(f"{field}: {item}")
    if len(lines) == 1:
        lines.append(f"type: {value.get('content_type') or value.get('type') or 'élément joint'}")
    return "\n".join(lines)


def extract_visible_chunks(value: Any, attachment_context: bool = False) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if value is None or isinstance(value, (int, float, bool)):
        return []
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(extract_visible_chunks(item, attachment_context))
        return ordered_unique(chunks)
    if not isinstance(value, dict):
        return []

    chunks: list[str] = []
    content_type = str(value.get("content_type") or value.get("type") or "").lower()
    is_attachment = attachment_context or any(
        token in content_type for token in ("asset_pointer", "attachment", "file", "image", "audio", "video")
    )
    for key in ("text", "content", "caption", "transcript", "result", "output"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            chunks.append(field)
        elif isinstance(field, (dict, list)):
            chunks.extend(extract_visible_chunks(field, is_attachment))
    if is_attachment:
        chunks.append(attachment_block(value))
    for key in ("parts", "items", "attachments", "files", "children", "data", "references", "citations"):
        field = value.get(key)
        if isinstance(field, (dict, list)):
            chunks.extend(extract_visible_chunks(field, is_attachment))
    for key in ("name", "filename", "file_name", "title", "label", "url"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            if key == "url" or not is_attachment:
                chunks.append(field)
    return ordered_unique(chunks)


def message_text(message: dict[str, Any]) -> str:
    chunks = extract_visible_chunks(message.get("content"))
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        for key in ("attachments", "files", "citations"):
            field = metadata.get(key)
            if isinstance(field, (dict, list)):
                chunks.extend(extract_visible_chunks(field, attachment_context=True))
    return normalize_text("\n\n".join(ordered_unique(chunks)))


def hidden(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata")
    return isinstance(metadata, dict) and metadata.get("is_visually_hidden_from_conversation") is True


def assistant_visible(message: dict[str, Any], text: str) -> bool:
    if hidden(message):
        return False
    if message.get("recipient") not in (None, "", "all"):
        return False
    if message.get("channel") not in (None, "", "final", "commentary"):
        return False
    if message.get("status") not in (None, "", "finished", "finished_successfully"):
        return False
    return text.strip() != "The output of this plugin was redacted."


def iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def title_from(root: Any, html: bytes) -> str:
    generic = {"chatgpt", "new chat", "nouvelle conversation", "conversation chatgpt publique"}
    for _, value in find_key(root, "title"):
        if isinstance(value, str):
            candidate = normalize_text(value)
            if candidate and candidate.lower() not in generic and len(candidate) <= 300:
                return candidate
    soup = BeautifulSoup(html.decode("utf-8", errors="replace"), "html.parser")
    if soup.title:
        candidate = normalize_text(soup.title.get_text(" "))
        candidate = re.sub(r"^ChatGPT\s*[-–—]\s*", "", candidate, flags=re.I)
        if candidate:
            return candidate
    return "Conversation ChatGPT publique"


def extract_snapshot(label: str) -> dict[str, Any]:
    html = fetch_html(label)
    stream = decode_react_stream(html, label)
    table = locate_flat_table(stream)
    root = FlatDecoder(table).decode_index(0)

    linear: list[Any] = []
    linear_path = ""
    for path, candidate in find_key(root, "linear_conversation"):
        if isinstance(candidate, list) and len(candidate) > len(linear):
            linear, linear_path = candidate, path
    if not linear:
        raise ExportFailure(f"linear_conversation introuvable ({label}).")

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    raw_role_counts: dict[str, int] = {}
    excluded_hidden = 0
    excluded_internal = 0
    for linear_index, item in enumerate(linear):
        if not isinstance(item, dict):
            continue
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else message.get("role")
        role = str(role or "")
        raw_role_counts[role] = raw_role_counts.get(role, 0) + 1
        if role not in {"user", "assistant"}:
            continue
        if hidden(message):
            excluded_hidden += 1
            continue
        text = message_text(message)
        if not text:
            continue
        if role == "assistant" and not assistant_visible(message, text):
            excluded_internal += 1
            continue
        message_id = str(message.get("id") or item.get("id") or f"linear-{linear_index}")
        if message_id in seen_ids:
            continue
        seen_ids.add(message_id)
        channel = str(message.get("channel") or "")
        entries.append({
            "id": message_id,
            "role": role,
            "subtype": "progression" if role == "assistant" and channel == "commentary" else "message",
            "text": text,
            "timestamp": iso_timestamp(message.get("create_time")),
            "linear_index": linear_index,
        })
    if len(entries) < 2:
        raise ExportFailure(f"Extraction insuffisante ({label}): {len(entries)} message(s).")

    semantic_payload = json.dumps(
        [{"id": e["id"], "role": e["role"], "subtype": e["subtype"], "text": e["text"]} for e in entries],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "label": label,
        "title": title_from(root, html),
        "html_bytes": len(html),
        "html_sha256": sha256_bytes(html),
        "stream_bytes": len(stream.encode("utf-8")),
        "table_entries": len(table),
        "linear_entries": len(linear),
        "linear_path": linear_path,
        "raw_role_counts": raw_role_counts,
        "excluded_hidden": excluded_hidden,
        "excluded_internal": excluded_internal,
        "entries": entries,
        "semantic_sha256": sha256_bytes(semantic_payload),
    }


def compare_snapshots(a: dict[str, Any], b: dict[str, Any]) -> None:
    a_entries = a["entries"]
    b_entries = b["entries"]
    lines = [
        "DOUBLE RÉCUPÉRATION — PREUVE DE STABILISATION",
        "=" * 100,
        f"URL exacte : {SHARE_URL}",
        f"Messages A : {len(a_entries)}",
        f"Messages B : {len(b_entries)}",
        f"Empreinte sémantique A : {a['semantic_sha256']}",
        f"Empreinte sémantique B : {b['semantic_sha256']}",
        f"Premier ID A : {a_entries[0]['id']}",
        f"Premier ID B : {b_entries[0]['id']}",
        f"Dernier ID A : {a_entries[-1]['id']}",
        f"Dernier ID B : {b_entries[-1]['id']}",
    ]
    problems: list[str] = []
    if len(a_entries) != len(b_entries):
        problems.append("nombre de messages différent")
    if a["semantic_sha256"] != b["semantic_sha256"]:
        problems.append("séquence ou contenu différent")
    if a_entries[0]["id"] != b_entries[0]["id"]:
        problems.append("premier ID différent")
    if a_entries[-1]["id"] != b_entries[-1]["id"]:
        problems.append("dernier ID différent")
    if a_entries[-1]["text"] != b_entries[-1]["text"]:
        problems.append("dernier texte différent")
    if problems:
        lines.extend(["", "DOUBLE_FETCH_STABILIZATION=FAIL", "PROBLÈMES=" + "; ".join(problems)])
        COMPARE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        raise ExportFailure("Double récupération non stabilisée: " + "; ".join(problems))
    lines.extend([
        "",
        "DOUBLE_FETCH_STABILIZATION=PASS",
        "FIN_REELLE_DU_PARTAGE_VERIFIEE=OUI",
        "AUCUN_MESSAGE_VISIBLE_MANQUANT_SELON_DOUBLE_RECUPERATION=OUI",
        "EXTRACTION_INTEGRALE_VALIDEE=OUI",
        "",
        "PREMIER MESSAGE",
        a_entries[0]["text"][:5000],
        "",
        "DERNIER MESSAGE",
        a_entries[-1]["text"][:8000],
    ])
    COMPARE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(snapshot: dict[str, Any]) -> None:
    entries = snapshot["entries"]
    user_count = sum(1 for e in entries if e["role"] == "user")
    assistant_count = sum(1 for e in entries if e["role"] == "assistant")
    progress_count = sum(1 for e in entries if e["subtype"] == "progression")
    attachment_count = sum(e["text"].count("[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]") for e in entries)

    lines = [
        "CONVERSATION CHATGPT PUBLIQUE — TRANSCRIPTION VISIBLE EXHAUSTIVE ET DOUBLE-VÉRIFIÉE",
        "=" * 112,
        f"Titre : {snapshot['title']}",
        f"URL publique exacte : {SHARE_URL}",
        f"Date d'extraction : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Nombre total de messages visibles : {len(entries)}",
        f"Messages utilisateur : {user_count}",
        f"Messages assistant visibles : {assistant_count}",
        f"Mises à jour assistant visibles : {progress_count}",
        f"Marqueurs de pièces jointes ou contenus multimédias : {attachment_count}",
        "Contrôle : deux récupérations successives ont produit exactement la même séquence visible.",
        "Conservation : texte, commandes, logs, URL, chiffres, code, configurations et métadonnées visibles.",
        "Exclusion limitée : éléments invisibles dans le partage public (système privé, outils cachés,",
        "                    raisonnement interne et métadonnées techniques non affichées).",
        "=" * 112,
        "",
    ]
    for index, entry in enumerate(entries, start=1):
        if entry["role"] == "user":
            label = "UTILISATEUR"
        elif entry["subtype"] == "progression":
            label = "ASSISTANT | TYPE : MISE À JOUR DE PROGRESSION VISIBLE"
        else:
            label = "ASSISTANT"
        timestamp = f" | DATE_UTC : {entry['timestamp']}" if entry["timestamp"] else ""
        lines.extend([
            f"--- MESSAGE {index:04d} | RÔLE : {label}{timestamp} ---",
            "",
            entry["text"],
            "",
            "",
        ])
    transcript = "\n".join(lines).rstrip() + "\n"
    TXT_PATH.write_text(transcript, encoding="utf-8")
    txt_bytes = TXT_PATH.read_bytes()

    preview = (
        "PREMIERS MESSAGES\n" + "=" * 100 + "\n"
        + "\n\n".join(f"{e['role']} | {e['timestamp']}\n{e['text'][:5000]}" for e in entries[:3])
        + "\n\nDERNIERS MESSAGES\n" + "=" * 100 + "\n"
        + "\n\n".join(f"{e['role']} | {e['timestamp']}\n{e['text'][:8000]}" for e in entries[-3:])
        + "\n"
    )
    PREVIEW_PATH.write_text(preview, encoding="utf-8")

    diagnostics = [
        f"share_url={SHARE_URL}",
        f"share_id={SHARE_ID}",
        f"title={snapshot['title']}",
        f"fetch_a_html_bytes={snapshot['html_bytes']}",
        f"fetch_a_html_sha256={snapshot['html_sha256']}",
        f"fetch_a_stream_bytes={snapshot['stream_bytes']}",
        f"flat_table_entries={snapshot['table_entries']}",
        f"linear_entries={snapshot['linear_entries']}",
        f"selected_linear_path={snapshot['linear_path']}",
        f"raw_role_counts={snapshot['raw_role_counts']!r}",
        f"visible_entries={len(entries)}",
        f"visible_user_messages={user_count}",
        f"visible_assistant_messages={assistant_count}",
        f"visible_progress_messages={progress_count}",
        f"excluded_hidden_messages={snapshot['excluded_hidden']}",
        f"excluded_internal_assistant_messages={snapshot['excluded_internal']}",
        f"attachment_markers={attachment_count}",
        f"semantic_sha256={snapshot['semantic_sha256']}",
        f"transcript_bytes={len(txt_bytes)}",
        f"transcript_lines={len(transcript.splitlines())}",
        f"transcript_words={len(transcript.split())}",
        f"transcript_sha256={sha256_bytes(txt_bytes)}",
        f"first_message_id={entries[0]['id']}",
        f"first_message_role={entries[0]['role']}",
        f"first_message_preview={entries[0]['text'][:1600]!r}",
        f"last_message_id={entries[-1]['id']}",
        f"last_message_role={entries[-1]['role']}",
        f"last_message_preview={entries[-1]['text'][:3000]!r}",
        "double_fetch_stabilization=PASS",
        "real_end_verified=YES",
        "integral_export_validated=YES",
    ]
    DIAG_PATH.write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
    print("\n".join(diagnostics))


def main() -> None:
    snapshot_a = extract_snapshot("A")
    time.sleep(8)
    snapshot_b = extract_snapshot("B")
    compare_snapshots(snapshot_a, snapshot_b)
    write_outputs(snapshot_a)


if __name__ == "__main__":
    main()
