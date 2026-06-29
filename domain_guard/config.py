"""YAML config loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class LayerConfig:
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class FallbackConfig:
    reply: str = "抱歉，这个问题不在我能处理的范围。"
    suggested_replies: list[str] = field(default_factory=list)


@dataclass
class GuardConfig:
    name: str
    pipeline: list[LayerConfig]
    fallback: FallbackConfig
    domain_description: str = ""
    mode: str = "enforce"  # "enforce" or "shadow"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "GuardConfig":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GuardConfig":
        layers = []
        for item in data.get("pipeline", []):
            if not isinstance(item, dict) or "type" not in item:
                raise ValueError(f"Each pipeline item needs a 'type' field, got: {item}")
            layer_type = item["type"]
            options = {k: v for k, v in item.items() if k != "type"}
            layers.append(LayerConfig(type=layer_type, options=options))

        fb_raw = data.get("fallback", {}) or {}
        fallback = FallbackConfig(
            reply=fb_raw.get("reply", "抱歉，这个问题不在我能处理的范围。"),
            suggested_replies=fb_raw.get("suggested_replies", []),
        )

        return cls(
            name=data.get("name", "unnamed-guard"),
            pipeline=layers,
            fallback=fallback,
            domain_description=(data.get("domain") or {}).get("description", ""),
            mode=data.get("mode", "enforce"),
            raw=data,
        )
