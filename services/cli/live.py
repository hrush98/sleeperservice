"""
Live TUI monitor for LoL Lead-Lag Arbitrage Bot (read-only).
"""

from __future__ import annotations

import logging
import time
from threading import Lock

from rich.console import Console
from rich.live import Live

from shared.config import settings
from shared.fixture_state import FixtureStateManager
from shared.oddspapi_client import AsyncOddsPapiClient
from shared.polymarket_client import AsyncPolymarketClient
from shared.polymarket_ws import PolymarketWSManager

from .live_tui import build_layout, render_layout
from .monitor_core import EventDrivenMonitor
from .monitor_types import LiveState, LogBuffer, LogBufferHandler

logger = logging.getLogger("cli.live")


def _configure_logging_for_live(system_log_buffer: LogBuffer) -> None:
    handler = LogBufferHandler(system_log_buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)

    live_logger = logging.getLogger("cli.live")
    for existing in list(live_logger.handlers):
        live_logger.removeHandler(existing)
    live_logger.addHandler(handler)
    live_logger.setLevel(logging.INFO)
    live_logger.propagate = False


def live_command(
    interval: float | None = None,
    speed: str = "MED",
    edge_threshold: float = 0.03,
    spread_factor: float = 1.0,
    min_confidence: float = 0.5,
    lookahead_minutes: int = 30,
    stale_minutes: int = 180,
) -> None:
    """
    Live TUI monitor:
    - Reads mappings from DB
    - Polls OddsPapi + Polymarket CLOB
    - Computes net edge and displays a stationary TUI
    """
    speed_map = {"FAST": 0.9, "MED": 5.0, "SLOW": 10.0}
    if interval is None:
        speed_key = speed.upper()
        if speed_key in speed_map:
            interval = speed_map[speed_key]
    if interval is None:
        interval = 5

    console = Console()
    oddspapi = AsyncOddsPapiClient(
        global_cooldown_ms=settings.oddspapi_global_cooldown_ms_live
    )
    polymarket = AsyncPolymarketClient()
    ws_manager = PolymarketWSManager()
    fixture_states = FixtureStateManager()

    alerts_buffer = LogBuffer(max_lines=6)
    trade_buffer = LogBuffer(max_lines=10)
    log_buffer = LogBuffer(max_lines=8)
    log_buffer.add("Live monitor started")
    system_log_buffer = LogBuffer(max_lines=8)
    _configure_logging_for_live(system_log_buffer)
    layout = build_layout()

    state_lock = Lock()
    live_state = LiveState(
        snapshots=[],
        recently_ended=[],
        upcoming=[],
        focus_match=None,
        focus_game1=None,
        last_update=None,
        last_loop_duration=None,
        perf=None,
    )

    monitor = EventDrivenMonitor(
        oddspapi=oddspapi,
        polymarket=polymarket,
        ws_manager=ws_manager,
        fixture_states=fixture_states,
        state_lock=state_lock,
        live_state=live_state,
        log_buffer=log_buffer,
        alerts_buffer=alerts_buffer,
        trade_buffer=trade_buffer,
        edge_threshold=edge_threshold,
        spread_factor=spread_factor,
        min_confidence=min_confidence,
        lookahead_minutes=lookahead_minutes,
        stale_minutes=stale_minutes,
    )
    thread = monitor.start()

    ui_interval = min(1.0, interval)
    refresh_rate = max(2.0, 1.0 / ui_interval)
    try:
        with Live(layout, console=console, refresh_per_second=refresh_rate, screen=True):
            while True:
                with state_lock:
                    snapshots = list(live_state.snapshots)
                    recently_ended_list = list(live_state.recently_ended)
                    upcoming_list = list(live_state.upcoming)
                    focus_match = live_state.focus_match
                    focus_game1 = live_state.focus_game1
                    perf = live_state.perf
                render_layout(
                    layout=layout,
                    snapshots=snapshots,
                    recently_ended=recently_ended_list,
                    upcoming=upcoming_list,
                    focus_match=focus_match,
                    focus_game1=focus_game1,
                    edge_threshold=edge_threshold,
                    alerts_buffer=alerts_buffer,
                    trade_buffer=trade_buffer,
                    log_buffer=log_buffer,
                    system_log_buffer=system_log_buffer,
                    spread_factor=spread_factor,
                    perf=perf,
                )
                time.sleep(ui_interval)
    finally:
        monitor.stop()
        thread.join(timeout=5)
