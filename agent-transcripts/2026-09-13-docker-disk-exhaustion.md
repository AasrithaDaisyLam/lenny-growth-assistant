# 2026-09-13 — Docker disk exhaustion on C:, and a relocation that didn't take

**Deliverable:** 6 (agent transcripts)
**Phase:** 3/4 boundary — running the full ingest
**Verdict:** **PARTIAL** — space recovered, work resumed; the requested relocation silently failed.
**Impact:** ~40 minutes lost, no data lost (ingestion is idempotent).

---

## 1. Goal

Run the full 303-episode ingest. It was deliberately sequenced *after* retrieval
was settled, so a CPU-bound two-hour job would not compete with Phase 4's
measurements.

## 2. What happened

C: went from **5.88 GB free to 0.53 GB free** while building and running the stack.
The immediate cause was Docker: `docker_data.vhdx` grew from **1.56 GB to 4.44 GB**.

`docker system df` named the components:

| Type | Size | Reclaimable |
|---|---|---|
| Images (12 total) | 2.18 GB | 1.08 GB — superseded builds, one per `compose build` |
| Build Cache (57) | 1.59 GB | ~1.59 GB |
| Volumes (3) | 93 MB | — |

Repeated `docker compose build api` cycles had accumulated a full image and a build
cache per iteration. That is the mechanism: Docker Desktop keeps every superseded
layer, and nothing had pruned them.

## 3. What was wrong or risky

**The drive was near-full with the database on it.** `pgdata` lives in the same
VHDX as everything else. At 0.53 GB, PostgreSQL could fail mid-write, so the risk
was not "the build stops" but "the volume is corrupted".

**Pruning does not return the space to Windows.** This is the non-obvious part, and
it invalidated the first fix:

```
Before prune:  Images 2.178GB  Build Cache 1.589GB  →  C: 0.53 GB free
After  prune:  Images 1.098GB  Build Cache 0.532GB  →  C: 0.53 GB free
```

Over 1.6 GB was freed *inside* the Docker image and **C: did not move at all**. A
WSL2 VHDX is a dynamically *expanding* disk: deleting blocks inside it frees space in
the guest filesystem, not in the host file, so Windows still sees the whole 4.44 GB.
The metric that looked like progress (Docker's own `system df`) was not the metric
that mattered (Windows free space).

**Stopping Docker Desktop hung.** `docker desktop stop` exceeded a 180 s timeout, and
after it, `docker info` also hung — the engine was wedged mid-stop. Waiting was the
wrong instinct; the DB is disposable and rebuildable, so the blocker was not worth
being careful about.

## 4. Correction

- **Stopped the ingest first**, before touching anything else. Because ingestion is
  content-hash idempotent, killing it costs nothing and removes an active writer from
  a nearly-full drive — the correct move under uncertainty.
- **Killed the wedged Docker processes** (`com.docker.backend`, `com.docker.build`,
  `Docker Desktop`, `docker-agent`) and ran `wsl --shutdown`. This released **~3.9 GB**
  of spool/temporary files: **C: 0.53 GB → 4.46 GB.** That was larger than all of the
  pruning, and it was the actual fix.
- **Re-verified the stack survived**: the db container came back healthy and the
  `lenny-growth-assistant-api` image was still present (660 MB), so nothing had to be
  rebuilt from scratch.

## 5. Attempted mitigation that failed, and why it is recorded

The chosen remediation was to move Docker's data to D: (262 GB free, and already
where the Ollama models live). The attempt was:

1. Stop the engine, back up `%APPDATA%\Docker\settings-store.json`.
2. Add `"DataFolder": "D:\\Docker"`.
3. Restart Docker Desktop.
4. Verify.

**Result: silently ignored.** The VHDX stayed at
`%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx`, `D:\Docker` stayed empty, and the
image and volume IDs were unchanged — so it was demonstrably the same data, not a
completed migration. `docker desktop --help` confirms the CLI exposes no setting for
the disk location. The config change was **reverted** so the user's Docker settings
are untouched.

The reliable path is the GUI: **Settings → Resources → Advanced → Disk image
location**. Recorded because a config key that appears to be accepted but is ignored
is worse than one that errors — it looks like success.

**Verified state after:** C: 4.46 GB free, db healthy, api image present, ingest
resumed with `corpus_cached` (no re-download) and 303 episodes.

## 6. Forward actions

- Prune after image-rebuild loops (`docker image prune -f`, `docker builder prune -f`).
  Cheap, and the accumulation is otherwise invisible until the drive is full.
- Watch C: during the remaining phases. Phase 7 adds a frontend image (node + a Vite
  build), which is another few hundred MB of layers on a drive with 4.46 GB.
- If headroom becomes tight again, relocate via the GUI **before** Phase 7 rather than
  under pressure.

## 7. Outcome

Space recovered (0.53 GB → 4.46 GB), the stack verified intact, and the full ingest
resumed and running. The relocation to D: remains **open** and now has a documented
cause: the settings key is ignored by Docker Desktop 4.90, so it needs the GUI.
