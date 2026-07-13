from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from stream_extract import extract_content, linear

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp_message_inspect"
OUT.mkdir(parents=True, exist_ok=True)

def scalar(value: Any) -> str:
    if value is None:
        return "<none>"
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return type(value).__name__

counter: Counter[tuple[str, ...]] = Counter()
samples: dict[tuple[str, ...], list[str]] = defaultdict(list)
metadata_keys: Counter[str] = Counter()
message_keys: Counter[str] = Counter()

for item in linear:
    if not isinstance(item, dict):
        continue
    message = item.get("message") if isinstance(item.get("message"), dict) else item
    author = message.get("author")
    role = str(author.get("role") if isinstance(author, dict) else message.get("role") or "")
    if role != "assistant":
        continue
    for key in message:
        message_keys[str(key)] += 1
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    for key in metadata:
        metadata_keys[str(key)] += 1
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    group = (
        f"content_type={scalar(content.get('content_type'))}",
        f"recipient={scalar(message.get('recipient'))}",
        f"channel={scalar(message.get('channel'))}",
        f"end_turn={scalar(message.get('end_turn'))}",
        f"status={scalar(message.get('status'))}",
        f"hidden={scalar(metadata.get('is_visually_hidden_from_conversation'))}",
        f"message_type={scalar(metadata.get('message_type'))}",
        f"model_slug={scalar(metadata.get('model_slug'))}",
    )
    counter[group] += 1
    text = extract_content(message.get("content"))
    if text and len(samples[group]) < 5:
        samples[group].append(text[:500].replace("\n", "\\n"))

lines = [
    "ASSISTANT GROUP DISTRIBUTION",
    f"groups={len(counter)}",
    "",
    "MESSAGE_KEYS:",
    repr(message_keys.most_common()),
    "",
    "METADATA_KEYS:",
    repr(metadata_keys.most_common()),
    "",
]
for group, count in counter.most_common():
    lines.append(f"COUNT={count} | " + " | ".join(group))
    for idx, sample in enumerate(samples.get(group, []), 1):
        lines.append(f"  SAMPLE{idx}={sample!r}")
    lines.append("")

(OUT / "diagnostics.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines[:80]))
