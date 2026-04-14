from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health(request: Request):
    settings = request.app.state.api_settings
    return {
        "status": "ok",
        "public_beta_enabled": settings.api_public_beta_enabled,
        "maintenance_mode": settings.api_maintenance_mode,
    }
