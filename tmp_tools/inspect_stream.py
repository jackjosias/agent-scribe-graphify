from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "tmp_export" / "share-page.html").read_text(encoding="utf-8")
out = ROOT / "tmp_stream_inspect"
out.mkdir(parents=True, exist_ok=True)
soup = BeautifulSoup(html, "html.parser")

scripts = soup.find_all("script")
stream_scripts = []
decoded_chunks: list[str] = []
errors: list[str] = []
for index, script in enumerate(scripts):
    text = script.string if script.string is not None else script.get_text()
    if "streamController.enqueue" not in text:
        continue
    stream_scripts.append((index, text))
    # Every streamed script normally contains one enqueue call with a JSON string literal.
    match = re.search(r"streamController\.enqueue\((.*)\)\s*;?\s*$", text, flags=re.S)
    if not match:
        errors.append(f"script={index} regex_no_match prefix={text[:200]!r}")
        continue
    argument = match.group(1).strip()
    try:
        value = json.loads(argument)
        if isinstance(value, str):
            decoded_chunks.append(value)
        else:
            decoded_chunks.append(json.dumps(value, ensure_ascii=False))
    except Exception as exc:
        errors.append(f"script={index} json_error={type(exc).__name__}:{exc} argument_prefix={argument[:300]!r}")

decoded = "".join(decoded_chunks)
(out / "decoded-stream.txt").write_text(decoded, encoding="utf-8")

patterns = [
    "mapping", "current_node", "author", "content_type", "parts",
    "Continuation projet LLM", "streamController.enqueue", "user", "assistant",
    "conversation_id", "message", "title",
]
lines = [
    f"html_chars={len(html)}",
    f"script_tags={len(scripts)}",
    f"stream_scripts={len(stream_scripts)}",
    f"decoded_chunks={len(decoded_chunks)}",
    f"decoded_chars={len(decoded)}",
    f"errors={len(errors)}",
]
for pattern in patterns:
    lines.append(f"html_count[{pattern}]={html.count(pattern)}")
    lines.append(f"decoded_count[{pattern}]={decoded.count(pattern)}")

lines.append("\nSTREAM_SCRIPT_LENGTHS:")
for index, text in stream_scripts[:100]:
    lines.append(f"script={index} chars={len(text)} prefix={text[:160]!r}")

lines.append("\nERRORS:")
lines.extend(errors[:50])

lines.append("\nDECODED_SNIPPETS:")
for pattern in patterns:
    start = 0
    found = 0
    while found < 5:
        pos = decoded.find(pattern, start)
        if pos < 0:
            break
        left = max(0, pos - 500)
        right = min(len(decoded), pos + len(pattern) + 1200)
        snippet = decoded[left:right].replace("\n", "\\n")
        lines.append(f"--- pattern={pattern!r} pos={pos} ---\n{snippet}")
        start = pos + len(pattern)
        found += 1

(out / "diagnostics.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines[:40]))
