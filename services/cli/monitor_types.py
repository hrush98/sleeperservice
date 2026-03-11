"""
Shared dataclasses and log buffer utilities for live monitor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock

from rich.text import Text

from shared.edge import NetEdgeResult
from shared.fixture_state import TriggerEvent

logger = logging.getLogger("cli.live")


@dataclass
class FocusSnapshot:
    mapping_id: str
    league: str
    match: str
    start_time: datetime | None
    market_type: str | None
    game_number: int | None
    tick_size: float | None
    pm_resolution_status: str
    pm_winner: str | None
    p_ref_a: float | None
    p_ref_b: float | None
    odds_a: float | None
    odds_b: float | None
    bid_a: float | None
    ask_a: float | None
    bid_b: float | None
    ask_b: float | None
    mid_a: float | None
    mid_b: float | None
    token_id_a: str | None
    token_id_b: str | None
    edge: NetEdgeResult
    updated_at: datetime
    ws_connected: bool
    last_odds_update: datetime | None
    last_gamma_update: datetime | None
    line_value: float | None = None
    side_a_label: str | None = None
    side_b_label: str | None = None
    p_ref_source: str = "direct"
    orientation_locked: bool = False
    orientation_source: str | None = None
    orientation_conflict: bool = False
    pin_is_inplay: bool = False
    p_ref_stale: bool = False


@dataclass
class PositionRollup:
    wins_count: int
    wins_total_pnl_percent: float
    wins_avg_pnl_percent: float
    best_win_pnl_percent: float | None
    last_closed_at: datetime | None
    modes: set[str] = field(default_factory=set)


@dataclass
class MonitorState:
    snapshot: FocusSnapshot | None
    trade_tape: list[str]
    last_update: datetime | None
    last_loop_duration: float | None
    perf: "PerfStats" | None


@dataclass
class PerfStats:
    loop_ms: float | None = None
    oddspapi_ms_total: float = 0.0
    oddspapi_calls: int = 0
    # Delta since last snapshot (per-loop window)
    oddspapi_ms_window: float = 0.0
    oddspapi_calls_window: int = 0
    clob_batch_ms: float | None = None
    gamma_ms_total: float = 0.0
    gamma_calls: int = 0
    oddspapi_last_batch: int = 0
    ws_assets: int = 0
    ws_books_fallback: int = 0


@dataclass
class TriggerRecord:
    trigger: TriggerEvent
    ts: datetime
    logged: bool = False
    entry_checked_at: datetime | None = None
    entry_done: bool = False
    catchup_logged_sides: set[str] = field(default_factory=set)


@dataclass
class PaperTrade:
    key: str
    mapping_id: str
    market_id: str
    token_id: str | None
    market_type: str | None
    game_number: int | None
    side: str
    trigger_type: str | None
    trigger_ts: datetime | None
    entry_ts: datetime
    entry_price: float
    limit_price: float | None
    size_available: float | None
    quantity: float
    alpha: float
    net_edge: float | None
    p_ref_entry: float | None
    db_position_id: str | None = None
    status: str = "open"
    external_order_id: str | None = None
    external_status: str | None = None
    delayed_retries: int = 0
    last_retry_ts: datetime | None = None


class ComplementArbState(str, Enum):
    IDLE = "IDLE"
    SUBMITTING = "SUBMITTING"
    BOTH_FILLED = "BOTH_FILLED"
    LEG_A_ONLY = "LEG_A_ONLY"
    RETRYING_B = "RETRYING_B"
    UNWINDING_A = "UNWINDING_A"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"


@dataclass
class ComplementArbSignal:
    mapping_id: str
    market_id: str
    market_type: str | None
    game_number: int | None
    token_id_a: str
    token_id_b: str
    vwap_a: float
    vwap_b: float
    fillable_size: float
    edge: float
    detected_at: datetime


@dataclass
class ComplementArbRecord:
    key: str
    mapping_id: str
    market_id: str
    market_type: str | None
    game_number: int | None
    token_id_a: str
    token_id_b: str
    state: ComplementArbState
    created_at: datetime
    updated_at: datetime
    edge: float | None = None
    target_size: float | None = None
    vwap_a: float | None = None
    vwap_b: float | None = None
    position_id_a: str | None = None
    position_id_b: str | None = None
    db_id: str | None = None


class LogBuffer:
    """Rolling log buffer for TUI display."""

    def __init__(self, max_lines: int = 8) -> None:
        self._lines: list[str] = []
        self._max_lines = max_lines
        self._last_logged: dict[str, float] = {}
        self._lock = Lock()

    def add(self, message: str, key: str | None = None, cooldown_seconds: int = 0) -> None:
        with self._lock:
            now = datetime.now(tz=timezone.utc)
            if key and cooldown_seconds > 0:
                last = self._last_logged.get(key, 0.0)
                if (now.timestamp() - last) < cooldown_seconds:
                    return
                self._last_logged[key] = now.timestamp()

            ts = now.strftime("%H:%M:%S")
            self._lines.append(f"[{ts}] {message}")
            if len(self._lines) > self._max_lines:
                self._lines = self._lines[-self._max_lines :]

    def render(self) -> Text:
        with self._lock:
            text = Text()
            if not self._lines:
                text.append("No logs yet.")
                return text
            text.append("\n".join(self._lines))
            return text


class LogBufferHandler(logging.Handler):
    """Logging handler that writes to a LogBuffer instead of stdout."""

    def __init__(self, buffer: LogBuffer, last_events: "LastLogEvents | None" = None) -> None:
        super().__init__()
        self._buffer = buffer
        self._last_events = last_events

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        self._buffer.add(message)
        if self._last_events:
            self._last_events.update(record, message)


class LastLogEvents:
    """Track the latest warning and error messages."""

    def __init__(self) -> None:
        self._last_warning: str | None = None
        self._last_error: str | None = None
        self._lock = Lock()

    def update(self, record: logging.LogRecord, message: str) -> None:
        if record.levelno < logging.WARNING:
            return
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with self._lock:
            if record.levelno >= logging.ERROR:
                self._last_error = line
            else:
                self._last_warning = line

    def get_last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def get_last_warning(self) -> str | None:
        with self._lock:
            return self._last_warning

    def render(self) -> Text:
        text = Text()
        err = self.get_last_error()
        warn = self.get_last_warning()
        if err:
            text.append(f"Error: {err}\n", style="bold red")
        else:
            text.append("Error: -\n", style="dim")
        if warn:
            text.append(f"Warn:  {warn}", style="yellow")
        else:
            text.append("Warn:  -", style="dim")
        return text


def format_market_label(
    market_type: str | None,
    game_number: int | None,
    line_value: float | None = None,
    p_ref_source: str | None = None,
) -> str:
    if market_type == "match_winner":
        return "MATCH (ML)"
    if market_type == "game_winner":
        base = f"GAME {game_number}" if game_number else "GAME"
        if p_ref_source == "derived_series":
            return f"{base} *"
        return base
    if market_type == "totals":
        line = f"{line_value:.1f}" if line_value is not None else "?"
        base = f"TOTAL {line}"
        return f"{base} *" if p_ref_source == "derived_series" else base
    return market_type or "-"


def format_pm_winner(value: str | None) -> str:
    if not value:
        return "-"
    return value.strip()
