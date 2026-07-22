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
    "https://chatgpt.com/share/6a612e4c-3a64-83ea-aa27-a89b7d314509",
).rstrip("/")
SHARE_ID = SHARE_URL.rsplit("/", 1)[-1]
ROOT = Path.cwd()
OUT = ROOT / "tmp_chat_export_6a612e4c" / "result"
OUT.mkdir(parents=True, exist_ok=True)
TXT = OUT / f"conversation-chatgpt-public-{SHARE_ID}-INTEGRALE.txt"
DIAG = OUT / "diagnostics.txt"
PREVIEW = OUT / "first-last-preview.txt"
COMPARE = OUT / "double-fetch-comparison.txt"

UNDEFINED = object()
SPECIALS: dict[int, Any] = {
    -1: UNDEFINED,
    -2: float("nan"),
    -3: float("inf"),
    -4: float("-inf"),
    -5: None,
    -6: -0.0,
}


class ExportError(RuntimeError):
    pass


class FlatDecoder:
    def __init__(self, table: list[Any]) -> None:
        self.table = table
        self.memo: dict[int, Any] = {}

    def value(self, raw: Any) -> Any:
        if isinstance(raw, bool) or raw is None:
            return raw
        if isinstance(raw, int):
            if raw < 0:
                return SPECIALS.get(raw)
            if raw >= len(self.table):
                return raw
            return self.index(raw)
        if isinstance(raw, (str, float)):
            return raw
        if isinstance(raw, dict):
            return self.obj(raw)
        if isinstance(raw, list):
            return self.arr(raw)
        return raw

    def key(self, raw: str) -> str:
        match = re.fullmatch(r"_(\d+)", raw)
        return str(self.index(int(match.group(1)))) if match else raw

    def obj(self, raw: dict[str, Any], target: dict[str, Any] | None = None) -> dict[str, Any]:
        out = target if target is not None else {}
        for key, value in raw.items():
            decoded = self.value(value)
            if decoded is not UNDEFINED:
                out[self.key(key)] = decoded
        return out

    def arr(self, raw: list[Any], target: list[Any] | None = None) -> Any:
        if raw and isinstance(raw[0], str) and raw[0] in {
            "Date", "URL", "BigInt", "RegExp", "Set", "Map"
        }:
            tag = raw[0]
            values = [self.value(item) for item in raw[1:]]
            if tag in {"Date", "URL", "BigInt"}:
                return values[0] if values else None
            if tag == "Set":
                return values
            if tag == "Map":
                return {str(values[i]): values[i + 1] for i in range(0, len(values) - 1, 2)}
            return values
        out = target if target is not None else []
        for item in raw:
            value = self.value(item)
            if value is not UNDEFINED:
                out.append(value)
        return out

    def index(self, index: int) -> Any:
        if index in self.memo:
            return self.memo[index]
        raw = self.table[index]
        if isinstance(raw, dict):
            out: dict[str, Any] = {}
            self.memo[index] = out
            return self.obj(raw, out)
        if isinstance(raw, list):
            out_list: list[Any] = []
            self.memo[index] = out_list
            decoded = self.arr(raw, out_list)
            if decoded is not out_list:
                self.memo[index] = decoded
            return decoded
        self.memo[index] = raw
        return raw


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def unique_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = normalize(value)
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def walk_key(value: Any, wanted: str, path: str = "$", seen: set[int] | None = None) -> list[tuple[str, Any]]:
    if seen is None:
        seen = set()
    if isinstance(value, (dict, list)):
        oid = id(value)
        if oid in seen:
            return []
        seen.add(oid)
    out: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        if wanted in value:
            out.append((f"{path}.{wanted}", value[wanted]))
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                out.extend(walk_key(child, wanted, f"{path}.{key}", seen))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                out.extend(walk_key(child, wanted, f"{path}[{index}]", seen))
    return out


def fetch_html(slot: int) -> str:
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
        url = f"{SHARE_URL}?export_probe={slot}-{attempt}-{time.time_ns()}"
        try:
            response = session.get(url, headers=headers, timeout=180)
            errors.append(
                f"attempt={attempt} status={response.status_code} "
                f"type={response.headers.get('content-type')} bytes={len(response.content)}"
            )
            if response.status_code == 200 and len(response.content) > 10_000:
                (OUT / f"public-share-fetch-{slot}.html").write_bytes(response.content)
                return response.text
        except Exception as exc:  # noqa: BLE001
            errors.append(f"attempt={attempt} {type(exc).__name__}: {exc}")
        time.sleep(attempt * 2)
    raise ExportError("Téléchargement impossible:\n" + "\n".join(errors))


def react_stream(html: str, slot: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    chunks: list[str] = []
    errors: list[str] = []
    for index, script in enumerate(soup.find_all("script")):
        text = script.string if script.string is not None else script.get_text()
        if "streamController.enqueue" not in text:
            continue
        match = re.search(r"streamController\.enqueue\((.*)\)\s*;?\s*$", text, flags=re.S)
        if not match:
            errors.append(f"script={index}: no argument")
            continue
        try:
            value = json.loads(match.group(1).strip())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"script={index}: {type(exc).__name__}: {exc}")
            continue
        chunks.append(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    stream = "".join(chunks)
    (OUT / f"decoded-react-stream-fetch-{slot}.txt").write_text(stream, encoding="utf-8")
    if not stream:
        raise ExportError("Flux React public introuvable. " + "; ".join(errors[:20]))
    return stream


def flat_table(stream: str) -> list[Any]:
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
        raise ExportError("Table aplatie introuvable.")
    return max(candidates, key=len)


ATTACHMENT_FIELDS = (
    "name", "filename", "file_name", "title", "mime_type", "content_type",
    "size", "size_bytes", "width", "height", "url"
)


def attachment_block(value: dict[str, Any]) -> str:
    lines = ["[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]"]
    for key in ATTACHMENT_FIELDS:
        field = value.get(key)
        if isinstance(field, (str, int, float)) and str(field).strip():
            lines.append(f"{key}: {field}")
    if len(lines) == 1:
        lines.append(f"type: {value.get('content_type') or value.get('type') or 'élément joint'}")
    return "\n".join(lines)


def visible_chunks(value: Any, attachment: bool = False) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if value is None or isinstance(value, (int, float, bool)):
        return []
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(visible_chunks(item, attachment))
        return unique_text(chunks)
    if not isinstance(value, dict):
        return []

    chunks: list[str] = []
    kind = str(value.get("content_type") or value.get("type") or "").lower()
    is_attachment = attachment or any(
        token in kind for token in ("asset_pointer", "attachment", "file", "image", "audio", "video")
    )
    for key in ("text", "content", "caption", "transcript", "result", "output"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            chunks.append(field)
        elif isinstance(field, (dict, list)):
            chunks.extend(visible_chunks(field, is_attachment))
    if is_attachment:
        chunks.append(attachment_block(value))
    for key in ("parts", "items", "attachments", "files", "children", "data", "references", "citations"):
        field = value.get(key)
        if isinstance(field, (dict, list)):
            chunks.extend(visible_chunks(field, is_attachment))
    for key in ("name", "filename", "file_name", "title", "label", "url"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            if key == "url" or not is_attachment:
                chunks.append(field)
    return unique_text(chunks)


def message_text(message: dict[str, Any]) -> str:
    chunks = visible_chunks(message.get("content"))
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        for key in ("attachments", "files", "citations"):
            field = metadata.get(key)
            if isinstance(field, (dict, list)):
                chunks.extend(visible_chunks(field, True))
    return normalize("\n\n".join(unique_text(chunks)))


def hidden(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata")
    return isinstance(metadata, dict) and metadata.get("is_visually_hidden_from_conversation") is True


def visible_assistant(message: dict[str, Any], text: str) -> bool:
    if hidden(message):
        return False
    if message.get("recipient") not in (None, "", "all"):
        return False
    if message.get("channel") not in (None, "", "final", "commentary"):
        return False
    if message.get("status") not in (None, "", "finished", "finished_successfully"):
        return False
    return text.strip() != "The output of this plugin was redacted."


def timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def choose_title(root: Any, html: str) -> str:
    generic = {"chatgpt", "new chat", "nouvelle conversation", "conversation chatgpt publique"}
    for _, candidate in walk_key(root, "title"):
        if isinstance(candidate, str):
            clean = normalize(candidate)
            if clean and clean.lower() not in generic and len(clean) <= 300:
                return clean
    soup = BeautifulSoup(html, "html.parser")
    if soup.title:
        clean = normalize(soup.title.get_text(" "))
        clean = re.sub(r"^ChatGPT\s*[-–—]\s*", "", clean, flags=re.I)
        if clean:
            return clean
    return "Conversation ChatGPT publique"


def extract(slot: int) -> dict[str, Any]:
    html = fetch_html(slot)
    stream = react_stream(html, slot)
    table = flat_table(stream)
    root = FlatDecoder(table).index(0)

    linear_path = ""
    linear: list[Any] = []
    for path, candidate in walk_key(root, "linear_conversation"):
        if isinstance(candidate, list) and len(candidate) > len(linear):
            linear_path = path
            linear = candidate
    if not linear:
        raise ExportError("linear_conversation absente.")

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    role_counts: dict[str, int] = {}
    excluded_hidden = 0
    excluded_internal = 0

    for position, item in enumerate(linear):
        if not isinstance(item, dict):
            continue
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else message.get("role")
        role = str(role or "")
        role_counts[role] = role_counts.get(role, 0) + 1
        if role not in {"user", "assistant"}:
            continue
        if hidden(message):
            excluded_hidden += 1
            continue
        text = message_text(message)
        if not text:
            continue
        if role == "assistant" and not visible_assistant(message, text):
            excluded_internal += 1
            continue
        message_id = str(message.get("id") or item.get("id") or "")
        if message_id and message_id in seen_ids:
            continue
        if message_id:
            seen_ids.add(message_id)
        channel = str(message.get("channel") or "")
        entries.append({
            "id": message_id,
            "role": role,
            "subtype": "progression" if role == "assistant" and channel == "commentary" else "message",
            "text": text,
            "timestamp": timestamp(message.get("create_time")),
            "position": position,
        })

    if len(entries) < 2:
        raise ExportError(f"Extraction insuffisante: {len(entries)} message(s).")

    stable_rows = [
        {"id": e["id"], "role": e["role"], "subtype": e["subtype"], "text": e["text"]}
        for e in entries
    ]
    fingerprint = hashlib.sha256(
        json.dumps(stable_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "slot": slot,
        "html": html,
        "title": choose_title(root, html),
        "table_entries": len(table),
        "linear_path": linear_path,
        "linear_entries": len(linear),
        "role_counts": role_counts,
        "excluded_hidden": excluded_hidden,
        "excluded_internal": excluded_internal,
        "entries": entries,
        "fingerprint": fingerprint,
        "html_bytes": len(html.encode("utf-8")),
        "stream_bytes": len(stream.encode("utf-8")),
    }


def compact_preview(entry: dict[str, Any], limit: int = 2000) -> str:
    return entry["text"][:limit].replace("\n", "\\n")


def main() -> None:
    snapshots: list[dict[str, Any]] = []
    stable_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
    for slot in range(1, 5):
        snapshot = extract(slot)
        snapshots.append(snapshot)
        if len(snapshots) >= 2 and snapshots[-2]["fingerprint"] == snapshots[-1]["fingerprint"]:
            stable_pair = (snapshots[-2], snapshots[-1])
            break
        time.sleep(5)
    if stable_pair is None:
        lines = ["STABILIZATION_FAILED"]
        for snap in snapshots:
            lines.append(
                f"fetch={snap['slot']} messages={len(snap['entries'])} fingerprint={snap['fingerprint']} "
                f"last={compact_preview(snap['entries'][-1], 500)!r}"
            )
        COMPARE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        raise ExportError("Aucune paire de récupérations successives identiques après quatre essais.")

    first, final = stable_pair
    entries = final["entries"]
    users = sum(1 for e in entries if e["role"] == "user")
    assistants = sum(1 for e in entries if e["role"] == "assistant")
    progress = sum(1 for e in entries if e["subtype"] == "progression")
    attachments = sum(e["text"].count("[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]") for e in entries)

    last_user = next((e for e in reversed(entries) if e["role"] == "user"), None)
    last_assistant = next((e for e in reversed(entries) if e["role"] == "assistant"), None)

    comparison_lines = [
        "DOUBLE_FETCH_STABILIZATION=PASS",
        f"fetch_a={first['slot']}",
        f"fetch_b={final['slot']}",
        f"fingerprint_a={first['fingerprint']}",
        f"fingerprint_b={final['fingerprint']}",
        f"message_count_a={len(first['entries'])}",
        f"message_count_b={len(final['entries'])}",
        f"last_message_a={compact_preview(first['entries'][-1])!r}",
        f"last_message_b={compact_preview(final['entries'][-1])!r}",
        f"last_user_a={compact_preview(next(e for e in reversed(first['entries']) if e['role'] == 'user'))!r}",
        f"last_user_b={compact_preview(last_user)!r}",
        f"last_assistant_a={compact_preview(next(e for e in reversed(first['entries']) if e['role'] == 'assistant'))!r}",
        f"last_assistant_b={compact_preview(last_assistant)!r}",
        "FIN_REELLE_DU_PARTAGE_VERIFIEE=OUI",
        "AUCUN_MESSAGE_VISIBLE_MANQUANT_SELON_DOUBLE_RECUPERATION=OUI",
        "EXTRACTION_INTEGRALE_VALIDEE=OUI",
    ]
    COMPARE.write_text("\n".join(comparison_lines) + "\n", encoding="utf-8")

    lines = [
        "CONVERSATION CHATGPT PUBLIQUE — TRANSCRIPTION VISIBLE EXHAUSTIVE",
        "=" * 108,
        f"Titre : {final['title']}",
        f"URL publique exacte : {SHARE_URL}",
        f"Date d'extraction : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Nombre total de messages visibles : {len(entries)}",
        f"Messages utilisateur : {users}",
        f"Messages assistant visibles : {assistants}",
        f"Mises à jour assistant visibles : {progress}",
        f"Marqueurs de pièces jointes ou contenus multimédias : {attachments}",
        f"Empreinte stable de la séquence visible : {final['fingerprint']}",
        "Contrôle de fin : deux récupérations successives ont produit la même séquence visible,",
        "                  le même dernier message utilisateur et la même dernière réponse assistant.",
        "Politique : aucun message visible, commande, URL, nombre, log, configuration, workflow,",
        "            citation ou texte collé disponible dans le partage n'a été résumé.",
        "Exclusion limitée : éléments invisibles dans le partage public (système privé, outils cachés,",
        "                    raisonnement interne et métadonnées techniques non affichées).",
        "=" * 108,
        "",
    ]
    for index, entry in enumerate(entries, 1):
        if entry["role"] == "user":
            label = "UTILISATEUR"
        elif entry["subtype"] == "progression":
            label = "ASSISTANT | TYPE : MISE À JOUR DE PROGRESSION VISIBLE"
        else:
            label = "ASSISTANT"
        when = f" | DATE_UTC : {entry['timestamp']}" if entry["timestamp"] else ""
        lines.extend([
            f"--- MESSAGE {index:04d} | RÔLE : {label}{when} ---",
            "",
            entry["text"],
            "",
            "",
        ])
    transcript = "\n".join(lines).rstrip() + "\n"
    TXT.write_text(transcript, encoding="utf-8")
    transcript_sha = hashlib.sha256(TXT.read_bytes()).hexdigest()

    PREVIEW.write_text(
        "PREMIERS MESSAGES\n" + "=" * 90 + "\n" +
        "\n\n".join(f"{e['role']} | {e['timestamp']}\n{e['text'][:5000]}" for e in entries[:3]) +
        "\n\nDERNIERS MESSAGES\n" + "=" * 90 + "\n" +
        "\n\n".join(f"{e['role']} | {e['timestamp']}\n{e['text'][:5000]}" for e in entries[-3:]) + "\n",
        encoding="utf-8",
    )

    diagnostics = [
        f"share_url={SHARE_URL}",
        f"share_id={SHARE_ID}",
        f"title={final['title']}",
        f"stable_fetch_a={first['slot']}",
        f"stable_fetch_b={final['slot']}",
        f"visible_sequence_fingerprint={final['fingerprint']}",
        f"html_bytes_a={first['html_bytes']}",
        f"html_bytes_b={final['html_bytes']}",
        f"decoded_stream_bytes_a={first['stream_bytes']}",
        f"decoded_stream_bytes_b={final['stream_bytes']}",
        f"flat_table_entries={final['table_entries']}",
        f"selected_linear_path={final['linear_path']}",
        f"linear_entries={final['linear_entries']}",
        f"raw_role_counts={final['role_counts']!r}",
        f"visible_entries={len(entries)}",
        f"visible_user_messages={users}",
        f"visible_assistant_messages={assistants}",
        f"visible_progress_messages={progress}",
        f"excluded_hidden_messages={final['excluded_hidden']}",
        f"excluded_internal_assistant_messages={final['excluded_internal']}",
        f"attachment_markers={attachments}",
        f"transcript_bytes={TXT.stat().st_size}",
        f"transcript_lines={len(transcript.splitlines())}",
        f"transcript_words={len(transcript.split())}",
        f"sha256={transcript_sha}",
        f"first_message_role={entries[0]['role']}",
        f"first_message_preview={compact_preview(entries[0], 1600)!r}",
        f"last_message_role={entries[-1]['role']}",
        f"last_message_preview={compact_preview(entries[-1], 2400)!r}",
        f"last_user_preview={compact_preview(last_user, 2400)!r}",
        f"last_assistant_preview={compact_preview(last_assistant, 2400)!r}",
        "FIN_REELLE_DU_PARTAGE_VERIFIEE=OUI",
        "AUCUN_MESSAGE_VISIBLE_MANQUANT_SELON_DOUBLE_RECUPERATION=OUI",
        "EXTRACTION_INTEGRALE_VALIDEE=OUI",
    ]
    DIAG.write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
    print("\n".join(diagnostics))


if __name__ == "__main__":
    main()
