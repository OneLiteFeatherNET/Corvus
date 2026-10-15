"""RFC 9457 problem details (``application/problem+json``).

Validation errors never echo the offending input: it may contain clear player UUIDs, tokens or notes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("corvus.api")

PROBLEM_JSON = "application/problem+json"
_TYPE_BASE = "https://docs.onelitefeather.net/corvus/problems/"

_TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Payload too large",
    422: "Unprocessable content",
    500: "Internal server error",
    503: "Service unavailable",
}


class ProblemError(Exception):
    """Raise anywhere in a request to produce a problem response."""

    def __init__(
        self,
        status: int,
        detail: str,
        *,
        slug: str | None = None,
        headers: dict[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.slug = slug
        self.headers = headers
        self.extensions = extensions


def problem_body(
    status: int, detail: str, slug: str | None = None, instance: str | None = None, **ext: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": _TYPE_BASE + slug if slug else "about:blank",
        "title": _TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
    }
    if instance:
        body["instance"] = instance
    body.update(ext)
    return body


def _response(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        body = problem_body(exc.status, exc.detail, exc.slug, request.url.path, **exc.extensions)
        return _response(exc.status, body, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        body = problem_body(exc.status_code, detail, None, request.url.path)
        return _response(exc.status_code, body, dict(exc.headers) if exc.headers else None)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "loc": [str(p) for p in e.get("loc", ())],
                "msg": str(e.get("msg", "invalid")),
                "type": str(e.get("type", "")),
            }
            for e in exc.errors()
        ]
        body = problem_body(
            422, "request validation failed", "validation", request.url.path, errors=errors
        )
        return _response(422, body)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the type only: messages of driver errors can contain parameter values.
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        body = problem_body(500, "internal error", None, request.url.path)
        return _response(500, body)
