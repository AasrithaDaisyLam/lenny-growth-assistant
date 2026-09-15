"""
Terminal outcome of one assistant turn.

`intent` says what was asked for; this says what actually happened. They are
separate because a grounded question can end in a cited answer, an explicit
decline, a provider failure, or a degradation -- and the evaluation must be able
to count the answers without counting the failures as citation gaps.

Stored in `retrieval_trace['outcome']` rather than in a column: the trace is the
existing home for per-turn provenance, so this needs no schema change.
"""

from __future__ import annotations

# A grounded answer, produced from the retrieved sources.
ANSWERED = "answered"
# Sources existed but no answer could be grounded in them with citations.
UNCITED = "uncited"
# The relevance floor was not cleared, so the agent was never invoked.
ABSTAINED = "abstained"
# A dependency failed in a way that is reported rather than guessed around.
DEGRADED = "degraded"
# The agent produced nothing usable: a protocol error, a timeout, or empty output.
FAILED = "failed"
# Smalltalk, which touches no dependency and makes no claim about the corpus.
SMALLTALK = "smalltalk"
