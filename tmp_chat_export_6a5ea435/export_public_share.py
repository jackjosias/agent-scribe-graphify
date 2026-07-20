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
    "https://chatgpt.com/share/6a5ea435-a9a0-83ea-8c7a-b99d525f09dd",
)
SHARE_ID = SHARE_URL.rstrip("/").rsplit("/", 1)[-1]
ROOT = Path.cwd()
OUT_DIR = ROOT / "tmp_chat_export_6a5ea435" / "result"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HTML_PATH = OUT_DIR / "public-share.html"
STREAM_PATH = OUT_DIR / "decoded-react-stream.txt"
TXT_PATH = OUT_DIR / f"conversation-chatgpt-public-{SHARE_ID}-INTEGRALE.txt"
DIAGNOSTICS_PATH = OUT_DIR / "diagnostics.txt"
PREVIEW_PATH = OUT_DIR / "first-last-preview.txt"


class ExportFailure(RuntimeError):
    pass


def fetch_html() -> str:
    session = requests.Session(impersonate="chrome124")
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    attempts: list[str] = []
    for attempt in range(1, 6):
        try:
            response = session.get(SHARE_URL, headers=headers, timeout=180)
            attempts.append(
                f"attempt={attempt} status={response.status_code} "
                f"content_type={response.headers.get('content-type')} bytes={len(response.content)}"
            )
            if response.status_code == 200 and len(response.content) > 10_000:
                HTML_PATH.write_bytes(response.content)
                return response.text
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"attempt={attempt} exception={type(exc).__name__}: {exc}")
        time.sleep(attempt * 3)
    raise ExportFailure("Téléchargement public impossible:\n" + "\n".join(attempts))


def decode_react_stream(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    chunks: list[str] = []
    parse_errors: list[str] = []
    for index, script in enumerate(soup.find_all("script")):
        text = script.string if script.string is not None else script.get_text()
        if "streamController.enqueue" not in text:
            continue
        match = re.search(r"streamController\.enqueue\((.*)\)\s*;?\s*$", text, flags=re.S)
        if not match:
            parse_errors.append(f"script={index}: enqueue non reconnu")
            continue
        raw_argument = match.group(1).strip()
        try:
            decoded = json.loads(raw_argument)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"script={index}: {type(exc).__name__}: {exc}")
            continue
        if isinstance(decoded, str):
            chunks.append(decoded)
        else:
            chunks.append(json.dumps(decoded, ensure_ascii=False))

    stream = "".join(chunks)
    STREAM_PATH.write_text(stream, encoding="utf-8")
    if not stream:
        raise ExportFailure(
            "Aucun flux React Router décodable. " + "; ".join(parse_errors[:20])
        )
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
        raise ExportFailure("Table de données aplatie introuvable dans le flux public.")
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
        if not match:
            return key
        return str(self.decode_index(int(match.group(1))))

    def decode_object(
        self,
        raw: dict[str, Any],
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = target if target is not None else {}
        for raw_key, raw_value in raw.items():
            decoded = self.decode(raw_value)
            if decoded is not UNDEFINED:
                result[self.decode_key(raw_key)] = decoded
        return result

    def decode_list(self, raw: list[Any], target: list[Any] | None = None) -> Any:
        if raw and isinstance(raw[0], str) and raw[0] in {
            "Date",
            "URL",
            "BigInt",
            "RegExp",
            "Set",
            "Map",
        }:
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
            result: dict[str, Any] = {}
            self.memo[index] = result
            return self.decode_object(raw, result)
        if isinstance(raw, list):
            result_list: list[Any] = []
            self.memo[index] = result_list
            decoded = self.decode_list(raw, result_list)
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


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


ATTACHMENT_FIELDS = (
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
    for field_name in ATTACHMENT_FIELDS:
        field_value = value.get(field_name)
        if isinstance(field_value, (str, int, float)) and str(field_value).strip():
            lines.append(f"{field_name}: {field_value}")
    if len(lines) == 1:
        detected = value.get("content_type") or value.get("type") or "élément joint"
        lines.append(f"type: {detected}")
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
        token in content_type
        for token in ("asset_pointer", "attachment", "file", "image", "audio", "video")
    )

    # Preserve body text before auxiliary labels.
    for key in ("text", "content", "caption", "transcript", "result", "output"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            chunks.append(field)
        elif isinstance(field, (dict, list)):
            chunks.extend(extract_visible_chunks(field, is_attachment))

    if is_attachment:
        chunks.append(attachment_block(value))

    for key in (
        "parts",
        "items",
        "attachments",
        "files",
        "children",
        "data",
        "references",
        "citations",
    ):
        field = value.get(key)
        if isinstance(field, (dict, list)):
            chunks.extend(extract_visible_chunks(field, is_attachment))

    # Keep human-readable labels and public links when exposed.
    for key in ("name", "filename", "file_name", "title", "label", "url"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            if key == "url":
                chunks.append(field)
            elif not is_attachment:
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


def iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def select_title(root: Any, html: str) -> str:
    candidates: list[str] = []
    for _, value in find_key(root, "title"):
        if isinstance(value, str):
            cleaned = normalize_text(value)
            if cleaned:
                candidates.append(cleaned)
    generic = {
        "chatgpt",
        "new chat",
        "nouvelle conversation",
        "conversation chatgpt publique",
    }
    for candidate in candidates:
        if candidate.lower() not in generic and len(candidate) <= 300:
            return candidate

    soup = BeautifulSoup(html, "html.parser")
    if soup.title:
        title = normalize_text(soup.title.get_text(" "))
        title = re.sub(r"^ChatGPT\s*[-–—]\s*", "", title, flags=re.I)
        if title:
            return title
    return "Conversation ChatGPT publique"


def main() -> None:
    html = fetch_html()
    stream = decode_react_stream(html)
    table = locate_flat_table(stream)
    root = FlatDecoder(table).decode_index(0)

    linear_matches = find_key(root, "linear_conversation")
    linear_path = ""
    linear: list[Any] = []
    for path, candidate in linear_matches:
        if isinstance(candidate, list) and len(candidate) > len(linear):
            linear_path = path
            linear = candidate
    if not linear:
        raise ExportFailure("Aucune linear_conversation détectée dans le partage public.")

    title = select_title(root, html)
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    raw_role_counts: dict[str, int] = {}
    excluded_hidden = 0
    excluded_internal_assistant = 0

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
            excluded_internal_assistant += 1
            continue

        message_id = str(message.get("id") or item.get("id") or "")
        if message_id and message_id in seen_ids:
            continue
        if message_id:
            seen_ids.add(message_id)

        channel = str(message.get("channel") or "")
        subtype = "progression" if role == "assistant" and channel == "commentary" else "message"
        entries.append(
            {
                "role": role,
                "subtype": subtype,
                "text": text,
                "timestamp": iso_timestamp(message.get("create_time")),
                "linear_index": linear_index,
            }
        )

    if len(entries) < 2:
        raise ExportFailure(f"Seulement {len(entries)} message(s) visible(s) extrait(s).")

    user_count = sum(1 for entry in entries if entry["role"] == "user")
    assistant_count = sum(1 for entry in entries if entry["role"] == "assistant")
    progress_count = sum(1 for entry in entries if entry["subtype"] == "progression")
    attachment_count = sum(
        entry["text"].count("[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]") for entry in entries
    )

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
        f"Marqueurs de pièces jointes ou contenus multimédias : {attachment_count}",
        "Politique : aucun message visible, commande, URL, nombre, log, configuration, workflow,",
        "            citation ou texte collé disponible dans le partage n'a été résumé.",
        "Exclusion limitée : éléments invisibles dans le partage public (système privé, outils cachés,",
        "                    raisonnement interne et métadonnées techniques non affichées).",
        "=" * 108,
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
        lines.extend(
            [
                f"--- MESSAGE {index:04d} | RÔLE : {label}{timestamp} ---",
                "",
                entry["text"],
                "",
                "",
            ]
        )

    transcript = "\n".join(lines).rstrip() + "\n"
    TXT_PATH.write_text(transcript, encoding="utf-8")
    sha256 = hashlib.sha256(TXT_PATH.read_bytes()).hexdigest()

    PREVIEW_PATH.write_text(
        "PREMIERS MESSAGES\n"
        + "=" * 90
        + "\n"
        + "\n\n".join(
            f"{entry['role']} | {entry['timestamp']}\n{entry['text'][:4000]}"
            for entry in entries[:3]
        )
        + "\n\nDERNIERS MESSAGES\n"
        + "=" * 90
        + "\n"
        + "\n\n".join(
            f"{entry['role']} | {entry['timestamp']}\n{entry['text'][:4000]}"
            for entry in entries[-3:]
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
        f"selected_linear_path={linear_path}",
        f"linear_entries={len(linear)}",
        f"raw_role_counts={raw_role_counts!r}",
        f"visible_entries={len(entries)}",
        f"visible_user_messages={user_count}",
        f"visible_assistant_messages={assistant_count}",
        f"visible_progress_messages={progress_count}",
        f"excluded_hidden_messages={excluded_hidden}",
        f"excluded_internal_assistant_messages={excluded_internal_assistant}",
        f"attachment_markers={attachment_count}",
        f"transcript_bytes={TXT_PATH.stat().st_size}",
        f"transcript_lines={len(transcript.splitlines())}",
        f"sha256={sha256}",
        f"first_message_role={entries[0]['role']}",
        f"first_message_preview={entries[0]['text'][:1200]!r}",
        f"last_message_role={entries[-1]['role']}",
        f"last_message_preview={entries[-1]['text'][:1600]!r}",
    ]
    DIAGNOSTICS_PATH.write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
    print("\n".join(diagnostics))


if __name__ == "__main__":
    main()
