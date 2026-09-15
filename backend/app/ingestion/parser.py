"""
Transcript parsing: YAML frontmatter plus speaker-attributed turns.

The upstream corpus formats each transcript as `Name (HH:MM:SS):` on its own
line followed by the spoken text, with blank lines separating paragraphs. The
non-obvious part is the continuation label: after the first paragraph of a turn,
later paragraphs are introduced by `(HH:MM:SS):` with **no name** -- the speaker
is the one carried over from the previous label. Missing that turns the host
into the guest for most of an episode, which is exactly the kind of silent
attribution error that would poison a citation, so it is handled explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog
import yaml

log = structlog.get_logger("app.ingestion.parser")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_LABEL_RE = re.compile(r"^(?P<speaker>[^(\n]*?)\s*\((?P<timestamp>\d{1,2}:\d{2}:\d{2})\):\s*$")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass(slots=True)
class Turn:
    """One speaker's contiguous speech. `speaker` is None only if none was ever seen."""

    speaker: str | None
    text: str
    timestamp: str | None = None


@dataclass(slots=True)
class ParsedTranscript:
    slug: str
    meta: dict[str, Any]
    turns: list[Turn]
    body: str

    @property
    def title(self) -> str:
        title = self.meta.get("title")
        return str(title).strip() if title else self.slug.replace("-", " ").title()

    @property
    def guest(self) -> str | None:
        guest = self.meta.get("guest")
        return str(guest).strip() or None if guest else None

    @property
    def youtube_url(self) -> str | None:
        url = self.meta.get("youtube_url")
        return str(url).strip() or None if url else None

    @property
    def published_at(self) -> date | None:
        raw = self.meta.get("publish_date")
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw.strip())
            except ValueError:
                log.warning("unparseable_publish_date", slug=self.slug, value=raw)
        return None


def split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Return (metadata, body). A transcript without frontmatter parses to empty metadata."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        log.warning("frontmatter_parse_failed", error=str(exc))
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, raw[match.end() :]


def _clean(text: str) -> str:
    """Collapse the intra-paragraph newlines that transcripts use for wrapping."""
    return re.sub(r"[ \t]*\n[ \t]*", " ", text).strip()


def parse_turns(body: str) -> list[Turn]:
    """
    Walk paragraphs, starting a new turn at each speaker label.

    A paragraph is a label line plus the speech that follows it. Paragraphs with
    no label at all (rare, but present in some episodes) are folded into the
    previous turn rather than becoming an unattributed turn of their own.
    """
    turns: list[Turn] = []
    current_speaker: str | None = None

    for paragraph in _PARAGRAPH_SPLIT_RE.split(body):
        lines = paragraph.strip().split("\n")
        if not lines:
            continue

        first = lines[0].strip()
        if first.startswith("#"):
            continue

        match = _LABEL_RE.match(first)
        if match:
            speaker = match.group("speaker").strip() or current_speaker
            current_speaker = speaker
            text = _clean("\n".join(lines[1:]))
            if text:
                turns.append(Turn(speaker=speaker, text=text, timestamp=match.group("timestamp")))
            continue

        text = _clean(paragraph)
        if not text:
            continue
        if turns:
            turns[-1].text = f"{turns[-1].text}\n\n{text}" if turns[-1].text else text
        else:
            turns.append(Turn(speaker=current_speaker, text=text))

    return turns


def parse_transcript(raw: str, slug: str) -> ParsedTranscript:
    """Parse one `transcript.md` into metadata and speaker-attributed turns."""
    meta, body = split_frontmatter(raw)
    return ParsedTranscript(slug=slug, meta=meta, turns=parse_turns(body), body=body)
