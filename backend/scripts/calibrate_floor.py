"""
Calibrate the relevance floor against the golden set.

The floor's only job is to answer "does this corpus cover the question at all?".
Rather than pick a number by intuition, this prints the two score distributions
and sweeps every candidate threshold, so the choice -- and any overlap between the
classes -- is visible.

    python -m scripts.calibrate_floor

Latency is reported for the retrieval path alone (query embedding + both arms +
fusion), with no generation in the loop, so a slow answer can be attributed to the
retriever rather than the model.

Reported as measured. If no threshold separates the classes, the honest output is
that no threshold separates the classes; that is the evidence behind the layered
abstention design (`docs/decisions.md`, ADR-003).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionLocal, dispose_engine
from app.retrieval.hybrid import best_similarity, search

GOLDEN_SET = Path(__file__).resolve().parents[1] / "tests" / "eval" / "golden_set.yaml"


@dataclass(slots=True)
class Observation:
    id: str
    question: str
    in_corpus: bool
    expected_slugs: list[str]
    best_similarity: float | None
    top_slug: str | None
    recall_hit: bool
    latency_ms: float


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. n is small here, so interpolation would be false precision."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))
    return ordered[index]


def _summary(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"min={min(values):.3f} median={statistics.median(values):.3f} max={max(values):.3f}"


def _passes(observation: Observation, threshold: float) -> bool:
    return observation.best_similarity is not None and observation.best_similarity >= threshold


def _sweep(observations: list[Observation]) -> list[tuple[float, float, float, float]]:
    """
    For each candidate threshold, return (threshold, recall, specificity, balanced).

    recall: share of in-corpus questions that still clear the floor.
    specificity: share of out-of-corpus questions the floor correctly rejects.
    """
    candidates = sorted(
        {round(o.best_similarity, 3) for o in observations if o.best_similarity is not None}
    )
    in_corpus = [o for o in observations if o.in_corpus]
    out_corpus = [o for o in observations if not o.in_corpus]

    rows = []
    for threshold in candidates:
        recall = (
            sum(_passes(o, threshold) for o in in_corpus) / len(in_corpus) if in_corpus else 0.0
        )
        specificity = (
            sum(not _passes(o, threshold) for o in out_corpus) / len(out_corpus)
            if out_corpus
            else 0.0
        )
        rows.append((threshold, recall, specificity, (recall + specificity) / 2))
    return rows


async def run(golden_path: Path, show_all: bool) -> int:
    data = yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    questions = data.get("questions", [])

    observations: list[Observation] = []
    async with AsyncSessionLocal() as db:
        for item in questions:
            query = item["question"]
            started = time.perf_counter()
            results = await search(db, query)
            latency_ms = (time.perf_counter() - started) * 1000

            expected = item.get("expected_slugs") or []
            top_slug = results[0].episode_slug if results else None
            observation = Observation(
                id=item["id"],
                question=query,
                in_corpus=bool(item.get("in_corpus")),
                expected_slugs=expected,
                best_similarity=best_similarity(results),
                top_slug=top_slug,
                recall_hit=bool(expected) and top_slug in expected,
                latency_ms=latency_ms,
            )
            observations.append(observation)
            shown = (
                "n/a"
                if observation.best_similarity is None
                else f"{observation.best_similarity:.3f}"
            )
            print(
                f"  {observation.id:<22} in_corpus={str(observation.in_corpus):<5} "
                f"best={shown:<6} top={str(top_slug):<18} "
                f"recall={'HIT' if observation.recall_hit else 'miss':<4} "
                f"latency={latency_ms:.0f}ms"
            )

    in_scores = [
        o.best_similarity for o in observations if o.in_corpus and o.best_similarity is not None
    ]
    out_scores = [
        o.best_similarity for o in observations if not o.in_corpus and o.best_similarity is not None
    ]
    latencies = [o.latency_ms for o in observations]

    print()
    print("Score distributions (dense cosine)")
    print(f"  in corpus      {_summary(in_scores)}")
    print(f"  not in corpus  {_summary(out_scores)}")

    if in_scores and out_scores:
        if min(in_scores) > max(out_scores):
            midpoint = (min(in_scores) + max(out_scores)) / 2
            print(f"  classes are SEPARABLE; midpoint threshold = {midpoint:.3f}")
        else:
            print(
                f"  classes OVERLAP: max(out-of-corpus) {max(out_scores):.3f} >= "
                f"min(in-corpus) {min(in_scores):.3f} -- no single threshold separates them."
            )

    print()
    print("Retrieval latency (embed + vector + lexical + fusion, no generation)")
    print(
        f"  p50={_percentile(latencies, 0.50):.0f}ms  "
        f"p95={_percentile(latencies, 0.95):.0f}ms  max={max(latencies):.0f}ms"
    )

    sweep = _sweep(observations)
    print()
    print("Threshold sweep (recall = in-corpus kept, specificity = out-of-corpus rejected)")
    ranked = sorted(sweep, key=lambda row: (-row[3], -row[1]))
    for threshold, recall, specificity, balanced in sweep if show_all else ranked[:8]:
        print(
            f"  t={threshold:.3f}  recall={recall:.2f}  specificity={specificity:.2f}  "
            f"balanced={balanced:.2f}"
        )

    if ranked:
        best = ranked[0]
        print()
        print(
            f"  best balanced threshold: {best[0]:.3f} (recall={best[1]:.2f}, specificity={best[2]:.2f})"
        )

    current = settings.rag_min_score
    kept = sum(1 for o in observations if o.in_corpus and _passes(o, current))
    rejected = sum(1 for o in observations if not o.in_corpus and not _passes(o, current))
    print(
        f"  configured RAG_MIN_SCORE={current}: in-corpus kept {kept}/{len(in_scores)}, "
        f"out-of-corpus rejected {rejected}/{len(out_scores)}"
    )
    return 0


async def _main(golden_path: Path, show_all: bool) -> int:
    try:
        return await run(golden_path, show_all)
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate RAG_MIN_SCORE against the golden set.")
    parser.add_argument("--golden", type=Path, default=GOLDEN_SET)
    parser.add_argument("--all", action="store_true", help="print the full threshold sweep")
    args = parser.parse_args()

    configure_logging()
    return asyncio.run(_main(args.golden, args.all))


if __name__ == "__main__":
    sys.exit(main())
