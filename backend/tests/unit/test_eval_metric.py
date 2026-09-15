"""Citation coverage must count genuine grounded answers, not refusals or breakdowns."""

from __future__ import annotations

import pytest

from scripts.eval import counts_toward_citation_coverage


def test_answered_grounded_turn_counts() -> None:
    assert counts_toward_citation_coverage("grounded_qa", {"outcome": "answered"}) is True


@pytest.mark.parametrize("outcome", ["abstained", "failed", "degraded", "uncited"])
def test_non_answers_do_not_count(outcome: str) -> None:
    assert counts_toward_citation_coverage("grounded_qa", {"outcome": outcome}) is False


def test_non_grounded_turns_do_not_count() -> None:
    assert counts_toward_citation_coverage("smalltalk", {"outcome": "smalltalk"}) is False
    assert counts_toward_citation_coverage("ship30_essay", {"outcome": "answered"}) is False
    assert counts_toward_citation_coverage("artifact", {"outcome": "answered"}) is False


def test_rows_without_a_recorded_outcome_do_not_count() -> None:
    """Turns persisted before outcomes were recorded are not assumed to be answers."""
    assert counts_toward_citation_coverage("grounded_qa", None) is False
    assert counts_toward_citation_coverage("grounded_qa", {}) is False
