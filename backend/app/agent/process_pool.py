"""
Pi process pool.

One Pi process per active session, held open so the model keeps its in-memory
conversation context between turns. That is what makes a follow-up cheap and
coherent -- but it cannot scale, so the pool is bounded by an LRU cap and idle
eviction, and both limits are configuration rather than hard-coded.

The important property is that **eviction is not data loss**. Postgres is the
source of truth for the conversation; only the process's context is discarded.
`acquire()` reports whether the process is fresh so the caller can rehydrate the
thread by replaying recent turns (see `prompts.compose_turn`). Without that
distinction, an evicted session would silently lose the thread of the
conversation -- a bug that only appears under load, which is exactly when it is
hardest to diagnose.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from app.agent.pi_client import PiClient
from app.config import settings

log = structlog.get_logger("app.agent.pool")


@dataclass
class _Entry:
    client: PiClient
    last_used: float = field(default_factory=time.monotonic)
    turns: int = 0


class PiProcessPool:
    def __init__(self, max_sessions: int | None = None, idle_timeout: float | None = None) -> None:
        self.max_sessions = max_sessions or settings.pi_max_sessions
        self.idle_timeout = idle_timeout or float(settings.pi_idle_timeout_seconds)
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self, session_id: str, *, provider: str | None = None, model: str | None = None
    ) -> tuple[PiClient, bool]:
        """
        Return (client, is_fresh).

        `is_fresh` is True when the process was just spawned and has no memory of
        the conversation, so the caller must supply the prior turns.

        `provider`/`model` come from the session, not from global settings: a
        session can have switched provider, and spawning with the default would
        silently answer with the wrong model.
        """
        async with self._lock:
            await self._evict_idle_locked()

            entry = self._entries.get(session_id)
            if (
                entry is not None
                and entry.client.is_alive
                and entry.client.matches(provider, model)
            ):
                entry.last_used = time.monotonic()
                entry.turns += 1
                log.info("pi_reused", session_id=session_id, turns=entry.turns)
                return entry.client, False

            if entry is not None:
                # Dead, or the session changed model: the process is replaced.
                reason = "dead" if not entry.client.is_alive else "model_changed"
                log.info("pi_replacing", session_id=session_id, reason=reason)
                await self._discard_locked(session_id)

            while len(self._entries) >= self.max_sessions:
                await self._evict_lru_locked()

            client = PiClient(provider=provider, model=model, session_id=session_id)
            try:
                await client.start()
            except Exception:
                await client.stop()
                raise
            self._entries[session_id] = _Entry(client=client)
            log.info("pi_spawned", session_id=session_id, pool_size=len(self._entries))
            return client, True

    async def discard(self, session_id: str) -> None:
        """Drop a session's process, e.g. after a protocol error."""
        async with self._lock:
            await self._discard_locked(session_id)

    def peek(self, session_id: str) -> PiClient | None:
        """
        The live client for a session, if any.

        Synchronous on purpose: the dict read is atomic in a single-threaded event
        loop, and callers use it to *try* a live model switch before falling back
        to respawning. Returning a client that dies a moment later is harmless --
        the switch fails and the caller discards it.
        """
        entry = self._entries.get(session_id)
        if entry is None or not entry.client.is_alive:
            return None
        return entry.client

    async def _discard_locked(self, session_id: str) -> None:
        entry = self._entries.pop(session_id, None)
        if entry is not None:
            await entry.client.stop()

    async def _evict_idle_locked(self) -> None:
        now = time.monotonic()
        stale = [
            session_id
            for session_id, entry in self._entries.items()
            if now - entry.last_used > self.idle_timeout
        ]
        for session_id in stale:
            log.info(
                "pi_evicted_idle",
                session_id=session_id,
                idle_s=round(now - self._entries[session_id].last_used),
            )
            await self._discard_locked(session_id)

    async def _evict_lru_locked(self) -> None:
        if not self._entries:
            return
        oldest = min(self._entries.items(), key=lambda item: item[1].last_used)
        log.info("pi_evicted_lru", session_id=oldest[0], pool_size=len(self._entries))
        await self._discard_locked(oldest[0])

    async def shutdown(self) -> None:
        async with self._lock:
            for session_id in list(self._entries):
                await self._discard_locked(session_id)

    @property
    def size(self) -> int:
        return len(self._entries)


pool = PiProcessPool()
