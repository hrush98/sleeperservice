from __future__ import annotations

from fastapi import Request

from services.api.analysis_v0 import V0AnalysisService
from services.api.auth import ApiCaller, authenticate_request
from services.api.config import ApiSettings
from services.api.errors import ApiError
from services.api.rate_limit import InMemoryRateLimiter
from services.research.artifacts import HistoricalArtifactStore
from services.shared.db import get_db_session
from services.shared.polymarket_client import get_client


def get_db():
    yield from get_db_session()


def get_api_settings(request: Request) -> ApiSettings:
    return request.app.state.api_settings


def get_rate_limiter(request: Request) -> InMemoryRateLimiter:
    return request.app.state.api_rate_limiter


def enforce_public_api_access(request: Request) -> ApiCaller:
    settings = get_api_settings(request)

    if not settings.api_public_beta_enabled:
        raise ApiError(
            status_code=503,
            code="public_beta_disabled",
            message="SleeperService public beta is not enabled on this deployment.",
        )

    if settings.api_maintenance_mode:
        raise ApiError(
            status_code=503,
            code="maintenance_mode",
            message=settings.api_maintenance_message,
            retryable=True,
        )

    caller = authenticate_request(request, settings)

    if settings.api_auth_mode == "disabled":
        return caller

    limiter = get_rate_limiter(request)
    result = limiter.evaluate(
        caller.subject,
        limit=settings.api_default_rate_limit_per_minute,
    )
    request.state.api_rate_limit_remaining = result.remaining
    if not result.allowed:
        raise ApiError(
            status_code=429,
            code="rate_limited",
            message="Rate limit exceeded for this caller. Retry after the indicated cooldown.",
            retryable=True,
            headers={"Retry-After": str(result.retry_after_seconds)},
            details={"retry_after_seconds": result.retry_after_seconds},
        )
    return caller


def get_v0_analysis_service() -> V0AnalysisService:
    return V0AnalysisService(
        artifact_store=HistoricalArtifactStore.from_env(),
        polymarket_client=get_client(),
    )
