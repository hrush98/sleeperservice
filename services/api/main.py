from fastapi import FastAPI

from api.routers.health import router as health_router
from api.routers.markets import router as markets_router

app = FastAPI(title="Prediction Market Intelligence API")

app.include_router(health_router)
app.include_router(markets_router)
