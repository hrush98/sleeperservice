"""
FastAPI application for LoL Lead-Lag Arbitrage Bot.

Minimal API with only operations endpoints:
- GET /health — health check
- GET /ops/status — system status
- GET /ops/live — live matches + gaps
"""

from fastapi import FastAPI

from services.api.routers.health import router as health_router
from services.api.routers.ops import router as ops_router

app = FastAPI(
    title="LoL Lead-Lag Arbitrage Bot API",
    description="Operations endpoints for the LoL lead-lag arbitrage bot",
    version="2.0.0",
)

app.include_router(health_router)
app.include_router(ops_router)


@app.get("/")
def root():
    """Root endpoint with basic info."""
    return {
        "name": "LoL Lead-Lag Arbitrage Bot",
        "version": "2.0.0",
        "endpoints": [
            "/health",
            "/ops/status",
            "/ops/live",
        ],
    }
