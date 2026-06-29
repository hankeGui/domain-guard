"""Provider registry and factory helpers."""

import os

from .embedding_local import HashEmbeddingProvider, LocalEmbeddingProvider
from .llm_claude import ClaudeLLMProvider


def make_default_embedding():
    """Build the default embedding provider.

    If DOMAIN_GUARD_EMBEDDING=hash, use the dependency-free HashEmbeddingProvider
    (fine for demos / offline). Otherwise try sentence-transformers; if it
    fails (e.g. no network on first run), fall back to hash with a warning.
    """
    if os.environ.get("DOMAIN_GUARD_EMBEDDING") == "hash":
        return HashEmbeddingProvider()
    try:
        return LocalEmbeddingProvider()
    except Exception as e:
        import warnings
        warnings.warn(
            f"LocalEmbeddingProvider failed ({type(e).__name__}: {e}); "
            "falling back to HashEmbeddingProvider. Set DOMAIN_GUARD_EMBEDDING=hash "
            "to skip this attempt next time."
        )
        return HashEmbeddingProvider()


def make_default_llm() -> ClaudeLLMProvider:
    return ClaudeLLMProvider()


__all__ = [
    "LocalEmbeddingProvider",
    "HashEmbeddingProvider",
    "ClaudeLLMProvider",
    "make_default_embedding",
    "make_default_llm",
]
