"""
Shared dataclasses and log buffer utilities for live monitor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock

from rich.text import Text

from shared.edge import NetEdgeResult
from shared.fixture_state import TriggerEvent

logger = logging.getLogger("cli.live")


@dataclass
class MatchSnapshot:
    mapping_id: str
    league: str
    match: str
    start_time: datetime | None
    market_type: str | None
    game_number: int | None
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
    source_note: str
    edge: NetEdgeResult
    updated_at: datetime
    ended: bool
    is_live: bool


@dataclass
class LiveState:
    snapshots: list[MatchSnapshot]
    recently_ended: list[MatchSnapshot]
    upcoming: list[MatchSnapshot]
    focus_match: MatchSnapshot | None
    focus_game1: MatchSnapshot | None
    last_update: datetime | None
    last_loop_duration: float | None
    perf: "PerfStats" | None


@dataclass
class PerfStats:
    loop_ms: float | None = None
    oddspapi_ms_total: float = 0.0
    oddspapi_calls: int = 0
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
    entry_logged: bool = False
    catchup_logged_sides: set[str] = field(default_factory=set)


@dataclass
class PaperTrade:
    key: str
    mapping_id: str
    market_id: str
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
    status: str = "open"


class LogBuffer:
    """Rolling log buffer for TUI display."""

    def __init__(self, max_lines: int = 8) -> None:
        self._lines: list[str] = []
        self._max_lines = max_lines
        self._last_logged: dict[str, float] = {}
        self._lock = Lock()

    def add(self, message: str, key: str | None = None, cooldown_seconds: int = 0) -> None:
        from datetime import timezone

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

    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        self._buffer.add(message)


def format_market_label(market_type: str | None, game_number: int | None) -> str:
    if market_type == "match_winner":
        return "MATCH (ML)"
    if market_type == "game_winner":
        return f"GAME {game_number}" if game_number else "GAME"
    return market_type or "-"


def format_pm_winner(value: str | None) -> str:
    if not value:
        return "-"
    return value.strip()
