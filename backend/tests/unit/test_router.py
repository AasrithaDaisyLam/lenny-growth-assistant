"""Intent routing: deterministic, explainable, and fully covered by tests."""

from __future__ import annotations

import pytest

from app.agent.router import Intent, classify, intent_to_str

CASES: list[tuple[str, Intent]] = [
    ("", Intent.SMALLTALK),
    ("hi", Intent.SMALLTALK),
    ("Hey there", Intent.SMALLTALK),
    ("thanks!", Intent.SMALLTALK),
    ("good morning", Intent.SMALLTALK),
    ("what can you do?", Intent.SMALLTALK),
    ("Write me a Ship 30 essay about activation", Intent.SHIP30_ESSAY),
    ("Ship30 this: retention", Intent.SHIP30_ESSAY),
    ("draft a LinkedIn post on retention", Intent.SHIP30_ESSAY),
    ("write a blog post about growth loops", Intent.SHIP30_ESSAY),
    ("make me a chart of activation metrics", Intent.ARTIFACT),
    ("visualize retention over time", Intent.ARTIFACT),
    ("build an HTML dashboard", Intent.ARTIFACT),
    ("How does Lovable think about activation?", Intent.GROUNDED_QA),
    ("What did Casey Winters say about retention?", Intent.GROUNDED_QA),
]


@pytest.mark.parametrize(
    "question,expected", CASES, ids=[f"{i}-{c[1]}" for i, c in enumerate(CASES)]
)
def test_classify_routes_each_intent(question: str, expected: Intent) -> None:
    assert classify(question) is expected


def test_a_polite_long_question_is_not_smalltalk() -> None:
    """
    The length guard exists so a real question that opens with "hi" is not
    swallowed by the greeting rule and answered without retrieval.
    """
    question = "hi, how does the growth team at Lovable think about activation and retention?"
    assert classify(question) is Intent.GROUNDED_QA


def test_essay_wins_over_artifact() -> None:
    # An essay may well contain a table; that does not make it a chart request.
    assert classify("write an essay with a table of activation metrics") is Intent.SHIP30_ESSAY


def test_unmatched_input_defaults_to_grounded_qa() -> None:
    # The safe default: it is the only path with retrieval and citation validation.
    assert classify("zzzz nothing matches this") is Intent.GROUNDED_QA


def test_classification_is_deterministic() -> None:
    question = "write a ship30 essay about retention"
    assert {classify(question) for _ in range(20)} == {Intent.SHIP30_ESSAY}


def test_intent_serializes_to_a_stable_string() -> None:
    # Stored on the message row, so the value must not drift.
    assert intent_to_str(Intent.GROUNDED_QA) == "grounded_qa"
    assert intent_to_str(Intent.SHIP30_ESSAY) == "ship30_essay"
    assert intent_to_str(Intent.ARTIFACT) == "artifact"
    assert intent_to_str(Intent.SMALLTALK) == "smalltalk"
