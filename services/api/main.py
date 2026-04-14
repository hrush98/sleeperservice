"""FastAPI application for SleeperService."""

from __future__ import annotations

from fastapi import FastAPI

from services.api.config import ApiSettings, get_api_settings
from services.api.errors import install_exception_handlers
from services.api.middleware import install_request_context_middleware
from services.api.rate_limit import InMemoryRateLimiter
from services.api.routers.analysis_v0 import router as analysis_v0_router
from services.api.routers.health import router as health_router
from services.api.routers.ops import router as ops_router
from services.research.bucket_sync import sync_artifact_bucket_from_env


def create_app(api_settings: ApiSettings | None = None) -> FastAPI:
    """Build the FastAPI application with shared API plumbing."""

    app = FastAPI(
        title="SleeperService API",
        description="Read-only analysis and operations API for SleeperService",
        version="0.4.0",
    )
    app.state.api_settings = api_settings or get_api_settings()
    app.state.api_rate_limiter = InMemoryRateLimiter()

    install_request_context_middleware(app)
    install_exception_handlers(app)

    app.include_router(analysis_v0_router)
    app.include_router(health_router)
    app.include_router(ops_router)

    @app.on_event("startup")
    def sync_promoted_artifacts() -> None:
        """Fetch promoted study artifacts from object storage when configured."""

        sync_artifact_bucket_from_env()

    @app.get("/")
    def root():
        """Root endpoint with basic info."""

        settings = app.state.api_settings
        return {
            "name": "SleeperService API",
            "version": "0.4.0",
            "public_beta_enabled": settings.api_public_beta_enabled,
            "maintenance_mode": settings.api_maintenance_mode,
            "endpoints": [
                "/v0/analysis/opportunities",
                "/v0/markets/{market_id}/analysis",
                "/health",
                "/ops/status",
                "/ops/live",
            ],
        }

    return app


app = create_app()
