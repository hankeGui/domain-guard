"""LLM provider — Anthropic Claude (Haiku by default, cheap & fast)."""

from __future__ import annotations


class ClaudeLLMProvider:
    """Minimal wrapper around the Anthropic SDK for one-shot classification.

    Requires ANTHROPIC_API_KEY env var.
    """

    DEFAULT_MODEL = "claude-haiku-4-5"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "anthropic SDK not installed. Install with: pip install domain-guard[claude]"
            ) from e
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.default_model = model or self.DEFAULT_MODEL

    def classify(self, system: str, user: str, model: str | None = None) -> str:
        resp = self._client.messages.create(
            model=model or self.default_model,
            max_tokens=8,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Concatenate text blocks
        parts: list[str] = []
        for block in resp.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts)
