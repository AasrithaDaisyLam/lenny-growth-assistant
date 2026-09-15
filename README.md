# The Lenny Growth Assistant

A grounded conversational assistant over **Lenny's Podcast** transcripts. Ask a
product-growth question, get an answer built **only** from what the transcripts
actually say, with an inline `[S#]` citation behind every claim — or an explicit
decline when the corpus does not cover it.

Runs entirely offline on CPU by default. No API key, no cloud account.

---

## Quickstart

```bash
cp .env.example .env        # defaults work as-is: local, offline, Ollama only
make up                     # or: docker compose up --build -d
make ingest                 # pulls models (once), then indexes the corpus
open http://localhost:8080
```

`make ingest` is content-hash idempotent: re-running it indexes only what is new.
For a fast first run, `docker compose run --rm --profile ingest ingest --limit 25`
gives a working corpus in minutes instead of the full 303-episode pass.

On Windows without `make`, use the `docker compose` equivalents printed by
`docker compose --help`; every Makefile target is a thin wrapper over Compose.

---

## What is here

```
backend/     FastAPI + the Pi Coding Agent it spawns as a subprocess
frontend/    React (Vite) SPA, served by nginx which also proxies /api
agent/       Pi model catalog + the three-tool extension (config, mounted read-only)
skills/      The Ship-30 essay house style, read at request time
docker-compose.yml   db (pgvector), ollama, api, web, ingest (one-shot, opt-in)
docs/        PRD.md, design.md, architecture.md, decisions.md (ADRs), manual-test-plan.md
agent-transcripts/   Build logs, including failures and corrections (deliverable 6)
```

Five services and no more. No Redis, no Celery, no separate vector store, no
model gateway — each would be a dependency that buys nothing at this scale.

### The request path

1. **Retrieve first** — pgvector cosine **and** Postgres full-text, fused with
   Reciprocal Rank Fusion. The two arms fail differently: dense matches meaning
   and misses exact names; lexical matches terms and misses paraphrase.
2. **Short-circuit before the model runs** — if nothing clears the relevance
   floor, the agent is never invoked. The most dangerous failure (a confident
   answer invented from an uncovered corpus) is impossible by construction
   rather than discouraged by prompt.
3. **Then invoke Pi**, streaming deltas straight through as SSE tokens.
4. **Enforce citations** — a substantive answer that resolved no citation from a
   turn that had sources is retried once, and **declined** if the retry also
   fails to cite. An uncited answer is never presented as a grounded one.
5. **Persist** the message with its citations, retrieval trace, outcome, usage
   and latency.

---

## Measured behaviour

These are measured on the reference machine (CPU-only) and printed by `make eval`.
The evaluation harness is in `backend/scripts/eval.py`.

| Metric | Value | Notes |
|---|---|---|
| Corpus | 303 episodes, 10,842 chunks | full ingest, content-hash idempotent |
| Retrieval recall | 86% (6/7) | top hit is the expected episode |
| Dense similarity, in corpus | min 0.647 / median 0.731 / max 0.783 | golden set, 7 questions |
| Dense similarity, not in corpus | min 0.492 / median 0.610 / max 0.649 | golden set, 6 questions |
| Uncovered questions declined | 100% (6/6) | at `RAG_MIN_SCORE=0.65` |
| Retrieval latency | p50 ~130–210 ms, p95 ~250 ms | CPU-bound embedding, warm model |
| Citation compliance | 3/3 grounded answers cited | after the enforcement gate |

### The citation gate is load-bearing, not decorative

On this hardware `llama3.2:3b` produced a substantive but **citation-free** first
attempt on **4 of 4** grounded turns in the reference sample. The gate caught
every one: three were rewritten with citations on the retry, and one question
that could not be grounded on either attempt was declined instead of being shown
as sourced. This is documented as a measured trade-off, not a hidden cost — see
`docs/decisions.md` (ADR-006).

### `RAG_MIN_SCORE = 0.65` is an operating point, not a calibrated constant

The golden set's in-corpus and out-of-corpus score bands **overlap** (worst
out-of-corpus 0.649, best in-corpus 0.647), so no threshold separates them. 0.65
was chosen to favour abstention: a confident wrong answer is not recoverable,
while a missed answer is. It clears the highest out-of-corpus score by 0.001 and
costs one covered question. The golden set is still 13 questions and must be
widened before the floor is treated as settled — see ADR-003.

---

## Configuration

Everything is environment-driven; see `.env.example` for the full list.

**Model selection is the provider toggle.** `LLM_PROVIDER` picks `ollama`
(default) or `anthropic`, and the active provider is surfaced in the UI on every
session. Nothing needs rebuilding to switch.

Local models run through Ollama. The catalog in `agent/models.json` ships
`llama3.2:3b` (default), `qwen3:4b` and `qwen2.5:7b`; the last two need more RAM
or time than the reference machine can spare (see ADR-007). Point
`OLLAMA_BASE_URL` at a native Ollama (`http://host.docker.internal:11434`) to use
host CPU instead of the Compose service.

Cloud is optional. With `ANTHROPIC_API_KEY` unset, `anthropic` appears in the UI
as **unavailable with the reason** rather than silently disappearing, and
switching to it is refused with a 503 that names the missing key.

---

## Commands

```bash
make up           # build + start the stack
make ingest       # pull models, then index the corpus (idempotent)
make test         # unit + integration + XSS suites
make eval         # print the measured metrics above
make calibrate    # re-derive RAG_MIN_SCORE from the golden set (writes nothing)
make lint         # ruff check + format check
make down         # stop the stack, keep volumes
```

---

## Known limitations

- **Retrieval is CPU-bound.** Retrieval is ~130–210 ms once the embedding model
  is resident; a generated turn is tens of seconds to minutes on CPU.
- **One Pi process per session**, LRU-capped and idle-evicted. Eviction loses
  in-memory context only — Postgres is the source of truth and recent turns are
  replayed on the next turn. The essay path does not replay history, so an essay
  after an eviction loses thread continuity.
- **`create_all` instead of migrations.** Correct for this scope; Alembic is the
  first thing to add for a real deployment.
- **Citation enforcement costs a second model call** whenever the first attempt
  omits citations, roughly doubling that turn's latency.
- **Small golden set.** 13 questions, scoped to two episodes when it was written.
  Good enough to calibrate a method; not enough to settle a threshold.

---

## Documentation map

| Document | Purpose |
|---|---|
| `README.md` | this file — what it is, how to run it, what is measured |
| `docs/PRD.md` | the user, the problem, the success metrics, scope, risks, acceptance criteria |
| `docs/design.md` | UI/UX principles, information architecture, interaction states, accessibility |
| `docs/architecture.md` | schema, API surface, the turn pipeline, retrieval, agent integration, security |
| `docs/decisions.md` | ADRs: every load-bearing choice and its trade-off |
| `docs/manual-test-plan.md` | the checks a human, a broken dependency, or a browser must do |
| `docs/demo-video-script.md` | the 2–3 min demo shot list, including the trade-off to explain |
| `agent-transcripts/` | build logs including failed attempts and corrections |
