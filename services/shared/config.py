from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    polymarket_base_url: str = "https://gamma-api.polymarket.com"
    worker_poll_interval_seconds: int = 300
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    class Config:
        env_file = ".env"
        env_prefix = ""


settings = Settings()
