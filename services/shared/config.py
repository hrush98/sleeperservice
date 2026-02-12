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
    oddspapi_cs2_sport_id: int = 17  # CS2 sportId in OddsPapi

    # Polymarket
    polymarket_base_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com"
    poly_api_key: str | None = None
    polymarket_game_bets_tag_id: int = 100639  # Game bets tag for LoL
    polymarket_keyfile_path: str = "~/.sleeprservice/keys/polymarket.key.age"
    polymarket_chain_id: int = 137
    polymarket_signature_type: int = 0  # EOA
    polymarket_funder_address: str | None = None  # Set for proxy wallets (Polymarket displayed address)
    polymarket_token_decimals: int = 6

    # Polymarket WebSocket
    ws_ping_interval_seconds: int = 5  # Send keepalive every 5 seconds
    ws_reconnect_base_seconds: float = 1.0
    ws_reconnect_max_seconds: float = 30.0
    user_ws_enabled: bool = True
    user_ws_placement_timeout_seconds: float = 10.0
    user_ws_untracked_timeout_seconds: float = 60.0

    # Target leagues (comma-separated)
    target_leagues: str = "LCK,LPL,LEC,LCS,LTA,LCP,CBLOL,LFL"
    target_cs2_leagues: str = "BLAST Premier Series,ESL Pro League,Intel Extreme Masters,PGL"

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

    # Pinnacle display classification
    pin_pre_fresh_minutes: int = 10
    starting_soon_minutes: int = 15
    starting_soon_grace_minutes: int = 60
    # Consider a market "done" when one side is near 1.0 and the other near 0.0.
    # This guards against thin-book transient prints by requiring BOTH extremes.
    pm_done_threshold_high: float = 0.995
    pm_done_threshold_low: float = 0.01

    # Discovery lookback (hours) — include matches that started this many
    # hours ago so in-play fixtures get discovered and mapped.
    discovery_lookback_hours: float = 6.0

    # OddsPapi league poll cadence (seconds)
    oddspapi_league_poll_seconds_pre: float = 5.0
    oddspapi_league_poll_seconds_inplay: float = 1.0

    # Live concurrency caps (async)
    oddspapi_max_concurrent_live: int = 4
    polymarket_gamma_max_concurrent_live: int = 4
    polymarket_clob_max_concurrent_live: int = 2

    # Polymarket Gamma refresh cadence (seconds)
    polymarket_gamma_refresh_seconds: float = 15.0

    # Trigger thresholds
    trigger_primary_threshold: float = 0.02
    trigger_burst_threshold: float = 0.05
    trigger_adaptive_multiplier: float = 3.0
    trigger_edge_persist_threshold: float = 0.03
    trigger_edge_persist_polls: int = 2
    trigger_edge_spike_threshold: float = 0.04

    # Edge calculation
    alpha_min: float = 0.03
    alpha_spread_factor: float = 1.5
    exit_epsilon: float = 0.015

    # Live monitoring
    monitor_poll_interval_seconds: int = 5
    shadow_gap_threshold: float = 0.05  # 5% gap to record shadow order

    # Trade loop cadence
    trade_loop_idle_seconds: float = 0.5
    trade_loop_hot_seconds: float = 0.05
    entry_reeval_seconds: float = 3.0

    # Live trading safeguards (only used in live mode)
    live_max_usd_per_order: float = 10.0
    live_max_shares_per_order: float = 1000.0
    live_max_open_positions: int = 5
    live_min_seconds_between_orders: float = 3.0
    live_share_step: float = 0.0001
    min_book_depth_usd: float = 250.0  # Skip entry when bid-side USD depth is below this
    live_kill_switch_path: str = "~/.sleeprservice/keys/STOP_TRADING"
    live_require_allowance_check: bool = True
    # Stop-loss guards
    stop_thesis_death_enabled: bool = True   # exit when p_ref < entry_price
    stop_hard_enabled: bool = True           # exit when bid drops X% below entry
    stop_hard_pct: float = 0.20             # hard stop threshold (20%)

    exit_order_not_found_seconds: float = 60.0
    exit_gtc_reprice_seconds: float = 12.0
    exit_price_step_ticks: int = 1
    exit_phantom_id_null_threshold: int = 5
    exit_max_attempts: int = 5
    exit_retry_cooldown_seconds: float = 30.0
    exit_degraded_chunk_fraction: float = 0.25
    balance_poll_interval_seconds: float = 30.0
    stale_position_sweep_seconds: float = 300.0
    delayed_grace_seconds: float = 5.0
    delayed_retry_cooldown_seconds: float = 2.0
    delayed_max_retries: int = 2

    # Logging
    # When running the live TUI, stdout logging is redirected into the on-screen buffer.
    # These settings enable a "black box" rotating log file for postmortems.
    log_dir: str = "logs"
    live_log_backup_days: int = 14
    live_log_per_run: bool = True
    live_log_run_id_format: str = "%Y%m%d-%H%M%S"
    live_log_include_pid: bool = True

    # Display (CLI-only)
    # Stored timestamps remain UTC; this is for human-friendly rendering.
    # Use an IANA timezone name (e.g. "America/Los_Angeles").
    display_timezone: str = "America/Los_Angeles"
    # Max completed positions to show in live TUI positions panel.
    live_positions_limit: int = 20

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @property
    def target_league_list(self) -> list[str]:
        """Return target leagues as a list."""
        return [league.strip().upper() for league in self.target_leagues.split(",")]

    @property
    def target_league_patterns(self) -> list[str]:
        """Return normalized league patterns for keyword matching."""
        return self._normalized_patterns(self.target_league_list)

    @property
    def target_cs2_league_list(self) -> list[str]:
        """Return target CS2 leagues as a list."""
        return [league.strip().upper() for league in self.target_cs2_leagues.split(",")]

    @property
    def target_cs2_league_patterns(self) -> list[str]:
        """Return normalized CS2 league patterns for keyword matching."""
        return self._normalized_patterns(self.target_cs2_league_list)

    @staticmethod
    def _normalized_patterns(leagues: list[str]) -> list[str]:
        """Normalize league names into robust keyword patterns."""
        patterns: list[str] = []
        for league in leagues:
            normalized = "".join(
                ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in league
            )
            tokens = [token for token in normalized.split() if token]
            if not tokens:
                continue
            if len(tokens) == 1:
                patterns.append(tokens[0])
            else:
                patterns.append(" ".join(tokens))
                patterns.extend(tokens)

        unique: list[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            if pattern not in seen:
                seen.add(pattern)
                unique.append(pattern)
        return unique


settings = Settings()
