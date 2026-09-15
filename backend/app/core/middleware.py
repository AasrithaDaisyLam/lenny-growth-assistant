"""Request middleware: correlation ids and access logging."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

log = structlog.get_logger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Attach a correlation id to every request.

    Accepts an inbound X-Request-ID so a trace can span the frontend, the API,
    and the Pi extension's tool callbacks, and echoes it back on the response.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            log.exception("request_failed", duration_ms=elapsed_ms)
            structlog.contextvars.clear_contextvars()
            raise

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        # Health probes fire constantly; don't drown the log in them.
        if not request.url.path.startswith("/api/health"):
            log.info("request_completed", status=response.status_code, duration_ms=elapsed_ms)

        structlog.contextvars.clear_contextvars()
        return response
