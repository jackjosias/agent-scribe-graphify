from __future__ import annotations

from functools import lru_cache
from typing import Iterable

MODEL_NAME = "all-MiniLM-L6-v2"


def available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def encode_texts(texts: Iterable[str]) -> list[list[float]]:
    vectors = model().encode(list(texts), normalize_embeddings=True)
    return [[float(value) for value in vector] for vector in vectors]


def dimension() -> int:
    return len(encode_texts(["dimension probe"])[0])
