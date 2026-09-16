"""HTTP-level safety for RepoLens (Phase 9).

* ``request_id_for`` / middleware: every response carries a stable
  ``X-Request-ID`` so a failure can be correlated server-side without leaking
  internals to the client.
* ``SecurityHeadersMiddleware``: safe defaults for a dev/API-only backend.
* ``install_error_handlers``: converts unexpected ``Exception`` and Pydantic
  validation failures into categorized, redacted JSON with no stack traces,
  filesystem paths, or request bodies.

Category values: ``INVALID_INPUT``, ``LIMIT_EXCEEDED``,
``PROVIDER_UNAVAILABLE``, and ``INTERNAL_ERROR``, plus the transport
statuses produced by existing route handlers (400/404/409/413/422/502/503).
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("repolens.http")

REQUEST_ID_HEADER = "x-request-id"


def request_id_for(request: Request) -> str:
    """Return the request's correlation id, generating one on demand."""
    existing = getattr(request.state, "request_id", None)
    if existing:
        return existing
    rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
    request.state.request_id = rid
    return rid


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative security headers for a browser-facing JSON API."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


def _error_body(request: Request, category: str, message: str) -> dict[str, object]:
    return {
        "error": category,
        "message": message,
        "request_id": request_id_for(request),
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> object:
        # Keep the default validation body (tests and clients rely on it) and
        # add the correlation id and category on top. Errors never echo the
        # offending raw body, only Pydantic's field-level summary.
        detail = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", []))
            detail.append(f"{loc}: {err.get('msg', 'invalid')}")
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=422,
            content={
                "detail": detail,
                "error": "INVALID_INPUT",
                "message": "Request validation failed.",
                "request_id": request_id_for(request),
            },
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception) -> object:
        rid = request_id_for(request)
        logger.error(
            "unhandled error request_id=%s: %s", rid, exc, exc_info=True
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=500,
            content=_error_body(
                request,
                "INTERNAL_ERROR",
                "An unexpected internal error occurred. See server logs "
                f"(request id {rid}).",
            ),
        )