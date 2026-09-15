"""
Error handling.

Every failure becomes a structured problem object carrying the correlation id, so
a user-facing error can be matched to a log line without guesswork. Resilience is
an explicit requirement: missing keys, an unreachable Ollama, model timeouts,
empty retrieval, and database failures each degrade to a defined response rather
than a bare 500.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger("app.errors")


class AppError(Exception):
    """Base class for errors that carry a deliberate HTTP mapping."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, **detail: Any) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.detail = detail


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found."


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"
    message = "Request validation failed."


class ProviderUnavailableError(AppError):
    """The selected model provider is unreachable or misconfigured."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "provider_unavailable"
    message = "The selected model provider is unavailable."


class RetrievalError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "retrieval_unavailable"
    message = "Retrieval is temporarily unavailable."


class DatabaseUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "database_unavailable"
    message = "The database is unavailable."


class ModelTimeoutError(AppError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "model_timeout"
    message = "The model did not respond in time."


class ArtifactError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "artifact_error"
    message = "The artifact could not be processed."


def _problem(
    request: Request, status_code: int, code: str, message: str, **extra: Any
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if extra:
        body["error"]["detail"] = extra
    return JSONResponse(status_code=status_code, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "app_error",
            code=exc.code,
            status=exc.status_code,
            message=exc.message,
            **exc.detail,
        )
        return _problem(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        log.info("request_validation_failed", errors=exc.errors())
        return _problem(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed.",
            errors=exc.errors(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(
            request,
            exc.status_code,
            f"http_{exc.status_code}",
            str(exc.detail),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return _problem(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "An unexpected error occurred.",
        )
