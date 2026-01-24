"""API routers for LoL Lead-Lag Arbitrage Bot."""

from api.routers.health import router as health_router
from api.routers.ops import router as ops_router

__all__ = ["health_router", "ops_router"]

