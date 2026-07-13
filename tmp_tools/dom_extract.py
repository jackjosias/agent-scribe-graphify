from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "tmp_export" / "share-page.html"
OUT_DIR = ROOT / "tmp_export_dom"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHARE_URL = "https://chatgpt.com/share/6a55289f-b540-83ea-a397-0183fd86f5a4"

html = HTML_PATH.read_text(encoding="utf-8")
soup = BeautifulSoup(html, "html.parser")

def normalize(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def cleaned_text(node: Tag) -> str:
    fragment = BeautifulSoup(str(node), "html.parser")
    for selector in (
        "script", "style", "svg", "button", "textarea", "input",
        '[role="button"]', '[aria-hidden="true"]',
        '[data-testid*="copy"]', '[data-testid*="action"]',
        '[data-testid*="feedback"]', '.sr-only',
    ):
        for element in fragment.select(selector):
            element.decompose()
    return normalize(fragment.get_text("\n"))


def attachment_labels(node: Tag) -> list[str]:
    labels: list[str] = []
    selectors = [
        '[data-testid="file-thumbnail"] [aria-label]',
        '[data-testid="file-thumbnail"][aria-label]',
        'button[aria-label*="fichier" i]',
        'button[aria-label*="image" i]',
        'button[aria-label*="file" i]',
    ]
    for selector in selectors:
        for element in node.select(selector):
            label = normalize(element.get("aria-label") or "")
            if label and label.lower() not in {"copier", "copy", "modifier", "edit"}:
                labels.append(label)
    return list(dict.fromkeys(labels))

articles = soup.select('article[data-testid^="conversation-turn-"]')
turn_nodes = articles or soup.select('[data-testid^="conversation-turn-"]')
role_nodes = soup.select('[data-message-author-role]')
user_nodes = soup.select('[data-testid="user-message"]')
markdown_nodes = soup.select('.markdown')

messages: list[dict[str, str | int | None]] = []
seen_keys: set[str] = set()

if turn_nodes:
    candidates = turn_nodes
else:
    candidates = []
    for node in role_nodes:
        parent_role = node.find_parent(attrs={"data-message-author-role": True})
        if parent_role is None:
            candidates.append(node)

for dom_index, node in enumerate(candidates):
    if not isinstance(node, Tag):
        continue
    role_element = node if node.has_attr("data-message-author-role") else node.select_one('[data-message-author-role]')
    role = ""
    if isinstance(role_element, Tag):
        role = str(role_element.get("data-message-author-role") or "").strip().lower()
    if role not in {"user", "assistant"}:
        heading = normalize((node.select_one("h1,h2,h3,h4,h5,h6") or node).get_text(" ")).lower()
        if "vous avez dit" in heading or "you said" in heading:
            role = "user"
        elif "chatgpt a dit" in heading or "assistant" in heading:
            role = "assistant"
    if role not in {"user", "assistant"}:
        continue

    if role == "user":
        preferred = node.select_one('[data-testid="user-message"]') or role_element or node
    else:
        preferred = (
            node.select_one('[data-message-author-role="assistant"] .markdown')
            or node.select_one('.markdown.prose')
            or node.select_one('.markdown')
            or role_element
            or node
        )
    if not isinstance(preferred, Tag):
        continue

    text = cleaned_text(preferred)
    if role == "user":
        attachments = attachment_labels(node)
        if attachments:
            prefix = "\n".join(f"[PIÈCE JOINTE : {label}]" for label in attachments)
            text = f"{prefix}\n\n{text}" if text else prefix
    if not text:
        continue

    test_id = str(node.get("data-testid") or "")
    match = re.search(r"conversation-turn-(\d+)", test_id)
    turn_number = int(match.group(1)) if match else None
    stable = str(node.get("data-message-id") or test_id or "")
    key = stable or hashlib.sha256(f"{role}\n{text}".encode()).hexdigest()
    if key in seen_keys:
        continue
    seen_keys.add(key)
    messages.append({
        "role": role,
        "text": text,
        "turn_number": turn_number,
        "dom_index": dom_index,
    })

if messages and sum(m["turn_number"] is not None for m in messages) >= max(2, int(len(messages) * 0.7)):
    messages.sort(key=lambda m: (m["turn_number"] is None, m["turn_number"] if m["turn_number"] is not None else 10**12, m["dom_index"]))
else:
    messages.sort(key=lambda m: int(m["dom_index"]))

title = normalize(soup.title.get_text(" ") if soup.title else "Conversation ChatGPT publique")
title = re.sub(r"^ChatGPT\s*[-–—]\s*", "", title, flags=re.I) or "Conversation ChatGPT publique"

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
labels = {"user": "UTILISATEUR", "assistant": "ASSISTANT"}
for index, message in enumerate(messages, 1):
    lines.extend([
        f"--- MESSAGE {index:03d} | RÔLE : {labels[str(message['role'])]} ---",
        "",
        str(message["text"]).rstrip(),
        "",
        "",
    ])
out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

first_preview = normalize(str(messages[0]["text"]))[:500] if messages else ""
last_preview = normalize(str(messages[-1]["text"]))[:500] if messages else ""
diagnostics = [
    f"html_bytes={len(html.encode('utf-8'))}",
    f"title={title}",
    f"article_turn_nodes={len(articles)}",
    f"all_turn_nodes={len(turn_nodes)}",
    f"role_nodes={len(role_nodes)}",
    f"user_message_nodes={len(user_nodes)}",
    f"markdown_nodes={len(markdown_nodes)}",
    f"messages={len(messages)}",
    f"user_messages={sum(1 for m in messages if m['role'] == 'user')}",
    f"assistant_messages={sum(1 for m in messages if m['role'] == 'assistant')}",
    f"txt_bytes={out_path.stat().st_size}",
    f"first_preview={first_preview!r}",
    f"last_preview={last_preview!r}",
]
(OUT_DIR / "diagnostics.txt").write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
if len(messages) < 2:
    (OUT_DIR / "EXTRACTION_FAILED.txt").write_text("Moins de deux messages DOM extraits.\n", encoding="utf-8")
print("\n".join(diagnostics))
