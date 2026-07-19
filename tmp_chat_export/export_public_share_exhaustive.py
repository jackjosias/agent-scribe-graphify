from __future__ import annotations

import hashlib
import json
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
    "https://chatgpt.com/share/6a5cc6b4-e414-83ea-919a-21248677c938",
)
SHARE_ID = SHARE_URL.rstrip("/").rsplit("/", 1)[-1]
ROOT = Path.cwd()
OUT = ROOT / "tmp_chat_export" / "result"
OUT.mkdir(parents=True, exist_ok=True)

HTML_PATH = OUT / "public-share.html"
STREAM_PATH = OUT / "decoded-react-stream.txt"
PAYLOAD_PATH = OUT / "decoded-public-payload.json"
TRANSCRIPT_PATH = OUT / f"conversation-chatgpt-public-{SHARE_ID}-INTEGRALE.txt"
DIAGNOSTICS_PATH = OUT / "diagnostics.txt"
PREVIEW_PATH = OUT / "first-last-preview.txt"


def fetch_public_html() -> str:
    session = requests.Session(impersonate="chrome124")
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    errors: list[str] = []
    for attempt in range(1, 6):
        try:
            response = session.get(SHARE_URL, headers=headers, timeout=180)
            if response.status_code == 200 and len(response.content) > 10_000:
                HTML_PATH.write_bytes(response.content)
                return response.text
            errors.append(
                f"attempt={attempt} status={response.status_code} "
                f"content_type={response.headers.get('content-type')} bytes={len(response.content)}"
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"attempt={attempt} {type(exc).__name__}: {exc}")
        time.sleep(attempt * 3)
    raise RuntimeError("Impossible de télécharger le partage public:\n" + "\n".join(errors))


def decode_stream_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    chunks: list[str] = []
    errors: list[str] = []
    for index, script in enumerate(soup.find_all("script")):
        text = script.string if script.string is not None else script.get_text()
        if "streamController.enqueue" not in text:
            continue
        match = re.search(r"streamController\.enqueue\((.*)\)\s*;?\s*$", text, flags=re.S)
        if not match:
            errors.append(f"script={index}: enqueue argument not parsed")
            continue
        argument = match.group(1).strip()
        try:
            decoded = json.loads(argument)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"script={index}: {type(exc).__name__}: {exc}")
            continue
        if isinstance(decoded, str):
            chunks.append(decoded)
        else:
            chunks.append(json.dumps(decoded, ensure_ascii=False))
    stream = "".join(chunks)
    STREAM_PATH.write_text(stream, encoding="utf-8")
    if not stream:
        raise RuntimeError("Aucun flux React Router public n'a été décodé. " + "; ".join(errors[:10]))
    return stream


def locate_flat_table(stream: str) -> list[Any]:
    candidates: list[list[Any]] = []
    for line in stream.splitlines():
        stripped = line.strip()
        if not stripped.startswith("["):
            continue
        try:
            value = json.loads(stripped)
        except Exception:
            continue
        if isinstance(value, list):
            candidates.append(value)
    if not candidates:
        # Fallback: find a large JSON array bounded by line breaks.
        for match in re.finditer(r"(?m)^\s*(\[.*\])\s*$", stream):
            try:
                value = json.loads(match.group(1))
            except Exception:
                continue
            if isinstance(value, list):
                candidates.append(value)
    if not candidates:
        raise RuntimeError("La table aplatie du partage public n'a pas été trouvée.")
    return max(candidates, key=len)


UNDEFINED = object()
IN_PROGRESS = object()
SPECIALS: dict[int, Any] = {
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

    def decode_reference(self, value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int):
            if value < 0:
                return SPECIALS.get(value)
            if value >= len(self.table):
                return value
            return self.decode_index(value)
        if isinstance(value, (float, str)):
            return value
        if isinstance(value, dict):
            return self.decode_raw_object(value)
        if isinstance(value, list):
            return self.decode_raw_list(value)
        return value

    def decode_key(self, raw_key: str) -> str:
        match = re.fullmatch(r"_(\d+)", raw_key)
        if not match:
            return raw_key
        return str(self.decode_index(int(match.group(1))))

    def decode_raw_object(
        self,
        raw: dict[str, Any],
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = target if target is not None else {}
        for raw_key, raw_value in raw.items():
            decoded = self.decode_reference(raw_value)
            if decoded is not UNDEFINED:
                result[self.decode_key(raw_key)] = decoded
        return result

    def decode_raw_list(self, raw: list[Any], target: list[Any] | None = None) -> Any:
        if raw and isinstance(raw[0], str) and raw[0] in {
            "Date",
            "URL",
            "BigInt",
            "RegExp",
            "Set",
            "Map",
        }:
            tag = raw[0]
            values = [self.decode_reference(item) for item in raw[1:]]
            if tag in {"Date", "URL", "BigInt"}:
                return values[0] if values else None
            if tag == "Set":
                return values
            if tag == "Map":
                mapped: dict[str, Any] = {}
                for index in range(0, len(values) - 1, 2):
                    mapped[str(values[index])] = values[index + 1]
                return mapped
            return values
        result = target if target is not None else []
        for item in raw:
            decoded = self.decode_reference(item)
            if decoded is not UNDEFINED:
                result.append(decoded)
        return result

    def decode_index(self, index: int) -> Any:
        if index in self.memo:
            cached = self.memo[index]
            return None if cached is IN_PROGRESS else cached
        raw = self.table[index]
        if isinstance(raw, dict):
            result: dict[str, Any] = {}
            self.memo[index] = result
            return self.decode_raw_object(raw, result)
        if isinstance(raw, list):
            result_list: list[Any] = []
            self.memo[index] = result_list
            decoded = self.decode_raw_list(raw, result_list)
            if decoded is not result_list:
                self.memo[index] = decoded
            return decoded
        self.memo[index] = raw
        return raw


def find_key(
    value: Any,
    wanted: str,
    path: str = "$",
    seen: set[int] | None = None,
) -> list[tuple[str, Any]]:
    if seen is None:
        seen = set()
    if isinstance(value, (dict, list)):
        object_id = id(value)
        if object_id in seen:
            return []
        seen.add(object_id)
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        if wanted in value:
            found.append((f"{path}.{wanted}", value[wanted]))
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                found.extend(find_key(child, wanted, f"{path}.{key}", seen))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                found.extend(find_key(child, wanted, f"{path}[{index}]", seen))
    return found


def normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = normalize_line_endings(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


VISIBLE_ATTACHMENT_FIELDS = (
    "name",
    "filename",
    "file_name",
    "title",
    "mime_type",
    "content_type",
    "size",
    "size_bytes",
    "width",
    "height",
    "url",
)


def attachment_block(value: dict[str, Any]) -> str:
    lines = ["[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]"]
    for key in VISIBLE_ATTACHMENT_FIELDS:
        field = value.get(key)
        if isinstance(field, (str, int, float)) and str(field).strip():
            lines.append(f"{key}: {field}")
    if len(lines) == 1:
        content_type = value.get("content_type") or value.get("type") or "élément joint"
        lines.append(f"type: {content_type}")
    return "\n".join(lines)


def extract_all_visible_strings(value: Any, *, attachment_context: bool = False) -> list[str]:
    """Preserve all textual payloads that can represent visible conversation content."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (int, float, bool)) or value is None:
        return []
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(extract_all_visible_strings(item, attachment_context=attachment_context))
        return ordered_unique(chunks)
    if not isinstance(value, dict):
        return []

    chunks: list[str] = []
    content_type = str(value.get("content_type") or value.get("type") or "").lower()
    is_attachment = attachment_context or any(
        token in content_type
        for token in ("asset_pointer", "attachment", "file", "image", "audio", "video")
    )

    # Exact visible body fields first.
    for key in ("text", "content", "caption", "transcript", "result", "output"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            chunks.append(field)
        elif isinstance(field, (dict, list)):
            chunks.extend(extract_all_visible_strings(field, attachment_context=is_attachment))

    if is_attachment:
        chunks.append(attachment_block(value))

    # Recurse into containers likely to hold pasted text, citations, or attachments.
    for key in (
        "parts",
        "items",
        "attachments",
        "files",
        "children",
        "data",
        "metadata",
        "references",
        "citations",
    ):
        field = value.get(key)
        if isinstance(field, (dict, list)):
            chunks.extend(extract_all_visible_strings(field, attachment_context=is_attachment))

    # Keep visible URLs and labels not already captured, but ignore opaque internal pointers/IDs.
    for key in ("name", "filename", "file_name", "title", "label", "url"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            if key == "url":
                chunks.append(field)
            elif is_attachment:
                # Already represented in the attachment block.
                continue
            else:
                chunks.append(field)

    return ordered_unique(chunks)


def extract_message_text(message: dict[str, Any]) -> str:
    chunks = extract_all_visible_strings(message.get("content"))

    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        for key in ("attachments", "files", "citations"):
            field = metadata.get(key)
            if isinstance(field, (dict, list)):
                chunks.extend(extract_all_visible_strings(field, attachment_context=True))

    return normalize_line_endings("\n\n".join(ordered_unique(chunks)))


def is_hidden(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata")
    return isinstance(metadata, dict) and metadata.get("is_visually_hidden_from_conversation") is True


def visible_assistant_message(message: dict[str, Any], text: str) -> bool:
    if is_hidden(message):
        return False
    recipient = message.get("recipient")
    if recipient not in (None, "", "all"):
        return False
    channel = message.get("channel")
    if channel not in (None, "", "final", "commentary"):
        return False
    status = message.get("status")
    if status not in (None, "", "finished", "finished_successfully"):
        return False
    if text.strip() == "The output of this plugin was redacted.":
        return False
    return True


def iso_time(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def main() -> None:
    html = fetch_public_html()
    stream = decode_stream_from_html(html)
    table = locate_flat_table(stream)
    root = FlatDecoder(table).decode_index(0)
    PAYLOAD_PATH.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")

    linear_matches = find_key(root, "linear_conversation")
    selected_path = ""
    linear: list[Any] = []
    for path, candidate in linear_matches:
        if isinstance(candidate, list) and len(candidate) > len(linear):
            selected_path = path
            linear = candidate
    if not linear:
        raise RuntimeError("Aucune linear_conversation n'a été trouvée dans le partage public.")

    title = "Conversation ChatGPT publique"
    for _, candidate in find_key(root, "title"):
        if isinstance(candidate, str) and candidate.strip() and len(candidate.strip()) < 300:
            cleaned = candidate.strip()
            if cleaned.lower() not in {"chatgpt", "new chat", "nouvelle conversation"}:
                title = cleaned
                break

    entries: list[dict[str, Any]] = []
    seen_message_ids: set[str] = set()
    raw_role_counts: dict[str, int] = {}
    excluded_assistant = 0
    attachment_markers = 0

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
        if is_hidden(message):
            if role == "assistant":
                excluded_assistant += 1
            continue

        text = extract_message_text(message)
        if not text:
            continue
        if role == "assistant" and not visible_assistant_message(message, text):
            excluded_assistant += 1
            continue

        message_id = str(message.get("id") or item.get("id") or "")
        if message_id and message_id in seen_message_ids:
            continue
        if message_id:
            seen_message_ids.add(message_id)

        channel = str(message.get("channel") or "")
        subtype = "progression" if role == "assistant" and channel == "commentary" else "message"
        timestamp = iso_time(message.get("create_time"))
        attachment_markers += text.count("[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]")
        entries.append(
            {
                "id": message_id,
                "role": role,
                "subtype": subtype,
                "text": text,
                "timestamp": timestamp,
                "linear_index": linear_index,
            }
        )

    if len(entries) < 2:
        raise RuntimeError(f"Extraction insuffisante : seulement {len(entries)} message(s) visible(s).")

    user_count = sum(1 for entry in entries if entry["role"] == "user")
    assistant_count = sum(1 for entry in entries if entry["role"] == "assistant")
    progress_count = sum(1 for entry in entries if entry["subtype"] == "progression")

    lines = [
        "CONVERSATION CHATGPT PUBLIQUE — TRANSCRIPTION VISIBLE EXHAUSTIVE",
        "=" * 108,
        f"Titre : {title}",
        f"URL publique exacte : {SHARE_URL}",
        f"Date d'extraction : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Nombre total de messages visibles : {len(entries)}",
        f"Messages utilisateur : {user_count}",
        f"Messages assistant visibles : {assistant_count}",
        f"Mises à jour assistant visibles : {progress_count}",
        f"Marqueurs de pièces jointes ou contenus multimédias : {attachment_markers}",
        "Politique : aucune donnée visible, commande, URL, chiffre, log, configuration, workflow,",
        "            citation, texte collé ou contenu externe présent dans les messages n'a été résumé.",
        "Exclusion limitée : éléments non affichés dans le partage public (système privé, outils cachés,",
        "                    raisonnement interne invisible et métadonnées purement techniques).",
        "=" * 108,
        "",
    ]

    for index, entry in enumerate(entries, start=1):
        if entry["role"] == "user":
            role_label = "UTILISATEUR"
        elif entry["subtype"] == "progression":
            role_label = "ASSISTANT | TYPE : MISE À JOUR DE PROGRESSION VISIBLE"
        else:
            role_label = "ASSISTANT"
        timestamp = f" | DATE_UTC : {entry['timestamp']}" if entry["timestamp"] else ""
        lines.extend(
            [
                f"--- MESSAGE {index:04d} | RÔLE : {role_label}{timestamp} ---",
                "",
                entry["text"],
                "",
                "",
            ]
        )

    transcript = "\n".join(lines).rstrip() + "\n"
    TRANSCRIPT_PATH.write_text(transcript, encoding="utf-8")
    sha256 = hashlib.sha256(TRANSCRIPT_PATH.read_bytes()).hexdigest()

    first_entries = entries[:3]
    last_entries = entries[-3:]
    PREVIEW_PATH.write_text(
        "PREMIERS MESSAGES\n"
        + "=" * 80
        + "\n"
        + "\n\n".join(
            f"{entry['role']} | {entry['timestamp']}\n{entry['text'][:3000]}" for entry in first_entries
        )
        + "\n\nDERNIERS MESSAGES\n"
        + "=" * 80
        + "\n"
        + "\n\n".join(
            f"{entry['role']} | {entry['timestamp']}\n{entry['text'][:3000]}" for entry in last_entries
        )
        + "\n",
        encoding="utf-8",
    )

    diagnostics = [
        f"share_url={SHARE_URL}",
        f"share_id={SHARE_ID}",
        f"title={title}",
        f"html_bytes={HTML_PATH.stat().st_size}",
        f"decoded_stream_bytes={STREAM_PATH.stat().st_size}",
        f"flat_table_entries={len(table)}",
        f"selected_linear_path={selected_path}",
        f"linear_entries={len(linear)}",
        f"raw_role_counts={raw_role_counts!r}",
        f"visible_entries={len(entries)}",
        f"visible_user_messages={user_count}",
        f"visible_assistant_messages={assistant_count}",
        f"visible_progress_messages={progress_count}",
        f"excluded_internal_assistant_messages={excluded_assistant}",
        f"attachment_markers={attachment_markers}",
        f"transcript_bytes={TRANSCRIPT_PATH.stat().st_size}",
        f"transcript_lines={len(transcript.splitlines())}",
        f"sha256={sha256}",
        f"first_message_role={entries[0]['role']}",
        f"first_message_preview={entries[0]['text'][:800]!r}",
        f"last_message_role={entries[-1]['role']}",
        f"last_message_preview={entries[-1]['text'][:1200]!r}",
    ]
    DIAGNOSTICS_PATH.write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
    print("\n".join(diagnostics))


if __name__ == "__main__":
    main()
