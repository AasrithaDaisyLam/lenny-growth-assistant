# 2026-09-13 — Phase 8: artifacts, sanitization, and sandboxed rendering

**Deliverable:** 6 (agent transcripts)
**Phase:** 8 — `save_artifact`, sanitization with visible removal, sandboxed viewer, XSS suite
**Verdict:** **PASS** — 16 hostile payloads neutralized *and reported*; a `<script src>` is stripping
that the user can see.

---

## 1. Goal

Plan exit condition: *"XSS suite green; a `<script src>` is stripped visibly."*

Also carried in from Phase 7's review: the **topic-linking flush fix**, so an interrupted
ingest cannot leave the corpus indexed but the topic taxonomy empty.

## 2. What was produced

- `artifacts/sanitize.py` — nh3 allowlist plus a stdlib `HTMLParser` audit that reports what
  will be removed (elements, attributes, `on*` handlers, dangerous URL schemes).
- `services/artifact_service.py` — persists raw *and* sanitized content, versions by
  `(session, title)`, and regenerates by re-sanitizing stored raw content.
- `api/routes/artifacts.py` — create / list / get / **render** (sanitized HTML + no-egress CSP) /
  regenerate.
- `POST /internal/tools/save_artifact` (token-gated) and a third extension tool.
- Session binding: the pool passes `PI_SESSION_ID` in the subprocess env, and the extension sends
  it as `X-Session-Id`. One process serves one session, so the agent cannot write into another
  conversation.
- An `artifact` SSE event, plus `ArtifactViewer` / `SandboxFrame` in the frontend.
- `tests/xss/test_vectors.py` — 16 hostile payloads, each asserted twice: neutralized, and
  **reported**.

Totals: **49 unit tests, 37 XSS assertions**, `ruff check` + `ruff format --check` green
(61 files), frontend `tsc` + `vite build` clean.

## 3. What was wrong

**`data:text/html` survived sanitization, and the vector suite caught it.** I had allowed the
`data:` URL scheme so that self-contained charts (inline PNGs) keep working. nh3 filters URLs by
*scheme*, and `data:` is `data:` — it cannot tell an inline image from an inline HTML document. So
`<a href="data:text/html;base64,…">` passed straight through, giving a click that navigates the
frame to attacker-authored HTML.

Fixed with an explicit pre-pass that neutralizes dangerous `href`/`src` values before nh3 runs:
`javascript:`, `vbscript:`, `data:text/html`, and any `data:` URL that is not `data:image/` or
`data:font/`. This is precisely the class of bug a payload suite exists to find — reasoning about
the allowlist would not have surfaced it, because the allowlist entry was *justified*.

**The plan's assumption about nh3 was wrong.** It says the pre-pass records what was removed;
nh3 in fact reports nothing about removals, so the audit has to be a separate parse. Written that
way from the start because the assumption was checkable — but worth recording, since it means the
report and the sanitizer are two independent traversals of the same input.

**A formatter-collision of my own making.** Several edits landed against stale file contents after
a format run, and one MessageList edit collapsed a JSX line. Both were caught immediately by the
snippets the edit tool returns, and fixed.

**Import order.** `ArtifactSavedResponse` must precede `ArtifactSaveRequest` under ruff's
case-insensitive sort (`'d' < 'r'`), which is the opposite of the ASCII intuition.

## 4. Decisions worth recording

- **Two stored forms.** `raw_content` is exactly what the model produced; `sanitized_content` is
  the only thing the viewer may render. Keeping the raw form makes stripping auditable later
  instead of destroying the evidence.
- **The report is not optional.** `removed_elements` renders in the viewer. Silent stripping is a
  control nobody can audit, and an artifact that quietly loses a chart's data is worse than being
  told about it.
- **`sandbox="allow-scripts"` without `allow-same-origin`, plus `srcDoc`.** Combining those two
  flags is the specific mistake that defeats iframe sandboxing; `srcDoc` gives the frame an opaque
  origin with no cookies, storage, or parent-DOM access.
- **`script-src 'unsafe-inline'` is an accepted trade-off.** Self-contained charts need it, and it
  is contained by the sandbox. A data-exfiltrating artifact is far worse than a missing web font.
- **Regeneration re-sanitizes rather than re-generates.** Its value is not non-determinism — it is
  that the allowlist can change. When it tightens, existing artifacts can be brought forward
  without asking the model for anything.

## 5. Verified

Every vector asserted to be both neutralized and reported: `<script src>`, inline `<script>`,
`<img onerror>`, `javascript:` href, `<iframe>`, `<object>`, `<embed>`, `<svg onload>`,
CSS `url(javascript:)`, `<meta http-equiv=refresh>`, `data:text/html`, `<form action>`, `<base>`,
`<link>`, `srcdoc`, and a nested-breakout attempt.

Positive cases matter as much as negative ones, so the suite also asserts that **benign HTML
survives intact**, that **`data:image/png` is preserved** (blocking it would break the charts the
feature exists for), that `on*` removals are labelled `event_handler`, that a `<script>`'s body is
not left visible as prose, and that the audit does not mutate its input.

## 6. Not verified

**No artifact has been generated end to end by the agent.** That needs a model turn which decides
to call `save_artifact`, and with `llama3.2:3b` failing to follow even the citation contract, it is
unlikely to trigger — so the tool path is unit-and-transport verified, not observed working from a
prompt. The sandboxed iframe also remains visually unchecked, along with the rest of the Phase 7 UI.

## 7. Topic-linking fix

`_link_topics` now runs inside the existing every-25-episodes flush, not only after the loop, and
returns a total rather than a delta so the stat cannot inflate when it is called repeatedly. A run
interrupted by disk exhaustion used to leave `topics` empty, which silently emptied the abstention
"suggestions" payload. **The fix does not retroactively apply to the in-flight run** (its image is
already built); it takes effect on the next ingest, and the current run will populate topics when it
completes.
