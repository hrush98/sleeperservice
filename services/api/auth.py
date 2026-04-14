"""Auth helpers for the public analysis API."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from services.api.config import ApiSettings
from services.api.errors import ApiError


@dataclass(frozen=True)
class ApiCaller:
    """Caller identity derived from the configured auth boundary."""

    subject: str
    auth_mode: str
    tier: str
    key_id: str | None = None


@dataclass(frozen=True)
class ApiKeyRecord:
    """Configured API key record."""

    key_id: str
    secret: str
    tier: str


def authenticate_request(request: Request, settings: ApiSettings) -> ApiCaller:
    """Authenticate the current request and persist caller identity on request state."""

    if settings.api_auth_mode == "disabled":
        caller = ApiCaller(
            subject="local:anonymous",
            auth_mode="disabled",
            tier="internal",
        )
        request.state.api_caller = caller
        return caller

    if settings.api_auth_mode != "api_key":
        raise ApiError(
            status_code=503,
            code="auth_mode_unavailable",
            message=f"API auth mode '{settings.api_auth_mode}' is not supported.",
        )

    key_records = parse_api_key_records(settings.api_key_records)
    if not key_records:
        raise ApiError(
            status_code=503,
            code="auth_not_configured",
            message="API key authentication is enabled but no API keys are configured.",
        )

    raw_key = extract_api_key(request)
    if raw_key is None:
        raise ApiError(
            status_code=401,
            code="missing_api_key",
            message="Provide an API key with the Authorization Bearer header or X-API-Key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    record = key_records.get(raw_key)
    if record is None:
        raise ApiError(
            status_code=403,
            code="invalid_api_key",
            message="The supplied API key is not valid for this API.",
        )

    caller = ApiCaller(
        subject=f"api_key:{record.key_id}",
        auth_mode="api_key",
        tier=record.tier,
        key_id=record.key_id,
    )
    request.state.api_caller = caller
    return caller


def extract_api_key(request: Request) -> str | None:
    """Extract an API key from Authorization or X-API-Key headers."""

    auth_header = request.headers.get("Authorization")
    if auth_header:
        scheme, _, credentials = auth_header.partition(" ")
        if scheme.lower() == "bearer" and credentials.strip():
            return credentials.strip()

    header_key = request.headers.get("X-API-Key")
    if header_key and header_key.strip():
        return header_key.strip()

    return None


def parse_api_key_records(raw: str) -> dict[str, ApiKeyRecord]:
    """Parse comma-separated API keys of the form key_id:secret[:tier]."""

    records: dict[str, ApiKeyRecord] = {}
    if not raw.strip():
        return records

    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split(":") if part.strip()]
        if len(parts) < 2:
            continue
        key_id, secret = parts[0], parts[1]
        tier = parts[2] if len(parts) >= 3 else "beta"
        records[secret] = ApiKeyRecord(
            key_id=key_id,
            secret=secret,
            tier=tier,
        )
    return records
