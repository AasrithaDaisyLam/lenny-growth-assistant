# 2026-09-13 — Phase 5: the agent loop (Pi over RPC, streaming, citations)

**Deliverable:** 6 (agent transcripts)
**Phase:** 5 — Pi extension, RPC client, process pool, sessions/messages, SSE, citation validation
**Verdict:** **PASS on the pipeline, FAIL on answer quality** — both stated plainly below. A 3B CPU
model streams, but does not follow the citation contract, and fabricated a quote.

---

## 1. Goal

Plan exit condition: *"Grounded answers stream end-to-end with resolvable citations; a
follow-up reuses context."* Both were demonstrated (§5). What also emerged is the
plan's own highest residual risk — model quality — now with evidence rather than
suspicion (§6).

## 2. What was produced

- `agent/growth-extension.ts` — two tools (`search_transcripts`, `get_episode`) as a
  thin HTTP shim to FastAPI, so retrieval keeps one implementation.
- `app/agent/pi_client.py` — subprocess RPC: LF-only JSONL framing, concurrent stderr
  drain, serialized turns, `agent_settled` as the terminator.
- `app/agent/process_pool.py` — one process per session, LRU cap + idle eviction,
  rehydration from Postgres on a fresh process.
- `app/agent/prompts.py` — grounded prompt, sources fenced as untrusted data.
- `app/services/cite_service.py` — resolves `[S#]`, strips and reports the unresolvable.
- `app/services/chat_service.py` — retrieve → short-circuit → invoke → validate → persist.
- `routes/sessions.py`, `routes/messages.py`, `routes/tools.py` (internal, token-gated),
  `scripts/agent_smoke.py` (raw event dump).
- **41 unit tests**; `ruff check` and `ruff format --check` green (50 files).

## 3. What was wrong or risky

**The read-only config mount broke Pi outright.** `PI_CODING_AGENT_DIR=/app/agent` is
mounted read-only, and Pi writes into its config directory:

```
Error: ENOENT: no such file or directory, mkdir '/app/agent/sessions/--app--'
Credential store read failed for ollama: EROFS: read-only file system, open '/app/agent/auth.json'
```

Fixed by keeping the mount as the read-only *source* (`PI_CONFIG_SOURCE`) and
materialising a writable home (`PI_AGENT_HOME=/tmp/pi-agent`) at spawn.

**`baseUrl` is not interpolated in `models.json`, and the failure was silent.** I changed
the catalog to `"${OLLAMA_BASE_URL}/v1"` to make it configuration-driven. Pi interpolates
credentials (`$VAR` in `key`/`apiKey`) but not URLs, so the provider failed with
`stopReason: "error"`, `errorMessage: "Invalid URL"` — and because **no tokens were
emitted**, the turn produced my fallback string and a `done` frame. The user would have
seen "no answer" with no cause and no error. Fixed two ways: the catalog is now generated
at spawn from `OLLAMA_BASE_URL`, and a provider error is surfaced as an SSE `error` frame
plus `provider_error` in the retrieval trace.

**A provider failure is a *message*, not an exception.** It arrives as a normal
`message_start`/`message_end` with `stopReason: "error"`. Nothing in the stream *throws*,
so exception-based handling can never see it. This is why the silent-empty-answer bug
existed; the fix is to inspect message stop reasons explicitly.

**`PI_MODEL_CONFIG` was inert.** It is not a Pi variable; the real mechanism is
`$PI_CODING_AGENT_DIR/models.json`. A knob that looks like it controls the catalog but
does nothing is worse than no knob, so it was removed rather than left in place.

**Diagnosis needed a new instrument.** The chat path reduces Pi's events to a small SSE
vocabulary by design — which is correct for the product and useless for debugging an
empty turn. `scripts/agent_smoke.py` prints the full raw event stream, and is what turned
"it returned nothing" into "Invalid URL" in one run.

## 4. Correction

All four defects fixed and verified. Notably, the `baseUrl` bug was caught **only because
a turn was executed end to end** — a static review of `models.json` looks correct, and the
docs' statement that interpolation works is true for credentials.

## 5. What was verified

**Streaming, end to end** (in-corpus question, 59 s on CPU):

```
event: usage    {"provider": "ollama", "model": "llama3.2:3b"}
event: token    {"text": "Unfortunately"}
... 144+ token frames ...
event: citations {"citations": []}
event: done     {"message_id": 4}
```

**The empty-retrieval short-circuit works, and is fast.** The follow-up *"What did I ask
you in my previous message?"* matched nothing above the floor, so the agent was **never
invoked**: `abstained` in **0.3 s**, versus 59 s for a model turn. The most dangerous
failure path is genuinely unreachable rather than merely discouraged.

**A follow-up reuses context.** `pi_reused ... turns=1` — the same process served the
third turn with no respawn and no rehydration.

**Citation validation is enforced** (8 unit tests): `[S9]` against a 1-source answer is
stripped from the stored text *and* reported in `unknown_markers`, so a fabricated
citation can neither survive nor vanish silently.

## 6. The finding that matters: the model does not follow the contract

Two runs against `llama3.2:3b`, with six retrieved sources supplied as `[S1]`–`[S6]`:

- **Citation coverage was 0.** No `[S#]` markers at all, despite that being rule 1 of the
  prompt. `citations: []`, `citation_count: 0`.
- **It answered past its sources.** Turn 1 said the material "does not provide" an answer,
  then proceeded to "infer" one anyway — declining and speculating in the same breath.
- **It fabricated a quotation.** Asked to quote a phrase from the retrieved episode, it
  produced "a quote from an episode of the AI Alignment Podcast" — a show that does not
  exist in this corpus — and admitted it did not know the source.

This is the plan's `Local model quality` risk (rated *High, likely*) materialising exactly
as predicted, and it is the single biggest threat to the demo. What the architecture does
about it is real but partial: retrieval finds the right passages, the short-circuit keeps
uncovered questions away from the model, and validation strips bad markers — but none of
those can stop a model that writes confident prose with no markers at all. Citation
coverage is precisely the metric `make eval` exists to report, and on this evidence it is
currently near zero. The documented lever is the model upgrade ladder (`qwen3:4b` →
`qwen2.5:7b`), which is one environment variable and no code change. **That is the next
experiment, and it should be run before the demo video.**

## 7. Environment note carried forward

C: fell to **0.25 GB free** during this phase. The cause was **not** Docker: its VHDX is
unchanged at 4.45 GB (a sparse file containing ~2.3 GB of images, cache, and volumes).
The consumer is `C:\pagefile.sys` at **7.19 GB allocated** (peak use 1.59 GB), grown by
Windows under memory pressure from running the whole stack — Docker VM, Postgres, and
node/Pi processes. Rebuilding the api image repeatedly also re-accumulated ~1 GB of build
cache. The ingest and api were stopped to relieve pressure. This needs a decision the
agent cannot make alone: free space on C:, cap the pagefile, or move Docker's data to D:
via the GUI (`Settings → Resources → Advanced`), which the `settings-store.json`
`DataFolder` key does **not** achieve (see the disk-exhaustion entry).

## 8. Outcome

Verified working: isolated tool surface, streaming tokens, validated citations, working
short-circuit, context reuse across turns, 41 unit tests, lint and format green. **Open:**
citation adherence with the default 3B model is unacceptable; the model-upgrade experiment
is required. The full-corpus ingest restarted and reached **27 episodes / 946 chunks**
before being paused for disk headroom.
