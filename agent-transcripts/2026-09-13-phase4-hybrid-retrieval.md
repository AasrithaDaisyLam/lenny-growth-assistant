# 2026-09-13 — Phase 4: hybrid retrieval and a measured relevance floor

**Deliverable:** 6 (agent transcripts)
**Phase:** 4 — hybrid RRF, floor calibration, abstention short-circuit + topic suggestions
**Verdict:** **PASS** — retrieval measured; floor derived from data rather than assumed.
**Corpus at measurement:** 2 episodes (mini corpus), by deliberate choice — see §6.

---

## 1. Goal

Plan exit condition: *"Retrieval measured; floor derivation written into
`docs/decisions.md`."* Two things had to be true:

1. Retrieval beats either single arm (dense alone blurs names; lexical alone misses
   paraphrase).
2. `RAG_MIN_SCORE` is a *measured* threshold, not a placeholder.

## 2. What the agent produced

- `retrieval/hybrid.py`: `vector_search`, `lexical_search` (Postgres
  `websearch_to_tsquery` over the `tsv` column), `reciprocal_rank_fusion`,
  `search` (both arms + fusion), `best_similarity`, `should_abstain`,
  `topic_suggestions`.
- `tests/eval/golden_set.yaml`: 13 questions — 7 in-corpus, 6 **uncovered but
  plausible** (pricing, SEO, patenting, hiring a VP Eng, Kubernetes, sourdough).
  The plausible ones are the point; a non-sequitur is trivially rejected.
- `scripts/calibrate_floor.py` + `make calibrate`: prints both score distributions,
  the threshold sweep, retrieval latency, and whether the classes are separable.
- `/api/search` now returns `abstained`, `best_similarity`, `topics`, and per-result
  `similarity`, so it doubles as the calibration surface.
- 10 new unit tests for fusion and the gate (24 total).

## 3. Measured result

Best dense cosine similarity per question, hybrid retrieval:

| Class | min | median | max |
|---|---|---|---|
| In corpus (7) | 0.610 | 0.720 | 0.783 |
| Not in corpus (6) | 0.396 | 0.475 | 0.555 |

- Classes are **separable** on this set: `min(in) 0.610 > max(out) 0.555`.
- Best balanced threshold: **0.610** (recall 0.86, specificity 1.00).
- Retrieval latency (embed + both arms + fusion, no generation): **p50 140 ms,
  p95 208 ms**, excluding a one-off ~1.5 s cold embedding-model load.
- Recall: every in-corpus question's top hit was the expected episode (7/7).

## 4. What was wrong or risky

**The placeholder floor was materially wrong, and only measurement showed it.**
At `RAG_MIN_SCORE=0.45` the floor kept all 7 in-corpus questions but rejected only
**1 of 6** out-of-corpus questions. It was cheerfully admitting "how do I file a
patent", "how do I price a B2B SaaS product" and "how do I set up Kubernetes" as
answerable from Lenny's transcripts. No amount of reasoning about the number would
have surfaced that; running the two classes against each other did.

**My first calibration script was broken and would have crashed.** It called
`search(None, query)` — passing `None` as the database session — papered over with a
`# type: ignore`, and it declared a `latency_ms` field that was never assigned, so
the latency reporting would have silently printed zeros. Caught on review before the
first run and rewritten to open a real session and time each query. The `type:
ignore` was the tell: it existed to silence the exact error that mattered.

**A test encoded the thing it should have been decoupled from.** The gate tests
asserted literal similarities of 0.44/0.45, i.e. they hardcoded the old floor. When
the floor moved to 0.58 the test failed — not because the gate broke, but because it
was testing a constant. Rewritten to derive from `settings.rag_min_score`, which
matters specifically because the floor is *expected* to change again after full
ingestion.

**An unused import after the rewrite** (`should_abstain`) — caught by ruff, not by
me.

## 5. Correction

- Floor set to **0.58**: just below the best balanced threshold of 0.610, chosen to
  favour recall. A missed answer is recoverable ("try rephrasing"); a confident
  wrong answer is not.
- Derivation, distributions, sweep and the caveat written into `docs/decisions.md`
  as ADR-003, with ADR-005 covering why RRF is used and why the gate is asymmetric.
- Gate tests made floor-relative so recalibration is a data change, not a test edit.

The abstention gate is deliberately asymmetric, and it is tested: fusion decides
*ordering*, the floor (on dense similarity) decides coverage, and a **lexical-only**
result set is *not* treated as ungrounded — a literal term match is much harder to
produce spuriously than a dense near-match, so it is evidence of coverage.

## 6. The caveat that matters

This was measured against **two episodes**, not 269, because the ~2 hour full ingest
was deliberately sequenced *after* retrieval was settled (see the Phase 3 entry).
Separation will **narrow** as the corpus grows — more chunks mean more chances for an
unrelated question to find a near-match, so out-of-corpus scores will rise. 0.58 is
an informed starting point, not a final value, and `make calibrate` must be re-run
once the full corpus is indexed. **The measurement method is the deliverable; the
number is provisional.**

## 7. Outcome

Verified working end to end over HTTP against the indexed corpus:

```
IN-CORPUS : abstained=False best=0.746 top=elena-verna-40
OUT-CORPUS: abstained=True  best=0.506 topics=[Growth Strategy]
```

The out-of-corpus question is declined **and** the response names what the corpus
does cover, which is the graceful exit the plan asked for. Lint and format green
(35 files), **24 unit tests pass**. Full-corpus ingest started immediately after, so
the short-circuit is measured before Phase 5 wires the agent to it.
