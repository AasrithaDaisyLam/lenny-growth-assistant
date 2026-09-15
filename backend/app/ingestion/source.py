"""
Transcript sources.

The corpus can come from the upstream GitHub repo (default, so a clean clone
ingests with no extra assets) or from a local directory. Both adapters hand the
pipeline the same thing -- a root containing `episodes/` and `index/` -- so
nothing downstream needs to know which was used.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Protocol

import httpx
import structlog

from app.config import settings

log = structlog.get_logger("app.ingestion.source")

_TARBALL_URL = "https://codeload.github.com/{repo}/tar.gz/refs/heads/main"
_READY_MARKER = ".corpus-ready"


class SourceError(RuntimeError):
    """Raised when the corpus cannot be fetched or located."""


class TranscriptSource(Protocol):
    def prepare(self, force: bool = False) -> Path:
        """Return a directory containing `episodes/` and (usually) `index/`."""
        ...


def _find_corpus_root(root: Path) -> Path:
    """The tarball extracts to a single top-level directory; find the one with episodes/."""
    if (root / "episodes").is_dir():
        return root
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "episodes").is_dir():
            return child
    raise SourceError(f"no 'episodes/' directory found under {root}")


class RepoSource:
    """
    Fetch the upstream corpus as a tarball into a cache directory.

    The tarball is kept on a volume so re-ingestion is a no-op on the network:
    the cache is only re-downloaded with `--force`.
    """

    def __init__(self, repo: str | None = None, cache_dir: str | None = None) -> None:
        self.repo = repo or settings.transcript_repo
        self.cache_dir = Path(cache_dir or settings.transcript_cache_dir)
        self.root = self.cache_dir / "corpus"

    def prepare(self, force: bool = False) -> Path:
        if (self.root / _READY_MARKER).exists() and not force:
            log.info("corpus_cached", root=str(self.root))
            return _find_corpus_root(self.root)

        url = _TARBALL_URL.format(repo=self.repo)
        log.info("corpus_download_start", url=url)
        try:
            with httpx.Client(timeout=180.0, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                payload = response.content
        except httpx.HTTPError as exc:
            raise SourceError(f"could not download corpus from {url}: {exc}") from exc

        log.info("corpus_download_done", bytes=len(payload))
        self.root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            # filter="data" refuses absolute paths, `..` traversal, and links that
            # escape the destination -- the tarball is remote input.
            archive.extractall(self.root, filter="data")

        (self.root / _READY_MARKER).write_text(url, encoding="utf-8")
        return _find_corpus_root(self.root)


class LocalSource:
    """Read the corpus from a directory already on disk (`TRANSCRIPT_SOURCE=local`)."""

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.transcript_local_path or "")

    def prepare(self, force: bool = False) -> Path:
        if not self.path.exists():
            raise SourceError(f"TRANSCRIPT_LOCAL_PATH does not exist: {self.path}")
        log.info("corpus_local", root=str(self.path))
        return _find_corpus_root(self.path)


def get_source() -> TranscriptSource:
    """Select the adapter from configuration."""
    if settings.transcript_source == "local":
        return LocalSource()
    return RepoSource()
