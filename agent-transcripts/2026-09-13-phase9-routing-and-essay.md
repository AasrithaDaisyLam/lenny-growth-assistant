# 2026-09-13 — Phase 9: intent routing and the Ship 30 essay skill

**Deliverable:** 6 (agent transcripts)
**Phase:** 9 — `SKILL.md` as data, deterministic router, section-wise essay generation
**Verdict:** **PASS on routing and on the structural contract.** The essay's four sections are
enforced by construction and asserted in tests; no live essay has been generated (deferred with
the model experiment).

---

## 1. Goal

Plan exit condition: *"Essay meets its structural contract; all intents routable."*

## 2. What was produced

- `skills/ship30-essay/SKILL.md` — the house style as Markdown: structure, voice, formatting,
  grounding rules, anti-patterns. Mounted read-only at `/app/skills` and read **per request**, so
  editing it changes output with no code change and no restart.
- `app/agent/router.py` — `Intent` and a deterministic `classify()`: smalltalk, ship30_essay,
  artifact, grounded_qa.
- `prompts.py` — `load_skill`, `ESSAY_SECTIONS`, `essay_section_prompt`, `artifact_prompt`,
  `SMALLTALK_REPLY`.
- `chat_service.py` — routing, a dependency-free smalltalk path, and multi-prompt turns.
- Tests: 12 parametrized routing cases plus the essay contract. **75 unit tests** (from 49), 37 XSS,
  lint and format clean (63 files).

## 3. The design point that matters

**The structural contract is enforced by the pipeline, not requested from the model.**

Asking `llama3.2:3b` for a four-section essay gets whichever sections it feels like writing — the
same failure already observed with the citation contract. So the essay is generated **one section
at a time**, one prompt per section, and the sections are joined by code. Hook, Context, Main
points and Takeaway therefore exist, in order, regardless of how cooperative the model is. The
model's job shrinks to "write this one section well", which is a job a 3B model can actually do.

This is the same principle applied three times now: the tool surface is closed by *flags*, not
instructions; uncovered questions never reach the model because of a *short-circuit*, not a plea to
abstain; and now essay structure comes from a *list*, not from the model's compliance.

## 4. Why routing is a regex

A model call to decide what to do would cost seconds of prompt prefill on this CPU before any real
work — and, worse, would fail invisibly. A regex that misfires is reviewable, testable, and
fixable. Two details carry behaviour:

- **Ordering.** Smalltalk is checked first, so a greeting never triggers retrieval; essay beats
  artifact, because "write an essay with a table" is not a request for a chart; anything unmatched
  falls through to grounded QA, the only path with retrieval and citation validation.
- **A length guard.** Smalltalk patterns only apply to messages under 60 characters, so
  *"hi, how does the growth team at Lovable think about activation and retention?"* is treated as
  the question it is, rather than swallowed by the greeting rule and answered without sources.

## 5. Smalltalk is dependency-free

The greeting path touches **nothing**: no retrieval, no provider preflight, no model. A greeting
cannot fail because Ollama is down, embeddings are unavailable, or the corpus does not cover small
talk. It is the only turn that is guaranteed to work, which seemed like the right property for the
path a new user hits first.

## 6. What was wrong

**Formatter collisions, repeatedly.** Two `prompts.py` edits were rejected because the file had been
rewritten by `ruff format` since the last read. Recovered by re-reading, but the pattern is worth
naming: an edit tool that refuses stale content is correct, and it cost three round trips this
phase because I batched edits after a format run.

**A large edit avoided rather than attempted.** Multi-prompt turns initially implied re-indenting
the whole event loop inside a `for` — a big, fragile, cosmetic diff. Instead the prompts are
flattened into a single event stream by a small local generator, with a synthetic
`_section_heading` event between sections. The heading is emitted by the pipeline, so section
titles appear in the transcript even though no single model call produced them. One line changed
at the call site instead of fifty.

## 7. Verified

- **All intents routable**, with the ordering and the length guard asserted explicitly
  (12 parametrized cases, plus "essay beats artifact", "unmatched defaults to grounded QA",
  "classification is deterministic over 20 runs", and stable serialized values for the `intent`
  column).
- **The essay contract**: exactly the four contracted titles in order, every section carrying
  guidance, and the section prompt asking for *only* that section while including both the skill
  text and the numbered sources.
- `load_skill` reads the mounted Markdown and **degrades gracefully** when a skill is missing, so a
  bad path cannot take a turn down.

## 8. Not verified

**No essay has been generated.** The contract is enforced structurally and asserted in tests, but a
real four-section essay has not been produced, read, or judged. Section-wise generation also costs
four sequential model calls, so on this CPU an essay is minutes of inference — which is the honest
reason it belongs in final verification alongside the model experiment.

**A limitation worth recording:** the essay path does not replay conversation history for a fresh
process. Section prompts carry the question and the sources, but not prior turns, so an essay
request following an evicted session loses thread continuity. The grounded-QA path rehydrates;
this one does not, and it should, if essays ever become a follow-up-heavy flow.
