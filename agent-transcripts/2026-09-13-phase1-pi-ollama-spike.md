# 2026-09-13 — Phase 1: Pi Coding Agent ↔ Ollama spike

**Deliverable:** 6 (agent transcripts)
**Phase:** 1 — risk gate before any application code
**Verdict:** **PASS** — Pi RPC is viable as the single agent loop. Proceed.
**Model:** `llama3.2:3b` (Ollama, CPU-only, Intel i5-1240P)
**Pi version:** 0.85.1

---

## 1. Goal

The entire plan hinges on one unproven assumption (A1 in the plan's risk table):

> Can `pi --mode rpc` complete a **tool-using** turn against a local Ollama model,
> driven from a Python subprocess, with token-level streaming?

If no, the fallback was the Claude Agent SDK or a plain Anthropic-SDK loop, which
would have been a multi-hour rewrite of the agent layer. So this was sequenced
**first**, before a single line of application code, precisely so a failure would
be cheap to absorb.

Two secondary questions mattered for the product:

1. Is a warm second turn materially faster than the cold first one?
2. Does a single Pi process retain conversation context across turns?

## 2. Setup

Environment discovery produced two findings that changed the plan before any code
was written:

| Finding | Consequence |
|---|---|
| **Docker was not installed** (not on Windows, not in WSL) | Compose deliverable cannot be locally verified yet; Ollama is native, so the spike did not need to wait |
| **C: had 5.3 GB free; D: had 266 GB** | Relocated the Ollama model store to `D:\ollama\models` via `OLLAMA_MODELS`; C: went to **7.47 GB** free |

Models pulled: `llama3.2:3b` (2.0 GB) and `nomic-embed-text` (274 MB).

The spike itself was three files:

- `models.json` — Ollama declared as a native `openai-completions` provider
- `spike-extension.ts` — one registered tool, `search_transcripts`, returning
  **canned** fixtures (the point was to prove transport, not retrieval)
- `spike_client.py` — spawns `pi --mode rpc`, writes JSONL to stdin, reads events from stdout

The isolation flags are the load-bearing part:

```
pi --mode rpc --no-session --provider ollama --model llama3.2:3b \
   --no-builtin-tools --no-extensions --no-skills --no-prompt-templates \
   --no-context-files --no-themes \
   -e spike-extension.ts --tools search_transcripts
```

`--no-builtin-tools` is what removes `read`/`bash`/`edit`/`write` from a *coding*
agent so it can be used as a RAG agent. `--tools search_transcripts` then narrows
the surface to exactly one tool of our own.

## 3. What the agent produced

Turn 1 — tool called, answer grounded and correctly cited:

```
>>> Search the transcripts for what operators say about choosing an activation
    metric, then answer in at most 3 sentences and cite sources inline like [S1].

[tool] search_transcripts args={"query": "operating system activation metric choosing"}
[first token 6.56s] When choosing an activation metric, operators suggest
prioritizing retention over acquisition, as retaining users is crucial for
long-term growth [S2]. It's also recommended to pick a single, critical metric
that informs growth strategy [S3]. Meanwhile, Elena Verna emphasizes the
importance of considering the sequence of user activation, which may not be a
single event [S1].
```

Turn 2 — follow-up in the same process:

```
>>> Which guest said that retention is the only metric that matters, and what
    was the exact phrase?

[tool] search_transcripts args={"query": "retention is the only metric that matters"}
[first token 7.16s] Casey Winters said that "Retention is the only metric that
matters for long-term growth" [S2].
```

## 4. What was wrong or risky

**The tool-calling behaviour is correct but more expensive than it looks.**
On turn 2 the model had the answer to "which guest said that?" already in context
from turn 1 — and re-called `search_transcripts` anyway rather than answering from
memory. Correctness is unaffected and grounding arguably improves, but every
follow-up turn pays a retrieval round-trip and a larger prompt. Worth knowing
before writing cost assumptions.

**A first run reported a 17.74 s time-to-first-token.** That number was alarming
enough to nearly trigger the fallback path. It was not a transport problem.

**My own verification check was wrong.** The second spike compared "warm turn 2"
against "cold turn 1" and asserted `t2.ttft < t1.ttft`. It reported:

```
PASS  turn 1 called search_transcripts
PASS  turn 1 streamed an answer
PASS  turn 1 settled cleanly
PASS  turn 2 answered (context retained)
FAIL  turn 2 warm TTFT < cold TTFT
VERDICT: PARTIAL
```

The premise was false. Turn 1 in that run was *already warm*, because the previous
run had left the model resident via `OLLAMA_KEEP_ALIVE`. Comparing two warm turns
and labelling the difference a cold/warm effect measured noise (6.56 s vs 7.16 s)
and manufactured a failure.

## 5. Correction

The 17.74 s figure was diagnosed rather than worked around: it was **cold model
load**, not per-turn cost. The evidence is the gap between runs — the same prompt
and model produced 17.74 s on a cold model and 6.56 s once resident.

The mislabelled check was corrected by reasoning about what the comparison could
actually support. The honest measurement is:

| Condition | TTFT | Total |
|---|---|---|
| Cold (model not resident) | 17.74 s | 24.60 s |
| Warm (model resident) | 6.56 s | 12.40 s |
| Warm, follow-up turn | 7.16 s | 9.05 s |

**Warm TTFT is ~6.5–7 s and does not improve further with warmth.** It is dominated
by prompt prefill of the system prompt, tool definitions, and retrieved context on
CPU — not by generation. That is a real ceiling of this hardware, and it is the
number the README and architecture doc must quote.

This has a direct design consequence: **the "3 s time-to-first-token" target is not
achievable on this reference machine with a tool-bearing prompt.** Rather than
quietly restate the target or cherry-pick a favourable run, the measured figure is
carried forward as measured, and the documented upgrade path is a smaller prompt or
a better machine.

## 6. What this proves

- Pi can be used as a **model-agnostic RAG agent**: tool use, streaming, and
  multi-turn context all work against a 3B local model with no API key.
- The tool surface can be **reduced to a single extension tool**, with every
  built-in capability removed. This is a configuration guarantee, not a prompt
  one — and it is the enforcement mechanism for "answers come only from the
  transcripts".
- Pi loads TypeScript extensions directly through jiti; `typebox` and the
  `@earendil-works/pi-coding-agent` type import both resolved with no build step.
- The RPC event contract (`tool_execution_start`, `message_update` with
  `text_delta`, `agent_settled`) maps cleanly onto the SSE event union the
  frontend will consume.

## 7. Follow-ups carried into later phases

1. **`OLLAMA_KEEP_ALIVE` is not optional.** Without it, every first turn pays
   ~11 s of model load. Already set in `docker-compose.yml` and `.env.example`.
2. Measure the *real* prompt size once the grounded-QA system prompt and six
   retrieved chunks are in place; the ~6.5 s baseline will grow.
3. Test `qwen3:4b` against the same harness — the model catalog already includes it,
   and better tool adherence is the main quality lever available on this hardware.
4. Confirm behaviour when Ollama is stopped mid-session (resilience requirement),
   which the spike did not cover.
