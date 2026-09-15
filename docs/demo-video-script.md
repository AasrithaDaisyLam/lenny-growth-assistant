# Demo video — shot list and script

Deliverable 8 of the brief: **2–3 minutes, camera on**, covering the problem, the
product, **local Ollama** (not a cloud model), and **one technical trade-off**.

This is a script, not a transcript. The point of the video is judgement, so the
trade-off segment matters more than the happy path.

---

## Pre-flight (do this before recording)

```bash
docker compose ps                     # api, web, db, ollama all Up
curl -s localhost:8000/api/health/ready | jq '.status'   # "ok"
docker compose logs api --tail=5      # no error frames
```

Have the browser at `http://localhost:8080`, one **empty** session, sidebar cleared
so the demo starts from nothing. Close every other tab.

Have a second terminal ready with:

```bash
docker compose logs -f api | grep -E "turn_|citations_|pi_"
```

The logs are a feature of the demo, not clutter — they are how "the model was never
invoked" is shown rather than asserted.

---

## 0:00 — 0:20 · The problem (camera on)

> "Lenny's Podcast is 303 episodes of genuinely useful growth experience, and it's
> nearly unusable as a reference. You can't ask it a question.
>
> The obvious fix — put an LLM in front of it — has a worse failure mode than not
> having it. The model answers confidently from general knowledge, it *sounds* like
> Lenny's Podcast, and nothing in the answer tells you it was never in the corpus.
>
> So I built the assistant around that specific failure."

**Show:** nothing, or the transcript folder scrolling. Camera segment.

---

## 0:20 — 1:00 · The product: a grounded answer (screen recording)

Type in the empty session:

> How does the growth team at Lovable work on activation?

**Narrate (pick out loud, point at the screen):**

> "Watch three things. Tokens arrive progressively — that's CPU inference, so the
> first words land while the rest is still being generated. Every claim carries a
> citation. And clicking one tells me the episode, the guest, and **the speaker**,
> because that's the claim the product is actually making — 'Elena Verna said this,
> in this episode'."
>
> *(click a `[S#]` chip, let the source panel show)*
>
> "That's checkable. That's the whole point."

**Show:** the stream, a citation click, the source panel.

---

## 1:00 — 1:35 · The hard part: abstention (screen recording + logs)

Type:

> How do I file a patent for a software algorithm?

**Show the answer comes back in well under a second, and point at the log terminal:**

> "That returned almost instantly, and here's why — `turn_abstained`, and the model
> was **never invoked**. Retrieval cleared nothing against the relevance floor, so
> the agent never ran.
>
> This is the design decision I'd defend hardest. A prompt that says 'only use the
> sources' is a request. A short-circuit that returns before the model starts is a
> guarantee. The most dangerous failure — a confident answer invented from an
> uncovered corpus — is impossible by construction, not discouraged by instruction."

**Show:** the amber *Not covered by the transcripts* panel with the topic list, and
the `turn_abstained` log line. **Also point out the topics**: "it doesn't dead-end,
it says what the corpus *does* cover."

---

## 1:35 — 2:20 · The technical trade-off, stated honestly

This is the segment that earns the grade. Do not skip it and do not soften it.

> "The trade-off I want to show you is one I measured and lost.
>
> On this CPU, with a 3B model, the model produced an answer with **no citations at
> all** on **four out of four** grounded questions. Not bad citations — none. It was
> answering substantively from the retrieved passages and simply ignoring the
> instruction to cite them.
>
> I had two options. I could tune the metric, or ship a demo that only showed the
> runs where it cooperated. Both would have looked better and been dishonest.
>
> So the citation gate is a real gate: if a grounded answer comes back with sources
> available and cites none of them, it is **retried once**, and if the retry also
> fails to cite, it is **declined** — not shown to you with a caveat. An uncited
> answer is the hallucination risk in a weaker disguise: it reads as authoritative
> and it is unverifiable.
>
> The cost is real and I'll say it: it's a second model call, so it roughly doubles
> that turn's latency. And the model is one environment variable away from changing
> — which is exactly why the gate stays even if a better model makes it a no-op."

**Show, if you have the run handy:** the *Declined* outcome in a transcript, or the
`citations_missing_retrying` → `turn_completed` log pair.

---

## 2:20 — 2:40 · Close (camera on)

> "Everything here runs locally — Ollama for both embeddings and generation, no API
> key, no cloud account. There's a provider toggle for Claude, and it surfaces
> unavailable providers with the reason rather than hiding them.
>
> The golden set is thirteen questions, which is enough to calibrate a method and
> not enough to settle a threshold — the floor's derivation and its caveat are both
> written up in the repo, including the measurement that showed the classes can't be
> separated."

---

## If something fails live

Say so, and keep going — this is a demo of a system with documented failure modes,
and handling one on camera is consistent with the rest of the submission.

- **Ollama down / slow first token:** mention the model is loading; the health badge
  will say `degraded`, and the turn degrades with a named cause rather than a hang.
- **A turn takes a minute:** it is CPU-bound; that is why streaming exists.
- **The model answers without citations:** perfect — that is the segment in §1:35.
  Show the decline.

---

## What not to do

- **Do not claim citation coverage that was not measured.** Say what the sample
  actually showed.
- **Do not demo on Anthropic** and imply it is the default. The offline local path
  is the point.
- **Do not skip the trade-off** to fit the product tour in. The tour is the easy
  half; the measured failure and what was done about it is the submission.
