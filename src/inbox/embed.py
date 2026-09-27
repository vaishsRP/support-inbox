"""Local sentence embeddings. Free, runs on CPU, multilingual.

Vectors are cached per firm next to the database, tagged with the model name so a
model change never mixes vectors from two models in one index.
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL_NAME = "intfloat/multilingual-e5-small"
# e5 models expect a prefix; "query: " is the symmetric (question-to-question) mode.
PREFIX = "query: "


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, device="cpu")


def _fake_encode(texts: list[str]) -> np.ndarray:
    """Hashed bag of words. Tests and CI only (INBOX_FAKE_EMBED=1): deterministic,
    instant, no model download, and texts sharing words still land close together."""
    out = np.zeros((len(texts), 384), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 384] += 1.0
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.where(norms == 0, 1, norms)


def encode(texts: list[str], batch_size: int = 128, progress: bool = False) -> np.ndarray:
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    if os.environ.get("INBOX_FAKE_EMBED"):
        return _fake_encode(texts)
    vecs = _model().encode(
        [PREFIX + t for t in texts],
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=progress,
        convert_to_numpy=True,
    )
    return vecs.astype(np.float32)


def cached(path: Path, ids: list[int], texts: list[str], progress: bool = True) -> np.ndarray:
    """Vectors for (ids, texts), reusing whatever is already in the cache file."""
    have: dict[int, np.ndarray] = {}
    model_tag = "fake" if os.environ.get("INBOX_FAKE_EMBED") else MODEL_NAME
    if path.exists():
        z = np.load(path, allow_pickle=False)
        if str(z["model"]) == model_tag:
            have = dict(zip(z["ids"].tolist(), z["vecs"]))
    missing = [(i, t) for i, t in zip(ids, texts) if i not in have]
    if missing:
        new = encode([t for _, t in missing], progress=progress)
        for (i, _), v in zip(missing, new):
            have[i] = v
        all_ids = np.array(list(have.keys()), dtype=np.int64)
        np.savez(path, ids=all_ids, vecs=np.stack([have[i] for i in all_ids.tolist()]), model=model_tag)
    return np.stack([have[i] for i in ids]) if ids else np.zeros((0, 384), dtype=np.float32)
