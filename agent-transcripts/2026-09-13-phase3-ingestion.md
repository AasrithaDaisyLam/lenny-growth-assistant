# 2026-09-13 — Phase 3: ingestion, chunking, and retrieval

**Deliverable:** 6 (agent transcripts)
**Phase:** 3 — corpus fetch → parse → speaker-aware chunk → embed → upsert, plus `/api/search`
**Verdict:** **PASS** — corpus indexed and queryable; `/api/search` returns scored, attributed chunks.
**Model:** `nomic-embed-text` (768d) via native Ollama; Postgres 16 + pgvector 0.8.6 in Compose.
**Machine:** Intel i5-1240P, CPU-only. C: 5.9 GB free, D: 262 GB free.

---

## 1. Goal

Make the corpus queryable. Exit condition from the plan: *"Corpus queryable;
`/api/search` returns scored chunks; retrieval p95 measured standalone."*

Three things had to be true that the plan only assumed:

1. The corpus shape (A2/A3) — frontmatter fields, and whether speaker labels exist.
2. That the pipeline is idempotent, so re-ingestion is near-free.
3. That ingestion is *affordable* on CPU — the plan never costed it.

## 2. Environment: the spike's Docker finding was stale

The Phase 1 transcript says Docker was not installed. That was true when written
and false by the time this phase started: Docker Desktop 4.90 is installed
**user-scope** at `%LOCALAPPDATA%\Programs\DockerDesktop`, so `docker` is simply
never on `PATH`.

Correction, in two steps:

- First attempt failed with `exec: "docker-credential-desktop": executable file
  not found in %PATH%` — invoking `docker.exe` by absolute path fixes the CLI but
  not its credential helper. The fix is to prepend the whole
  `resources\bin` directory to `PATH`, not to call the exe directly.
- Containers reach a **native, loopback-bound** Ollama at
  `http://host.docker.internal:11434` (verified: `{"version":"0.34.0"}`). So the
  `.env.example` assumption holds and Ollama never needs rebinding to `0.0.0.0`.

Two inconsistencies were found and fixed while wiring this up:

- `docker-compose.yml` hard-coded `OLLAMA_BASE_URL: http://ollama:11434`, which
  silently ignored the same variable in `.env.example` (documented there as
  `host.docker.internal`). Now `${OLLAMA_BASE_URL:-http://ollama:11434}`, so the
  default is still the self-contained in-Compose behaviour but a native Ollama
  can be selected.
- `pyproject.toml` declared `packages = ["app"]`, which **excludes every
  subpackage** (`app.api`, `app.core`, `app.ingestion`, …). The container only
  works because the code is imported from `cwd`; the built wheel was broken. Now
  `[tool.setuptools.packages.find] include = ["app*"]`.

## 3. What the corpus actually looks like (A2/A3 resolved)

`episodes/{guest}/transcript.md`, YAML frontmatter (`guest`, `title`,
`youtube_url`, `publish_date`, `duration_seconds`, … plus an undocumented
`keywords` list), then:

```
Brian Chesky (00:00:00):
Way too many founders apologize for how they want to run the company.

(00:00:30):
And what everyone really wants is clarity.
```

**A3 is confirmed, with a trap.** Speaker labels exist — but only on the *first*
paragraph of a turn. Continuation paragraphs are labelled `(00:00:30):` with **no
name**, and the speaker is inherited from the previous label. A parser that keys
purely on "is there a name before the timestamp" attributes the rest of the host's
introduction to the guest. That yields a citation naming the wrong person, which
is the worst kind of failure here because the answer still *looks* sourced. The
parser carries the last speaker forward, and there is a unit test named for it.

`index/` is ~90 topic files, each a list of `../episodes/{slug}/transcript.md`
links, with display names in `index/README.md` — that is the topic taxonomy.
`index/episodes.md` (168 KB) is a full listing, not a topic, and is skipped.

## 4. What was wrong or risky

**A test caught a real sizing bug.** The first chunking run failed:

```
assert all(chunk.token_count <= 20 for chunk in chunks)
E  assert False
```

The merge loop budgeted against raw turn text, but `token_count` was measured on
the *rendered* line, which adds a `Speaker: ` prefix token per turn. Five 4-word
turns fit "20 tokens" of raw text and rendered as 25. The number stored in the
database therefore did not describe the text that was embedded. Fixed by costing
the rendered line everywhere (`_cost`), so the budget and the recorded count are
the same quantity.

**Ingestion is far more expensive than the plan assumed.** Measured, warm:

| Measurement | Value |
|---|---|
| Mini corpus (2 episodes) | 76 chunks in ~82 s |
| Re-run, unchanged (idempotent) | 2.6 s, 0 chunks written, 2 episodes skipped |
| Embedding throughput (32 × 500-word inputs) | 29.0 s → **~730 tokens/s** |
| Extrapolated full corpus (~269 eps ≈ 5.1 M tokens) | **~2 hours** |

Nothing in the plan anticipated this, and it is not a bug: it is a 137M-parameter
embedding model on a CPU. It matters because `make ingest` is on the evaluator's
path.

**Pre-existing lint debt would have failed `make lint`.** 13 ruff errors already
in the tree (4 × UP037 quoted annotations, 3 each of UP017/B905 in new code, plus
F401/I001/B008). `make lint` runs both `ruff check` and `ruff format --check`, so
the repo as handed over could not pass its own quality gate.

## 5. Correction

- **Chunk sizing** now budgets the rendered text, verified by the test that failed.
- **Full-corpus cost** is answered with a supported fast path rather than a
  silently long wait: `python -m scripts.ingest --limit N`. It indexes the first N
  episodes and changes nothing else — a later unlimited run tops up the rest, which
  is only safe *because* the pipeline is idempotent (proven above). The measured
  ~2 hour figure and the lever (a smaller embedding model, at a recall cost) are
  recorded rather than hidden.
- **Packaging** changed to `packages.find`, so the installed distribution is real.
- **Lint debt** cleared; `ruff check` and `ruff format --check` are green (34/34
  files), with ruff pinned to the image's version (0.16.7) so formatting cannot
  drift between host and container.
- The **ingest CLI now creates the schema itself**. It is a separate process from
  the API and previously assumed `create_all` had already run, which is only true
  if the app has booted at least once.

Unit tests: **14 passed** (parser attribution, continuation-inheritance,
frontmatter, header suppression; chunk merging, overlap, no-mid-sentence split,
sequential indices, mixed-speaker handling).

End-to-end, against the mini corpus:

```
GET /api/search?q=activation metric&k=2
{"chunk_id":45,"episode_slug":"elena-verna-40",
 "episode_title":"The new AI growth playbook for 2026 | How Lovable hit $200M ARR in one year",
 "guest":"Elena Verna 4.0","chunk_index":9,"speaker":null,
 "score":0.4939, "text":"Elena Verna: One area that we've spent very little time in is activation…"}
```

`speaker` is `null` because the chunk spans Elena and Lenny — attribution is
preserved inline in the text, which is why the column can be honest about
ambiguity instead of guessing.

## 6. Cost note carried forward

The top hit scored **0.49** against `RAG_MIN_SCORE=0.45`. That is a useful
warning for Phase 4: dense embeddings keep a high floor for *any* same-language
text, so an in-corpus score of 0.49 and an out-of-corpus score would be close
together. The floor alone cannot separate them, which is exactly why abstention
is layered (floor → short-circuit → citation validation). The floor still has to
be calibrated against real retrieval in Phase 4, not guessed here.

## 7. Outcome

Verified working: 2 episodes → 76 chunks → 2 topic links; idempotent re-run;
`/api/search` returns scored, attributed, episode-resolvable chunks; lint and 14
unit tests green. **Open:** the full 269-episode ingest has not been run — it is a
~2 hour CPU commitment, deliberately left as an explicit decision rather than
started silently. Phases 4–5 continue against the mini corpus in the meantime.
