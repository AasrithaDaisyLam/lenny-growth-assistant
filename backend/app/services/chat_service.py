"""
The chat turn pipeline.

The ordering here is the safety design, not just sequence:

1. **Retrieve first.**
2. **Short-circuit before the model is ever invoked.** If nothing clears the
   relevance floor, Pi is not started for this turn at all. That makes the most
   dangerous failure -- a confident answer invented from an uncovered corpus --
   impossible by construction rather than by asking the model nicely.
3. **Then invoke Pi**, streaming deltas straight through as SSE tokens so the
   first token reaches the browser while CPU inference is still working.
4. **Enforce citations** against the sources actually retrieved. An answer that
   resolved no citation from a turn that had sources is not a grounded answer: it
   is retried once, and declined if the retry also fails to cite.
5. **Persist** the message with its citations, retrieval trace, outcome, usage, and
   latency.

Persistence happens inside the generator, in its own database session. A
request-scoped dependency session would already be closed by the time the stream
is consumed, which is a silent way to lose the assistant's answer.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

import structlog

from app.agent import prompts
from app.agent.pi_client import PiError
from app.agent.process_pool import pool
from app.agent.router import Intent, classify
from app.db.session import AsyncSessionLocal
from app.retrieval.hybrid import best_similarity, search, should_abstain, topic_suggestions
from app.schemas import events
from app.services import outcomes, session_service, source_registry
from app.services.cite_service import (
    ValidationResult,
    is_substantive,
    needs_citation_retry,
    validate_citations,
)
from app.services.provider_service import resolve_provider

log = structlog.get_logger("app.chat")

ABSTAIN_TEXT = (
    "I could not find anything in Lenny's transcripts that answers that. "
    "Rather than guess, I'll say so: the corpus does not cover it."
)

UNCITED_TEXT = (
    "I found passages in the transcripts that look relevant, but I could not ground an "
    "answer in them with citations. Rather than hand you an unsourced answer, I'll say so."
)


def _artifact_id_from_result(result: object) -> str | None:
    """
    Pull the artifact id out of a `save_artifact` tool result.

    The extension returns it as JSON text, which is what lets the tool call become
    an `artifact` SSE event without a second round trip to look it up.
    """
    try:
        content = (result or {}).get("content") or []  # type: ignore[union-attr]
        for block in content:
            if block.get("type") == "text" and block.get("text"):
                data = json.loads(block["text"])
                if isinstance(data, dict) and data.get("artifact_id"):
                    return str(data["artifact_id"])
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return None
    return None


def _turn_plans(
    intent: Intent,
    question: str,
    chunks: list,
    prior: list[tuple[str, str]],
    fresh: bool,
) -> list[tuple[str | None, str]]:
    """
    Turn an intent into the sequence of prompts to run.

    The essay is generated **one section at a time**, which is what actually
    enforces its structural contract. Asking a 3B CPU model for a 1,250-word essay
    with four required sections gets whichever sections it feels like writing;
    asking it four times, once per section, guarantees all four exist and in order,
    because the structure comes from this list rather than from the model's
    cooperation.
    """
    if intent is Intent.SHIP30_ESSAY:
        skill = prompts.load_skill()
        return [
            (
                title,
                prompts.essay_section_prompt(
                    skill=skill,
                    section_title=title,
                    guidance=guidance,
                    question=question,
                    chunks=chunks,
                ),
            )
            for title, guidance in prompts.ESSAY_SECTIONS
        ]

    if intent is Intent.ARTIFACT:
        # Still grounded in the retrieved sources; the `save_artifact` tool does the
        # rendering, and a hostile artifact is sanitized on the way in.
        return [(None, prompts.artifact_prompt(question, chunks))]

    return [(None, prompts.compose_turn(question, chunks, prior, fresh))]


async def stream_turn(session_id: uuid.UUID, question: str) -> AsyncIterator[str]:
    """Yield SSE frames for one turn, persisting the user and assistant messages."""
    async with AsyncSessionLocal() as db:
        session = await session_service.get_session(db, session_id)

        # Read history *before* appending this question, so the rehydration
        # preamble cannot include the question twice.
        prior = await session_service.recent_turns(db, session_id)
        await session_service.add_message(db, session_id, "user", question)

        started = time.perf_counter()

        # --- Route (deterministic, no model call) -----------------------------
        intent = classify(question)

        if intent is Intent.SMALLTALK:
            # The cheapest path, and deliberately independent of every dependency:
            # a greeting should not fail because Ollama is down, retrieval is
            # unavailable, or the corpus does not cover small talk.
            assistant = await session_service.add_message(
                db,
                session_id,
                "assistant",
                prompts.SMALLTALK_REPLY,
                intent=str(intent),
                citations=[],
                retrieval_trace={"intent": str(intent), "outcome": outcomes.SMALLTALK},
                provider=session.provider,
                model=session.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            log.info("turn_smalltalk", session_id=str(session_id))
            yield events.usage(session.provider, session.model)
            yield events.done(assistant.id)
            return

        # --- Provider preflight, with fallback --------------------------------
        # Before retrieval, not after: retrieval embeds the query, so if the chat
        # provider is down there is no point doing work that will also fail. An
        # unreachable provider degrades with a named cause instead of an opaque
        # error from inside the model call.
        resolution = await resolve_provider(session.provider)
        if not resolution.ok:
            reason = resolution.reason_summary()
            latency_ms = int((time.perf_counter() - started) * 1000)
            assistant = await session_service.add_message(
                db,
                session_id,
                "assistant",
                f"No model provider is reachable right now, so I can't answer this. ({reason})",
                intent=str(intent),
                citations=[],
                retrieval_trace={
                    "abstained": False,
                    "degraded": True,
                    "outcome": outcomes.DEGRADED,
                    "reason": reason,
                    "attempted": resolution.attempted,
                },
                provider=session.provider,
                model=session.model,
                latency_ms=latency_ms,
            )
            log.error("turn_degraded_no_provider", session_id=str(session_id), reason=reason)
            yield events.usage(session.provider, session.model)
            yield events.error("provider_unavailable", reason)
            yield events.done(assistant.id)
            return

        # --- Retrieve ---------------------------------------------------------
        # A retrieval failure is a *refusal*, not a fallback to ungrounded prose:
        # without sources there is nothing to ground an answer in, so the honest
        # outcome is to say so.
        try:
            chunks = await search(db, question)
        except Exception as exc:  # noqa: BLE001 -- embeddings/DB are external
            reason = f"{type(exc).__name__}: {exc}"
            latency_ms = int((time.perf_counter() - started) * 1000)
            assistant = await session_service.add_message(
                db,
                session_id,
                "assistant",
                "I can't search the transcripts right now, so I won't guess. "
                "Retrieval is unavailable.",
                intent=str(intent),
                citations=[],
                retrieval_trace={
                    "abstained": False,
                    "degraded": True,
                    "outcome": outcomes.DEGRADED,
                    "reason": reason,
                },
                provider=session.provider,
                model=session.model,
                latency_ms=latency_ms,
            )
            log.error("turn_degraded_retrieval", session_id=str(session_id), error=reason)
            yield events.usage(session.provider, session.model)
            yield events.error("retrieval_unavailable", reason)
            yield events.done(assistant.id)
            return

        top_similarity = best_similarity(chunks)

        # --- Short-circuit: the agent is never invoked ------------------------
        if should_abstain(chunks):
            topics = await topic_suggestions(db)
            latency_ms = int((time.perf_counter() - started) * 1000)
            assistant = await session_service.add_message(
                db,
                session_id,
                "assistant",
                ABSTAIN_TEXT,
                intent=str(intent),
                citations=[],
                retrieval_trace={
                    "abstained": True,
                    "reason": "below_relevance_floor",
                    "outcome": outcomes.ABSTAINED,
                    "best_similarity": top_similarity,
                    "candidates": len(chunks),
                },
                provider=session.provider,
                model=session.model,
                latency_ms=latency_ms,
            )
            log.info(
                "turn_abstained",
                session_id=str(session_id),
                best_similarity=top_similarity,
                candidates=len(chunks),
            )
            yield events.usage(session.provider, session.model)
            yield events.abstained("below_relevance_floor", topics)
            yield events.done(assistant.id)
            return

        if resolution.provider != session.provider:
            # Make the session reflect what actually answered, so the displayed
            # active provider stays truthful rather than aspirational.
            session = await session_service.update_session(
                db, session_id, provider=resolution.provider
            )

        # --- Invoke the agent -------------------------------------------------
        client, fresh = await pool.acquire(
            str(session_id), provider=resolution.provider, model=resolution.model
        )
        plans = _turn_plans(intent, question, chunks, prior, fresh)
        yield events.usage(resolution.provider, resolution.model)

        # The turn's citable sources: seeded with what retrieval injected, then
        # extended by the internal tool endpoints if the agent fetches more. That
        # is what lets a tool-returned passage be cited by an [S#] that resolves.
        turn_sources = source_registry.open_turn(str(session_id), chunks)

        async def _events():
            """
            Flatten one-or-more prompts into a single event stream.

            Section-wise essay generation means several prompts in one turn, and a
            synthetic heading event is emitted between them so the assembled text
            has section titles even though no single model call produced them.
            """
            for heading, prompt_text in plans:
                if heading:
                    yield {"type": "_section_heading", "title": heading}
                async for event in client.prompt(prompt_text):
                    yield event

        collected: list[str] = []
        token_usage: dict = {}
        failure: str | None = None
        provider_error: str | None = None
        result: ValidationResult | None = None
        citation_retried = False

        try:
            try:
                async for event in _events():
                    kind = event.get("type")

                    if kind == "_section_heading":
                        banner = f"\n\n## {event['title']}\n\n"
                        collected.append(banner)
                        yield events.token(banner)
                        continue

                    if kind == "message_update":
                        if isinstance(event.get("usage"), dict):
                            token_usage = event["usage"]
                        delta = (event.get("assistantMessageEvent") or {}).get("delta")
                        if (event.get("assistantMessageEvent") or {}).get(
                            "type"
                        ) == "text_delta" and delta:
                            collected.append(delta)
                            yield events.token(delta)

                    elif kind in ("message_start", "message_end"):
                        # A provider failure arrives as a *message* with stopReason
                        # "error", not as a raised exception. Left unhandled it becomes
                        # an empty answer with no explanation, which is the worst
                        # possible presentation of a broken provider.
                        message = event.get("message") or {}
                        if message.get("stopReason") == "error":
                            provider_error = str(message.get("errorMessage") or "provider error")

                    elif kind == "tool_execution_start":
                        # Surfaced in the UI as retrieval observability, not hidden.
                        yield events.tool(
                            str(event.get("toolName") or "unknown"), "start", event.get("args")
                        )

                    elif kind == "tool_execution_end":
                        yield events.tool(str(event.get("toolName") or "unknown"), "end")
                        if str(event.get("toolName")) == "save_artifact":
                            artifact_id = _artifact_id_from_result(event.get("result"))
                            if artifact_id:
                                yield events.artifact(artifact_id)
            except PiError as exc:
                failure = f"{type(exc).__name__}: {exc}"
                log.error("turn_agent_failed", session_id=str(session_id), error=failure)
                # A failed process must not be handed to the next turn.
                await pool.discard(str(session_id))
                yield events.error("agent_failed", str(exc))

            raw_text = "".join(collected)
            result = validate_citations(raw_text, turn_sources.chunks())

            # --- Citation gate ------------------------------------------------
            # Sources were handed to the model and it cited none of them, so this is
            # not yet a grounded answer. One stricter attempt is made; if that also
            # fails, the turn is declined below rather than presented as sourced.
            if (
                intent is Intent.GROUNDED_QA
                and not failure
                and not provider_error
                and needs_citation_retry(result, turn_sources.chunks())
            ):
                citation_retried = True
                log.warning(
                    "citations_missing_retrying",
                    session_id=str(session_id),
                    chars=len(raw_text),
                )
                retry_parts: list[str] = []
                try:
                    async for event in client.prompt(
                        prompts.citation_retry_prompt(question, turn_sources.items(), raw_text)
                    ):
                        kind = event.get("type")
                        if kind == "message_update":
                            if isinstance(event.get("usage"), dict):
                                token_usage = event["usage"]
                            delta = (event.get("assistantMessageEvent") or {}).get("delta")
                            if (event.get("assistantMessageEvent") or {}).get(
                                "type"
                            ) == "text_delta" and delta:
                                retry_parts.append(delta)
                        elif kind in ("message_start", "message_end"):
                            message = event.get("message") or {}
                            if message.get("stopReason") == "error":
                                provider_error = str(
                                    message.get("errorMessage") or "provider error"
                                )
                except PiError as exc:
                    failure = f"{type(exc).__name__}: {exc}"
                    log.error("citation_retry_failed", session_id=str(session_id), error=failure)
                    await pool.discard(str(session_id))

                if not failure:
                    retry_result = validate_citations("".join(retry_parts), turn_sources.chunks())
                    if retry_result.citations:
                        result = retry_result
                    else:
                        log.warning("citations_still_missing", session_id=str(session_id))
        finally:
            all_chunks = turn_sources.chunks()
            source_registry.close_turn(str(session_id))

        if result is None:  # pragma: no cover - the block above always assigns it
            result = validate_citations("", all_chunks)
        if provider_error:
            log.error("turn_provider_error", session_id=str(session_id), error=provider_error)
        if result.unknown_markers:
            log.warning(
                "citations_unresolved",
                session_id=str(session_id),
                markers=result.unknown_markers,
            )

        # A decline is the honest outcome when sources existed but none of them
        # could be cited. Genuine failures keep their own outcome so the evaluation
        # does not read them as citation gaps.
        uncited = (
            intent is Intent.GROUNDED_QA
            and bool(all_chunks)
            and not result.citations
            and not failure
            and not provider_error
            and is_substantive(result.text)
        )

        latency_ms = int((time.perf_counter() - started) * 1000)

        if not result.text:
            final_text = failure or provider_error or "The agent returned no answer."
            outcome = outcomes.FAILED
        elif uncited:
            final_text = UNCITED_TEXT
            outcome = outcomes.UNCITED
        elif failure or provider_error:
            final_text = result.text
            outcome = outcomes.FAILED
        else:
            final_text = result.text
            outcome = outcomes.ANSWERED

        if uncited:
            yield events.abstained("uncited_answer", await topic_suggestions(db))
        else:
            yield events.citations(result.citations)
        if provider_error:
            yield events.error("provider_error", provider_error)

        assistant = await session_service.add_message(
            db,
            session_id,
            "assistant",
            final_text,
            intent=str(intent),
            citations=result.citations,
            retrieval_trace={
                "abstained": False,
                "best_similarity": top_similarity,
                "candidates": len(chunks),
                "sources": len(chunks),
                "tool_sources": len(all_chunks) - len(chunks),
                "unknown_markers": result.unknown_markers,
                "citation_count": len(result.citations),
                "citation_retried": citation_retried,
                "outcome": outcome,
                "error": failure,
                "provider_error": provider_error,
            },
            provider=session.provider,
            model=session.model,
            token_usage=token_usage,
            latency_ms=latency_ms,
        )

        log.info(
            "turn_completed",
            session_id=str(session_id),
            citations=len(result.citations),
            outcome=outcome,
            chars=len(final_text),
            latency_ms=latency_ms,
            failed=bool(failure),
        )
        yield events.done(assistant.id)
