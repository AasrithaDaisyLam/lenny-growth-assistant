# Design

UI and interaction design for the Lenny Growth Assistant. This document covers
principles, information architecture, interaction states, responsive behaviour,
accessibility, and the decisions behind them. What the product *does* is in
`docs/PRD.md`; how it is built is in `docs/architecture.md`.

---

## 1. Design principles

**1. The citation is the product.** This is a grounded assistant, not a chatbot
with sources bolted on. If a claim cannot be traced to a passage, it should not be
on screen — so the source behind every claim is one click away and named (episode,
guest, speaker), never a footnote-style afterthought.

**2. Show the system's honesty, not just its answers.** Abstention is a first-class
screen, not an error. "Not covered by the transcripts" plus the topics it *does*
cover turns a refusal into the most trust-building moment in the product.

**3. Never show an ungrounded claim as a grounded one.** Enforced by construction:
unresolvable `[S#]` markers are stripped server-side, and an answer that could not
be grounded is declined rather than displayed with a caveat.

**4. Degradation is named.** "degraded" alone is useless. Every unavailable thing —
a provider, a dependency, an artifact element — says *what* and *why*, in the UI
or in a `title`.

**5. Streaming is a design commitment, not a progress bar.** On CPU, a turn takes
tens of seconds. Tokens appear as they are produced so the product feels alive and
is interruptible; the shell never shows a spinner where text will appear.

**6. Quiet by default.** Responsive light/dark, a slate/sky palette, no chrome that
competes with the transcript. The interface has exactly one accent colour, and it
is reserved for citations and the primary action.

---

## 2. Information architecture

One screen, two panes, plus a source panel that appears only when it has
something to show.

```
┌──────────────────────────────────────────────────────────────┐
│ ☰  The Lenny Growth Assistant      [Model ▾]  [● healthy]     │  header
├──────────────┬───────────────────────────────────────────────┤
│ New chat     │  transcript (scrolls)                         │
│ ───────────  │    user bubble ............. (right, sky)     │
│ ▸ Session A  │    assistant bubble ........ (left, card)     │
│   Session B  │      with [S1][S2] chips inline               │
│   Session C  │    tools: search_transcripts(end)             │
│              │    artifact viewer (if any)                   │
│              │    abstention / error panels                  │
│              ├───────────────────────────────────────────────┤
│              │  Sources (n)            ← appears with a turn │
│              ├───────────────────────────────────────────────┤
│              │  [ Ask about growth… ]            [ Send ]    │  composer
└──────────────┴───────────────────────────────────────────────┘
```

**Regions and their jobs**

- **Sidebar** — sessions, newest activity first, each showing its title and its
  `provider · model` so the active model is unambiguous at a glance. A session with
  no explicit title is auto-named from its first question (`SessionService`
  truncates it to 80 chars), so the list is never a column of "Untitled".
- **Transcript** — the conversation, and the only scrolling region. Assistant
  bubbles carry the latency of that turn in seconds.
- **Source panel** — the citations for whichever answer is on screen: the message
  owning the selected citation, or else all citations of the live turn. Selecting a
  citation shows episode title, guest, speaker, chunk index, and a link to the
  episode. It is absent when there is nothing to show, rather than empty.
- **Header** — the model control and the readiness badge, because both answer
  "what am I actually talking to right now?".
- **Composer** — pinned to the bottom; the send button becomes Stop while a turn
  streams.

**Why one screen.** This is a single-purpose tool with a single object of interest
(the grounded answer and its sources). Tabs, a settings page, or a separate sources
route would all add navigation to reach things that belong next to the answer.

---

## 3. Interaction states

The interface is explicit about every state a turn can be in. These are not
edge-case handling bolted on — each maps to a real, reachable outcome of the
pipeline.

| State | On screen |
|---|---|
| **Empty** | An onboarding card: what the product does, what it will cite, and that uncovered questions are declined. Not a blank pane. |
| **Retrieving** | `retrieving…` — retrieval is fast (~150 ms) and precedes the model, so this is honest and brief. |
| **Streaming** | Text grows token by token with a blinking caret; the newest text stays in view; the composer offers **Stop**. |
| **Tool call** | A quiet line — `tools: search_transcripts(end)` — so mid-turn retrieval is observability rather than a mystery pause. |
| **Cited answer** | `[S#]` rendered as tappable chips. Unmatched markers render as plain text (the server strips the ones that cannot resolve), so a chip is never dead. |
| **Abstained** | An amber panel: *Not covered by the transcripts*, followed by *It does cover: …* with the corpus's own topic names. |
| **Declined (uncited)** | The answer could not be grounded; the same abstention treatment with the reason travel in the event, not as a silently empty bubble. |
| **Degraded** | A structured error panel naming the code and the cause (provider unavailable, retrieval unavailable), alongside a persisted explanation in the transcript. |
| **Artifact** | An inline viewer: Preview/Source toggle, a version tag, and — when anything was stripped — an amber *Removed before display* list. |
| **Stopped** | The client aborts the stream and marks the turn with code `cancelled`. |
| **Loading/refused** | Readiness shows `checking…` before first response, and `API unreachable` if the catalogue call fails. |

**One deliberate asymmetry.** The live turn is cleared only *after* the persisted
transcript reloads successfully. Clearing it first would flash the answer away, and
if the reload failed it would be lost entirely.

---

## 4. Citation UX

The citation is the trust mechanism, so it gets the interaction budget.

- Markers are rendered inline as chips at the exact point of the claim.
- A chip is a button, not a link: selecting one highlights it and pins the source
  panel to that source.
- The source panel names the episode, the guest, **the speaker**, and the chunk
  index, and links to the episode. "Elena Verna said this, in this episode" is the
  claim the product is making; the panel is where it is cashed.
- Speakers are surfaced because the chunk carries them: chunk text is rendered as
  `Speaker: text`, so attribution is in the passage the model read, not inferred.

---

## 5. Feedback and trust surfaces

| Surface | What it answers |
|---|---|
| **Health badge** | Healthy / `degraded · N`, with each failing component and its error in the `title`. |
| **Provider select** | Unavailable providers stay **listed and disabled with the reason** (`anthropic — ANTHROPIC_API_KEY is not set`). Hiding them would make a configuration problem look like a missing feature. |
| **Per-turn latency** | Shown in seconds under each assistant bubble, because tens of seconds on CPU is the expected cost and the user should see it attributed. |
| **Removed elements** | The artifact sanitizer's work is displayed, not silent. A chart that quietly lost a data element is worse than being told. |
| **Tool lines** | Which tools ran, so a slow turn is attributable to retrieval or generation. |

---

## 6. Responsive behaviour

Optimised for a **360 px** viewport first, because that is the narrowest realistic
phone width and the transcript is the thing that must not be cramped.

- **Below `md` (768 px).** The sidebar becomes a full-height overlay toggled from a
  hamburger button; the transcript gets the whole width. The source panel docks
  below the transcript (`border-t`) rather than beside it, so it never steals
  horizontal space from the text.
- **At `md` and up.** The sidebar is a fixed 288 px column; the source panel becomes
  a 384 px column to the right of the transcript.
- **Layout mechanics that matter.** The shell is `h-full` with `min-h-0` on the
  flex child that must shrink — without it a flex child refuses to shrink and the
  *page* scrolls instead of the transcript, which is the classic broken-chat-layout
  bug. The composer is capped (`max-h-40`) and resizable.
- **Long content.** Cited text, session titles, and model names all truncate rather
  than overflow, and every truncated element keeps a `title` with the full value.

---

## 7. Accessibility

- **Semantics.** The sidebar is a `<nav>`, the source panel an `<aside>`, headings
  are real heading levels, and the model control is a `<label>`-wrapped `<select>`
  with an associated text span.
- **Keyboard.** The composer is a `<form>`: Enter sends, Shift+Enter inserts a
  newline. Every citation chip, session row, toggle and button is a native
  `<button>`, so tab order, focus and activation come from the platform rather than
  from handlers.
- **Labelled controls.** The hamburger carries `aria-label="Toggle sessions"` and
  `aria-expanded`; each session's delete button carries
  `aria-label="Delete <title>"`.
- **Visible focus.** The delete button is revealed on `focus` as well as on hover,
  so a keyboard user can reach it — a hover-only affordance hides it from exactly
  the users who need the label.
- **Not colour alone.** Status is carried by text (`healthy`, `degraded · N`, the
  code and message of an error), not by the pill's colour. Removed elements are
  listed by name and type.
- **Motion.** The only animation is the streaming caret, which is decorative and
  carries no information the text does not.

---

## 8. Design decisions

| Decision | Why | Trade-off |
|---|---|---|
| **Two panes, one screen** | The answer and its sources belong together; navigation would separate them | No settings page; configuration is `.env`-driven by design |
| **Source panel appears only when populated** | An always-present empty aside wastes the narrow width | The panel's location shifts slightly between states |
| **Streaming tokens, no spinner** | On CPU a turn is tens of seconds; a spinner would hide that work | The caret and partial sentences require the reader to be comfortable with in-progress text |
| **Abstention styled as information, not error** | It is a correct outcome and the best trust signal in the product | Amber is used for both abstention and degradation, distinguished by heading text |
| **Unavailable providers listed and disabled** | A hidden capability looks like a missing feature | The select has visually longer options |
| **Removed-element report** | Sanitization the user cannot audit is a control they cannot trust | Adds a panel to artifact viewing |
| **Markdown artifacts preformatted, not rendered** | Rendering means a markdown pipeline; showing the source is never unsafe | Charts/tables in markdown artifacts lose their formatting — the documented upgrade is `react-markdown` + `rehype-sanitize` |
| **Tailwind, no component library** | Eleven small components; a library would be more surface than product | Styling conventions live in class names rather than a theme file |
| **Light/dark via `dark:` variants** | Follows the OS setting with no toggle to explain | No per-user override |

---

## 9. What is deliberately minimal

- **No artifact editor.** Artifacts are generate-and-regenerate; the regenerate
  endpoint re-sanitizes the stored raw content, which is a security feature
  (tightening the allowlist re-applies to existing artifacts) rather than an
  editing feature.
- **No streaming markdown rendering.** Assistant text is rendered as plain text
  with citation chips, so no partial markdown is ever mis-parsed mid-stream.
- **No onboarding tour, no empty-state illustration, no settings screen.** The one
  paragraph of onboarding copy, the readiness badge, and the disabled-with-reason
  provider control carry the whole of the setup story.

These are judgement calls about where the remaining effort was worth more — the
citation path and the abstention path, which are the two things the product is
actually claiming.
