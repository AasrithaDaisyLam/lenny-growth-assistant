"""
The ingestion pipeline: fetch -> parse -> chunk -> hash -> embed -> upsert.

Two properties make this safe to re-run. It is **idempotent**: chunks are keyed
by a content hash, so re-ingesting an unchanged corpus embeds nothing. And it is
**incremental**: only episodes whose file hash changed are touched, so a weekly
corpus refresh costs one episode's worth of embedding, not 269.

The run is recorded in `ingestion_runs` so "when did the corpus last change, and
what did it do?" is answerable without reading logs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.ingestion.chunking import chunk_turns
from app.ingestion.parser import ParsedTranscript, parse_transcript
from app.ingestion.source import get_source
from app.ingestion.topics import parse_topic_index
from app.models.corpus import Chunk, Episode, Topic, episode_topics
from app.models.ops import IngestionRun
from app.services.ollama import ollama

log = structlog.get_logger("app.ingestion")


@dataclass
class IngestionStats:
    episodes_seen: int = 0
    episodes_written: int = 0
    episodes_skipped: int = 0
    chunks_written: int = 0
    chunks_skipped: int = 0
    chunks_deleted: int = 0
    topics_linked: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "episodes_seen": self.episodes_seen,
            "episodes_written": self.episodes_written,
            "episodes_skipped": self.episodes_skipped,
            "chunks_written": self.chunks_written,
            "chunks_skipped": self.chunks_skipped,
            "chunks_deleted": self.chunks_deleted,
            "topics_linked": self.topics_linked,
            "errors": self.errors,
        }


def content_hash(*parts: str) -> str:
    """sha256 over the fields that define a chunk's identity."""
    joined = "\x1f".join(part or "" for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def discover_transcripts(root: Path) -> list[tuple[str, Path]]:
    """Find every `episodes/<slug>/transcript.md`, sorted for a deterministic run."""
    found: list[tuple[str, Path]] = []
    for episode_dir in sorted((root / "episodes").iterdir()):
        transcript = episode_dir / "transcript.md"
        if episode_dir.is_dir() and transcript.is_file():
            found.append((episode_dir.name, transcript))
    return found


async def _upsert_episode(db, parsed: ParsedTranscript, raw_hash: str, source_url: str) -> int:
    values = {
        "slug": parsed.slug,
        "title": parsed.title,
        "guest": parsed.guest,
        "youtube_url": parsed.youtube_url,
        "published_at": parsed.published_at,
        "source_url": source_url,
        "source_updated_at": datetime.now(UTC),
        "content_hash": raw_hash,
    }
    statement = (
        pg_insert(Episode)
        .values(**values)
        .on_conflict_do_update(index_elements=[Episode.slug], set_=values)
        .returning(Episode.id)
    )
    return (await db.execute(statement)).scalar_one()


async def _ingest_episode(db, slug: str, path: Path, stats: IngestionStats) -> int:
    raw = path.read_text(encoding="utf-8")
    raw_hash = content_hash(raw)
    parsed = parse_transcript(raw, slug)

    existing = (
        await db.execute(select(Episode.id, Episode.content_hash).where(Episode.slug == slug))
    ).first()

    # Unchanged file: the episode row and its chunks are already correct.
    if existing is not None and existing.content_hash == raw_hash:
        stats.episodes_skipped += 1
        return int(existing.id)

    source_url = (
        f"https://github.com/{settings.transcript_repo}/blob/main/episodes/{slug}/transcript.md"
    )
    episode_id = await _upsert_episode(db, parsed, raw_hash, source_url)
    stats.episodes_written += 1

    chunks = chunk_turns(
        parsed.turns,
        target_tokens=settings.chunk_target_tokens,
        overlap_ratio=settings.chunk_overlap_ratio,
    )
    hashes = [content_hash(slug, chunk.speaker or "", chunk.text) for chunk in chunks]

    existing_rows = (
        await db.execute(
            select(Chunk.chunk_index, Chunk.content_hash).where(Chunk.episode_id == episode_id)
        )
    ).all()
    existing_hash_set = {row.content_hash for row in existing_rows}

    # Embed only what is genuinely new. This is the difference between a
    # re-ingestion costing seconds and costing minutes of CPU inference.
    pending = [
        chunk
        for chunk, digest in zip(chunks, hashes, strict=True)
        if digest not in existing_hash_set
    ]
    vectors: dict[str, list[float]] = {}
    if pending:
        embeddings = await ollama.embed_batched([chunk.text for chunk in pending])
        pending_digests = [d for d in hashes if d not in existing_hash_set]
        vectors = dict(zip(pending_digests, embeddings, strict=True))

    for chunk, digest in zip(chunks, hashes, strict=True):
        row = {
            "episode_id": episode_id,
            "chunk_index": chunk.index,
            "speaker": chunk.speaker,
            "text": chunk.text,
            "token_count": chunk.token_count,
            "content_hash": digest,
        }
        if digest in vectors:
            row["embedding"] = vectors[digest]
            stats.chunks_written += 1
        else:
            stats.chunks_skipped += 1

        await db.execute(
            pg_insert(Chunk)
            .values(**row)
            .on_conflict_do_update(index_elements=[Chunk.episode_id, Chunk.chunk_index], set_=row)
        )

    # Drop chunks that no longer exist in the source (edited or shortened episode).
    stale = delete(Chunk).where(Chunk.episode_id == episode_id)
    if hashes:
        stale = stale.where(Chunk.content_hash.not_in(hashes))
    stats.chunks_deleted += (await db.execute(stale)).rowcount or 0

    return episode_id


async def _link_topics(db, root: Path, slug_to_id: dict[str, int]) -> int:
    """
    Link each topic to the episodes that cover it, returning the total link count.

    Called repeatedly during a run, so it returns a *total* rather than a delta: an
    incremental counter would inflate on every call, whereas re-counting is
    naturally idempotent (the inserts themselves are ON CONFLICT DO NOTHING).

    Linking during the run, not only at the end, is what makes the abstention
    topic suggestions durable. A run that is interrupted used to leave the topics
    table empty -- so the "the corpus does not cover that, but it does cover these"
    response had nothing to suggest until a full run completed.
    """
    entries = parse_topic_index(root / "index")
    for entry in entries:
        topic_id = (
            await db.execute(
                pg_insert(Topic)
                .values(name=entry.name)
                .on_conflict_do_update(index_elements=[Topic.name], set_={"name": entry.name})
                .returning(Topic.id)
            )
        ).scalar_one()

        links = [slug_to_id[s] for s in entry.episode_slugs if s in slug_to_id]
        if not links:
            continue
        await db.execute(
            pg_insert(episode_topics)
            .values([{"episode_id": episode_id, "topic_id": topic_id} for episode_id in links])
            .on_conflict_do_nothing()
        )

    return int((await db.execute(select(func.count()).select_from(episode_topics))).scalar_one())


async def ingest(force: bool = False, limit: int | None = None) -> IngestionStats:
    """
    Fetch, parse, and index the corpus. Safe to call repeatedly.

    `limit` indexes only the first N episodes. Embedding the whole corpus is a
    CPU-bound job measured in hours on the reference machine, so the limit is the
    supported fast path for demos and tests; it changes nothing else, and a later
    unlimited run tops up the remaining episodes.
    """
    stats = IngestionStats()
    root = get_source().prepare(force=force)
    transcripts = discover_transcripts(root)
    if limit is not None:
        transcripts = transcripts[:limit]
    log.info("ingestion_start", root=str(root), episodes=len(transcripts), force=force, limit=limit)

    async with AsyncSessionLocal() as db:
        run = IngestionRun(status="running", source=str(root))
        db.add(run)
        await db.commit()
        await db.refresh(run)

        slug_to_id: dict[str, int] = {}
        try:
            for slug, path in transcripts:
                stats.episodes_seen += 1
                try:
                    slug_to_id[slug] = await _ingest_episode(db, slug, path, stats)
                except Exception as exc:  # noqa: BLE001 -- one bad episode must not abort the run
                    message = f"{slug}: {type(exc).__name__}: {exc}"
                    stats.errors.append(message)
                    log.error("episode_ingest_failed", slug=slug, error=message)
                if stats.episodes_seen % 25 == 0:
                    await db.commit()
                    # Flush links with the chunks, so an interruption cannot leave
                    # the corpus indexed but the topic taxonomy empty.
                    stats.topics_linked = await _link_topics(db, root, slug_to_id)
                    await db.commit()
                    log.info("ingestion_progress", **stats.as_dict())

            stats.topics_linked = await _link_topics(db, root, slug_to_id)

            run.status = "success" if not stats.errors else "failed"
            run.finished_at = datetime.now(UTC)
            run.episodes_seen = stats.episodes_seen
            run.chunks_written = stats.chunks_written
            run.chunks_skipped = stats.chunks_skipped
            run.error = "\n".join(stats.errors[:20]) or None
            await db.commit()
        except Exception as exc:
            await db.rollback()
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error = f"{type(exc).__name__}: {exc}"
            db.add(run)
            await db.commit()
            raise

    log.info("ingestion_done", **stats.as_dict())
    return stats
