"""Shared error envelope and exception handlers for the API."""

from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass
class ApiError(Exception):
    """Deterministic API error with status code and stable machine code."""

    status_code: int
    code: str
    message: str
    retryable: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    details: dict[str, object] | None = None


def install_exception_handlers(app: FastAPI) -> None:
    """Register shared exception handlers on the FastAPI app."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            headers=exc.headers,
            details=exc.details,
        )

    @app.exception_handler(HTTPException)
    async def _handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = _code_for_status(exc.status_code)
        message = _message_for_http_exception(exc)
        headers = dict(exc.headers or {})
        retryable = exc.status_code in {429, 502, 503, 504}
        details = exc.detail if isinstance(exc.detail, dict) else None
        return build_error_response(
            request=request,
            status_code=exc.status_code,
            code=code,
            message=message,
            retryable=retryable,
            headers=headers,
            details=details,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=422,
            code="invalid_request",
            message="The request parameters or body did not match the expected schema.",
            retryable=False,
            details={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=500,
            code="internal_error",
            message="The API encountered an unexpected internal error.",
            retryable=False,
            details={"exception_type": type(exc).__name__},
        )


def build_error_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    headers: dict[str, str] | None = None,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    request.state.api_error_code = code
    payload = {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
        "request_id": request_id,
    }
    if details:
        payload["error"]["details"] = details

    response_headers = {"X-Request-ID": request_id}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers=response_headers,
    )


def get_request_id(request: Request) -> str:
    """Return the request ID tracked for the current request."""

    request_id = getattr(request.state, "request_id", None)
    if request_id:
        return str(request_id)
    generated = uuid4().hex
    request.state.request_id = generated
    return generated


def _code_for_status(status_code: int) -> str:
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "invalid_request",
        429: "rate_limited",
        502: "upstream_unavailable",
        503: "service_unavailable",
        504: "upstream_timeout",
    }
    if status_code in mapping:
        return mapping[status_code]
    try:
        return HTTPStatus(status_code).phrase.lower().replace(" ", "_")
    except ValueError:
        return "http_error"


def _message_for_http_exception(exc: HTTPException) -> str:
    if isinstance(exc.detail, str) and exc.detail.strip():
        return exc.detail
    if isinstance(exc.detail, dict):
        message = exc.detail.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return "The request could not be completed."
