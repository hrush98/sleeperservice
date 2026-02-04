"""
Live TUI monitor for LoL Lead-Lag Arbitrage Bot (read-only).
"""

from __future__ import annotations

import logging
import select
import sys
import time
from threading import Lock

from rich.console import Console
from rich.live import Live

from shared.config import settings
from shared.agent_debug import agent_log
from shared.fixture_state import FixtureStateManager
from shared.oddspapi_client import AsyncOddsPapiClient
from shared.polymarket_client import AsyncPolymarketClient
from shared.polymarket_ws import PolymarketWSManager

from .live_tui import build_layout, build_log_layout, render_layout, render_log_layout
from .monitor_core import EventDrivenMonitor
from .monitor_types import LastLogEvents, LiveState, LogBuffer, LogBufferHandler

logger = logging.getLogger("cli.live")


def _configure_logging_for_live(log_buffer: LogBuffer, last_events: LastLogEvents) -> None:
    handler = LogBufferHandler(log_buffer, last_events=last_events)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    for existing in list(root_logger.handlers):
        root_logger.removeHandler(existing)
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    loggers_to_redirect = [
        "cli.live",
        "shared.oddspapi_client",
        "shared.polymarket_client",
        "shared.polymarket_ws",
    ]
    for logger_name in loggers_to_redirect:
        target_logger = logging.getLogger(logger_name)
        for existing in list(target_logger.handlers):
            target_logger.removeHandler(existing)
        target_logger.addHandler(handler)
        target_logger.setLevel(logging.INFO)
        target_logger.propagate = False


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
    log_buffer = LogBuffer(max_lines=12)
    log_buffer.add("Live monitor started")
    last_events = LastLogEvents()
    _configure_logging_for_live(log_buffer, last_events)
    layout = build_layout()
    log_layout = build_log_layout()

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
        ws_connected=None,
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
    view_mode = "dashboard"
    current_layout = layout
    try:
        with Live(current_layout, console=console, refresh_per_second=refresh_rate, screen=True) as live:
            while True:
                with state_lock:
                    snapshots = list(live_state.snapshots)
                    recently_ended_list = list(live_state.recently_ended)
                    upcoming_list = list(live_state.upcoming)
                    focus_match = live_state.focus_match
                    focus_game1 = live_state.focus_game1
                    perf = live_state.perf
                    ws_connected = live_state.ws_connected
                # region agent log (TUI state + terminal size)
                size = console.size
                agent_log(
                    location="services/cli/live.py:live_command",
                    message="tui tick",
                    hypothesis_id="H4",
                    data={
                        "term_w": int(getattr(size, "width", 0) or 0),
                        "term_h": int(getattr(size, "height", 0) or 0),
                        "n_live": len(snapshots),
                        "n_upcoming": len(upcoming_list),
                        "n_recently_ended": len(recently_ended_list),
                        "ws_connected": bool(ws_connected),
                    },
                )
                # endregion

                if select.select([sys.stdin], [], [], 0)[0]:
                    line = sys.stdin.readline().strip().lower()
                    if line in {"l", "log", "logs"}:
                        view_mode = "logs" if view_mode == "dashboard" else "dashboard"
                        current_layout = log_layout if view_mode == "logs" else layout
                        live.update(current_layout, refresh=True)

                if view_mode == "logs":
                    render_log_layout(
                        layout=current_layout,
                        log_buffer=log_buffer,
                        perf=perf,
                        ws_connected=ws_connected,
                        last_events=last_events,
                    )
                else:
                    render_layout(
                        layout=current_layout,
                        snapshots=snapshots,
                        recently_ended=recently_ended_list,
                        upcoming=upcoming_list,
                        focus_match=focus_match,
                        focus_game1=focus_game1,
                        edge_threshold=edge_threshold,
                        trade_buffer=trade_buffer,
                        log_buffer=log_buffer,
                        spread_factor=spread_factor,
                        perf=perf,
                        ws_connected=ws_connected,
                        last_events=last_events,
                    )
                time.sleep(ui_interval)
    finally:
        monitor.stop()
        thread.join(timeout=5)
