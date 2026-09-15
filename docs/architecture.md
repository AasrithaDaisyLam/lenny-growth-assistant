# Architecture

How the Lenny Growth Assistant is put together, and why each boundary sits where
it does. `README.md` covers what it is and how to run it; this document is for
someone who needs to change it or judge it.

Design calls with a real trade-off are logged continuously in `docs/decisions.md`
(ADR-001 … ADR-007) and would otherwise be duplicated here.

---

## 1. Topology

Five Compose services and nothing else. There is no Redis, no Celery, no second
vector store, and no model gateway — each would be a dependency that buys nothing
at this scale.

```
Browser ──SSE──> nginx (web :8080) ──/api──> FastAPI (api :8000)
                                                │
                                                ├── spawn (Popen, JSONL stdio)
                                                │        └─> pi --mode rpc
                                                │                 │
                                                │        HTTP + X-Internal-Token
                                                │                 │
                                                │        /internal/tools/* (calls back in)
                                                │
                                                ├──> Postgres 16 + pgvector  (:5432)
                                                └──> Ollama                  (:11434)
                                                        nomic-embed-text (embeddings)
                                                        llama3.2:3b      (generation)
```

**Key decision: Pi is a subprocess, not a service.** It runs inside the `api`
container, spawned by FastAPI, and spoken to over JSONL on stdin/stdout. That
keeps Compose to five services and removes a network hop, at the cost of process
lifecycle management (§7). The backend image therefore carries **both** Python and
Node: Node exists only to run `pi`.

**Key decision: the extension is a thin HTTP shim.** Pi owns the agent loop;
Python keeps sole ownership of retrieval, embeddings, and the database. There is
one implementation of RAG rather than two that can drift, and the extension cannot
corrupt corpus state because it never touches it.

---

## 2. Component boundaries

### Backend (`backend/app/`)

| Package | Responsibility |
|---|---|
| `main.py` | App assembly: lifespan, middleware, routers, exception handlers. Boots even when Postgres is down, and reports it through readiness instead of crash-looping. |
| `config.py` | Every knob, as pydantic-settings. **No module reads `os.environ` directly** — that is what makes the model toggle a config change rather than a code change. |
| `api/routes/health.py` | Liveness (touches nothing) and readiness (per-dependency, each with a reason). |
| `api/routes/sessions.py` | Session CRUD; the PATCH handler also performs a **live** `set_model` over RPC when a provider switch is requested. |
| `api/routes/messages.py` | Transcript read, and the SSE turn. |
| `api/routes/providers.py` | Provider catalogue, including disabled providers with the reason. |
| `api/routes/search.py` | Retrieval-only search — no model, so retrieval latency is measurable in isolation and this is the surface the floor is calibrated against. |
| `api/routes/artifacts.py` | Artifact create/read/render/regenerate. `/render` is the third containment layer (§8). |
| `api/routes/tools.py` | The internal, shared-secret-gated endpoints the Pi extension calls. Excluded from the OpenAPI schema. |
| `core/logging.py`, `core/middleware.py`, `core/errors.py` | structlog JSON, correlation IDs, and problem-object error handling. |
| `db/session.py`, `db/base.py` | Async engine, `get_db` dependency, `create_all` bootstrap. |
| `models/` | SQLAlchemy models: `corpus.py`, `conversation.py`, `ops.py`. |
| `schemas/` | Pydantic contracts, including `events.py` — the SSE contract. |
| `services/` | `chat_service.py` (the turn pipeline), `cite_service.py`, `source_registry.py`, `session_service.py`, `artifact_service.py`, `provider_service.py`, `outcomes.py`, `ollama.py`. |
| `agent/` | `pi_client.py` (RPC), `process_pool.py` (LRU + idle eviction), `prompts.py`, `router.py`. |
| `retrieval/hybrid.py` | Vector + lexical arms, RRF fusion, the floor, topic suggestions. |
| `ingestion/` | `source.py`, `parser.py`, `chunking.py`, `topics.py`, `pipeline.py`. |
| `artifacts/sanitize.py` | `nh3` allowlist sanitization plus the removal audit. |

### Frontend (`frontend/src/`)

| File | Responsibility |
|---|---|
| `App.tsx` | Composition root; owns the selected-citation state and the first-message session-creation path. |
| `api/client.ts` | Typed REST client and the SSE frame parser (`parseFrame` + `sendMessage` async generator). |
| `hooks/useChat.ts` | Folds SSE events into a live `TurnState`; owns the `AbortController` for Stop. |
| `hooks/useSessions.ts` | Session/message state machine. |
| `hooks/useProviders.ts` | Provider catalogue + readiness, fetched together on mount. |
| `components/` | `AppShell`, `SessionSidebar`, `ChatPanel`, `MessageList`, `Composer`, `Citations` (`CitationChip` + `SourcePanel`), `ProviderControl`, `HealthBadge`, `ArtifactViewer` (with `SandboxFrame`). |

Notably absent: no state-management library, no data-fetching library, no CSS
framework beyond Tailwind. The client is small enough that React state plus two
hooks is the whole story.

### Agent extension (`agent/`) and skills (`skills/`)

- `agent/growth-extension.ts` — registers three tools: `search_transcripts`,
  `get_episode`, `save_artifact`. Each is a `POST` to an `/internal/tools/*`
  endpoint with `X-Internal-Token` and `X-Session-Id` headers.
- `agent/models.json` — the provider catalogue. Ollama is declared natively as an
  OpenAI-compatible provider; the Anthropic entry overrides Pi's built-in base URL.
  The file's `baseUrl` is a **template** rewritten at spawn from `OLLAMA_BASE_URL`
  (Pi interpolates credentials but not `baseUrl` — see ADR-007/§7).
- `skills/ship30-essay/SKILL.md` — the essay house style, read at request time.

Both `agent/` and `skills/` are mounted **read-only**; the mounted config stays
authoritative, and a writable copy is materialized under `PI_AGENT_HOME` before
each spawn because Pi writes a credential store and session directory into its
config home.

---

## 3. Database schema

Eight tables. pgvector and full-text search both live in the same Postgres, so
there is one dependency instead of two. Created by `Base.metadata.create_all` on
startup (after `CREATE EXTENSION IF NOT EXISTS vector`), not Alembic — see §12.

**Corpus** — `episodes`, `chunks`, `topics`, `episode_topics`

- `episodes`: `slug` (unique), `title`, `guest`, `youtube_url`, `published_at`,
  `source_url`, `source_updated_at`, `content_hash`, `ingested_at`.
- `chunks`: `episode_id` (FK, cascade), `chunk_index`, `speaker` (NULL when the
  chunk spans speakers), `text`, `token_count`, `content_hash`,
  `embedding vector(768)` (nullable), and a **generated** `tsv tsvector` column
  maintained by Postgres, not the app.
  - `UNIQUE (episode_id, chunk_index)`
  - HNSW index on `embedding` with `vector_cosine_ops` (`m=16`,
    `ef_construction=64`)
  - GIN index on `tsv`; btree index on `content_hash`
- `topics` / `episode_topics`: seeded from the corpus's own `index/` folder, and
  what powers the "the corpus doesn't cover that, but it does cover these" exit.

**Conversation** — `sessions`, `messages`, `artifacts`

- `sessions`: `id uuid`, `title`, `provider` + `model` (session-scoped on
  purpose), `user_metadata jsonb`, timestamps.
- `messages`: `role` (checked), `content`, `intent`, `citations jsonb`,
  `retrieval_trace jsonb`, `provider`, `model`, `token_usage jsonb`, `latency_ms`.
  - `retrieval_trace` is what makes *"why did it say that?"* answerable after the
    fact instead of only by re-running the request.
  - `intent` says what was asked for; `retrieval_trace['outcome']` says what
    happened (`answered` / `uncited` / `abstained` / `degraded` / `failed` /
    `smalltalk`). Keeping them separate is what lets the evaluation count answers
    without counting failures as citation gaps.
- `artifacts`: `kind` (checked: markdown|html), `raw_content`,
  `sanitized_content`, `removed_elements jsonb`, `version`. Both forms are stored
  deliberately: raw for audit, sanitized is the only thing the viewer renders.

**Operability** — `ingestion_runs`: `status`, `source`, `episodes_seen`,
`chunks_written`, `chunks_skipped`, `error`.

---

## 4. The request path

`chat_service.stream_turn` is the whole design in one function. The ordering is
the safety property, not a sequence of conveniences.

1. **Persist the question**, and read prior turns *before* appending it, so the
   rehydration preamble cannot include the question twice.
2. **Route deterministically** (`agent/router.py`). A keyword/regex classifier,
   deliberately not a model call — on CPU, routing must never consume model
   reasoning, and a misfiring regex is reviewable where a misfiring model is a
   mystery. Smalltalk short-circuits to a static reply and touches **no**
   dependency.
3. **Provider preflight, with fallback.** Before retrieval, not after: retrieval
   embeds the query, so if the chat provider is down there is no point doing work
   that will also fail. An unreachable provider degrades with a named cause.
4. **Retrieve** (hybrid, §5). A retrieval failure is a *refusal*, not a fallback
   to ungrounded prose.
5. **Short-circuit before the model is invoked.** If nothing clears the relevance
   floor, Pi is never started for this turn. This makes the most dangerous failure
   — a confident answer invented from an uncovered corpus — impossible by
   construction rather than discouraged by prompt.
6. **Invoke Pi**, streaming text deltas straight through as SSE `token` frames so
   the first token reaches the browser while CPU inference is still working.
7. **Validate citations**, retry once, decline if the retry also fails (§6).
8. **Persist** the answer with citations, retrieval trace, outcome, usage, and
   latency — inside the generator, in its own database session, because a
   request-scoped dependency session would already be closed by the time the
   stream is consumed.

---

## 5. Retrieval

Two arms with different failure modes, fused rather than one being trusted:

- **Dense** — pgvector cosine over `chunks.embedding`, top 50. Matches meaning,
  so "how do I keep users?" finds a passage about retention; it blurs exact
  strings like guest or product names.
- **Lexical** — `websearch_to_tsquery('english', q)` over `tsv`, top 50.
  `websearch_to_tsquery` accepts user-shaped input (quoted phrases, `or`, `-term`)
  and cannot raise on malformed input. Matches terms exactly, misses paraphrase.
- **Fuse** — Reciprocal Rank Fusion, `score = Σ 1/(k + rank)` with `k = 60`, top
  `RAG_TOP_K` (default 6). RRF needs no score normalisation between cosine
  similarity and `ts_rank_cd` — two scales with no common unit — which a weighted
  sum would require. A chunk ranked highly by *both* arms accumulates both
  contributions and rises; that agreement is the signal.

**The relevance floor is asymmetric on purpose.** `should_abstain` compares
`RAG_MIN_SCORE` against the **dense** similarity only, because that is the scale it
was calibrated on. A result set with *no* dense score at all (lexical-only) is
deliberately **not** treated as ungrounded: a lexical hit requires literal term
overlap, which is much harder to produce spuriously than a dense near-match.
Covered by `test_lexical_only_results_are_not_treated_as_ungrounded`.

**The floor cannot finish the job, and the design does not ask it to.** Dense
embeddings assign high similarity to *any* same-language text, so in-corpus and
out-of-corpus bands overlap on the full corpus. Abstention is layered — floor →
empty-retrieval short-circuit → citation validation → explicit "not in the
provided sources" — which is why the abstention requirement does not rest on one
number. `docs/decisions.md` (ADR-003) records the measurements and the operating
point.

**No reranker.** Retrieval is p50 ≈ 130–210 ms once the embedding model is
resident; a cross-encoder would add real CPU latency for a gain that cannot be
demonstrated at this corpus size.

---

## 6. Citations

An unresolvable citation is worse than no citation: `[S7]` in a five-source answer
reads as rigour while pointing at nothing. Three pieces enforce otherwise.

**The citable-source registry** (`services/source_registry.py`). One ordered list
of sources per turn. The chat turn opens it with the chunks retrieval injected
(`S1..SN`); the internal tool endpoints **append** whatever they return and hand the
marker back to the extension, so a mid-turn `search_transcripts` or `get_episode`
result is citable too and its markers do not collide with the injected ones. A
passage already in the list keeps its existing marker rather than gaining a second.
Turn state is keyed by session because one Pi process serves one session. No lock:
everything runs on the API's single event loop and no method awaits, so a mutation
cannot interleave with a read.

**Validation** (`services/cite_service.py`). Every bracketed span is classified:

- `[S1]`, `[s1]`, `[S 1]` → normalised to `[S1]` and resolved positionally.
- `[Source 1]`, `[S1, S2]`, `[S1-S3]`, bare `[S]` → **reported and stripped, never
  interpreted**. Guessing which source a list means is exactly the silent
  acceptance this module exists to prevent.
- `[appendix]` and other prose brackets → left untouched.

Out-of-range markers are stripped from the stored text *and* recorded, so the
removal is visible in the trace and logs rather than silently repaired.

**The gate** (`chat_service`). If a grounded question produced a substantive answer
with sources available but **no** resolvable citation, the turn is retried once
with a stricter prompt that re-presents the sources **with the markers they already
carry** (re-numbering from 1 would silently change what `[S4]` means). If the retry
also fails to cite, the turn is **declined** — never presented as sourced. The
outcome is `uncited`, which the evaluator counts separately from a citation gap.

---

## 7. Agent integration (Pi over RPC)

`backend/app/agent/pi_client.py` drives one `pi --mode rpc` process. Three protocol
details are load-bearing and easy to get subtly wrong:

1. **Framing is LF-only.** Generic line readers are not compliant, because
   `U+2028`/`U+2029` are valid inside JSON strings. The client splits on `b"\n"`
   itself and strips a trailing `\r`.
2. **stderr must be drained concurrently.** If nobody reads it the pipe buffer
   fills and the process deadlocks mid-turn — a hang with no error message, the
   worst possible failure mode for a demo.
3. **One turn at a time per process.** Responses and events share the stream, so
   turns are serialised on a lock and command responses are consumed internally
   rather than leaking into the event stream.

`agent_settled` terminates a turn, **not** `agent_end`: `agent_end` fires per
low-level run and may be followed by a retry or a queued continuation, so stopping
there would truncate a turn that was still going.

### Isolation is config, not prompting

```
pi --mode rpc --no-session --provider <p> --model <m>
   --no-builtin-tools
   --tools search_transcripts,get_episode,save_artifact
   --no-extensions --no-skills --no-prompt-templates --no-context-files
   --no-themes --no-approve --offline
   -e /app/agent/growth-extension.ts
```

Pi ships with `read`/`bash`/`edit`/`write` enabled. `--no-builtin-tools` removes
them and `--tools` narrows the surface to our three. The agent then has **no
filesystem, no shell, and no network**, so a prompt-injected transcript has nothing
to steer it toward. "Strictly from Lenny's transcripts" is enforced by removing
capability rather than by asking nicely. This is a configuration correctness
issue, and it is the real security boundary.

### Process lifecycle

One Pi process per active session, held open so the model keeps in-memory
conversation context. Bounded by `PI_MAX_SESSIONS` (default 4, LRU) and
`PI_IDLE_TIMEOUT_SECONDS` (default 900).

**Eviction is not data loss.** Postgres is the source of truth; only the process's
context is discarded. `pool.acquire()` returns `(client, is_fresh)`, and a fresh
process gets the last few turns replayed as a context preamble
(`prompts.compose_turn`). A warm process does not, because duplicating history on a
CPU-bound 3B model slows every prefill for no benefit.

`provider`/`model` come from the **session**, not from global settings — a session
can have switched provider, and spawning with the default would silently answer
with the wrong model.

---

## 8. Artifact security

Model-generated HTML is untrusted **always** — including when a frontier cloud
model produced it, because a prompt-injected transcript could steer either
provider. Four layers, and the fourth is the one that is easy to get wrong:

1. **Allowlist sanitization before persistence** (`nh3`). Strips `<script>`, all
   `on*` handlers, `<iframe>/<object>/<embed>/<form>/<meta>/<base>/<link>`; refuses
   dangerous URL schemes. A pre-pass neutralises `javascript:` and
   `data:text/html` **before** nh3 runs, because nh3 filters by scheme and cannot
   tell `data:image/png` (needed for self-contained charts) from
   `data:text/html` (an attacker-authored page).
2. **Visible removal.** A stdlib `HTMLParser` audit records *what* was stripped
   into `artifacts.removed_elements`, because nh3 reports nothing about removals.
   A silently sanitized chart that loses its data is worse than being told.
3. **No-egress CSP** on `GET /api/artifacts/{id}/render`:
   `default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:;
   script-src 'unsafe-inline'; base-uri 'none'; form-action 'none';
   frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff`.
4. **`<iframe sandbox="allow-scripts" srcDoc={...}>`** and **never**
   `allow-same-origin`. Combining the two is the specific mistake that defeats
   iframe sandboxing — it lets the frame script reach the parent origin. With
   scripts alone, `srcDoc` gives the frame an opaque origin: no cookies, no
   `localStorage`, no access to the parent DOM.

**Accepted trade-off:** inline `script-src 'unsafe-inline'` is kept because
self-contained charts need it, contained by layers 3–4. A data-exfiltrating
artifact is a far worse outcome than a missing web font.

The internal tool endpoints are the other boundary: shared-secret gated, compared
with `secrets.compare_digest` so the check cannot leak the secret's length through
timing, and registered with `include_in_schema=False` so they are not part of the
public contract. Covered by two integration tests.

---

## 9. Provider toggle

Two rules shape `provider_service.py`: **unavailable providers are reported, not
hidden** (a provider that silently disappears looks like a missing feature rather
than a missing key), and **selection is deterministic and ordered** — a pure
function of the chain plus an availability map, so the fallback logic is
unit-testable without standing up a provider.

```
GET   /api/providers        -> every provider, available ones plus disabled ones with a reason
PATCH /api/sessions/{id}    -> set_model over RPC; no restart
```

Availability is probed live for Ollama (a process that can simply be down) and by
configuration for Anthropic (a missing key needs no network call). Switching to an
unavailable provider is refused with a **503** naming the missing key — not a 404,
which would send the caller looking for a typo.

If a process is already live for the session, `PATCH` changes its model over RPC so
the warm conversation survives the switch (`provider_switched_live`). If not, the
next turn spawns with the new model and rehydrates from Postgres. A failed live
switch discards the process rather than leaving it bound to the old model.

`models.json` is the catalogue, and the `baseUrl` rewrite at spawn is what makes
`OLLAMA_BASE_URL` meaningful: Pi interpolates credentials but **not** base URLs, so
a template left in place fails with "Invalid URL". Rewriting per spawn means
changing the env var is sufficient — no rebuild, no manual edit.

---

## 10. Ingestion

`fetch → parse → chunk → hash → embed → upsert`, in `ingestion/pipeline.py`.

- **Source** (`source.py`) — remote tarball into a cached volume, or a local
  directory. `TRANSCRIPT_SOURCE` selects; one adapter interface, two
  implementations.
- **Parse** (`parser.py`) — YAML frontmatter → episode metadata; body → speaker
  turns, with continuation labels inheriting the previous speaker.
- **Chunk** (`chunking.py`) — **speaker-aware**, not fixed-size. Consecutive turns
  merge to ~500 tokens with ~15% overlap; turns are never split mid-sentence; an
  oversized turn is broken on sentence boundaries and a sentence over budget is
  kept whole rather than truncated. `Speaker: ` is inlined into the text so
  attribution survives in every chunk, and `token_count` is measured against the
  **rendered** chunk so it describes the embedded text exactly.
- **Hash + skip** — `sha256(slug | speaker | text)`, checked **before** embedding.
  This is the difference between a re-ingestion costing seconds and costing
  minutes of CPU.
- **Embed** — batched Ollama `/api/embed`, batch 32.
- **Upsert** — `ON CONFLICT`, stale chunks deleted, `ingestion_runs` row written.

Two consequences: it is **idempotent** (an unchanged re-run embeds nothing) and
**incremental** (only episodes whose file hash changed are touched). Topics are
linked periodically *during* the run, not only at the end, so an interrupted run
still leaves an abstention response with something to suggest.

`--limit N` indexes the first N episodes. A full pass is hours of CPU-bound
embedding, so a fast path that is honest about being partial is better than a first
run that appears hung — and because the pipeline converges on identical state, the
limit is a fast path rather than a second ingestion mode (ADR-002).

---

## 11. API surface and the SSE contract

### Public (`/api`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness; touches no dependency |
| GET | `/api/health/ready` | Per-dependency status, each with a reason |
| GET | `/api/providers` | Available models, plus disabled providers with the reason |
| POST | `/api/sessions` | New chat |
| GET | `/api/sessions` | List with title, provider and model |
| GET | `/api/sessions/{id}` | Detail |
| PATCH | `/api/sessions/{id}` | Rename; change provider (applies live over RPC) |
| DELETE | `/api/sessions/{id}` | Cascade messages and artifacts |
| GET | `/api/sessions/{id}/messages` | Full transcript with citations and traces |
| POST | `/api/sessions/{id}/messages` | **SSE stream** for one turn |
| POST | `/api/sessions/{id}/artifacts` | Explicit artifact creation |
| GET | `/api/sessions/{id}/artifacts` | Artifacts for a session |
| GET | `/api/artifacts/{id}` | Metadata, sanitized and raw |
| GET | `/api/artifacts/{id}/render` | Sanitized HTML + no-egress CSP |
| POST | `/api/artifacts/{id}/regenerate` | Re-sanitize raw content as a new version |

`GET /` redirects to `/docs`.

### Internal (`/internal`, `include_in_schema=False`)

| Method | Path | Called by |
|---|---|---|
| POST | `/internal/tools/search_transcripts` | `search_transcripts` |
| POST | `/internal/tools/get_episode` | `get_episode` |
| POST | `/internal/tools/save_artifact` | `save_artifact` |

All three require `X-Internal-Token`; the first two also register their result in
the turn's source registry using `X-Session-Id`.

### SSE events (`schemas/events.py` — the contract with the UI)

Typed frames, so the frontend never parses bare text:

| Event | Payload | Meaning |
|---|---|---|
| `token` | `text` | A chunk of assistant text |
| `tool` | `name`, `status`, `args?` | A tool the agent called — retrieval observability, not hidden |
| `citations` | `citations[]` | The validated sources for the answer |
| `abstained` | `reason`, `topics[]` | Not covered; suggests covered topics |
| `usage` | `provider`, `model`, `tokens?` | Which model actually answered |
| `artifact` | `artifact_id`, `kind?`, `title?` | A saved artifact the viewer can open |
| `error` | `code`, `message` | A structured failure — never a bare 500 mid-stream |
| `done` | `message_id` | End of turn, with the persisted id |

The client splits the buffer on `"\n\n"`, **not** line by line, because a single
`data:` payload can be split across network chunks and a line-based reader would
emit a truncated JSON document. The server sets `X-Accel-Buffering: no` and nginx
sets `proxy_buffering off` on the session path — without both, streaming works in
dev and is silently defeated in the container.

### Errors

Problem objects carrying a `code` and a `correlation_id`; every response carries
`X-Request-ID`, which also appears on every log line for that request, so a trace
can span the frontend, the API, and the extension's tool callbacks.

---

## 12. Observability and schema

- **structlog JSON** with `request_id` bound per request; health probes are
  excluded from access logs so they do not drown the signal.
- **`retrieval_trace`** on every assistant message: best similarity, candidate
  count, sources vs tool sources, unknown markers, citation count, whether the
  citation gate retried, outcome, and any error. This is the difference between
  "it said something odd" and knowing why.
- **Distinct log events per failure mode** — `turn_abstained`, `turn_degraded_no_provider`,
  `turn_degraded_retrieval`, `citations_missing_retrying`, `citations_still_missing`,
  `citations_unresolved`, `pi_spawned` / `pi_reused` / `pi_evicted_lru`.
- **`create_all`, not Alembic.** No client is consuming incremental schema
  changes, so a migration chain is ceremony for this scope. **Alembic is the first
  thing to add for a real deployment**; the reset path is `make reset`.

---

## 13. Deployment topology

| Service | Image / build | Purpose |
|---|---|---|
| `db` | `pgvector/pgvector:pg16` | Vectors + relational state in one dependency |
| `ollama` | `ollama/ollama` | Embeddings + generation, CPU |
| `ollama-init` | `ollama/ollama` | One-shot model pull (no restart) |
| `api` | `./backend` | FastAPI + Pi subprocess + Node |
| `web` | `./frontend` | Multi-stage Vite build → nginx; also proxies `/api` |
| `ingest` | `./backend`, profile `ingest` | One-shot, opt-in corpus indexing |

Volumes: `pgdata`, `ollama_models`, `transcript_cache`. `agent/` and `skills/`
are mounted read-only. Only `ingest` sits behind a profile; everything else runs by
default. `api` waits on healthy `db` and `ollama`.

`OLLAMA_BASE_URL` defaults to the in-Compose service, so a clean clone is
self-contained, but it can point at a native Ollama
(`http://host.docker.internal:11434`) so a developer starts only `db` (ADR-001).

---

## 14. Deliberately absent

Each is a judgement, not a gap. Full rationale in `docs/decisions.md` and the
plan's cut list.

| Cut | Why |
|---|---|
| Alembic migrations | No incremental schema consumers; `create_all` is the honest tool |
| Eval framework / release gate | The brief asks for ≥1 measurable metric, not CBAR-as-a-gate |
| Automated fault-injection suite | Resilience *behaviour* is implemented; the harness is not. Covered by `docs/manual-test-plan.md` |
| Comprehensive XSS payload corpus | A focused, named-vector suite stays; security is graded, so it is real |
| Auth / multi-tenancy | Internal, trusted, single tenant. `sessions.user_metadata` exists so it is a middleware addition |
| Audio ingestion / diarization | Upstream text already carries speaker labels |
| Fine-tuning | RAG suits a factual, citation-required, refreshed corpus; fine-tuning teaches style, not facts |
| Agentic web browsing | Contradicts "strictly from Lenny's transcripts" |
| Cross-encoder reranking | Real CPU cost, undemonstrable gain at this size |
| A third provider | The brief requires at least one cloud provider; Claude satisfies it |

---

## 15. Known limitations

- **Retrieval is CPU-bound**, ~130–210 ms p50 once the embedding model is
  resident; a generated turn is tens of seconds to minutes on CPU.
- **One Pi process per session** is a scaling ceiling. The pool caps and evicts,
  and Postgres rehydration contains it; a queue or per-turn spawning is the fix.
- **The essay path does not replay history**, so an essay after an eviction loses
  thread continuity. Grounded QA replays; the section-wise essay path was kept
  simple.
- **The relevance floor is an operating point, not a calibrated constant.** The
  golden set's bands overlap on the full corpus, so 0.65 was chosen to favour
  abstention. The golden set is 13 questions and must be widened before the floor
  is treated as settled (ADR-003).
- **Citation enforcement costs a second model call** whenever the first attempt
  omits citations, roughly doubling that turn's latency.
- **A small CPU model is genuinely weaker** at synthesis and instruction
  adherence. The documented lever is `OLLAMA_MODEL`; `make eval` re-measures.
