"""Embedding provider — local sentence-transformers."""

from __future__ import annotations

from typing import Sequence

import numpy as np


class LocalEmbeddingProvider:
    """Wraps a sentence-transformers model.

    Default model is small and multilingual (~120MB).
    Requires network on first run to download the model.
    """

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install domain-guard[local]"
            ) from e
        self._model = SentenceTransformer(model_name)
        self.model_name = model_name

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vecs = self._model.encode(list(texts), convert_to_numpy=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


class HashEmbeddingProvider:
    """Cheap-and-cheerful character-n-gram hash embedding. No dependencies, no
    network. Quality is far below a real model, but good enough to demonstrate
    the pipeline and to fall back on when offline.

    Use a real embedding model in production.
    """

    def __init__(self, dim: int = 256, ngram: tuple[int, int] = (2, 4)):
        self.dim = dim
        self.ngram = ngram

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i] = self._embed_one(t)
        return out

    def _embed_one(self, text: str) -> np.ndarray:
        text = text.lower().strip()
        v = np.zeros(self.dim, dtype=np.float32)
        lo, hi = self.ngram
        for n in range(lo, hi + 1):
            if len(text) < n:
                continue
            for i in range(len(text) - n + 1):
                token = text[i : i + n]
                h = hash(token) % self.dim
                v[h] += 1.0
        return v
