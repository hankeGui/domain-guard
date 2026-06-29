"""Layer registry. Add new layers here."""

from .base import Layer, LayerOutput
from .context_bypass import ContextBypassLayer
from .embedding import EmbeddingLayer
from .llm_fallback import LLMFallbackLayer
from .rule import RuleLayer

LAYER_REGISTRY: dict[str, type[Layer]] = {
    "context_bypass": ContextBypassLayer,
    "rule": RuleLayer,
    "embedding": EmbeddingLayer,
    "llm_fallback": LLMFallbackLayer,
}

__all__ = ["Layer", "LayerOutput", "LAYER_REGISTRY"]
