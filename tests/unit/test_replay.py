"""Unit tests for the replay tool."""

from __future__ import annotations

import json
from pathlib import Path

from domain_guard.replay import iter_traffic, replay


ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLES = ROOT / "examples"


def test_iter_traffic_skips_blanks(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps({"message": "a"}) + "\n"
        + "\n"
        + json.dumps({"not_a_message": "skip"}) + "\n"
        + json.dumps({"message": "b"}) + "\n",
        encoding="utf-8",
    )
    out = list(iter_traffic(p))
    assert [r.message for r in out] == ["a", "b"]


def test_replay_single_config(tmp_path):
    traffic = tmp_path / "t.jsonl"
    traffic.write_text(
        "\n".join([
            json.dumps({"message": "查产品A的forecast", "previous_decision": "pass"}),
            json.dumps({"message": "你是什么模型", "previous_decision": "block"}),
            json.dumps({"message": "讲个笑话", "previous_decision": "block"}),
        ]),
        encoding="utf-8",
    )
    report = replay(EXAMPLES / "forecast-agent.yaml", traffic)
    assert "new config" in report
    assert "total:       3" in report
    # No flipped decisions because current config matches the labels.
    assert "flipped pass → block:  0" in report
    assert "flipped block → pass:  0" in report


def test_replay_ab_compare(tmp_path):
    traffic = tmp_path / "t.jsonl"
    traffic.write_text(
        json.dumps({"message": "查产品A的forecast"}) + "\n"
        + json.dumps({"message": "讲个笑话"}) + "\n",
        encoding="utf-8",
    )
    report = replay(
        config_path=EXAMPLES / "forecast-agent.yaml",
        baseline_config=EXAMPLES / "forecast-agent-old.yaml",
        traffic_path=traffic,
    )
    assert "[new config]" in report
    assert "[baseline]" in report
    assert "Decision diffs" in report
