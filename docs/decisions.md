# Design decisions

Decisions taken during the build, with the evidence behind them. The plan asks for
this log to be maintained continuously rather than reconstructed at the end, so a
call made in Phase 3 can be audited when Phase 4 changes the thing next to it.

Format: decision, why, what would change our mind.

---

## ADR-001 — `OLLAMA_BASE_URL` is configurable, in-Compose by default

**Decision.** `docker-compose.yml` sets `OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://ollama:11434}`
instead of hard-coding the in-Compose service.

**Why.** The file previously hard-coded `http://ollama:11434` while `.env.example`
documented the same variable as `http://host.docker.internal:11434` for a native
Ollama. The two could not both be true, and the hard-coded value silently won. The
default still resolves to the self-contained in-Compose service — which is what an
evaluator on a clean clone needs — but a machine that already has Ollama running
natively can now point at it and start only what it needs (`docker compose up -d db`).

**Verified.** A container reaches a native, loopback-bound Ollama at
`host.docker.internal:11434` (Docker Desktop proxies it), so no rebinding to
`0.0.0.0` is required and the host is not exposed on the LAN.

**Would change our mind.** If the demo had to run on a host without Docker Desktop's
loopback proxy, the native-Ollama path would need `OLLAMA_HOST=0.0.0.0` on the host
instead, and the default would stay in-Compose regardless.

---

## ADR-002 — Embedding cost is a first-class constraint, with a `--limit` fast path

**Decision.** `python -m scripts.ingest --limit N` indexes the first N episodes.
The default remains the full corpus.

**Why.** Measured on the reference CPU (i5-1240P, `nomic-embed-text`, 768d, warm):
**~730 tokens/s**. The corpus is ~269 episodes ≈ 5.1 M tokens, so a full ingest is
**~2 hours**. The plan never costed this, and `make ingest` sits on the evaluator's
path. A fast path that is honest about being partial is better than a first run that
appears hung.

**Why a limit is safe rather than a shortcut.** The pipeline is content-hash keyed
and skips before embedding: an unchanged re-run completed in **2.6 s** with 0 chunks
written. So `--limit 25` now and an unlimited run later converges on exactly the same
database state as one full run. That property is what makes the fast path correct
instead of a second ingestion mode to maintain.

**Levers if two hours is unacceptable.** A smaller embedding model (`all-minilm`,
384d, ~6× fewer parameters) trades retrieval recall for speed and changes
`EMBEDDING_DIM`, which requires re-indexing. Not taken: `nomic-embed-text` is the
better default for a citation-quality demo, and ingestion is one-time.

**Would change our mind.** If the corpus refreshed often enough that ingest time hit
the demo path repeatedly, the answer is a faster embedding model or a GPU, not a
cleverer pipeline.

---

## ADR-003 — `RAG_MIN_SCORE` is measured, not assumed (0.45 → 0.58 → 0.65)

**Decision.** The default relevance floor is **0.58**, derived from the golden set by
`make calibrate`. The previous value, 0.45, was a placeholder.

**Why.** Guessing produced a floor that barely abstains. Measured on the golden set
(13 questions: 7 in-corpus, 6 uncovered-but-plausible):

| Class | min | median | max |
|---|---|---|---|
| In corpus | 0.610 | 0.720 | 0.783 |
| Not in corpus | 0.396 | 0.475 | 0.555 |

Best dense cosine similarity per question, hybrid retrieval (vector + lexical + RRF).

At the placeholder 0.45, **all 7** in-corpus questions were kept but only **1 of 6**
out-of-corpus questions was rejected — it was admitting pricing, SEO, patenting,
engineering-hiring and Kubernetes questions that the corpus cannot answer. The
classes are separable on this set (`min(in) 0.610 > max(out) 0.555`), and the best
balanced threshold is **0.610** (recall 0.86, specificity 1.00); 0.58 sits just below
it, favouring recall because a missed answer is recoverable and a confident wrong one
is not.

**Caveat, and it is a real one.** This was measured against **two episodes**, not
269. Separation will *narrow* as the corpus grows: more chunks mean more chances for
an unrelated question to find a near-match, so out-of-corpus scores will rise. The
value is therefore an informed starting point, and `make calibrate` must be re-run
once the full corpus is ingested. The measurement method is the deliverable here, not
the number.

**Why a floor cannot finish the job regardless.** Dense embeddings assign high
similarity to *any* same-language text, so the bands always overlap to some degree.
The floor is the first of four layers (floor → empty-retrieval short-circuit so the
model is never invoked → citation validation → explicit "not in the provided
sources"), which is why the abstention requirement does not rest on this one number.

**Would change our mind.** Re-calibration on the full corpus. If the bands then
overlap, the threshold should be chosen for an explicit operating point and that
choice recorded here, rather than being tuned until a metric looks good.

### Re-derived on a larger corpus: 0.58 → 0.65 (measured)

The prediction above was correct, and the numbers are now in. `make eval` against
**125 indexed episodes** (not 2):

| Class | min | median | max |
|---|---|---|---|
| In corpus | 0.642 | 0.731 | 0.783 |
| Not in corpus | 0.472 | 0.594 | **0.649** |

**The bands now overlap** — the worst out-of-corpus question scores *above* the best
in-corpus one, so no threshold separates them. The in-corpus band moved only
slightly; the out-of-corpus band rose by ~0.15 (max 0.555 → 0.649), exactly as
predicted, because more chunks mean more chances for an unrelated question to find a
near-match.

At 0.58 the consequences were measured, not assumed:

- covered questions answered: **100% (7/7)** — the floor costs no recall;
- uncovered questions declined: **33% (2/6)** — it was still admitting four questions
  the corpus cannot answer.

That is the wrong trade for this system. Abstention is the *critical* risk, and a
confident wrong answer is not recoverable while a missed one is. **Raised to 0.65**,
which keeps ~86% recall (one covered question is now declined) and rejects ~83% of
the uncovered set.

The floor is still not the safety mechanism — the layered design is (ADR-003's
short-circuit and citation validation). The number is an operating point chosen
against a measured trade-off, and `make calibrate` must be re-run once the ingest
finishes, because the corpus was still growing when this was measured.

### Re-derived on the full corpus (303 episodes): 0.65 holds

The re-run the two sections above both call for has now happened, against the
**complete** corpus — 303 episodes, 10,842 chunks, 0 ingest errors:

| Class | min | median | max |
|---|---|---|---|
| In corpus | **0.647** | 0.731 | 0.783 |
| Not in corpus | 0.492 | 0.610 | **0.649** |

The prediction held twice over. The out-of-corpus **median** moved the most
(0.594 → 0.610) while its maximum barely changed (0.649 → 0.649), and the
in-corpus band stayed essentially put — which is the shape you expect when the
cause is "more chunks, more chances at a near-match" rather than a shift in what
embeddings mean.

The classes still **overlap**, and now by 0.002: the worst out-of-corpus question
(0.649) scores above the best in-corpus one (0.647). No threshold separates them,
which is the finding, not a failure to find one.

At 0.65, measured:

- covered questions answered: **86% (6/7)** — the floor costs exactly one;
- uncovered questions declined: **100% (6/6)**.

That is the trade ADR-003 committed to favouring: a missed answer is recoverable, a
confident wrong one is not. 0.65 clears the highest out-of-corpus score by 0.001,
which is uncomfortable but is the honest consequence of overlapping bands.

**The caveat that survives.** This is still 13 questions, and it was widened to the
full corpus but not to more questions. Two of the seven in-corpus questions are
declined-or-answered on a 0.002 margin, so the *specific* operating point is far
less robust than the *method* that produced it. Widening the golden set is the next
real step before this number is treated as settled; the number is not the
deliverable, `make calibrate` is.


---

## ADR-005 — Hybrid retrieval via RRF, and an asymmetric abstention gate

**Decision.** Retrieve with pgvector cosine *and* Postgres full-text
(`websearch_to_tsquery`), then fuse with Reciprocal Rank Fusion. The relevance floor
is applied to the **dense** similarity only.

**Why fusion.** The two arms fail differently. Dense matches meaning, so "keep users"
finds a passage about retention, but blurs exact strings like a guest or product name.
Lexical matches terms exactly but misses paraphrase. RRF combines them without needing
to normalise `cosine similarity` against `ts_rank_cd` — two scales with no common unit
— which a weighted sum would require. A chunk ranked highly by *both* arms accumulates
both contributions and rises; that agreement is the signal.

**Why the gate is asymmetric.** Fusion decides *ordering*; the floor decides whether
the corpus covers the question. The floor is compared to cosine similarity because
that is the scale it was calibrated on (ADR-003). A result set with no dense score at
all — lexical-only — is deliberately **not** treated as ungrounded: a lexical hit
requires literal term overlap, which is much harder to produce spuriously than a dense
near-match, so it is evidence of coverage rather than absence. This asymmetry is
tested (`test_lexical_only_results_are_not_treated_as_ungrounded`).

**Measured cost.** Retrieval alone (query embedding + both arms + fusion) is
**p50 ≈ 145 ms, p95 ≈ 216 ms**, excluding a one-off ~1.5 s cold embedding-model load.
No reranker: the latency budget is already dominated by CPU inference, and a
cross-encoder's gain could not be demonstrated at this corpus size.


---

## ADR-004 — `Speaker:` is inlined into chunk text

**Decision.** Chunk text is rendered as `Speaker: text` lines; the `chunks.speaker`
column is set only when a chunk has exactly one speaker, and `NULL` otherwise.

**Why.** A chunk that spans speakers cannot be described by one column, and the model
needs to know who said what to cite a person. Inlining keeps attribution in every
chunk and lets the column be honest (`NULL` = genuinely mixed) rather than picking
the dominant speaker and implying a precision that is not there. The `Speaker: `
prefix is charged against the chunk's token budget so the stored `token_count`
describes the embedded text exactly.

---

## ADR-006 — An uncited answer is retried once and then declined

**Decision.** A grounded question that retrieved sources and produced a substantive
answer carrying **no** resolvable citation is retried once, with a stricter prompt
that re-presents the sources with the markers they already carry. If the retry also
resolves no citation, the turn is **declined** (outcome `uncited`) rather than
presented as a grounded answer. Alongside it:

- a **per-turn citable-source registry** was added, so a passage returned by a
  mid-turn tool call is citable by an `[S#]` that resolves, and tool markers
  continue the injected numbering instead of colliding with it;
- the **citation-coverage metric** was fixed to count only genuine grounded answers
  (`intent == grounded_qa` **and** `outcome == answered`).

**Why.** This is the failure the product exists to prevent, and it was measured, not
imagined. On the reference hardware `llama3.2:3b` produced a substantive but
**citation-free** first attempt on **4 of 4** grounded turns in the reference
sample. A prompt instruction saying "always cite" had already been tried; it was not
sufficient, so adherence is not left to instruction.

The gate is what makes the layering real: the empty-retrieval short-circuit stops
the model from being asked about an uncovered question, and this stops a model that
was asked from handing back an answer with no traceable source. An uncited answer
*is* the hallucination risk in a weaker disguise — it reads as authoritative while
being unverifiable.

**The registry was a correctness bug, not a feature.** Before it, the prompt
numbered the injected sources `S1..SN`, but a passage fetched by `search_transcripts`
or `get_episode` mid-turn had no marker at all — so the model either cited nothing
or invented a number, and an `[S4]` from a tool could silently collide with a fourth
injected source. One ordered list per turn, opened with the injected chunks and
appended to by the tools, makes both problems disappear and lets citation validation
stay a simple positional lookup.

**Verified.** A controlled sample of five turns: **3** grounded answers cited,
**1** grounded turn correctly declined after a failed citation retry, **1**
below-floor abstention with no model call. Citation compliance after the gate:
**3/3**.

**What it costs, stated plainly.** The gate adds a second model call whenever the
first attempt omits citations — roughly doubling that turn's latency — and one
grounded question was declined rather than answered. That is the accepted trade: an
unsourced answer is not a better outcome than a decline.

**Would change our mind.** A model on the default path that cites reliably makes the
retry a no-op; the gate stays as a safety net rather than being removed, because the
model is one environment variable away from changing. If declines became common on
*covered* questions, the retry prompt is the first thing to inspect — not the
threshold.

---

## ADR-007 — Local model catalog: `llama3.2:3b` default, two heavier models suggested

**Decision.** `agent/models.json` declares Ollama natively as an
`openai-completions` provider with three models — `llama3.2:3b` (default),
`qwen3:4b`, `qwen2.5:7b` — all zero-cost and CPU-runnable. The heavier two are
listed but not defaulted to.

**Why `llama3.2:3b`.** It is the only one of the three that fits comfortably in the
reference machine's RAM and produces a turn in tens of seconds rather than minutes.
The other two are listed because they are the documented quality lever — one env var
(`OLLAMA_MODEL`) changes which is used, with no code change and no rebuild — and
because hiding them would misrepresent the upgrade path as unavailable.

**Why the `baseUrl` is rewritten at spawn.** Pi interpolates credentials (`$VAR`
inside `key`/`apiKey`) but **not** `baseUrl`: a template like `"${OLLAMA_BASE_URL}/v1"`
resolves to the literal string and the provider fails with *Invalid URL*. So the
mounted `models.json` is a template and the effective catalog is written once per
spawn from `OLLAMA_BASE_URL`. Re-writing every time is what makes changing the
environment variable sufficient — no rebuild, no manual edit, no drift between the
mounted file and what Pi actually loads.

**Why the file is mounted read-only and copied.** Pi writes into its config
directory (it opens `auth.json` and creates a session directory) even when those
features are unused, so pointing `PI_CODING_AGENT_DIR` straight at the mount fails
at startup with `EROFS: read-only file system`. A writable copy under
`PI_AGENT_HOME` keeps the mounted config authoritative while letting Pi do its
bookkeeping.

**Would change our mind.** A machine with ~8 GB spare would make `qwen2.5:7b` the
better demo default for citation adherence (ADR-006). The catalog is the right place
for that judgement, which is why it is a file and not a constant.
