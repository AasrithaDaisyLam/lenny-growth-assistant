"""
Pi Coding Agent RPC client.

Pi is spawned as a subprocess and driven over JSONL on stdin/stdout. Three
protocol details are load-bearing and easy to get subtly wrong:

1. **Framing is LF-only.** The docs are explicit that generic line readers are
   not compliant, because `U+2028`/`U+2029` are valid inside JSON strings. So we
   split on `b"\\n"` ourselves and strip a trailing `\\r`, rather than using
   `readline`.
2. **stderr must be drained concurrently.** Pi writes to stderr; if nobody reads
   it, the pipe buffer fills and the process deadlocks mid-turn. That is a hang
   with no error message, which is the worst failure mode for a demo.
3. **One turn at a time per process.** Responses and events share the stream, so
   turns are serialized on a lock and responses are consumed internally by the
   generator rather than leaking into the event stream.

`agent_settled` (not `agent_end`) terminates a turn: `agent_end` fires per
low-level run and may be followed by a retry or a queued continuation, so
stopping there would truncate a turn that was still going.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger("app.agent.pi")

# A turn is done only when Pi says nothing further will happen on its own.
_SETTLED = "agent_settled"


class PiError(RuntimeError):
    """Pi rejected a command, or the protocol returned an error."""


class PiProcessExited(PiError):
    """The subprocess died. The pool will discard and respawn it."""


class PiTimeout(PiError):
    """No turn settlement within the configured ceiling."""


def _point_catalog_at_ollama(home: Path) -> None:
    """
    Rewrite the catalog's Ollama `baseUrl` from configuration.

    Pi interpolates credentials (`$VAR` inside `key`/`apiKey`) but **not**
    `baseUrl`: setting `"${OLLAMA_BASE_URL}/v1"` resolves to the literal string and
    the provider fails with "Invalid URL". So the mounted `models.json` is a
    template and the effective catalog is written here, once per spawn, from
    `OLLAMA_BASE_URL`. Re-writing every time means changing the env var is enough
    -- no rebuild, no manual edit.
    """
    path = home / "models.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("pi_catalog_unreadable", error=str(exc))
        return

    providers = data.get("providers")
    if not isinstance(providers, dict):
        return

    ollama = providers.get("ollama")
    if isinstance(ollama, dict):
        ollama["baseUrl"] = f"{settings.ollama_base_url.rstrip('/')}/v1"

    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("pi_catalog_write_failed", error=str(exc))


def ensure_agent_home(force: bool = False) -> str:
    """
    Materialize a *writable* Pi config home from the read-only mounted config.

    Pi writes into its config directory (it opens `auth.json`, and creates a
    session directory) even when those features are unused, so pointing
    `PI_CODING_AGENT_DIR` straight at the read-only mount fails at startup with
    `EROFS: read-only file system`. Copying the catalog into a writable location
    keeps the mounted config authoritative while letting Pi do its bookkeeping.

    Idempotent: only files that are missing or newer than the copy are refreshed,
    so this is safe to call before every spawn.
    """
    source = Path(settings.pi_config_source)
    home = Path(settings.pi_agent_home)
    home.mkdir(parents=True, exist_ok=True)

    if not source.is_dir():
        log.warning("pi_config_source_missing", path=str(source))
        return str(home)

    for entry in source.iterdir():
        if not entry.is_file():
            continue
        target = home / entry.name
        try:
            if force or not target.exists() or entry.stat().st_mtime > target.stat().st_mtime:
                shutil.copy2(entry, target)
        except OSError as exc:  # noqa: PERF203 -- per-file failure must not abort startup
            log.warning("pi_config_copy_failed", file=entry.name, error=str(exc))

    _point_catalog_at_ollama(home)
    return str(home)


class PiClient:
    """One `pi --mode rpc` process, one conversation."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        extension_path: str | None = None,
        tools: list[str] | None = None,
        session_id: str | None = None,
    ) -> None:
        self.provider = provider or settings.llm_provider
        self.model = model or settings.model_for(self.provider)
        self.extension_path = (
            extension_path if extension_path is not None else settings.pi_extension_path
        )
        self.tools = (
            tools if tools is not None else ["search_transcripts", "get_episode", "save_artifact"]
        )
        # Handed to the extension so a saved artifact is attributed to this session.
        # One process serves one session, so the agent cannot write to another.
        self.session_id = session_id

        self._process: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._turn_lock = asyncio.Lock()
        self._started = False

    # --- Lifecycle -----------------------------------------------------------

    def _build_command(self) -> list[str]:
        """
        The isolation flags are the security boundary, not decoration.

        `--no-builtin-tools` removes `read`/`bash`/`edit`/`write` so a coding agent
        cannot touch the filesystem, and `--tools` narrows the surface to exactly
        our two extension tools. Everything else is disabled so host or image
        configuration cannot leak in and change behaviour.
        """
        command = [
            settings.pi_bin,
            "--mode",
            "rpc",
            "--no-session",
            "--provider",
            self.provider,
            "--model",
            self.model,
            "--no-builtin-tools",
            "--tools",
            ",".join(self.tools),
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-themes",
            "--no-approve",
            "--offline",
        ]
        if self.extension_path:
            command.extend(["-e", self.extension_path])
        return command

    async def start(self) -> None:
        if self._started:
            return
        command = self._build_command()
        log.info("pi_spawn", provider=self.provider, model=self.model, tool_count=len(self.tools))

        # Pi needs a writable config dir; the mounted catalog is read-only.
        env = dict(os.environ)
        env["PI_CODING_AGENT_DIR"] = ensure_agent_home()
        if self.session_id:
            env["PI_SESSION_ID"] = self.session_id

        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise PiError(f"pi executable not found: {settings.pi_bin}") from exc

        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._started = True

    async def stop(self) -> None:
        self._started = False
        process = self._process
        if process is None:
            return
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        if process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
        self._process = None
        log.info("pi_stopped")

    @property
    def is_alive(self) -> bool:
        return self._started and self._process is not None and self._process.returncode is None

    def matches(self, provider: str | None, model: str | None) -> bool:
        """Whether this process is already configured for the requested provider/model."""
        if provider is not None and provider != self.provider:
            return False
        return model is None or model == self.model

    # --- Stream plumbing -----------------------------------------------------

    async def _read_stdout(self) -> None:
        """Parse LF-delimited JSON. Never uses readline (see module docstring)."""
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        buffer = b""
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.endswith(b"\r"):
                        line = line[:-1]
                    if not line.strip():
                        continue
                    try:
                        await self._queue.put(json.loads(line.decode("utf-8")))
                    except json.JSONDecodeError:
                        # A malformed record must not kill the reader; the turn
                        # will time out and surface a clear error instead.
                        log.warning("pi_bad_json", line=line[:200].decode("utf-8", "replace"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("pi_reader_failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            # Sentinel so any in-flight turn fails fast instead of hanging.
            await self._queue.put(None)

    async def _drain_stderr(self) -> None:
        """Read stderr so the pipe cannot fill and deadlock the process."""
        assert self._process is not None and self._process.stderr is not None
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    log.debug("pi_stderr", line=text[:500])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        if self._process.returncode is not None:
            raise PiProcessExited(f"pi exited with code {self._process.returncode}")
        self._process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._process.stdin.drain()

    # --- Turns ---------------------------------------------------------------

    async def prompt(
        self, message: str, timeout: float | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Send a prompt and yield Pi's events until the turn settles.

        Command responses are consumed here rather than yielded, so callers only
        see agent events. A rejected prompt raises `PiError` immediately.
        """
        await self.start()
        ceiling = timeout or float(settings.request_timeout_seconds)

        async with self._turn_lock:
            request_id = str(uuid.uuid4())
            await self._send({"id": request_id, "type": "prompt", "message": message})

            deadline = asyncio.get_running_loop().time() + ceiling
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise PiTimeout(f"pi did not settle within {ceiling:.0f}s")
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except TimeoutError as exc:
                    raise PiTimeout(f"pi did not settle within {ceiling:.0f}s") from exc

                if item is None:
                    raise PiProcessExited("pi stdout closed mid-turn")

                kind = item.get("type")

                if kind == "response":
                    # Only our own request is interesting; startup chatter is not.
                    if item.get("id") == request_id and item.get("success") is False:
                        raise PiError(str(item.get("error") or "pi rejected the prompt"))
                    continue

                if kind == "extension_error":
                    log.error(
                        "pi_extension_error",
                        extension=item.get("extensionPath"),
                        event=item.get("event"),
                        error=item.get("error"),
                    )

                yield item

                if kind == _SETTLED:
                    return

    # --- Commands ------------------------------------------------------------

    async def _command(self, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """
        Send a non-prompt command and await its matching response.

        Takes the same turn lock as `prompt`, so a command can never interleave
        with an in-flight turn and steal its events.
        """
        await self.start()
        request_id = str(uuid.uuid4())

        async with self._turn_lock:
            await self._send({**payload, "id": request_id})
            deadline = asyncio.get_running_loop().time() + timeout

            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise PiTimeout(
                        f"pi did not respond to {payload.get('type')} in {timeout:.0f}s"
                    )
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except TimeoutError as exc:
                    raise PiTimeout(
                        f"pi did not respond to {payload.get('type')} in {timeout:.0f}s"
                    ) from exc

                if item is None:
                    raise PiProcessExited("pi stdout closed while awaiting a response")
                if item.get("type") != "response" or item.get("id") != request_id:
                    # Not ours: a stray event, or a response to another request.
                    continue
                if item.get("success") is False:
                    raise PiError(str(item.get("error") or f"{payload.get('type')} failed"))
                return item

    async def set_model(self, provider: str, model_id: str) -> dict[str, Any]:
        """
        Switch the live process's model over RPC, with no restart.

        Used when a session's provider changes so the warm conversation survives
        the switch. On failure the caller discards the process instead, and the
        next turn spawns cleanly with the new model.
        """
        response = await self._command(
            {"type": "set_model", "provider": provider, "modelId": model_id}
        )
        self.provider = provider
        self.model = model_id
        log.info("pi_model_switched", provider=provider, model=model_id)
        return response.get("data") or {}

    async def get_available_models(self, timeout: float = 30.0) -> list[dict[str, Any]]:
        response = await self._command({"type": "get_available_models"}, timeout=timeout)
        data = response.get("data") or {}
        return list(data.get("models") or [])
