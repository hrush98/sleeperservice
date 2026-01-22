from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    polymarket_base_url: str = "https://gamma-api.polymarket.com"
    poly_api_key: str | None = None
    oddspapi_base_url: str = "https://api.oddspapi.io"
    odds_api_key: str | None = None
    worker_poll_interval_seconds: int = 300
    shadow_gap_threshold: float = 0.05
    shadow_min_event_interval_seconds: int = 5
    shadow_min_gap_delta: float = 0.01
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    class Config:
        env_file = ".env"
        env_prefix = ""


settings = Settings()
