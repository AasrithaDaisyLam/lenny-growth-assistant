# =============================================================================
# The Lenny Growth Assistant -- agent transcript log
#
# DELIVERABLE 6 of the take-home brief.
#
# The brief asks for "coding-agent transcripts/logs in a dedicated folder,
# including failed attempts and how you corrected them."
#
# =============================================================================

## What belongs here

Raw logs of the AI coding agents used to build this system, kept as evidence of
*judgement* rather than as a highlight reel. The brief is explicit that the
evaluation is about "your judgment and ability to direct, verify, and improve
AI-assisted work -- not whether every line was typed manually."

That means the failures are the valuable part. A transcript that only shows
things going right demonstrates nothing about direction or verification.

## Naming

    YYYY-MM-DD-<short-topic>.md

Examples:

    2026-09-13-phase1-pi-ollama-spike.md
    2026-09-13-ingestion-chunking-failure.md
    2026-09-14-artifact-sanitizer-bypass.md

## What each entry should contain

1. **Goal** -- what the agent was asked to do, and why.
2. **What the agent produced** -- the actual output or a faithful excerpt.
3. **What was wrong or risky** -- verification step, and what it caught.
4. **Correction** -- what was changed and what the fix demonstrates.
5. **Outcome** -- verified working, reverted, or still open.

## Secrets

Before committing anything here, strip:

- API keys (`sk-ant-...`, `sk-...`) and bearer tokens
- The 40-character hex account identifiers in provider log lines
- Absolute local paths containing a username
- Any transcript text pasted from the corpus that is not already public

`make scan-secrets` runs a pattern check over this folder and the repo.

## Provenance

The primary source for these logs is the Command Code session that produced the
implementation, exported per phase. Entries are added **as each phase is
completed**, not reconstructed at the end -- a retro-fitted log would defeat the
purpose, since it could not show a mistake discovered and corrected in flight.
