# 2026-09-13 — Phase 7: the React frontend

**Deliverable:** 6 (agent transcripts)
**Phase:** 7 — shell, sessions, chat, streaming, citation panel, provider control
**Verdict:** **PASS on build, serving, and the API contract. UNVERIFIED visually** — no browser
was driven, so the rendered UI and the 360px layout have not been seen.

---

## 1. Goal

Plan exit condition: *"Product usable in a browser at 360px."* The frontend must stream tokens,
show resolvable citations, expose the provider control, and survive a narrow viewport.

## 2. What was produced

Build layer: `package.json`, `tsconfig.json`, `vite.config.ts`, `index.html`,
`.dockerignore`, a multi-stage `Dockerfile` (node build → nginx runtime, **48.4 MB**), and
`nginx.conf`.

Source: `types.ts`, `api/client.ts` (fetch + SSE), `hooks/{useSessions,useProviders,useChat}.ts`,
and `components/{AppShell,SessionSidebar,ChatPanel,MessageList,Citations,Composer,ProviderControl,HealthBadge}.tsx`,
wired in `App.tsx`.

**Deliberate deviations from the plan's file list**, recorded rather than left implicit:
`ArtifactViewer` and `SandboxFrame` are deferred to Phase 8 (artifacts do not exist yet);
`CitationChip` and `SourcePanel` share one `Citations.tsx`; the hooks are three files rather
than four; and there is no `tailwind.config.js` because Tailwind v4 is configured from CSS.

## 3. Decisions worth recording

**The SSE parser splits on a blank line, not line by line.** A single `data:` payload can be
split across two network chunks, so a line-based reader would hand `JSON.parse` a truncated
document and silently drop an event. This is the same class of framing bug the Pi RPC docs warn
about, and it would present as "streaming randomly loses text".

**nginx has `proxy_buffering off` and a 900 s read timeout on `/api/sessions/`.** With buffering
on (the default), nginx accumulates the whole response and delivers it at once — the UI would
look identical to a non-streaming one, and the entire point of the SSE design would be lost in
production while working perfectly in dev.

**The live turn is cleared only after the persisted transcript reloads.** Clearing first would
flash the answer away and, if the reload failed, lose it entirely — the user would watch a
correct answer appear and then vanish.

**`useChat.send` takes an explicit session id for a new conversation.** The first message creates
the session inside the same handler, so the hook's `sessionId` prop has not re-rendered yet and
would still be `null`. Without the override the first message of every new chat would silently
no-op.

**Unavailable providers stay in the dropdown**, disabled and labelled with their reason, and the
health badge carries per-dependency failure reasons in its `title`. Both follow the same rule as
the API: a configuration problem must not look like a missing feature.

## 4. What was wrong

Two type errors in the first draft, both caught by the build gate:

- `ChatPanel` imported a `Citations` component that does not exist (the module exports
  `CitationChip` and `SourcePanel`).
- `MessageList` used `React.ReactNode` without importing the `React` namespace, which is not
  in scope under the modern `react-jsx` transform.

Neither is subtle, and both are exactly what `tsc --noEmit` exists to catch — which is why the
build script runs the type-check *before* `vite build` rather than relying on the bundler to
notice.

## 5. Verified

```
BUILD   tsc --noEmit  -> clean        vite build -> ✓ built
IMAGE   lenny-growth-assistant-web:latest  48.4 MB
GET /                     -> index.html with #root + bundled asset
GET /api/health           -> {"status":"ok", ...}            (proxied through nginx)
GET /api/providers        -> full catalogue incl. the anthropic "reason"
GET /some/deep/route      -> 200                              (SPA fallback)
```

Containers were stopped after the check; the api used for proxying was a throwaway on the
compose network with the `api` alias, so the long-running ingest was never disturbed.

## 6. Not verified, and this is the phase's main open risk

**Nothing visual was checked.** No browser was launched, so:

- the 360 px layout is **unverified** — it is built for it (viewport meta, overlay sidebar below
  `md`, full-width transcript) but not seen;
- live token streaming **in the browser** is unverified end to end (the parser is exercised, the
  transport is verified, but the two have not been watched together);
- citation-chip interaction and the source panel are unverified.

This should be closed with a browser screenshot at 360 px before the demo video, and it is the
one Phase 7 claim I would not make. The honest summary is: it builds, it serves, it proxies, and
its API contract is correct — but "usable in a browser" has not been demonstrated.

## 7. Outcome

Frontend builds clean, is served by nginx, proxies the API including the streaming route's
configuration, and falls back to the SPA for unknown paths. Full-corpus ingest continued in the
background throughout and was not interrupted.
