"""Configuration for the public analysis API runtime."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from services.shared.config import _find_env_files


class ApiSettings(BaseSettings):
    """Settings for the public-beta analysis API surface."""

    model_config = SettingsConfigDict(
        env_file=_find_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_public_beta_enabled: bool = True
    api_maintenance_mode: bool = False
    api_maintenance_message: str = (
        "SleeperService public beta is temporarily unavailable while maintenance is in progress."
    )
    api_auth_mode: Literal["disabled", "api_key"] = "disabled"
    api_default_rate_limit_per_minute: int = 60
    api_request_logging_enabled: bool = True
    api_key_records: str = ""


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    """Return cached API settings for the default runtime."""

    return ApiSettings()
