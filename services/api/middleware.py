"""Request-context middleware for the API runtime."""

from __future__ import annotations

import json
import logging
import time
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger("services.api")


class RequestContextMiddleware:
    """Pure ASGI middleware for request IDs and structured request logging."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id", "").strip() or uuid4().hex
        state["request_id"] = request_id

        start = time.perf_counter()
        status_code = 500

        async def send_with_context(message: Message) -> None:
            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            app = scope.get("app")
            settings = getattr(getattr(app, "state", None), "api_settings", None)
            if settings is not None and settings.api_request_logging_enabled:
                caller = state.get("api_caller")
                log_payload = {
                    "event": "api_request",
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000.0, 2),
                    "caller_subject": getattr(caller, "subject", None),
                    "caller_tier": getattr(caller, "tier", None),
                    "error_code": state.get("api_error_code"),
                }
                logger.info(json.dumps(log_payload, sort_keys=True))


def install_request_context_middleware(app) -> None:
    """Install request ID propagation and structured request logging."""

    app.add_middleware(RequestContextMiddleware)
