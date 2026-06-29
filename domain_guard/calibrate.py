"""Threshold calibration: given labeled samples, find the best (pass, block) cuts.

Usage:
    guard-cli calibrate --config forecast-agent.yaml \\
        --positive samples/in_domain.jsonl \\
        --negative samples/out_of_domain.jsonl
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .config import GuardConfig
from .context import GuardContext
from .layers.embedding import EmbeddingLayer
from .layers.rule import RuleLayer
from .layers.context_bypass import ContextBypassLayer
from .providers import make_default_embedding


@dataclass
class Sample:
    message: str
    label: str  # "positive" (in-domain) or "negative" (out-of-domain)


@dataclass
class CalibrationReport:
    n_positive: int
    n_negative: int
    recommended_pass: float
    recommended_block: float
    accuracy_at_recommended: float
    false_block_rate: float   # in-domain blocked (误杀)
    false_pass_rate: float    # out-of-domain passed (漏放)
    sweep: list[dict]
    decided_by_earlier_layers: dict[str, int]
    edge_cases: list[dict]

    def render(self) -> str:
        lines = [
            f"Samples: {self.n_positive} positive, {self.n_negative} negative",
            "",
            "Decided by earlier layers (not used in sweep):",
        ]
        for k, v in self.decided_by_earlier_layers.items():
            lines.append(f"  {k}: {v}")
        lines += [
            "",
            "Threshold sweep (only embedding-layer cases):",
            f"  {'pass':>6}  {'block':>6}  {'acc':>6}  {'false_block':>11}  {'false_pass':>10}",
        ]
        for row in self.sweep:
            lines.append(
                f"  {row['pass']:>6.2f}  {row['block']:>6.2f}  "
                f"{row['accuracy']:>6.2%}  {row['false_block_rate']:>11.2%}  "
                f"{row['false_pass_rate']:>10.2%}"
            )
        lines += [
            "",
            f"→ Recommended:  pass={self.recommended_pass:.2f}  "
            f"block={self.recommended_block:.2f}",
            f"  accuracy:       {self.accuracy_at_recommended:.2%}",
            f"  误杀率(in-domain blocked):  {self.false_block_rate:.2%}",
            f"  漏放率(out-of-domain pass): {self.false_pass_rate:.2%}",
        ]
        if self.edge_cases:
            lines += ["", "Edge cases (lowest-confidence decisions):"]
            for ec in self.edge_cases[:10]:
                lines.append(
                    f"  [{ec['label']:>8}] sim={ec['domain_sim']:.2f}  "
                    f"{ec['message']!r}"
                )
        return "\n".join(lines)


def load_samples(path: str | Path, label: str) -> list[Sample]:
    out: list[Sample] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            msg = obj["message"] if isinstance(obj, dict) else str(obj)
            out.append(Sample(message=msg, label=label))
    return out


def calibrate(
    config_path: str | Path,
    positive_path: str | Path,
    negative_path: str | Path,
    providers: dict | None = None,
) -> CalibrationReport:
    config = GuardConfig.from_yaml(config_path)

    providers = dict(providers or {})
    if "embedding" not in providers:
        providers["embedding"] = make_default_embedding()

    # Build only the layers we need to simulate
    bypass: ContextBypassLayer | None = None
    rule: RuleLayer | None = None
    embedding: EmbeddingLayer | None = None
    for lc in config.pipeline:
        if lc.type == "context_bypass":
            bypass = ContextBypassLayer(options=lc.options, providers=providers)
        elif lc.type == "rule":
            rule = RuleLayer(options=lc.options, providers=providers)
        elif lc.type == "embedding":
            embedding = EmbeddingLayer(options=lc.options, providers=providers)

    if embedding is None:
        raise ValueError("Calibration requires an 'embedding' layer in the config.")

    positives = load_samples(positive_path, "positive")
    negatives = load_samples(negative_path, "negative")
    samples = positives + negatives

    decided = {"context_bypass": 0, "rule_pass": 0, "rule_block": 0}
    sims: list[tuple[Sample, float, float]] = []  # (sample, domain_sim, ood_sim)
    ctx = GuardContext()

    for s in samples:
        if bypass is not None:
            out = bypass.decide(s.message, ctx)
            if out.verdict != "defer":
                decided["context_bypass"] += 1
                continue
        if rule is not None:
            out = rule.decide(s.message, ctx)
            if out.verdict == "pass":
                decided["rule_pass"] += 1
                continue
            if out.verdict == "block":
                decided["rule_block"] += 1
                continue
        # Compute embedding similarity for the sweep
        q = embedding._normalize(embedding.embedder.embed([s.message]))[0]
        domain_sim = float(np.max(embedding._domain_vecs @ q))
        ood_sim = (
            float(np.max(embedding._ood_vecs @ q))
            if embedding._ood_vecs is not None else 0.0
        )
        sims.append((s, domain_sim, ood_sim))

    # Sweep thresholds
    sweep_rows: list[dict] = []
    best: tuple[float, float, float, float, float] | None = None
    pass_grid = np.arange(0.40, 0.90, 0.02)
    block_grid = np.arange(0.20, 0.55, 0.05)

    for thr_pass in pass_grid:
        for thr_block in block_grid:
            if thr_block >= thr_pass:
                continue
            tp = fp = tn = fn = ambiguous = 0
            for s, d, o in sims:
                # Replicate EmbeddingLayer.decide logic
                if embedding._ood_vecs is not None and o > d and o >= thr_pass:
                    verdict = "block"
                elif d >= thr_pass:
                    verdict = "pass"
                elif d <= thr_block:
                    verdict = "block"
                else:
                    verdict = "defer"

                # Treat 'defer' as block (fail-closed default)
                effective_pass = (verdict == "pass")

                if s.label == "positive":
                    if effective_pass:
                        tp += 1
                    else:
                        fn += 1
                else:
                    if effective_pass:
                        fp += 1
                    else:
                        tn += 1
                if verdict == "defer":
                    ambiguous += 1

            total = len(sims)
            n_pos = sum(1 for s, _, _ in sims if s.label == "positive")
            n_neg = total - n_pos
            acc = (tp + tn) / max(total, 1)
            false_block_rate = fn / max(n_pos, 1)
            false_pass_rate = fp / max(n_neg, 1)
            sweep_rows.append({
                "pass": float(thr_pass), "block": float(thr_block),
                "accuracy": acc, "false_block_rate": false_block_rate,
                "false_pass_rate": false_pass_rate, "ambiguous": ambiguous,
            })
            # Optimize: highest accuracy, ties broken by lower false_block_rate
            score = (acc, -false_block_rate)
            if best is None or score > (best[2], -best[3]):
                best = (float(thr_pass), float(thr_block), acc,
                        false_block_rate, false_pass_rate)

    # Pick rows to display: every 4th, sorted by accuracy desc
    sweep_rows.sort(key=lambda r: -r["accuracy"])
    top = sweep_rows[:10]

    # Edge cases: positives with lowest domain_sim, negatives with highest
    edges: list[dict] = []
    pos_sorted = sorted([(s, d, o) for s, d, o in sims if s.label == "positive"],
                        key=lambda x: x[1])
    neg_sorted = sorted([(s, d, o) for s, d, o in sims if s.label == "negative"],
                        key=lambda x: -x[1])
    for s, d, o in pos_sorted[:5]:
        edges.append({"label": s.label, "message": s.message,
                      "domain_sim": d, "ood_sim": o})
    for s, d, o in neg_sorted[:5]:
        edges.append({"label": s.label, "message": s.message,
                      "domain_sim": d, "ood_sim": o})

    assert best is not None, "Sweep produced no candidates"

    return CalibrationReport(
        n_positive=len(positives),
        n_negative=len(negatives),
        recommended_pass=best[0],
        recommended_block=best[1],
        accuracy_at_recommended=best[2],
        false_block_rate=best[3],
        false_pass_rate=best[4],
        sweep=top,
        decided_by_earlier_layers=decided,
        edge_cases=edges,
    )


# ---------------- CLI entry point ----------------

def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="guard-cli calibrate")
    ap.add_argument("--config", required=True, help="Path to guard YAML")
    ap.add_argument("--positive", required=True, help="JSONL of in-domain samples")
    ap.add_argument("--negative", required=True, help="JSONL of out-of-domain samples")
    args = ap.parse_args(argv)

    report = calibrate(args.config, args.positive, args.negative)
    print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
