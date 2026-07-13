from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stream_extract import extract_content, linear

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "tmp_final_export"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHARE_URL = "https://chatgpt.com/share/6a55289f-b540-83ea-a397-0183fd86f5a4"
TITLE = "Continuation projet LLM"


def normalize_visible_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    # Convert internal citation delimiters into readable plain-text markers.
    text = re.sub(r"\ue200(filecite|cite)\ue202(.*?)\ue201", lambda m: f"[citation: {m.group(2)}]", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def hidden(metadata: dict[str, Any]) -> bool:
    return metadata.get("is_visually_hidden_from_conversation") is True


def visible_assistant_message(message: dict[str, Any], text: str) -> bool:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    if hidden(metadata):
        return False
    if str(content.get("content_type") or "") != "text":
        return False
    if str(message.get("recipient") or "") != "all":
        return False
    if str(message.get("status") or "") not in {"finished_successfully", "finished"}:
        return False
    channel = message.get("channel")
    if channel not in {None, "final", "commentary"}:
        return False
    if text.strip() == "The output of this plugin was redacted.":
        return False
    return True

entries: list[dict[str, Any]] = []
raw_role_counts: dict[str, int] = {}
excluded_assistant = 0
visible_channel_counts: dict[str, int] = {}

for linear_index, item in enumerate(linear):
    if not isinstance(item, dict):
        continue
    message = item.get("message") if isinstance(item.get("message"), dict) else item
    author = message.get("author")
    role = str(author.get("role") if isinstance(author, dict) else message.get("role") or "")
    raw_role_counts[role] = raw_role_counts.get(role, 0) + 1
    if role not in {"user", "assistant"}:
        continue
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    if hidden(metadata):
        if role == "assistant":
            excluded_assistant += 1
        continue
    text = normalize_visible_text(extract_content(message.get("content")))
    if not text:
        continue
    subtype = "message"
    if role == "assistant":
        if not visible_assistant_message(message, text):
            excluded_assistant += 1
            continue
        channel = str(message.get("channel") or "none")
        visible_channel_counts[channel] = visible_channel_counts.get(channel, 0) + 1
        subtype = "progression" if channel == "commentary" else "reponse"
    entries.append({
        "id": message.get("id") or item.get("id"),
        "role": role,
        "subtype": subtype,
        "text": text,
        "linear_index": linear_index,
        "create_time": message.get("create_time"),
    })

out_path = OUT_DIR / "conversation-chatgpt-public-6a55289f-b540-83ea-a397-0183fd86f5a4.txt"
lines = [
    "CONVERSATION CHATGPT PUBLIQUE EXTRAITE",
    "=" * 96,
    f"Titre : {TITLE}",
    f"URL publique : {SHARE_URL}",
    f"Date d'extraction : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
    f"Nombre total de messages visibles : {len(entries)}",
    f"Messages utilisateur : {sum(1 for e in entries if e['role'] == 'user')}",
    f"Messages assistant visibles : {sum(1 for e in entries if e['role'] == 'assistant')}",
    "Note : les raisonnements internes, appels d'outils, sorties de plugins et messages cachés sont exclus.",
    "=" * 96,
    "",
]
for index, entry in enumerate(entries, 1):
    if entry["role"] == "user":
        header = f"--- MESSAGE {index:03d} | RÔLE : UTILISATEUR ---"
    elif entry["subtype"] == "progression":
        header = f"--- MESSAGE {index:03d} | RÔLE : ASSISTANT | TYPE : MISE À JOUR DE PROGRESSION ---"
    else:
        header = f"--- MESSAGE {index:03d} | RÔLE : ASSISTANT ---"
    lines.extend([header, "", entry["text"].rstrip(), "", ""])
out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

sha256 = hashlib.sha256(out_path.read_bytes()).hexdigest()
sequence_preview = [
    f"{e['role']}:{e['subtype']}:{e['text'][:180].replace(chr(10), ' ')}"
    for e in entries[:5]
]
sequence_tail = [
    f"{e['role']}:{e['subtype']}:{e['text'][:180].replace(chr(10), ' ')}"
    for e in entries[-5:]
]
diagnostics = [
    f"title={TITLE}",
    f"share_url={SHARE_URL}",
    f"linear_entries={len(linear)}",
    f"raw_role_counts={raw_role_counts!r}",
    f"visible_entries={len(entries)}",
    f"visible_user_messages={sum(1 for e in entries if e['role'] == 'user')}",
    f"visible_assistant_messages={sum(1 for e in entries if e['role'] == 'assistant')}",
    f"visible_assistant_channels={visible_channel_counts!r}",
    f"excluded_assistant_messages={excluded_assistant}",
    f"txt_bytes={out_path.stat().st_size}",
    f"txt_lines={len(out_path.read_text(encoding='utf-8').splitlines())}",
    f"sha256={sha256}",
    f"first_entries={sequence_preview!r}",
    f"last_entries={sequence_tail!r}",
]
(OUT_DIR / "diagnostics.txt").write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
print("\n".join(diagnostics))

# Produce compressed/base64 chunks so the result can be reconstructed even in a restricted client.
import base64
import gzip
payload = base64.b64encode(gzip.compress(out_path.read_bytes(), compresslevel=9)).decode("ascii")
chunk_dir = OUT_DIR / "chunks"
chunk_dir.mkdir(parents=True, exist_ok=True)
chunk_size = 40000
chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]
for old in chunk_dir.glob("chunk-*.b64"):
    old.unlink()
for index, chunk in enumerate(chunks, 1):
    (chunk_dir / f"chunk-{index:03d}-of-{len(chunks):03d}.b64").write_text(chunk, encoding="ascii")
(OUT_DIR / "chunks-manifest.txt").write_text(
    f"chunks={len(chunks)}\nchunk_size={chunk_size}\nbase64_chars={len(payload)}\nsha256={sha256}\n",
    encoding="utf-8",
)
