from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STREAM_PATH = ROOT / "tmp_stream_inspect" / "decoded-stream.txt"
OUT_DIR = ROOT / "tmp_stream_export"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHARE_URL = "https://chatgpt.com/share/6a55289f-b540-83ea-a397-0183fd86f5a4"

raw_stream = STREAM_PATH.read_text(encoding="utf-8")
json_line = next((line for line in raw_stream.splitlines() if line.lstrip().startswith("[")), "")
if not json_line:
    raise RuntimeError("Aucune charge JSON aplatie trouvée dans le flux React Router")
table = json.loads(json_line)
if not isinstance(table, list):
    raise RuntimeError("La charge aplatie n'est pas une liste")

UNDEFINED = object()
IN_PROGRESS = object()
memo: dict[int, Any] = {}

SPECIALS: dict[int, Any] = {
    -1: UNDEFINED,
    -2: float("nan"),
    -3: float("inf"),
    -4: float("-inf"),
    -5: None,
    -6: -0.0,
}


def decode_reference(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        if value < 0:
            return SPECIALS.get(value, None)
        if value >= len(table):
            return value
        return decode_index(value)
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return decode_raw_object(value)
    if isinstance(value, list):
        return decode_raw_list(value)
    return value


def decode_key(raw_key: str) -> str:
    match = re.fullmatch(r"_(\d+)", raw_key)
    if not match:
        return raw_key
    key_value = decode_index(int(match.group(1)))
    return str(key_value)


def decode_raw_object(raw: dict[str, Any], target: dict[str, Any] | None = None) -> dict[str, Any]:
    result = target if target is not None else {}
    for raw_key, raw_value in raw.items():
        key = decode_key(raw_key)
        decoded = decode_reference(raw_value)
        if decoded is not UNDEFINED:
            result[key] = decoded
    return result


def decode_raw_list(raw: list[Any], target: list[Any] | None = None) -> Any:
    # Turbo/devalue may use tagged arrays for built-ins. Preserve the useful value.
    if raw and isinstance(raw[0], str) and raw[0] in {"Date", "URL", "BigInt", "RegExp", "Set", "Map"}:
        tag = raw[0]
        values = [decode_reference(item) for item in raw[1:]]
        if tag in {"Date", "URL", "BigInt"}:
            return values[0] if values else None
        if tag == "Set":
            return values
        if tag == "Map":
            mapped: dict[str, Any] = {}
            for i in range(0, len(values) - 1, 2):
                mapped[str(values[i])] = values[i + 1]
            return mapped
        return values
    result = target if target is not None else []
    for item in raw:
        decoded = decode_reference(item)
        if decoded is not UNDEFINED:
            result.append(decoded)
    return result


def decode_index(index: int) -> Any:
    if index in memo:
        cached = memo[index]
        return None if cached is IN_PROGRESS else cached
    raw = table[index]
    if isinstance(raw, dict):
        result: dict[str, Any] = {}
        memo[index] = result
        return decode_raw_object(raw, result)
    if isinstance(raw, list):
        result_list: list[Any] = []
        memo[index] = result_list
        decoded = decode_raw_list(raw, result_list)
        if decoded is not result_list:
            memo[index] = decoded
        return decoded
    memo[index] = raw
    return raw

root = decode_index(0)


def find_key(value: Any, wanted: str, path: str = "$", seen: set[int] | None = None) -> list[tuple[str, Any]]:
    if seen is None:
        seen = set()
    if isinstance(value, (dict, list)):
        obj_id = id(value)
        if obj_id in seen:
            return []
        seen.add(obj_id)
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        if wanted in value:
            found.append((f"{path}.{wanted}", value[wanted]))
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                found.extend(find_key(child, wanted, f"{path}.{key}", seen))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            if isinstance(child, (dict, list)):
                found.extend(find_key(child, wanted, f"{path}[{idx}]", seen))
    return found

linear_matches = find_key(root, "linear_conversation")
linear_path = ""
linear: list[Any] = []
for path, candidate in linear_matches:
    if isinstance(candidate, list) and len(candidate) > len(linear):
        linear_path = path
        linear = candidate


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def attachment_description(part: dict[str, Any]) -> str:
    name = ""
    for key in ("name", "filename", "file_name", "title"):
        if isinstance(part.get(key), str) and part[key].strip():
            name = part[key].strip()
            break
    content_type = str(part.get("content_type") or part.get("type") or "pièce jointe")
    return f"[PIÈCE JOINTE : {name or content_type}]"


def extract_content(content: Any) -> str:
    if isinstance(content, str):
        return normalize_text(content)
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    chunks: list[str] = []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, str):
                if part.strip():
                    chunks.append(part)
            elif isinstance(part, dict):
                for key in ("text", "content", "caption"):
                    if isinstance(part.get(key), str) and part[key].strip():
                        chunks.append(part[key])
                        break
                else:
                    chunks.append(attachment_description(part))
    else:
        for key in ("text", "content"):
            if isinstance(content.get(key), str):
                chunks.append(content[key])
                break
    return normalize_text("\n".join(chunks))

messages: list[dict[str, Any]] = []
role_counts: dict[str, int] = {}
for item in linear:
    if not isinstance(item, dict):
        continue
    # Some linear entries are nodes wrapping a `message`, others are messages directly.
    message = item.get("message") if isinstance(item.get("message"), dict) else item
    author = message.get("author")
    role = author.get("role") if isinstance(author, dict) else message.get("role")
    role = str(role or "")
    role_counts[role] = role_counts.get(role, 0) + 1
    if role not in {"user", "assistant"}:
        continue
    text = extract_content(message.get("content"))
    if not text:
        continue
    messages.append({
        "id": message.get("id") or item.get("id"),
        "role": role,
        "text": text,
        "create_time": message.get("create_time"),
    })

# Locate title from the decoded object.
title = "Continuation projet LLM"
title_matches = find_key(root, "title")
for _, candidate in title_matches:
    if isinstance(candidate, str) and candidate.strip() == "Continuation projet LLM":
        title = candidate.strip()
        break

out_path = OUT_DIR / "conversation-chatgpt-public-6a55289f-b540-83ea-a397-0183fd86f5a4.txt"
lines = [
    "CONVERSATION CHATGPT PUBLIQUE EXTRAITE",
    "=" * 96,
    f"Titre : {title}",
    f"URL publique : {SHARE_URL}",
    f"Date d'extraction : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
    f"Nombre de messages : {len(messages)}",
    "=" * 96,
    "",
]
role_label = {"user": "UTILISATEUR", "assistant": "ASSISTANT"}
for index, message in enumerate(messages, start=1):
    lines.extend([
        f"--- MESSAGE {index:03d} | RÔLE : {role_label[message['role']]} ---",
        "",
        message["text"].rstrip(),
        "",
        "",
    ])
out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

first_preview = messages[0]["text"][:1000] if messages else ""
last_preview = messages[-1]["text"][:1000] if messages else ""
root_keys = list(root.keys())[:50] if isinstance(root, dict) else []
sample_keys: list[str] = []
for item in linear[:5]:
    if isinstance(item, dict):
        sample_keys.append(str(list(item.keys())[:30]))

diagnostics = [
    f"table_entries={len(table)}",
    f"root_type={type(root).__name__}",
    f"root_keys={root_keys!r}",
    f"linear_matches={[(path, len(value) if isinstance(value, list) else type(value).__name__) for path, value in linear_matches]!r}",
    f"selected_linear_path={linear_path}",
    f"linear_entries={len(linear)}",
    f"linear_sample_keys={sample_keys!r}",
    f"all_role_counts={role_counts!r}",
    f"messages={len(messages)}",
    f"user_messages={sum(1 for m in messages if m['role'] == 'user')}",
    f"assistant_messages={sum(1 for m in messages if m['role'] == 'assistant')}",
    f"txt_bytes={out_path.stat().st_size}",
    f"first_preview={first_preview!r}",
    f"last_preview={last_preview!r}",
]
(OUT_DIR / "diagnostics.txt").write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
if len(messages) < 2:
    (OUT_DIR / "EXTRACTION_FAILED.txt").write_text("Moins de deux messages extraits du flux aplati.\n", encoding="utf-8")
print("\n".join(diagnostics))
