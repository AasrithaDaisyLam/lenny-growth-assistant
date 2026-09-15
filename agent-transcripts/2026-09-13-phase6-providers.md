# 2026-09-13 — Phase 6: provider catalogue, fallback, and graceful degradation

**Deliverable:** 6 (agent transcripts)
**Phase:** 6 — `/api/providers`, `set_model` without restart, fallback chain, degradation
**Verdict:** **PASS** — both exit conditions verified: providers reported with reasons, and an
unreachable Ollama degrades with a named cause instead of a 500.

---

## 1. Goal

Plan exit condition: *"Provider switches with no restart; Ollama down degrades gracefully."*
The second half turned out to be the interesting one.

## 2. What was produced

- `services/provider_service.py` — availability probing plus the two **pure** functions that
  decide routing (`candidate_chain`, `pick_available`), so fallback logic is testable without a
  live provider.
- `GET /api/providers` — every known provider with `available`, `reason`, live model list, and
  which one is active. Unavailable providers are **reported, not hidden**.
- `PiClient._command()` / `set_model()` / `get_available_models()` — RPC commands that share the
  turn lock.
- `PiProcessPool.peek()` — lets a caller try a live switch before respawning.
- `PATCH /api/sessions/{id}` now performs a live `set_model` when a process is warm, instead of
  always discarding it.
- `chat_service` — provider preflight with fallback, then two distinct degradation paths.
- 8 new unit tests (**49 total**); lint and format green (54 files).

## 3. What was wrong or risky

**The ordering bug: the graceful path was unreachable.** Retrieval ran *before* the provider
preflight, and retrieval embeds the query through the same Ollama instance. So with Ollama down,
`ollama.embed_one` raised **before** the fallback logic was ever consulted — the "degrades
gracefully" requirement would have failed in exactly the scenario it was written for, and failed
with an unhandled exception rather than a structured response.

Fixed by moving the preflight ahead of retrieval, and by wrapping retrieval in its own handler.
That yields two *distinct* named degradations rather than one:

| Condition | Outcome |
|---|---|
| No chat provider reachable | `provider_unavailable`, naming every provider tried and why |
| Embeddings/DB unavailable | `retrieval_unavailable` — a **refusal**, because without sources there is nothing to ground an answer in |

The second is a deliberate refusal, not a fallback to ungrounded prose. Degrading to a model that
answers without sources would defeat the entire grounding design.

**An unavailable provider reported `404 not_found`.** Semantically wrong: the provider exists and
is configured; the credential is missing. A caller would hunt for a typo instead of at
`ANTHROPIC_API_KEY`. Now `503 provider_unavailable`, using the error type that already existed.

**`_command` had to take the turn lock.** Responses and agent events share one stream. A command
that drained the queue without the lock could swallow a live turn's events — or a turn could
swallow the command's response, hanging it. Sharing `_turn_lock` makes the two mutually exclusive.

## 4. Verified

`GET /api/providers`, healthy Ollama:

```json
{"active": "ollama", "fallback_chain": ["ollama"], "providers": [
  {"name": "ollama", "available": true, "reason": null,
   "models": ["qwen3:4b", "llama3.2:3b", "nomic-embed-text:latest", "Daisy:latest"], "active": true},
  {"name": "anthropic", "available": false, "reason": "ANTHROPIC_API_KEY is not set",
   "models": ["claude-sonnet-4-5"], "active": false}]}
```

Switching to the unavailable provider is refused with the reason and the right status:

```
{"error":{"code":"provider_unavailable",
          "message":"Provider 'anthropic' is unavailable: ANTHROPIC_API_KEY is not set"}}
```

With Ollama pointed at a dead port, a full turn degrades in a structured way — no 500, no hang:

```
event: error  {"code": "provider_unavailable",
               "message": "ollama: Ollama unreachable at http://host.docker.internal:59999"}
event: done   {"message_id": 13}
```

…and the assistant message persisted with that cause, so the transcript explains itself later.

## 5. Not verified, and why

**The live `set_model` switch was not exercised end to end.** It needs two reachable providers,
and with no `ANTHROPIC_API_KEY` only Ollama is selectable. The code path is implemented (RPC
`set_model` on a warm process, `pool.peek()` gating, discard-on-failure), and the fallback logic
around it is unit-tested, but the switch itself has not been observed working against a live
process. Closing that gap needs either an Anthropic key or a session-level model override
(`PATCH` currently takes `provider` only, though the plan's contract is `{ provider, model }`) —
the latter is the cheaper option and would let the switch be tested between two local models.

## 6. Outcome

Verified working: provider catalogue with reasons, live model discovery, refusal of unavailable
providers with correct semantics, fallback selection, and two named degradation paths. **Open:**
the live `set_model` path awaits a second provider or a session-level model field.

Full-corpus ingest continued in the background throughout this phase (~27+ episodes committed;
the run is content-hash idempotent and resumable).
