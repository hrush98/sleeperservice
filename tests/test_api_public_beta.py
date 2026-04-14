import json
import logging
from types import SimpleNamespace

import anyio
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from services.api.analysis_v0 import MarketNotFoundError
from services.api.auth import ApiCaller
from services.api.config import ApiSettings
from services.api.dependencies import enforce_public_api_access
from services.api.errors import ApiError, build_error_response
from services.api.middleware import RequestContextMiddleware
from services.api.rate_limit import InMemoryRateLimiter
from services.api.routers.analysis_v0 import get_market_analysis
from services.api.routers.health import health


def test_public_beta_requires_api_key():
    request = _build_request(
        ApiSettings(
            api_auth_mode="api_key",
            api_key_records="beta-client:test-secret:beta",
        )
    )

    with pytest.raises(ApiError) as exc_info:
        enforce_public_api_access(request)

    exc = exc_info.value
    assert exc.status_code == 401
    assert exc.code == "missing_api_key"
    assert exc.headers["WWW-Authenticate"] == "Bearer"


def test_public_beta_rejects_invalid_api_key():
    request = _build_request(
        ApiSettings(
            api_auth_mode="api_key",
            api_key_records="beta-client:test-secret:beta",
        ),
        headers={"authorization": "Bearer wrong-secret"},
    )

    with pytest.raises(ApiError) as exc_info:
        enforce_public_api_access(request)

    exc = exc_info.value
    assert exc.status_code == 403
    assert exc.code == "invalid_api_key"


def test_public_beta_maintenance_mode_blocks_analysis_but_not_health():
    settings = ApiSettings(
        api_auth_mode="api_key",
        api_key_records="beta-client:test-secret:beta",
        api_maintenance_mode=True,
    )
    blocked_request = _build_request(settings)
    health_request = _build_request(settings)

    with pytest.raises(ApiError) as exc_info:
        enforce_public_api_access(blocked_request)

    exc = exc_info.value
    assert exc.status_code == 503
    assert exc.code == "maintenance_mode"
    assert exc.retryable is True

    payload = health(health_request)
    assert payload["maintenance_mode"] is True
    assert payload["status"] == "ok"


def test_public_beta_rate_limits_per_caller():
    settings = ApiSettings(
        api_auth_mode="api_key",
        api_key_records="beta-client:test-secret:beta",
        api_default_rate_limit_per_minute=2,
    )
    app = _build_app(settings)

    first_request = _build_request(settings, headers={"authorization": "Bearer test-secret"}, app=app)
    second_request = _build_request(settings, headers={"authorization": "Bearer test-secret"}, app=app)
    third_request = _build_request(settings, headers={"authorization": "Bearer test-secret"}, app=app)

    first_caller = enforce_public_api_access(first_request)
    second_caller = enforce_public_api_access(second_request)

    assert first_caller.subject == "api_key:beta-client"
    assert second_caller.subject == "api_key:beta-client"

    with pytest.raises(ApiError) as exc_info:
        enforce_public_api_access(third_request)

    exc = exc_info.value
    assert exc.status_code == 429
    assert exc.code == "rate_limited"
    assert exc.retryable is True
    assert exc.details["retry_after_seconds"] >= 1


def test_public_beta_builds_deterministic_error_envelope():
    request = _build_request(
        ApiSettings(api_auth_mode="disabled"),
        headers={"x-request-id": "custom-request-id"},
    )
    request.state.request_id = "custom-request-id"

    response = build_error_response(
        request=request,
        status_code=503,
        code="maintenance_mode",
        message="Maintenance in progress.",
        retryable=True,
        headers={"Retry-After": "30"},
        details={"retry_after_seconds": 30},
    )

    payload = json.loads(response.body.decode())
    assert response.status_code == 503
    assert response.headers["X-Request-ID"] == "custom-request-id"
    assert response.headers["Retry-After"] == "30"
    assert payload["request_id"] == "custom-request-id"
    assert payload["error"]["code"] == "maintenance_mode"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["details"]["retry_after_seconds"] == 30


def test_public_beta_market_not_found_maps_to_http_404():
    class _MissingService:
        def get_market_analysis(self, market_id: str, **_: object):
            raise MarketNotFoundError(f"Market '{market_id}' was not found.")

    with pytest.raises(HTTPException) as exc_info:
        get_market_analysis(
            market_id="missing-market",
            include_trace=False,
            service=_MissingService(),
        )

    exc = exc_info.value
    assert exc.status_code == 404
    assert "missing-market" in str(exc.detail)


def test_public_beta_logs_request_context(caplog):
    caplog.set_level(logging.INFO, logger="services.api")
    settings = ApiSettings(
        api_auth_mode="disabled",
        api_request_logging_enabled=True,
    )
    app = SimpleNamespace(state=SimpleNamespace(api_settings=settings))

    async def downstream(scope, receive, send):
        scope["state"]["api_caller"] = ApiCaller(
            subject="api_key:beta-client",
            auth_mode="api_key",
            tier="beta",
            key_id="beta-client",
        )
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"status":"ok"}',
            }
        )

    middleware = RequestContextMiddleware(downstream)
    response_messages = anyio.run(
        _run_middleware_request,
        middleware,
        app,
        "/v0/analysis/opportunities",
    )

    start = next(message for message in response_messages if message["type"] == "http.response.start")
    headers = {
        key.decode().lower(): value.decode()
        for key, value in start["headers"]
    }

    assert "x-request-id" in headers
    records = [record for record in caplog.records if record.name == "services.api"]
    assert records
    payload = json.loads(records[-1].message)
    assert payload["path"] == "/v0/analysis/opportunities"
    assert payload["status_code"] == 200
    assert payload["caller_subject"] == "api_key:beta-client"
    assert payload["request_id"] == headers["x-request-id"]


def _build_app(settings: ApiSettings):
    return SimpleNamespace(
        state=SimpleNamespace(
            api_settings=settings,
            api_rate_limiter=InMemoryRateLimiter(),
        )
    )


def _build_request(
    settings: ApiSettings,
    headers: dict[str, str] | None = None,
    app=None,
):
    runtime_app = app or _build_app(settings)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v0/analysis/opportunities",
        "headers": [
            (key.encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "app": runtime_app,
        "state": {},
        "query_string": b"",
    }
    return Request(scope)


async def _run_middleware_request(middleware, app, path: str):
    messages = []
    receive_messages = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        if receive_messages:
            return receive_messages.pop(0)
        await anyio.sleep(0)
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
        "app": app,
        "state": {},
    }

    await middleware(scope, receive, send)
    return messages
