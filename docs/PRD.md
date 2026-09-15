# PRD — The Lenny Growth Assistant

## 1. User and problem

**User.** A product or growth practitioner who wants an answer drawn from
practitioner experience rather than generic advice — someone who already reads
Lenny's Podcast and wants the transcript's actual claims, attributable to the
guest who made them.

**Problem.** Lenny's Podcast is 303 episodes of high-value growth experience, and
it is nearly unusable as a reference. You cannot ask it a question, you cannot tell
which episode covered a topic, and searching it means skimming hour-long
transcripts. An LLM with no grounding will answer the same question confidently
from general knowledge — which is worse, because the answer *sounds* like Lenny's
Podcast but is not in it, and there is no way for the reader to tell.

**The failure that matters most** is not a refusal. It is a confident, plausible,
unsourced answer attributed to a podcast that never said it. Every design decision
below is ordered around preventing that specific outcome.

---

## 2. What the product does

Ask a growth question; get an answer built **only** from what the transcripts say,
with an inline `[S#]` citation behind every claim that opens the episode, guest and
speaker it came from — or an explicit decline when the corpus does not cover it.

Four capabilities:

1. **Grounded Q&A with citations.** The core loop.
2. **Explicit, useful abstention.** When nothing clears the relevance floor the
   model is never invoked, and the reply names topics the corpus *does* cover
   instead of dead-ending.
3. **Provider toggle.** Local Ollama (offline, default) or Anthropic Claude,
   switchable per session with no restart, with unavailable providers shown
   disabled *with the reason*.
4. **Artifacts and a Ship-30 essay.** A grounded answer can also be rendered as a
   sandboxed artifact, or expanded into a structured essay in the house style.

---

## 3. Success metrics

The brief requires at least one measurable metric. Three are reported, and they
are printed by `make eval` **as measured**, not as targeted — the harness exists so
a reviewer can re-measure after changing one environment variable.

| Metric | Measured | Why it is the metric |
|---|---|---|
| **Citation coverage** | 3/3 grounded answers cited (controlled sample) | Primary. An uncited answer is not a grounded answer. |
| **Abstention correctness** | Uncovered declined 100% (6/6) at `RAG_MIN_SCORE=0.65` | Scored separately, because it is the dangerous failure |
| **Retrieval recall** | 86% (6/7) — top hit is the expected episode | Confirms the retriever, not just the generator |
| Retrieval latency | p50 ≈ 130–210 ms, p95 ≈ 250 ms | CPU-bound embedding, warm model |

A metric that would have been easy to game — counting every persisted assistant
message as a citation opportunity — is deliberately **not** used. Citation coverage
counts only genuine grounded answers: a turn that abstained, failed, timed out,
degraded, or was not a grounded question could not carry a citation, and counting
it would report the system's own refusals as citation gaps.

---

## 4. Assumptions

Each names what breaks if it is wrong. The first was the highest-impact and was
retired by a spike before anything was built on it.

| # | Assumption | If wrong | Status |
|---|---|---|---|
| A1 | Pi's RPC mode drives Ollama reliably for a tool-using turn | Fall back to the Claude Agent SDK or a plain Anthropic-SDK loop | **Verified** in a Phase 1 spike |
| A2 | The corpus is the public `ChatPRD/lennys-podcast-transcripts` repo, 303 episodes | `TRANSCRIPT_SOURCE` swaps the adapter — an interface, not a rewrite | Held |
| A3 | Upstream transcripts carry speaker labels | Paragraph-based chunking; attribution quality drops, architecture unchanged | Held |
| A4 | Single user, so one Pi process per session scales | Documented limitation; the pool caps and evicts, and a queue is the fix | Accepted |
| A5 | A 3–4B CPU model is enough to demo | `OLLAMA_MODEL` is one env var; README carries an upgrade ladder | **Partially false** — see §7 |
| A6 | The corpus fits comfortably in one Postgres with pgvector | Justifies pgvector over a dedicated vector store; above ~1M chunks HNSW needs tuning | Held |
| A7 | Internal, trusted, single tenant | Largest single re-scope; `sessions.user_metadata` already exists so auth is middleware, not a migration | Accepted |

---

## 5. Scope

### In scope

- Full 303-episode ingestion, content-hash idempotent and incremental.
- Hybrid retrieval (pgvector cosine + Postgres full-text, fused with RRF).
- A measured relevance floor with the derivation recorded, not guessed.
- Grounded Q&A with citation validation, a retry-then-decline gate, and an
  explicit "not covered" response with topic suggestions.
- Session persistence with citations, retrieval trace, usage and latency.
- Provider toggle with no restart; graceful degradation when a provider is down.
- Sandboxed artifact rendering, with sanitization visible to the user.
- Ship-30 essay generation in the house style.
- Automated tests (unit + integration + XSS) and a manual test plan.
- Documentation an evaluator can reproduce the build from.

### Out of scope, deliberately

Alembic migrations; an eval framework or release gate; an automated
fault-injection suite; a comprehensive XSS payload corpus; auth and multi-tenancy;
audio ingestion and diarization; fine-tuning; agentic web browsing; cross-encoder
reranking; a third provider; Supabase/Railway; artifact collaborative editing or
Kubernetes. Rationale for each is in `docs/architecture.md` §14 and the plan's cut
list — the omissions are judgement, not oversight.

---

## 6. User flows

### Flow 1 — a covered question

1. User types *"How does the growth team at Lovable work on activation?"*
2. Hybrid retrieval returns candidate chunks and the top dense similarity clears
   the floor.
3. Pi streams an answer; tokens arrive progressively, so the first text lands while
   CPU inference is still working.
4. Every substantive claim carries `[S#]`. Clicking a chip opens the source panel:
   episode, guest, speaker, YouTube link.
5. If the first attempt omits citations entirely, the turn is retried once; if the
   retry still cites nothing, the turn is **declined** rather than shown as sourced.

### Flow 2 — an uncovered question

1. User asks *"How do I file a patent for a software algorithm?"*
2. Nothing clears the relevance floor.
3. **Pi is never invoked.** The reply declines and lists topics the corpus does
   cover. It returns in well under a second, because no model ran.
4. The trace records `abstained` with `best_similarity` below `RAG_MIN_SCORE`.

### Flow 3 — provider switch

1. The UI lists Ollama (available, with its real model list) and Anthropic
   (*unavailable — `ANTHROPIC_API_KEY is not set`*), never hidden.
2. With a key configured, selecting Anthropic on a live session issues `set_model`
   over RPC: the warm conversation survives, and no restart is needed.
3. If a process is not live, the next turn spawns with the new model and rehydrates
   from Postgres.

### Flow 4 — a hostile artifact

1. An artifact (or a prompt-injected transcript) contains
   `<script src="https://evil.example/x.js">`.
2. The sanitizer strips it, and the removal is reported in *Removed before display*.
3. The preview renders in an iframe with `sandbox="allow-scripts"` and **no**
   `allow-same-origin`, under a `default-src 'none'` CSP.

---

## 7. Risks

Ordered by expected damage, with the mitigation that is actually implemented.

| Risk | Severity | Mitigation |
|---|---|---|
| **Hallucination** | Critical | Layered and *ordered*: retrieval-constrained prompting → empty-retrieval short-circuit so the model is never invoked → citation validation → explicit "not in the provided sources". Not a prompt instruction; a construction. |
| Unsafe artifact rendering | High | Four layers; untrusted even from Claude. The `allow-scripts` **without** `allow-same-origin` combination is the one that is easy to get wrong, and it is asserted. |
| **Local model quality** | High, **realised** | See below — stated plainly rather than hidden behind a cherry-picked demo. |
| Pi RPC integration | High | Phase 1 gate with a named fallback; stderr drained to avoid pipe deadlock; LF-only JSONL framing. |
| Latency | Medium | SSE streaming, capped `RAG_TOP_K`, content-hash embedding cache, per-request accounting. |
| Data leakage | Medium | Local-first default, active provider visible on every session, `.env` gitignored, no secret ever logged. |
| Prompt injection via transcripts | Low | No filesystem, shell, or network in the tool surface; context fenced as untrusted data; the sanitizer catches the downstream effect. |
| Corpus staleness | Low | Content-hash incremental ingestion; `source_updated_at` per episode. |

### The realised risk, stated honestly

On this hardware `llama3.2:3b` produced a substantive but **citation-free** first
attempt on **4 of 4** grounded turns in the reference sample. This is A5 partially
failing, and it is the reason the citation gate exists rather than being a nicety:
the gate caught every one — three were rewritten with citations on the retry, and
one question that could not be grounded on either attempt was **declined** instead
of being presented as sourced.

The documented lever is `OLLAMA_MODEL` (`qwen3:4b` → `qwen2.5:7b`), and `make eval`
re-measures. The alternative — tuning the metric, or showing only the retry that
worked — would have produced a better-looking demo and a worse system.

---

## 8. Acceptance criteria

Each criterion is automated by `make test`, or is a step in
`docs/manual-test-plan.md` where it genuinely needs a human, a broken dependency,
or a browser.

| # | Criterion | Verified by |
|---|---|---|
| 1 | Stack starts from a clean clone with one command pair | Manual plan §1 |
| 2 | A grounded answer streams progressively and cites real episodes | Manual plan §2; SSE contract in integration tests |
| 3 | An uncovered question is declined **without invoking the model**, with alternatives | Manual plan §3; `test_lexical_only_results_are_not_treated_as_ungrounded`, fusion floor tests |
| 4 | A follow-up reuses context without a respawn | Manual plan §4 |
| 5 | Ollama down degrades with a named cause — no 500, no hang | Manual plan §5; provider fallback unit tests |
| 6 | Database down is reported, not fatal | Manual plan §6; readiness integration test |
| 7 | Streaming is not buffered by the proxy | Manual plan §7 |
| 8 | A hostile artifact is stripped **visibly** and sandboxed | Manual plan §8; XSS suite; artifact integration tests |
| 9 | Provider switching needs no restart | Manual plan §9; `test_switching_to_an_unconfigured_provider_is_refused_with_a_reason` |
| 10 | Metrics print as measured | Manual plan §10; `make eval` |
| 11 | An uncited grounded answer is never shown as sourced | `test_gate_triggers_on_a_substantive_uncited_answer_when_sources_exist` and the citation suite |
| 12 | Every dependency failure names its cause in `/api/health/ready` | `test_readiness_reports_each_dependency` |

---

## 9. Implementation plan

Delivered in eleven phases, each leaving the repo runnable, with
`agent-transcripts/` updated **during** every phase rather than reconstructed at the
end. Phase 1 was first because it was the only genuine unknown; Phase 3 preceded 4
so the floor was calibrated against real retrieval rather than guessed; Phase 4
preceded 5 so the safety short-circuit existed before the agent was wired to it.

| Phase | Scope | Status |
|---|---|---|
| 0 | `.gitignore` and `.env.example` **before any code**; scaffold; Compose; Makefile | Done |
| 1 | **Risk spike**: prove `pi --mode rpc` completes a tool-using turn through Ollama from a Python subprocess | Done — go |
| 2 | Schema + models + `create_all`; health/readiness; structlog + correlation IDs | Done |
| 3 | Ingestion: fetch → frontmatter → speaker-aware chunk → hash → embed → upsert; topics | Done |
| 4 | Hybrid RRF + relevance floor calibrated on the golden set; abstention + topic suggestions | Done |
| 5 | Pi extension + RPC client + process pool; sessions/messages; SSE; citation validation | Done |
| 6 | `/api/providers`, `set_model`, fallback chain, degradation responses | Done |
| 7 | Frontend: shell, sessions, chat, streaming, citation panel, provider control | Done |
| 8 | Artifacts: `save_artifact`, sanitization, removal reporting, sandbox, regenerate | Done |
| 9 | Ship-30 skill; deterministic router; section-wise essay generation | Done |
| 10 | Tests (unit + integration + XSS), `make eval`, manual test plan | Done |
| 11 | Docs (`PRD.md`, `design.md`, `architecture.md`, `README.md`), fresh-clone check, demo video | **This document is part of it** |

---

## 10. Open questions

Recorded rather than closed, because each is a real limit of the evidence:

- **Is 0.65 the right floor?** The golden set's in-corpus and out-of-corpus bands
  overlap on the full corpus, so no threshold separates them. 0.65 favours
  abstention deliberately, and costs roughly one covered question in seven. The
  set is 13 questions and must be widened before the number is treated as settled
  (ADR-003).
- **Which local model should ship by default?** `llama3.2:3b` is the conservative
  CPU choice; `qwen3:4b` and `qwen2.5:7b` need more RAM or time than the reference
  machine can spare (ADR-007). The ladder is documented; the comparison is not
  measured.
- **Does the essay path need history replay?** It does not currently replay after
  an eviction, so an essay following an eviction loses thread continuity.
