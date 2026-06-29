"""Unit tests for YAML config loading."""

from __future__ import annotations

import pytest

from domain_guard.config import GuardConfig


def test_minimal_config(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text(
        "name: x\n"
        "pipeline:\n"
        "  - type: rule\n"
        "    block_patterns: ['hello']\n"
        "fallback:\n"
        "  reply: nope\n",
        encoding="utf-8",
    )
    cfg = GuardConfig.from_yaml(p)
    assert cfg.name == "x"
    assert len(cfg.pipeline) == 1
    assert cfg.pipeline[0].type == "rule"
    assert cfg.fallback.reply == "nope"


def test_defaults_when_fields_absent(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("name: x\npipeline: []\n", encoding="utf-8")
    cfg = GuardConfig.from_yaml(p)
    assert cfg.fallback.reply  # some default
    assert cfg.fallback.suggested_replies == []
    assert cfg.mode == "enforce"


def test_pipeline_item_must_have_type(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text(
        "name: x\n"
        "pipeline:\n"
        "  - block_patterns: []   # missing type\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="type"):
        GuardConfig.from_yaml(p)


def test_from_dict_roundtrip():
    cfg = GuardConfig.from_dict({
        "name": "y",
        "pipeline": [{"type": "rule", "allow_keywords": ["foo"]}],
        "fallback": {"reply": "no", "suggested_replies": ["a"]},
    })
    assert cfg.name == "y"
    assert cfg.fallback.suggested_replies == ["a"]
