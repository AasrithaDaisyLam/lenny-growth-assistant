"""
The corpus ships its own topic index -- an `index/` folder of AI-generated
per-topic files, each linking to the episodes that cover it. Parsing it is what
makes the abstention response useful rather than a dead end: when nothing clears
the relevance floor we can name the ground the corpus *does* cover.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger("app.ingestion.topics")

_LINK_RE = re.compile(r"^\s*-\s*\[(?P<name>[^\]]+)\]\((?P<href>[^)]+)\)", re.MULTILINE)
_EPISODE_HREF_RE = re.compile(r"\.\./episodes/(?P<slug>[^/)]+)/transcript\.md")
_HEADING_RE = re.compile(r"^#\s+(?P<name>.+?)\s*$", re.MULTILINE)

# index/README.md is the topic list; index/episodes.md is a full episode listing,
# not a topic, and is large enough to be worth skipping outright.
_SKIP_FILES = {"readme.md", "episodes.md"}


@dataclass(slots=True)
class TopicEntry:
    name: str
    episode_slugs: list[str]


def _display_names(readme: Path) -> dict[str, str]:
    """Map `ab-testing.md` -> `Ab Testing` from the index README's link list."""
    names: dict[str, str] = {}
    if not readme.exists():
        return names
    for match in _LINK_RE.finditer(readme.read_text(encoding="utf-8")):
        href = match.group("href").strip()
        if href.endswith(".md") and "/" not in href:
            names[href.lower()] = match.group("name").strip()
    return names


def parse_topic_index(index_dir: Path) -> list[TopicEntry]:
    """Parse every topic file into a topic name and the episode slugs it references."""
    if not index_dir.is_dir():
        log.warning("topic_index_missing", path=str(index_dir))
        return []

    display = _display_names(index_dir / "README.md")
    entries: list[TopicEntry] = []

    for path in sorted(index_dir.glob("*.md")):
        if path.name.lower() in _SKIP_FILES:
            continue

        text = path.read_text(encoding="utf-8")
        slugs: list[str] = []
        for match in _EPISODE_HREF_RE.finditer(text):
            slug = match.group("slug").strip()
            if slug not in slugs:
                slugs.append(slug)

        if not slugs:
            continue

        heading = _HEADING_RE.search(text)
        name = display.get(path.name.lower()) or (
            heading.group("name").strip() if heading else path.stem
        )
        entries.append(TopicEntry(name=name, episode_slugs=slugs))

    log.info("topic_index_parsed", topics=len(entries))
    return entries
