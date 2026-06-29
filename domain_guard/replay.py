"""Replay historical traffic through a guard config.

Two modes:

  1) Evaluate a single config: just summarize what would happen.
     guard-cli replay --config new.yaml --traffic traffic.jsonl

  2) A/B compare two configs: highlight cases where decisions differ.
     guard-cli replay --config new.yaml --baseline old.yaml --traffic traffic.jsonl

Traffic file is JSONL with one record per line. Required field: `message`.
Optional: `state` (dict), `previous_decision` ("pass"|"block") for spot-checks.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .context import GuardContext, GuardResult
from .core import DomainGuard


@dataclass
class TrafficRecord:
    message: str
    state: dict | None = None
    previous_decision: str | None = None  # "pass" | "block"
    raw: dict | None = None


def iter_traffic(path: str | Path) -> Iterable[TrafficRecord]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "message" not in obj:
                continue
            yield TrafficRecord(
                message=obj["message"],
                state=obj.get("state"),
                previous_decision=obj.get("previous_decision"),
                raw=obj,
            )


@dataclass
class ReplayStats:
    total: int = 0
    passed: int = 0
    blocked: int = 0
    by_layer: Counter = None  # type: ignore
    latency_ms_sum: float = 0.0

    def __post_init__(self):
        if self.by_layer is None:
            self.by_layer = Counter()

    def record(self, r: GuardResult) -> None:
        self.total += 1
        if r.passed:
            self.passed += 1
        else:
            self.blocked += 1
        self.by_layer[r.matched_layer or "none"] += 1
        self.latency_ms_sum += r.latency_ms

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.latency_ms_sum / self.total if self.total else 0.0


def replay_one(guard: DomainGuard, records: list[TrafficRecord]) -> tuple[ReplayStats, list[GuardResult]]:
    stats = ReplayStats()
    results: list[GuardResult] = []
    for r in records:
        ctx = GuardContext(state=r.state) if r.state else GuardContext()
        result = guard.check(r.message, ctx)
        stats.record(result)
        results.append(result)
    return stats, results


def _render_stats(name: str, s: ReplayStats) -> list[str]:
    lines = [f"[{name}]"]
    lines.append(f"  total:       {s.total}")
    lines.append(f"  passed:      {s.passed}  ({s.pass_rate:.1%})")
    lines.append(f"  blocked:     {s.blocked}  ({1 - s.pass_rate:.1%})")
    lines.append(f"  avg latency: {s.avg_latency_ms:.2f}ms")
    lines.append(f"  by layer:")
    for layer, count in s.by_layer.most_common():
        lines.append(f"    {layer:18}  {count}")
    return lines


def replay(
    config_path: str | Path,
    traffic_path: str | Path,
    baseline_config: str | Path | None = None,
    show_diff_examples: int = 10,
) -> str:
    records = list(iter_traffic(traffic_path))
    if not records:
        return "No traffic records found."

    new_guard = DomainGuard.from_yaml(config_path)
    new_stats, new_results = replay_one(new_guard, records)

    out: list[str] = []
    out.append(f"Replayed {len(records)} messages through {config_path}")
    out.append("")
    out.extend(_render_stats("new config", new_stats))

    if baseline_config is None:
        # Compare against `previous_decision` field, if present in the traffic.
        with_prev = [r for r in records if r.previous_decision in ("pass", "block")]
        if with_prev:
            flips_to_block = []
            flips_to_pass = []
            for rec, res in zip(records, new_results):
                if rec.previous_decision is None:
                    continue
                was_pass = (rec.previous_decision == "pass")
                if was_pass and not res.passed:
                    flips_to_block.append((rec, res))
                elif (not was_pass) and res.passed:
                    flips_to_pass.append((rec, res))
            out += [
                "",
                f"Versus 'previous_decision' field in traffic "
                f"({len(with_prev)} labeled):",
                f"  flipped pass → block:  {len(flips_to_block)}",
                f"  flipped block → pass:  {len(flips_to_pass)}",
            ]
            out += _format_examples(
                "pass → block (newly blocked)", flips_to_block, show_diff_examples
            )
            out += _format_examples(
                "block → pass (newly allowed)", flips_to_pass, show_diff_examples
            )
        return "\n".join(out)

    # A/B mode
    base_guard = DomainGuard.from_yaml(baseline_config)
    base_stats, base_results = replay_one(base_guard, records)
    out.append("")
    out.extend(_render_stats("baseline", base_stats))

    flips_to_block = []
    flips_to_pass = []
    layer_changes: dict[tuple[str, str], int] = defaultdict(int)
    for rec, base, new in zip(records, base_results, new_results):
        if base.passed and not new.passed:
            flips_to_block.append((rec, new))
        elif (not base.passed) and new.passed:
            flips_to_pass.append((rec, new))
        if base.matched_layer != new.matched_layer:
            layer_changes[(base.matched_layer or "none", new.matched_layer or "none")] += 1

    out += [
        "",
        "Decision diffs (new vs baseline):",
        f"  pass → block:  {len(flips_to_block)}",
        f"  block → pass:  {len(flips_to_pass)}",
        f"  pass rate Δ:   {new_stats.pass_rate - base_stats.pass_rate:+.2%}",
    ]
    if layer_changes:
        out.append("")
        out.append("Layer transitions (baseline → new):")
        for (b, n), c in sorted(layer_changes.items(), key=lambda x: -x[1]):
            out.append(f"  {b:18} → {n:18}  {c}")

    out += _format_examples("pass → block", flips_to_block, show_diff_examples)
    out += _format_examples("block → pass", flips_to_pass, show_diff_examples)
    return "\n".join(out)


def _format_examples(title, items, limit):
    if not items:
        return []
    lines = ["", f"Examples — {title} (up to {limit}):"]
    for rec, res in items[:limit]:
        lines.append(
            f"  - {rec.message!r}   layer={res.matched_layer} conf={res.confidence:.2f}"
        )
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="guard-cli replay")
    ap.add_argument("--config", required=True, help="New guard YAML to evaluate")
    ap.add_argument("--traffic", required=True, help="JSONL of historical messages")
    ap.add_argument("--baseline", help="Optional baseline YAML to A/B compare against")
    ap.add_argument("--examples", type=int, default=10,
                    help="How many flip examples to print per direction")
    args = ap.parse_args(argv)
    print(replay(args.config, args.traffic, args.baseline, args.examples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
