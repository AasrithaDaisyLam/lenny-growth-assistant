"""Async Ollama client: embeddings, model inventory, and readiness."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger("app.ollama")


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


class OllamaClient:
    """
    Embeddings always come from Ollama, regardless of which provider generates
    prose. That keeps ingestion and retrieval independent of the model toggle --
    switching the chat model must never require re-embedding the corpus.
    """

    def __init__(
        self,
        base_url: str | None = None,
        embed_model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.embed_model = embed_model or settings.ollama_embed_model
        self.timeout = timeout

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)

    # --- Readiness -----------------------------------------------------------

    async def version(self) -> str | None:
        try:
            async with await self._client() as client:
                resp = await client.get("/api/version")
                resp.raise_for_status()
                return resp.json().get("version")
        except Exception as exc:  # noqa: BLE001 -- readiness must never raise
            log.warning("ollama_unreachable", error=f"{type(exc).__name__}: {exc}")
            return None

    async def list_models(self) -> list[str]:
        try:
            async with await self._client() as client:
                resp = await client.get("/api/tags")
                resp.raise_for_status()
                return [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception as exc:  # noqa: BLE001
            log.warning("ollama_list_models_failed", error=str(exc))
            return []

    async def has_model(self, name: str) -> bool:
        """Ollama reports 'model' or 'model:tag'; accept either spelling."""
        models = await self.list_models()
        if not models:
            return False
        base = name.split(":")[0]
        return any(m == name or m.split(":")[0] == base for m in models)

    # --- Embeddings ----------------------------------------------------------

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """
        Embed a batch. `/api/embed` accepts an array input, so ingestion can push
        batches rather than one HTTP round trip per chunk.
        """
        if not texts:
            return []

        payload: dict[str, Any] = {"model": model or self.embed_model, "input": texts}
        try:
            async with await self._client() as client:
                resp = await client.post("/api/embed", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise OllamaError(f"embedding request failed: {type(exc).__name__}: {exc}") from exc

        vectors = data.get("embeddings") or []
        if len(vectors) != len(texts):
            raise OllamaError(f"expected {len(texts)} embeddings, got {len(vectors)}")
        for vec in vectors:
            if len(vec) != settings.embedding_dim:
                raise OllamaError(
                    f"embedding dimension {len(vec)} != configured {settings.embedding_dim}. "
                    "Changing EMBEDDING_DIM requires re-indexing the corpus."
                )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]

    async def embed_batched(
        self, texts: list[str], batch_size: int | None = None
    ) -> list[list[float]]:
        """Embed many texts in sequential batches; used by ingestion."""
        size = batch_size or settings.embed_batch_size
        out: list[list[float]] = []
        for i in range(0, len(texts), size):
            batch = texts[i : i + size]
            out.extend(await self.embed(batch))
            await asyncio.sleep(0)  # yield so the event loop stays responsive
        return out


ollama = OllamaClient()
