"""
Configuration settings for the LoL Lead-Lag Arbitrage Bot.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> Path | None:
    """Find .env file in current dir or parent dirs."""
    current = Path.cwd()
    for path in [current, current.parent, current / "services", Path(__file__).parent.parent]:
        env_path = path / ".env"
        if env_path.exists():
            return env_path
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str

    # OddsPapi
    oddspapi_base_url: str = "https://api.oddspapi.io"
    odds_api_key: str | None = None
    oddspapi_lol_sport_id: int = 18  # LoL sportId in OddsPapi

    # Polymarket
    polymarket_base_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com"
    poly_api_key: str | None = None
    polymarket_game_bets_tag_id: int = 100639  # Game bets tag for LoL

    # Polymarket WebSocket
    ws_ping_interval_seconds: int = 10
    ws_reconnect_base_seconds: float = 1.0
    ws_reconnect_max_seconds: float = 30.0

    # Target leagues (comma-separated)
    target_leagues: str = "LCK,LPL,LEC,LCS,LTA,LCP"

    # Cooldowns (milliseconds) - OddsPapi rate limits
    # Discovery can be slower; live odds should be fast.
    oddspapi_global_cooldown_ms_discovery: int = 2000
    oddspapi_global_cooldown_ms_live: int = 900
    cooldown_tournaments_ms: int = 2000
    cooldown_participants_ms: int = 2000
    cooldown_fixtures_ms: int = 2500
    cooldown_odds_ms: int = 900
    cooldown_odds_by_tournaments_ms: int = 1000

    # OddsPapi polling
    hot_fixture_poll_ms: int = 500
    hot_fixture_ttl_seconds: int = 60

    # Trigger thresholds
    trigger_primary_threshold: float = 0.02
    trigger_burst_threshold: float = 0.05
    trigger_adaptive_multiplier: float = 3.0

    # Edge calculation
    alpha_min: float = 0.03
    alpha_spread_factor: float = 1.5
    exit_epsilon: float = 0.01

    # Live monitoring
    monitor_poll_interval_seconds: int = 5
    shadow_gap_threshold: float = 0.05  # 5% gap to record shadow order

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @property
    def target_league_list(self) -> list[str]:
        """Return target leagues as a list."""
        return [league.strip().upper() for league in self.target_leagues.split(",")]


settings = Settings()
