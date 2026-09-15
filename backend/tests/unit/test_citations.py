"""Citation validation: resolvable, deduplicated, and honest about the unresolvable."""

from __future__ import annotations

from app.retrieval.hybrid import RetrievedChunk
from app.services.cite_service import needs_citation_retry, validate_citations


def _chunk(chunk_id: int, slug: str = "ep", title: str = "Episode") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        episode_id=1,
        episode_slug=slug,
        episode_title=title,
        guest="Guest",
        youtube_url=None,
        chunk_index=chunk_id,
        speaker="Speaker",
        text=f"text {chunk_id}",
        score=0.0,
        similarity=0.7,
    )


def test_valid_markers_resolve_to_their_chunk() -> None:
    result = validate_citations("Retention matters most [S1].", [_chunk(10)])
    assert result.text == "Retention matters most [S1]."
    assert len(result.citations) == 1
    assert result.citations[0]["chunk_id"] == 10
    assert result.citations[0]["marker"] == "S1"
    assert result.unknown_markers == []


def test_positional_mapping_follows_prompt_order() -> None:
    chunks = [_chunk(1), _chunk(2), _chunk(3)]
    result = validate_citations("As shown [S3] and [S2].", chunks)
    assert [c["chunk_id"] for c in result.citations] == [3, 2]


def test_citations_are_deduplicated_and_ordered_by_first_use() -> None:
    result = validate_citations("[S2] then [S1] then [S2] again.", [_chunk(1), _chunk(2)])
    assert [c["marker"] for c in result.citations] == ["S2", "S1"]


def test_out_of_range_marker_is_stripped_and_reported() -> None:
    """
    The point of the module: an unresolvable marker must not survive into the
    stored answer, but must also not vanish silently.
    """
    result = validate_citations("Claim [S9] here.", [_chunk(1)])
    assert "[S9]" not in result.text
    assert result.text == "Claim here."
    assert result.unknown_markers == ["[S9]"]
    assert result.citations == []


def test_marker_zero_is_out_of_range() -> None:
    # Sources are numbered from 1, so [S0] cannot resolve.
    result = validate_citations("Bad [S0].", [_chunk(1)])
    assert result.unknown_markers == ["[S0]"]


def test_no_markers_yields_no_citations() -> None:
    result = validate_citations("An answer with no sources.", [_chunk(1)])
    assert result.citations == []
    assert result.had_citations is False
    assert result.text == "An answer with no sources."


def test_removal_does_not_leave_double_spaces() -> None:
    result = validate_citations("One [S9] two.", [_chunk(1)])
    assert "  " not in result.text
    assert result.text == "One two."


def test_no_space_before_punctuation_after_removal() -> None:
    result = validate_citations("Claim [S9].", [_chunk(1)])
    assert result.text == "Claim."


def test_case_variant_marker_is_normalised_and_resolves() -> None:
    """`[s1]` is the canonical marker with different casing, so rewriting it is unambiguous."""
    result = validate_citations("Retention matters [s1].", [_chunk(10)])
    assert result.text == "Retention matters [S1]."
    assert [c["marker"] for c in result.citations] == ["S1"]
    assert result.unknown_markers == []


def test_inner_spacing_is_normalised() -> None:
    result = validate_citations("Retention matters [S 1].", [_chunk(10)])
    assert result.text == "Retention matters [S1]."
    assert [c["marker"] for c in result.citations] == ["S1"]


def test_spelled_out_source_is_reported_and_stripped() -> None:
    """Source-shaped but not the contract: reported rather than interpreted."""
    result = validate_citations("Claim [Source 1] here.", [_chunk(1)])
    assert result.text == "Claim here."
    assert result.unknown_markers == ["[Source 1]"]
    assert result.citations == []


def test_marker_list_is_reported_rather_than_guessed() -> None:
    result = validate_citations("As shown [S1, S2].", [_chunk(1), _chunk(2)])
    assert result.citations == []
    assert result.unknown_markers == ["[S1, S2]"]
    assert "[S1, S2]" not in result.text


def test_marker_range_is_reported_rather_than_guessed() -> None:
    result = validate_citations("As shown [S1-S3].", [_chunk(1), _chunk(2), _chunk(3)])
    assert result.citations == []
    assert result.unknown_markers == ["[S1-S3]"]


def test_bare_s_marker_is_reported() -> None:
    result = validate_citations("As shown [S].", [_chunk(1)])
    assert result.unknown_markers == ["[S]"]


def test_prose_brackets_are_left_alone() -> None:
    """`[appendix]` is not a citation, and rewriting it would corrupt the answer."""
    result = validate_citations("See [appendix] and [2].", [_chunk(1)])
    assert result.text == "See [appendix] and [2]."
    assert result.unknown_markers == []


def test_gate_triggers_on_a_substantive_uncited_answer_when_sources_exist() -> None:
    answer = "Activation is the growth team's priority at Lovable, and they say so plainly. " * 2
    result = validate_citations(answer, [_chunk(1)])
    assert result.citations == []
    assert needs_citation_retry(result, [_chunk(1)]) is True


def test_gate_does_not_trigger_without_sources() -> None:
    """Nothing to cite: an uncited answer is the correct outcome, not a defect."""
    answer = "Activation is the growth team's priority at Lovable, and they say so plainly. " * 2
    assert needs_citation_retry(validate_citations(answer, []), []) is False


def test_gate_does_not_trigger_on_a_refusal_fragment() -> None:
    result = validate_citations("I can't answer that.", [_chunk(1)])
    assert needs_citation_retry(result, [_chunk(1)]) is False


def test_gate_is_satisfied_by_a_resolved_citation() -> None:
    result = validate_citations("Retention matters most [S1].", [_chunk(1)])
    assert needs_citation_retry(result, [_chunk(1)]) is False
