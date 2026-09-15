"""
Server-Sent Events for a chat turn.

Typed frames rather than bare text, so the frontend never has to guess what a
payload means. The event names are the contract between the backend and the UI:

    token     a chunk of assistant text
    tool      a tool the agent called (observability into mid-turn retrieval)
    citations the validated sources for the answer
    abstained the corpus does not support an answer; suggests covered topics
    usage     provider, model, and token accounting
    artifact  a saved artifact (kind, title) that the viewer can open
    error     a structured failure (never a bare 500 mid-stream)
    done      end of turn, with the persisted message id
"""

from __future__ import annotations

import json
from typing import Any

SSE_MEDIA_TYPE = "text/event-stream"


def frame(event: str, data: dict[str, Any]) -> str:
    """One SSE frame. Compact JSON, since the payload is machine-read."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def token(text: str) -> str:
    return frame("token", {"text": text})


def tool(name: str, status: str, args: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"name": name, "status": status}
    if args is not None:
        payload["args"] = args
    return frame("tool", payload)


def citations(items: list[dict]) -> str:
    return frame("citations", {"citations": items})


def abstained(reason: str, topics: list[str]) -> str:
    return frame("abstained", {"reason": reason, "topics": topics})


def usage(provider: str, model: str, tokens: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"provider": provider, "model": model}
    if tokens:
        payload["tokens"] = tokens
    return frame("usage", payload)


def artifact(artifact_id: str, kind: str | None = None, title: str | None = None) -> str:
    payload: dict[str, Any] = {"artifact_id": artifact_id}
    if kind:
        payload["kind"] = kind
    if title:
        payload["title"] = title
    return frame("artifact", payload)


def error(code: str, message: str) -> str:
    return frame("error", {"code": code, "message": message})


def done(message_id: int) -> str:
    return frame("done", {"message_id": message_id})
