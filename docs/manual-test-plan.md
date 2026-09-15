# Manual test plan

Automated coverage lives in `make test`. These are the checks that need a human, a
deliberately broken dependency, or a browser — the things `pytest` cannot do for us.

Ordered so the cheapest failures surface first.

---

## 1. Stack starts from a clean clone

```bash
cp .env.example .env
make up            # or: docker compose up --build -d
make ingest        # pulls models, then indexes the corpus
open http://localhost:8080
```

**Expected:** the UI loads, the health badge reads *healthy*, and the sidebar is empty.

**If ingestion is slow:** it is CPU-bound at roughly 730 embedding tokens/s, so a
full 303-episode run is on the order of two hours. `python -m scripts.ingest --limit 25`
gives a working corpus in a few minutes; a later unlimited run tops it up, because
ingestion is content-hash idempotent.

---

## 2. A grounded answer cites real episodes

Ask: *"How does the growth team at Lovable work on activation?"*

**Expected:** tokens stream in progressively (not all at once at the end), the answer
carries `[S#]` chips, and clicking one opens the source panel naming the episode,
guest, and speaker.

**Watch for:** a `[S#]` chip that resolves to nothing. Unresolvable markers are
stripped server-side and logged as `citations_unresolved`, so a *missing* chip is
expected behaviour and a *dead* chip is a bug.

---

## 3. An uncovered question is declined, with alternatives

Ask: *"How do I file a patent for a software algorithm?"*

**Expected:** the answer declines, and lists topics the corpus *does* cover. Pi is
never invoked for this turn, so it returns in well under a second.

**Check the log for `turn_abstained`** with `best_similarity` below
`RAG_MIN_SCORE`. If the abstention is *wrong* (the corpus does cover it), re-derive
the floor with `make calibrate` — the number was calibrated on a small corpus and is
expected to move.

---

## 4. Follow-up reuses context

In the same session, ask a retrieval-matching follow-up, e.g.
*"Quote the exact phrase about the habit loop."*

**Expected:** the log shows `pi_reused ... turns=1` — the same process, not a respawn.
A fresh process would log `pi_spawned` and the reply would rehydrate from Postgres.

---

## 5. Ollama down degrades gracefully

```bash
docker compose stop ollama          # or stop native Ollama
# ask any question in the UI
```

**Expected:** an `error` frame with code `provider_unavailable` naming the cause, the
turn ends cleanly with `done`, and the transcript stores an explanation. **No 500, no
hang.**

Restart Ollama and confirm the next turn works without restarting the API.

---

## 6. Database down is reported, not fatal

```bash
docker compose stop db
curl -s localhost:8000/api/health       # liveness: still ok
curl -s localhost:8000/api/health/ready # readiness: database not ok, with the error
```

**Expected:** liveness answers, readiness names the database as the culprit. The API
does not crash-loop.

---

## 7. Streaming is not buffered by the proxy

```bash
curl -N -X POST localhost:8080/api/sessions/<id>/messages \
  -H 'Content-Type: application/json' -d '{"content":"hello"}'
```

**Expected:** frames appear **as they are produced**. If the whole response lands at
once, `proxy_buffering off` has been lost from `frontend/nginx.conf` — the UI would
look identical and the streaming design would be silently defeated in production
while working in dev.

---

## 8. A hostile artifact is stripped, visibly

Ask for a chart, or post one directly:

```bash
curl -X POST localhost:8000/api/sessions/<id>/artifacts \
  -H 'Content-Type: application/json' \
  -d '{"kind":"html","title":"Hostile","content":"<script src=\"https://evil.example/x.js\"></script><p>chart</p>"}'
```

**Expected:** the artifact viewer shows a *Removed before display* panel listing
`script (element)`, the preview renders `chart` and nothing else, and
`GET /api/artifacts/{id}/render` returns a `Content-Security-Policy` header
beginning `default-src 'none'`.

**Then confirm the sandbox:** inspect the preview iframe in devtools. It must have
`sandbox="allow-scripts"` and must **not** have `allow-same-origin`. Those two
together are the specific mistake that defeats iframe sandboxing.

---

## 9. Provider switching needs no restart

```bash
curl -s localhost:8000/api/providers | jq
```

**Expected:** Ollama listed as available with its real model list; Anthropic listed
as **unavailable with the reason** (`ANTHROPIC_API_KEY is not set`) rather than
hidden. Switching to it is refused with that reason and a 503.

With a key configured, `PATCH /api/sessions/{id}` with a new provider should log
`provider_switched_live` and keep the warm conversation — no restart.

---

## 10. Model quality, measured

```bash
make eval
```

**Expected:** retrieval recall, the two similarity distributions, abstention
correctness, citation coverage over persisted answers, and latency percentiles.

**Read the citation line carefully — it counts only genuine grounded answers.** A
turn that abstained, failed, timed out, degraded, or was smalltalk could not carry
a citation, so it is excluded; counting it would report the system's own refusals as
citation gaps. A clean reading is reported alongside the count of grounded turns
that were *declined* for want of a citable answer, so "100%" is never mistaken for
"no such turns happened".

**This is the honest number for the demo.** On the reference CPU with
`llama3.2:3b`, the model produced a substantive but **citation-free** first attempt
on **4 of 4** grounded turns in the reference sample. That is a real weakness in the
model's instruction-following, and it is why the citation gate exists rather than
being a nicety: the gate caught every one — three were rewritten with citations on
the retry, and one question that could not be grounded on either attempt was
**declined** instead of shown as sourced (ADR-006). Retrieval, the short-circuit,
and citation validation all behaved correctly throughout.

The documented quality lever is `OLLAMA_MODEL` (`qwen3:4b` → `qwen2.5:7b`). Re-run
`make eval` after changing it and report the new number — the harness exists so a
reviewer can re-measure after changing one environment variable, not so a number can
be hit by tuning the metric.

---

## Known limitations to state, not hide

- **Retrieval latency is CPU-bound**: ~140 ms p50 for retrieval, tens of seconds for
  a generated turn.
- **One Pi process per session**, LRU-capped and idle-evicted. Eviction loses
  in-memory context only; Postgres is the source of truth and the thread is replayed
  on the next turn.
- **The essay path does not replay history** for a fresh process, so an essay after
  an eviction loses thread continuity.
- **`create_all` instead of migrations** — correct for a take-home; Alembic is the
  first thing to add for a real deployment.
- **The relevance floor is an operating point, not a calibrated constant.** It was
  re-derived on the full corpus (303 episodes) and the two classes still
  **overlap** — the worst out-of-corpus question scores 0.649 against a best
  in-corpus 0.647 — so 0.65 was chosen to favour abstention rather than to separate
  them. It costs one covered question and rejects all six uncovered ones. The golden
  set is still 13 questions and must be widened before the number is treated as
  settled (`docs/decisions.md`, ADR-003); `make calibrate` is the tool.
