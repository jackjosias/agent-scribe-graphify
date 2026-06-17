from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Iterable

SYNONYM_GROUPS = [
    {"jwt", "token", "bearer", "credential", "credentials", "secret", "secrets"},
    {"stocker", "stocke", "stockage", "store", "stored", "storage", "persist", "persiste", "persistence", "browser-stored"},
    {"client", "browser", "navigateur", "javascript", "js", "redux", "frontend", "localstorage", "sessionstorage"},
    {"cookie", "cookies", "httponly", "csrf", "samesite"},
    {"bug", "erreur", "error", "crash", "broken", "fail", "failure", "casse", "probleme", "problème", "fix"},
    {"refresh", "rotation", "concurrent", "race", "single-winner"},
    {"auth", "authentication", "authentification", "session", "login"},
]

SYNONYMS: dict[str, set[str]] = {}
for group in SYNONYM_GROUPS:
    for item in group:
        SYNONYMS.setdefault(item, set()).update(group - {item})

STOPWORDS = {
    "avec", "dans", "pour", "une", "des", "les", "the", "and", "that", "this", "vers", "mettre", "jamais", "utiliser",
    "cote", "côté", "sans", "sur", "par", "plus", "moins", "quoi", "dont", "comme", "only", "should", "must",
}


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(char for char in decomposed if not unicodedata.combining(char))
    return normalized.replace("local storage", "localstorage").replace("http only", "httponly")


def token_root(token: str) -> str:
    for suffix in ("ements", "ement", "ations", "ation", "iques", "ique", "ites", "ite", "ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"[a-z0-9_./-]{3,}", normalize_text(text)):
        token = raw.strip("./,;:!?()[]{}\'\"")
        if len(token) >= 3 and token not in STOPWORDS:
            tokens.append(token_root(token))
    return tokens


def expand_tokens(tokens: Iterable[str]) -> set[str]:
    expanded: set[str] = set()
    for token in tokens:
        expanded.add(token)
        expanded.add(token_root(token))
        expanded.update(SYNONYMS.get(token, set()))
    return expanded


def token_counts(text: str) -> Counter[str]:
    return Counter(tokenize(text))


def compact(text: str, limit: int = 78) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def bm25_scores(query: str, documents: list[list[str]]) -> list[float]:
    query_terms = expand_tokens(tokenize(query))
    if not query_terms or not documents:
        return [0.0 for _ in documents]
    doc_freq: Counter[str] = Counter()
    doc_counts: list[Counter[str]] = []
    lengths: list[int] = []
    for document in documents:
        counts = Counter(document)
        doc_counts.append(counts)
        lengths.append(sum(counts.values()))
        for term in counts:
            doc_freq[term] += 1
    total_docs = len(documents)
    avg_len = sum(lengths) / total_docs if total_docs else 0.0
    k1 = 1.5
    b = 0.75
    scores: list[float] = []
    for counts, length in zip(doc_counts, lengths):
        score = 0.0
        for term in query_terms:
            freq = counts.get(term, 0)
            if freq <= 0:
                continue
            idf = math.log(1 + (total_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            denom = freq + k1 * (1 - b + b * (length / avg_len if avg_len else 0.0))
            score += idf * (freq * (k1 + 1)) / denom
        scores.append(score)
    max_score = max(scores, default=0.0)
    if max_score <= 0:
        return scores
    return [score / max_score for score in scores]
