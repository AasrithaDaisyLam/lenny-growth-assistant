"""
Measured metrics.

Prints the numbers the brief asks to be measurable, and prints them as measured
rather than as targeted. Three of them come from the golden set (retrieval quality
and abstention correctness); citation coverage is computed over answers actually
persisted in the database, so it reports the system's real behaviour rather than a
freshly generated sample that might flatter it.

Citation coverage counts genuine grounded answers only. A turn that abstained,
failed, timed out, degraded, or was not a grounded question at all cannot carry a
citation, so counting it would report those outcomes as citation gaps.

    make eval
    python -m scripts.eval --generate   # also run live turns (slow on CPU)

Latency is reported for retrieval alone and end to end, because on this hardware
they differ by two orders of magnitude and averaging them would hide both.
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
from sqlalchemy import select

from app.agent.router import Intent
from app.config import settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionLocal, dispose_engine
from app.models.conversation import ChatMessage
from app.retrieval.hybrid import best_similarity, search, should_abstain
from app.services import outcomes

GOLDEN_SET = Path(__file__).resolve().parents[1] / "tests" / "eval" / "golden_set.yaml"


@dataclass
class Row:
    question_id: str
    in_corpus: bool
    best_similarity: float | None
    abstained: bool
    top_slug: str | None
    recall_hit: bool
    latency_ms: float


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))
    return ordered[index]


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{100 * numerator / denominator:.0f}% ({numerator}/{denominator})"


async def _retrieval_rows(db) -> list[Row]:
    questions = yaml.safe_load(GOLDEN_SET.read_text(encoding="utf-8")).get("questions", [])
    rows: list[Row] = []
    for item in questions:
        started = time.perf_counter()
        results = await search(db, item["question"])
        latency_ms = (time.perf_counter() - started) * 1000

        expected = item.get("expected_slugs") or []
        top_slug = results[0].episode_slug if results else None
        rows.append(
            Row(
                question_id=item["id"],
                in_corpus=bool(item.get("in_corpus")),
                best_similarity=best_similarity(results),
                abstained=should_abstain(results),
                top_slug=top_slug,
                recall_hit=bool(expected) and top_slug in expected,
                latency_ms=latency_ms,
            )
        )
    return rows


def counts_toward_citation_coverage(intent: str | None, retrieval_trace: dict | None) -> bool:
    """
    Whether a stored assistant turn is a genuine grounded answer.

    Only a grounded question that actually produced an answer counts. An explicit
    decline (the relevance floor, or an answer no source could be attached to), a
    provider failure or timeout, a degraded turn, smalltalk, and essay/artifact
    turns are all excluded -- counting them reports the system's refusals and
    breakdowns as citation gaps, which is the opposite of what the number means.
    """
    if intent != Intent.GROUNDED_QA:
        return False
    return (retrieval_trace or {}).get("outcome") == outcomes.ANSWERED


async def _citation_coverage(db) -> tuple[int, int, int]:
    """
    Share of genuine grounded answers that carry at least one citation.

    Returns `(cited, total, declined)`. `declined` counts grounded turns that were
    withheld because no source could be attached to them, and is reported next to
    coverage so a clean reading is not mistaken for "no such turns happened".
    """
    statement = select(
        ChatMessage.intent, ChatMessage.citations, ChatMessage.retrieval_trace
    ).where(ChatMessage.role == "assistant")

    cited = total = declined = 0
    for intent, citations, trace in (await db.execute(statement)).all():
        if intent == Intent.GROUNDED_QA and (trace or {}).get("outcome") == outcomes.UNCITED:
            declined += 1
        if not counts_toward_citation_coverage(intent, trace):
            continue
        total += 1
        if citations:
            cited += 1
    return cited, total, declined


async def _end_to_end_latency(db) -> list[float]:
    statement = select(ChatMessage.latency_ms).where(
        ChatMessage.role == "assistant", ChatMessage.latency_ms.is_not(None)
    )
    return [float(value) for (value,) in (await db.execute(statement)).all()]


def _print_retrieval(rows: list[Row]) -> None:
    in_corpus = [row for row in rows if row.in_corpus]
    out_corpus = [row for row in rows if not row.in_corpus]

    print("Retrieval")
    print(
        f"  recall (top hit is the expected episode): {_pct(sum(r.recall_hit for r in in_corpus), len(in_corpus))}"
    )

    in_scores = [r.best_similarity for r in in_corpus if r.best_similarity is not None]
    out_scores = [r.best_similarity for r in out_corpus if r.best_similarity is not None]
    if in_scores and out_scores:
        print(
            f"  best dense similarity, in corpus:  min={min(in_scores):.3f} "
            f"median={statistics.median(in_scores):.3f} max={max(in_scores):.3f}"
        )
        print(
            f"  best dense similarity, not in corpus: min={min(out_scores):.3f} "
            f"median={statistics.median(out_scores):.3f} max={max(out_scores):.3f}"
        )
        separable = min(in_scores) > max(out_scores)
        print(f"  classes separable at floor {settings.rag_min_score}: {separable}")

    latencies = [row.latency_ms for row in rows]
    print(
        f"  latency p50={_percentile(latencies, 0.50):.0f}ms "
        f"p95={_percentile(latencies, 0.95):.0f}ms"
    )


def _print_abstention(rows: list[Row]) -> None:
    in_corpus = [row for row in rows if row.in_corpus]
    out_corpus = [row for row in rows if not row.in_corpus]

    # Correct abstention: declined when uncovered, and answered when covered.
    correct_declines = sum(row.abstained for row in out_corpus)
    incorrect_declines = sum(row.abstained for row in in_corpus)
    answered_when_covered = sum(not row.abstained for row in in_corpus)

    print()
    print("Abstention")
    print(f"  uncovered questions declined:   {_pct(correct_declines, len(out_corpus))}")
    print(f"  covered questions answered:     {_pct(answered_when_covered, len(in_corpus))}")
    print(f"  covered questions wrongly declined: {_pct(incorrect_declines, len(in_corpus))}")


async def run(generate: bool) -> int:
    async with AsyncSessionLocal() as db:
        rows = await _retrieval_rows(db)
        cited, total, declined = await _citation_coverage(db)
        e2e = await _end_to_end_latency(db)

    _print_retrieval(rows)
    _print_abstention(rows)

    print()
    print("Citation coverage (persisted grounded answers)")
    if total == 0:
        print("  no grounded answers persisted yet -- run a few turns first")
    else:
        print(f"  answers carrying at least one citation: {_pct(cited, total)}")
        print("  NOTE: a citation must also resolve; this counts non-empty citation lists.")
    if declined:
        print(f"  grounded turns with no citable answer (declined): {declined}")

    print()
    print("End-to-end turn latency (persisted assistant messages)")
    if not e2e:
        print("  no completed turns recorded yet")
    else:
        print(
            f"  p50={_percentile(e2e, 0.50) / 1000:.1f}s "
            f"p95={_percentile(e2e, 0.95) / 1000:.1f}s n={len(e2e)}"
        )

    if generate:
        print()
        print("--generate was requested, but live generation is triggered by posting")
        print("messages to /api/sessions/{id}/messages; use scripts/agent_smoke.py for a")
        print("single prompt over the agent path.")

    return 0


async def _main(generate: bool) -> int:
    try:
        return await run(generate)
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the measured success metrics.")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="note how to produce live answers (turn generation is driven by the API)",
    )
    args = parser.parse_args()

    configure_logging()
    return asyncio.run(_main(args.generate))


if __name__ == "__main__":
    sys.exit(main())
