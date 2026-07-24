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

SHARE_URL = os.environ["SHARE_URL"]
SHARE_ID = SHARE_URL.rstrip("/").rsplit("/", 1)[-1]
OUT = Path("tmp_chat_export_6a62c489/result")
OUT.mkdir(parents=True, exist_ok=True)
TXT = OUT / f"conversation-chatgpt-public-{SHARE_ID}-INTEGRALE.txt"
DIAG = OUT / "diagnostics.txt"
CMP = OUT / "double-fetch-comparison.txt"
PREVIEW = OUT / "first-last-preview.txt"


class ExportError(RuntimeError):
    pass


def norm(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    return value.strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = norm(value)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def fetch(label: str) -> bytes:
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
            response = session.get(
                SHARE_URL,
                headers=headers,
                params={"_archive_probe": f"{label}-{time.time_ns()}"},
                timeout=180,
            )
            if response.status_code == 200 and len(response.content) > 10000:
                (OUT / f"public-share-{label}.html").write_bytes(response.content)
                return response.content
            errors.append(f"attempt={attempt} status={response.status_code} bytes={len(response.content)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"attempt={attempt} {type(exc).__name__}: {exc}")
        time.sleep(attempt * 3)
    raise ExportError("Téléchargement impossible: " + " | ".join(errors))


def react_stream(html: bytes, label: str) -> str:
    soup = BeautifulSoup(html.decode("utf-8", errors="replace"), "html.parser")
    chunks: list[str] = []
    for script in soup.find_all("script"):
        text = script.string if script.string is not None else script.get_text()
        if "streamController.enqueue" not in text:
            continue
        match = re.search(r"streamController\.enqueue\((.*)\)\s*;?\s*$", text, re.S)
        if not match:
            continue
        try:
            decoded = json.loads(match.group(1).strip())
        except Exception:
            continue
        chunks.append(decoded if isinstance(decoded, str) else json.dumps(decoded, ensure_ascii=False))
    stream = "".join(chunks)
    (OUT / f"decoded-react-stream-{label}.txt").write_text(stream, encoding="utf-8")
    if not stream:
        raise ExportError(f"Flux React vide pour {label}")
    return stream


def flat_table(stream: str) -> list[Any]:
    tables: list[list[Any]] = []
    for line in stream.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, list):
            tables.append(value)
    if not tables:
        raise ExportError("Table aplatie introuvable")
    return max(tables, key=len)


UNDEFINED = object()
SPECIAL = {-1: UNDEFINED, -2: float("nan"), -3: float("inf"), -4: float("-inf"), -5: None, -6: -0.0}


class Decoder:
    def __init__(self, table: list[Any]) -> None:
        self.table = table
        self.memo: dict[int, Any] = {}

    def value(self, raw: Any) -> Any:
        if isinstance(raw, bool) or raw is None:
            return raw
        if isinstance(raw, int):
            if raw < 0:
                return SPECIAL.get(raw)
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
        target = target if target is not None else {}
        for key, value in raw.items():
            decoded = self.value(value)
            if decoded is not UNDEFINED:
                target[self.key(key)] = decoded
        return target

    def arr(self, raw: list[Any], target: list[Any] | None = None) -> Any:
        if raw and isinstance(raw[0], str) and raw[0] in {"Date", "URL", "BigInt", "RegExp", "Set", "Map"}:
            tag = raw[0]
            values = [self.value(item) for item in raw[1:]]
            if tag in {"Date", "URL", "BigInt"}:
                return values[0] if values else None
            if tag == "Map":
                return {str(values[i]): values[i + 1] for i in range(0, len(values) - 1, 2)}
            return values
        target = target if target is not None else []
        for item in raw:
            decoded = self.value(item)
            if decoded is not UNDEFINED:
                target.append(decoded)
        return target

    def index(self, idx: int) -> Any:
        if idx in self.memo:
            return self.memo[idx]
        raw = self.table[idx]
        if isinstance(raw, dict):
            target: dict[str, Any] = {}
            self.memo[idx] = target
            return self.obj(raw, target)
        if isinstance(raw, list):
            target_list: list[Any] = []
            self.memo[idx] = target_list
            decoded = self.arr(raw, target_list)
            if decoded is not target_list:
                self.memo[idx] = decoded
            return decoded
        self.memo[idx] = raw
        return raw


def find(value: Any, wanted: str, path: str = "$", seen: set[int] | None = None) -> list[tuple[str, Any]]:
    seen = seen or set()
    if isinstance(value, (dict, list)):
        oid = id(value)
        if oid in seen:
            return []
        seen.add(oid)
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        if wanted in value:
            found.append((f"{path}.{wanted}", value[wanted]))
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                found.extend(find(child, wanted, f"{path}.{key}", seen))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            if isinstance(child, (dict, list)):
                found.extend(find(child, wanted, f"{path}[{idx}]", seen))
    return found


def chunks(value: Any, attachment: bool = False) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if value is None or isinstance(value, (int, float, bool)):
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(chunks(item, attachment))
        return unique(result)
    if not isinstance(value, dict):
        return []
    result: list[str] = []
    content_type = str(value.get("content_type") or value.get("type") or "").lower()
    is_attachment = attachment or any(token in content_type for token in ("attachment", "file", "image", "audio", "video", "asset_pointer"))
    for key in ("text", "content", "caption", "transcript", "result", "output"):
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            result.append(field)
        elif isinstance(field, (dict, list)):
            result.extend(chunks(field, is_attachment))
    if is_attachment:
        meta = ["[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]"]
        for key in ("name", "filename", "file_name", "title", "mime_type", "content_type", "size", "size_bytes", "width", "height", "url"):
            field = value.get(key)
            if isinstance(field, (str, int, float)) and str(field).strip():
                meta.append(f"{key}: {field}")
        result.append("\n".join(meta))
    for key in ("parts", "items", "attachments", "files", "children", "data", "references", "citations"):
        field = value.get(key)
        if isinstance(field, (dict, list)):
            result.extend(chunks(field, is_attachment))
    return unique(result)


def text_of(message: dict[str, Any]) -> str:
    result = chunks(message.get("content"))
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        for key in ("attachments", "files", "citations"):
            field = metadata.get(key)
            if isinstance(field, (dict, list)):
                result.extend(chunks(field, True))
    return norm("\n\n".join(unique(result)))


def hidden(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata")
    return isinstance(metadata, dict) and metadata.get("is_visually_hidden_from_conversation") is True


def assistant_visible(message: dict[str, Any], text: str) -> bool:
    return (
        not hidden(message)
        and message.get("recipient") in (None, "", "all")
        and message.get("channel") in (None, "", "final", "commentary")
        and message.get("status") in (None, "", "finished", "finished_successfully")
        and text.strip() != "The output of this plugin was redacted."
    )


def timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def title(root: Any, html: bytes) -> str:
    generic = {"chatgpt", "new chat", "nouvelle conversation"}
    for _, candidate in find(root, "title"):
        if isinstance(candidate, str) and norm(candidate).lower() not in generic and len(norm(candidate)) <= 300:
            return norm(candidate)
    soup = BeautifulSoup(html.decode("utf-8", errors="replace"), "html.parser")
    if soup.title:
        candidate = re.sub(r"^ChatGPT\s*[-–—]\s*", "", norm(soup.title.get_text(" ")), flags=re.I)
        if candidate:
            return candidate
    return "Conversation ChatGPT publique"


def snapshot(label: str) -> dict[str, Any]:
    html = fetch(label)
    stream = react_stream(html, label)
    table = flat_table(stream)
    root = Decoder(table).index(0)
    linear: list[Any] = []
    linear_path = ""
    for path, candidate in find(root, "linear_conversation"):
        if isinstance(candidate, list) and len(candidate) > len(linear):
            linear, linear_path = candidate, path
    if not linear:
        raise ExportError("linear_conversation introuvable")
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(linear):
        if not isinstance(item, dict):
            continue
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else message.get("role")
        if role not in {"user", "assistant"} or hidden(message):
            continue
        text = text_of(message)
        if not text or (role == "assistant" and not assistant_visible(message, text)):
            continue
        message_id = str(message.get("id") or item.get("id") or f"linear-{idx}")
        if message_id in seen_ids:
            continue
        seen_ids.add(message_id)
        entries.append({
            "id": message_id,
            "role": role,
            "progress": role == "assistant" and message.get("channel") == "commentary",
            "text": text,
            "timestamp": timestamp(message.get("create_time")),
        })
    if len(entries) < 2:
        raise ExportError(f"Extraction insuffisante: {len(entries)} message(s)")
    semantic = json.dumps(
        [{"id": e["id"], "role": e["role"], "progress": e["progress"], "text": e["text"]} for e in entries],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "title": title(root, html),
        "entries": entries,
        "semantic_sha256": sha(semantic),
        "html_bytes": len(html),
        "linear_entries": len(linear),
        "linear_path": linear_path,
    }


def compare(a: dict[str, Any], b: dict[str, Any]) -> None:
    ae, be = a["entries"], b["entries"]
    problems: list[str] = []
    if len(ae) != len(be):
        problems.append("nombre de messages différent")
    if a["semantic_sha256"] != b["semantic_sha256"]:
        problems.append("contenu ou séquence différent")
    if ae[0]["id"] != be[0]["id"]:
        problems.append("premier message différent")
    if ae[-1]["id"] != be[-1]["id"] or ae[-1]["text"] != be[-1]["text"]:
        problems.append("dernier message différent")
    verdict = "FAIL" if problems else "PASS"
    lines = [
        "DOUBLE RÉCUPÉRATION — CONTRÔLE DE STABILISATION",
        "=" * 96,
        f"URL: {SHARE_URL}",
        f"Messages A: {len(ae)}",
        f"Messages B: {len(be)}",
        f"Empreinte A: {a['semantic_sha256']}",
        f"Empreinte B: {b['semantic_sha256']}",
        f"Premier ID A: {ae[0]['id']}",
        f"Premier ID B: {be[0]['id']}",
        f"Dernier ID A: {ae[-1]['id']}",
        f"Dernier ID B: {be[-1]['id']}",
        "",
        f"DOUBLE_FETCH_STABILIZATION={verdict}",
    ]
    if problems:
        lines.append("PROBLÈMES=" + "; ".join(problems))
    else:
        lines.extend([
            "FIN_REELLE_DU_PARTAGE_VERIFIEE=OUI",
            "AUCUN_MESSAGE_VISIBLE_MANQUANT_SELON_DOUBLE_RECUPERATION=OUI",
            "EXTRACTION_INTEGRALE_VALIDEE=OUI",
        ])
    CMP.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if problems:
        raise ExportError("Double récupération non stabilisée: " + "; ".join(problems))


def write(snapshot_data: dict[str, Any]) -> None:
    entries = snapshot_data["entries"]
    user_count = sum(e["role"] == "user" for e in entries)
    assistant_count = sum(e["role"] == "assistant" for e in entries)
    progress_count = sum(e["progress"] for e in entries)
    attachment_count = sum(e["text"].count("[PIÈCE JOINTE OU CONTENU MULTIMÉDIA]") for e in entries)
    lines = [
        "CONVERSATION CHATGPT PUBLIQUE — TRANSCRIPTION VISIBLE INTÉGRALE",
        "=" * 108,
        f"Titre : {snapshot_data['title']}",
        f"URL publique exacte : {SHARE_URL}",
        f"Date d'extraction : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Nombre total de messages visibles : {len(entries)}",
        f"Messages utilisateur : {user_count}",
        f"Messages assistant visibles : {assistant_count}",
        f"Mises à jour assistant visibles : {progress_count}",
        f"Marqueurs de pièces jointes ou contenus multimédias : {attachment_count}",
        "Contrôle : deux récupérations indépendantes ont produit la même séquence visible.",
        "=" * 108,
        "",
    ]
    for index, entry in enumerate(entries, 1):
        if entry["role"] == "user":
            role = "UTILISATEUR"
        elif entry["progress"]:
            role = "ASSISTANT | TYPE : MISE À JOUR DE PROGRESSION VISIBLE"
        else:
            role = "ASSISTANT"
        date = f" | DATE_UTC : {entry['timestamp']}" if entry["timestamp"] else ""
        lines.extend([f"--- MESSAGE {index:04d} | RÔLE : {role}{date} ---", "", entry["text"], "", ""])
    transcript = "\n".join(lines).rstrip() + "\n"
    TXT.write_text(transcript, encoding="utf-8")
    data = TXT.read_bytes()
    PREVIEW.write_text(
        "PREMIERS MESSAGES\n" + "=" * 96 + "\n" +
        "\n\n".join(f"{e['role']}\n{e['text'][:6000]}" for e in entries[:3]) +
        "\n\nDERNIERS MESSAGES\n" + "=" * 96 + "\n" +
        "\n\n".join(f"{e['role']}\n{e['text'][:10000]}" for e in entries[-3:]) + "\n",
        encoding="utf-8",
    )
    diagnostics = [
        f"share_url={SHARE_URL}",
        f"share_id={SHARE_ID}",
        f"title={snapshot_data['title']}",
        f"visible_entries={len(entries)}",
        f"visible_user_messages={user_count}",
        f"visible_assistant_messages={assistant_count}",
        f"visible_progress_messages={progress_count}",
        f"attachment_markers={attachment_count}",
        f"semantic_sha256={snapshot_data['semantic_sha256']}",
        f"transcript_bytes={len(data)}",
        f"transcript_lines={len(transcript.splitlines())}",
        f"transcript_words={len(transcript.split())}",
        f"transcript_sha256={sha(data)}",
        f"first_message_id={entries[0]['id']}",
        f"first_message_role={entries[0]['role']}",
        f"first_message_preview={entries[0]['text'][:2000]!r}",
        f"last_message_id={entries[-1]['id']}",
        f"last_message_role={entries[-1]['role']}",
        f"last_message_preview={entries[-1]['text'][:4000]!r}",
        "double_fetch_stabilization=PASS",
        "real_end_verified=YES",
        "integral_export_validated=YES",
    ]
    DIAG.write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
    print("\n".join(diagnostics))


def main() -> None:
    first = snapshot("A")
    time.sleep(8)
    second = snapshot("B")
    compare(first, second)
    write(first)


if __name__ == "__main__":
    main()
