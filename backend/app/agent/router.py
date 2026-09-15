"""
Deterministic intent routing.

Routing is a keyword/regex prior, deliberately **not** a model call. Two reasons,
and the second is the load-bearing one:

1. A model call to decide what to do costs several seconds of prompt prefill on
   this CPU before any real work begins.
2. Model-based routing is occasionally wrong in ways the user cannot see. A regex
   that misfires is reviewable, testable, and fixable; a small model that misroutes
   is a mystery, and the misroute happens *before* the turn the user actually cares
   about.

The classifier is a pure function of the question text, so every branch is covered
by unit tests rather than by hoping.

Ordering matters: a short greeting wins over everything (it must not trigger
retrieval), essay requests win over artifact requests (an essay may contain a
table, but "write an essay" is not a request for a chart), and anything unmatched
falls through to grounded QA — the safe default, because it is the path with the
retrieval and citation safeguards.
"""

from __future__ import annotations

import re
from enum import StrEnum


class Intent(StrEnum):
    GROUNDED_QA = "grounded_qa"
    SHIP30_ESSAY = "ship30_essay"
    ARTIFACT = "artifact"
    SMALLTALK = "smalltalk"


# A greeting is only smalltalk when the message is short; "hi, how does Lovable
# think about activation?" is a real question that happens to start politely.
_SMALLTALK_MAX_CHARS = 60

_SMALLTALK = (
    r"^(hi|hey|hello|yo|howdy|sup)\b",
    r"^(thanks|thank you|cheers|nice|great|ok|okay|cool)\b",
    r"\bgood (morning|afternoon|evening)\b",
    r"\b(who are you|what can you do|what are you)\b",
)

_ESSAY = (
    r"\bship\s*30\b",
    r"\bship30\b",
    r"\bessay\b",
    r"\b(write|draft|compose) (me )?(an? )?(essay|post|article|piece|newsletter)\b",
    r"\bblog post\b",
    r"\blinkedin (post|article)\b",
    r"\b(short|long)[- ]form\b",
)

_ARTIFACT = (
    r"\b(chart|graph|plot|diagram|infographic)\b",
    r"\bvisuali[sz]e\b",
    r"\b(dashboard|mockup|wireframe)\b",
    r"\bartifact\b",
    r"\bhtml\b",
    r"\b(generate|make|build|create) (me )?(a|an) (table|page|document)\b",
)

_COMPILED = {
    Intent.SMALLTALK: tuple(re.compile(pattern) for pattern in _SMALLTALK),
    Intent.SHIP30_ESSAY: tuple(re.compile(pattern) for pattern in _ESSAY),
    Intent.ARTIFACT: tuple(re.compile(pattern) for pattern in _ARTIFACT),
}


def _matches(intent: Intent, text: str) -> bool:
    return any(pattern.search(text) for pattern in _COMPILED[intent])


def classify(question: str) -> Intent:
    """Map a question to an intent, deterministically."""
    text = question.strip().lower()
    if not text:
        return Intent.SMALLTALK

    if len(text) <= _SMALLTALK_MAX_CHARS and _matches(Intent.SMALLTALK, text):
        return Intent.SMALLTALK

    if _matches(Intent.SHIP30_ESSAY, text):
        return Intent.SHIP30_ESSAY

    if _matches(Intent.ARTIFACT, text):
        return Intent.ARTIFACT

    return Intent.GROUNDED_QA


def intent_to_str(intent: Intent) -> str:
    """Stored on the message row, so keep it stable and readable."""
    return str(intent)
