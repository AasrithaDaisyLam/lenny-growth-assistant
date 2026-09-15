"""Parser tests, written against the real corpus format rather than an idealised one."""

from __future__ import annotations

from datetime import date

from app.ingestion.parser import parse_transcript, split_frontmatter

SAMPLE = """---
guest: Brian Chesky
title: Brian Chesky's new playbook
youtube_url: https://www.youtube.com/watch?v=4ef0juAMqoE
video_id: 4ef0juAMqoE
publish_date: 2023-11-12
duration_seconds: 4408.0
channel: Lenny's Podcast
keywords:
- growth
- metrics
---

# Brian Chesky's new playbook

## Transcript

Brian Chesky (00:00:00):
Way too many founders apologize. They find a midpoint.

(00:00:30):
And what everyone really wants is clarity.

Lenny (00:01:01):
Today my guest is Brian Chesky.

A paragraph with no label at all.
"""


def test_frontmatter_is_parsed() -> None:
    meta, _ = split_frontmatter(SAMPLE)
    assert meta["guest"] == "Brian Chesky"
    assert meta["video_id"] == "4ef0juAMqoE"
    assert meta["keywords"] == ["growth", "metrics"]


def test_transcript_without_frontmatter_still_parses() -> None:
    meta, body = split_frontmatter("Just text, no frontmatter.\n")
    assert meta == {}
    assert body == "Just text, no frontmatter.\n"


def test_continuation_label_inherits_previous_speaker() -> None:
    """
    The load-bearing case: a `(00:00:30):` label carries no name. If it is not
    attributed to the previous speaker the host ends up credited for the guest's
    words, which would produce a wrong citation.
    """
    parsed = parse_transcript(SAMPLE, "brian-chesky")
    speakers = [turn.speaker for turn in parsed.turns]
    assert speakers == ["Brian Chesky", "Brian Chesky", "Lenny"]


def test_unlabelled_paragraph_folds_into_previous_turn() -> None:
    parsed = parse_transcript(SAMPLE, "brian-chesky")
    assert len(parsed.turns) == 3
    assert "no label at all" in parsed.turns[-1].text
    assert parsed.turns[-1].speaker == "Lenny"


def test_headers_are_not_treated_as_speech() -> None:
    parsed = parse_transcript(SAMPLE, "brian-chesky")
    assert all("Transcript" not in turn.text for turn in parsed.turns)


def test_metadata_properties() -> None:
    parsed = parse_transcript(SAMPLE, "brian-chesky")
    assert parsed.title == "Brian Chesky's new playbook"
    assert parsed.guest == "Brian Chesky"
    assert parsed.youtube_url == "https://www.youtube.com/watch?v=4ef0juAMqoE"
    assert parsed.published_at == date(2023, 11, 12)


def test_title_falls_back_to_slug() -> None:
    parsed = parse_transcript("no frontmatter here", "elena-verna")
    assert parsed.title == "Elena Verna"
    assert parsed.published_at is None
