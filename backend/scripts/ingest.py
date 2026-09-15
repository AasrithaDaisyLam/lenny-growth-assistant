"""
Ingestion entrypoint.

    python -m scripts.ingest            # incremental; embeds only what is new
    python -m scripts.ingest --force    # re-download the corpus and re-index
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.logging import configure_logging
from app.db.session import dispose_engine, init_db
from app.ingestion.pipeline import ingest


async def _run(force: bool, limit: int | None):
    # This process is separate from the API, so it creates any missing tables
    # itself rather than assuming the app has already started once.
    await init_db()
    try:
        return await ingest(force=force, limit=limit)
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest the Lenny's Podcast transcript corpus.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download the corpus tarball instead of using the cached copy",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="index at most N episodes (fast path; a later unlimited run tops it up)",
    )
    args = parser.parse_args()

    configure_logging()
    stats = asyncio.run(_run(args.force, args.limit))

    print()
    print("Ingestion complete")
    print(f"  episodes seen     {stats.episodes_seen}")
    print(f"  episodes indexed  {stats.episodes_written}")
    print(f"  episodes skipped  {stats.episodes_skipped}")
    print(f"  chunks written    {stats.chunks_written}")
    print(f"  chunks skipped    {stats.chunks_skipped}")
    print(f"  chunks deleted    {stats.chunks_deleted}")
    print(f"  topic links       {stats.topics_linked}")

    if stats.errors:
        print(f"  errors            {len(stats.errors)}")
        for message in stats.errors[:10]:
            print(f"    - {message}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
