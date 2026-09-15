# Ship 30 for 30 — essay principles

The house style for a written piece. This file is the source of truth: it is loaded
at request time, so editing it changes the output without touching code or
restarting anything.

The pipeline — not the model — enforces the *structure* (which sections exist and
in what order). This file governs the *writing* inside each section. That split is
deliberate: a 3B CPU model cannot be trusted to produce a required section on
request, but it is perfectly capable of writing one section well when asked for
exactly one.

## Structure

Every essay has these sections, in this order:

1. **Hook** — one or two sentences. The single most surprising or useful thing.
2. **Context** — two to three sentences. Why this matters, and to whom.
3. **Main points** — three to five distinct points, each carrying a citation.
4. **Takeaway** — one concrete action the reader can take.

## Voice

- Write like a practitioner talking to a peer, not like a consultant presenting.
- Prefer the guest's own framing and phrasing over paraphrase.
- Short sentences. One idea per sentence. If a sentence has two "and"s, split it.
- Concrete over abstract: name the metric, the tactic, the number.
- No throat-clearing. Never open with "In today's fast-paced world" or
  "As we all know".

## Formatting

- Short paragraphs — one to three sentences.
- Use a bulleted list when listing three or more parallel items.
- Bold at most one phrase per paragraph, and only where it aids skimming.
- The whole piece should be readable in about two minutes.

## Grounding (non-negotiable)

- Every factual claim carries an inline citation, `[S1]`, `[S2]`, and so on.
- Never invent a quote, a statistic, or a guest. If the sources do not support a
  point, cut the point — do not soften it into vagueness.
- If the sources do not cover the topic at all, say so instead of writing around
  it. A short honest refusal is a better artifact than a fluent fabrication.

## Anti-patterns

- Listicles with no argument connecting the items.
- Restating the question as an introduction.
- Ending with a summary that repeats the piece instead of telling the reader what
  to do.
- Hedging every claim into meaninglessness ("it depends", "results may vary").
