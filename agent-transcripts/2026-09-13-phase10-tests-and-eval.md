# 2026-09-13 — Phase 10: integration tests and measured metrics

**Deliverable:** 6 (agent transcripts)
**Phase:** 10 — integration tests, `make eval`, manual test plan
**Verdict:** **PASS** — acceptance criteria automated (129 tests), metrics printed as measured. The
numbers also **corrected a Phase 4 decision**, which is the point of having them.

---

## 1. Goal

Plan exit condition: *"Acceptance criteria automated; measured metrics printed."*

## 2. What was produced

- `tests/integration/test_api.py` — 17 tests over a real Postgres: session lifecycle, 404/422
  semantics, provider-switch refusal, the SSE contract, cascade deletes, artifact sanitization
  through the HTTP surface with its CSP headers, and internal-tool auth.
- `scripts/eval.py` (`make eval`) — retrieval recall, both similarity distributions, abstention
  correctness, citation coverage over persisted answers, and latency percentiles.
- `docs/manual-test-plan.md` — the ten checks that need a human or a deliberately broken
  dependency.

Full suite: **129 tests** (75 unit + 17 integration + 37 XSS), lint and format clean (65 files).

## 3. The payoff from Phase 9

The streaming contract is fully integration-tested **without a model**. That is a direct
consequence of routing smalltalk to a static reply: the turn touches no dependency at all, so
session creation → SSE frames → persistence can be asserted end to end in 12 seconds. Had every
turn required the model, this test would either not exist or be unusably slow.

## 4. Measured metrics

```
Retrieval   recall (top hit is the expected episode): 86% (6/7)
            best similarity, in corpus:      min=0.642 median=0.731 max=0.783
            best similarity, not in corpus:  min=0.472 median=0.594 max=0.649
            classes separable at floor 0.58: False
            latency p50=226ms p95=39780ms
Abstention  uncovered questions declined:     33% (2/6)
            covered questions answered:       100% (7/7)
Citation    answers carrying >=1 citation:    0% (0/6)
End-to-end  p50=7.6s p95=181.4s (n=7)
```

## 5. The finding that changed a decision

**Phase 4's prediction was right, and now it is measured.** I wrote then that the calibration was
valid only for a 2-episode corpus, and that separation would *narrow* as the corpus grew because
more chunks mean more chances for an unrelated question to find a near-match.

At **125 episodes** the out-of-corpus band rose from `max 0.555` to **`max 0.649`**, while the
in-corpus band barely moved (`min 0.610 → 0.642`). The bands now **overlap** — the worst
out-of-corpus question scores above the best in-corpus one — so no threshold separates them.

At the then-current floor of 0.58 the consequence was measurable, not theoretical: **two of six**
uncovered questions were declined. It was cheerfully accepting four questions the corpus cannot
answer. Per the plan, abstention is the *critical* risk, and a confident wrong answer is not
recoverable while a missed one is, so the floor was raised to **0.65** (recall ~86%, declines
~83%). Recorded in `docs/decisions.md` as an explicit re-derivation, including the fact that the
corpus was still growing when it was measured.

**Citation coverage is now a number: 0% of 6 persisted grounded answers carry a citation.** Phase 5
reported this as an observation; this is the same failure as a metric, which is what the brief
asked for. It is the single most important line in the README.

One number needs a caveat rather than a claim: retrieval p95 of **39.8 s** was measured *while the
full ingest was running*, competing for the same CPU-bound embedding model. The p50 (226 ms) is the
quotable figure; the p95 here measures contention, not retrieval.

## 6. What was wrong

**A test bug, caught by running it.** `test_artifact_regeneration_bumps_the_version` asserted
`raw_content` on the `/regenerate` response — but that field belongs to the *render* contract, not
the summary one. The API was right and the test was wrong. Fixed by asserting against
`GET /api/artifacts/{id}`, which also verifies the thing the test was actually about: regenerating
preserves the original text exactly.

**Formatter collisions, again.** Edits rejected against stale file contents after a format run —
three times this phase. Correct behaviour from the tool, but the lesson is to stop batching edits
immediately after formatting.

## 7. Verified

- 17 integration tests against real Postgres, including that internal tool endpoints require the
  shared secret (401 with none, 401 with a wrong one) and **do not appear in the OpenAPI schema**.
- Artifact sanitization asserted *through the API*: `removed_elements` populated, CSP header
  beginning `default-src 'none'`, `X-Content-Type-Options: nosniff`, and no `<script` in the
  rendered body.
- `make eval` prints all three required metric families.

## 8. Not verified

No new gaps. The deferred items stand: the model-quality experiment, the 360 px browser check, live
`set_model`, and a real generated essay. The floor will also need one more re-derivation once the
ingest completes, and `make calibrate` is the tool for it.
