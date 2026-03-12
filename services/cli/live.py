"""
Live TUI monitor for LoL Lead-Lag Arbitrage Bot (single match).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import select
import sys
import termios
import time
import tty
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from threading import Event, Thread
from typing import Literal

import typer
from rich.console import Console
from rich.live import Live
from sqlalchemy import select as sa_select
from sqlalchemy.orm import aliased

from services.shared.config import settings
from services.shared.db import SessionLocal
from services.shared.fixture_state import FixtureStateManager
from services.shared.models import Fixture, Mapping, Position
from services.shared.oddspapi_client import AsyncOddsPapiClient
from services.shared.polymarket_client import AsyncPolymarketClient
from services.shared.polymarket_user_ws import PolymarketUserWSManager
from services.shared.polymarket_ws import PolymarketWSManager
from services.shared.secret_utils import decrypt_age_keyfile

from .live_tui import apply_strategy_mode_to_layout, build_layout, render_layout
from .monitor_types import LogBuffer, LogBufferHandler
from .poller import SingleMatchPoller
from .complement_arb import ComplementArbManager
from .trader import TradeManager

logger = logging.getLogger("cli.live")


def _configure_logging_for_live(log_buffer: LogBuffer, *, trade_mode: str) -> None:
    buffer_handler = LogBufferHandler(log_buffer)
    buffer_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    file_handler: logging.Handler | None = None
    try:
        log_dir = Path(str(settings.log_dir)).expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        suffix = ""
        if settings.live_log_per_run:
            run_id = datetime.now(tz=timezone.utc).strftime(str(settings.live_log_run_id_format))
            pid_suffix = f"-pid{os.getpid()}" if settings.live_log_include_pid else ""
            suffix = f"-{run_id}{pid_suffix}"
        logfile = log_dir / f"live-{trade_mode}{suffix}.log"
        file_handler = TimedRotatingFileHandler(
            filename=str(logfile),
            when="midnight",
            backupCount=int(settings.live_log_backup_days),
            utc=True,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        log_buffer.add(f"Black box log: {logfile}")
    except Exception as exc:  # pragma: no cover - best effort logging  # pylint: disable=broad-exception-caught
        file_handler = None
        log_buffer.add(f"File logging disabled: {exc}")

    root_logger = logging.getLogger()
    # Live monitor should be INFO by default; allow `--verbose` to elevate to DEBUG.
    desired_level = logging.DEBUG if root_logger.level == logging.DEBUG else logging.INFO
    buffer_handler.setLevel(desired_level)
    if file_handler:
        file_handler.setLevel(desired_level)
    for existing in list(root_logger.handlers):
        root_logger.removeHandler(existing)
    root_logger.addHandler(buffer_handler)
    if file_handler:
        root_logger.addHandler(file_handler)
    root_logger.setLevel(desired_level)

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
        target_logger.addHandler(buffer_handler)
        if file_handler:
            target_logger.addHandler(file_handler)
        target_logger.setLevel(desired_level)
        target_logger.propagate = False


def live_command(
    interval: float | None = None,
    speed: str = "MED",
    mode: str | None = None,
    edge_threshold: float = 0.03,
    spread_factor: float = 1.0,
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

    trade_mode = _resolve_trade_mode(mode)
    live_private_key = None
    if trade_mode == "live":
        live_private_key = decrypt_age_keyfile(settings.polymarket_keyfile_path)

    console = Console()
    oddspapi = AsyncOddsPapiClient(
        global_cooldown_ms=settings.oddspapi_global_cooldown_ms_live
    )
    polymarket = AsyncPolymarketClient()
    ws_manager = PolymarketWSManager()
    user_ws: PolymarketUserWSManager | None = None
    fixture_states = FixtureStateManager()

    trade_buffer = LogBuffer(max_lines=10)
    comp_buffer = LogBuffer(max_lines=10)
    log_buffer = LogBuffer(max_lines=12)
    log_buffer.add("Live monitor started")
    log_buffer.add(f"Trading mode: {trade_mode}")
    _configure_logging_for_live(log_buffer, trade_mode=trade_mode)
    paused_event: Event = Event()

    selection = _prompt_match_selection()
    if not selection:
        typer.echo("No matches available.")
        return
    mapping, op_fixture, pm_fixtures, league_name = selection
    mapping = _prompt_orientation_confirmation(mapping, op_fixture, pm_fixtures)
    if mapping is None:
        typer.echo("Orientation cancelled.")
        return
    strategy_mode = _prompt_strategy_mode()
    layout = build_layout(strategy_mode)
    pm_fixture = pm_fixtures[0]  # primary (match_winner)
    pm_question = (pm_fixture.raw_json or {}).get("question")
    pm_condition_id = (pm_fixture.raw_json or {}).get("conditionId")
    condition_ids = [
        str((fixture.raw_json or {}).get("conditionId"))
        for fixture in pm_fixtures
        if (fixture.raw_json or {}).get("conditionId")
    ]
    poller = SingleMatchPoller(
        mapping=mapping,
        op_fixture=op_fixture,
        pm_fixtures=pm_fixtures,
        league_name=league_name,
        oddspapi=oddspapi,
        polymarket=polymarket,
        ws_manager=ws_manager,
        log_buffer=log_buffer,
        spread_factor=spread_factor,
        allow_rest_fallback=trade_mode != "live",
    )
    trader = TradeManager(
        poller=poller,
        ws_manager=ws_manager,
        user_ws=None,
        fixture_states=fixture_states,
        mapping=mapping,
        op_fixture=op_fixture,
        pm_fixtures=pm_fixtures,
        trade_mode=trade_mode,
        edge_threshold=edge_threshold,
        spread_factor=spread_factor,
        log_buffer=log_buffer,
        trade_buffer=trade_buffer,
        live_private_key=live_private_key,
        paused_event=paused_event,
    )
    comp_manager = ComplementArbManager(
        poller=poller,
        ws_manager=ws_manager,
        mapping=mapping,
        pm_fixtures=pm_fixtures,
        trade_mode=trade_mode,
        log_buffer=log_buffer,
        trade_buffer=comp_buffer,
        executor=trader.executor,
        paused_event=paused_event,
    )
    if trade_mode == "live" and settings.user_ws_enabled and trader.executor:
        user_ws = PolymarketUserWSManager(auth=trader.executor.api_creds)
        trader.set_user_ws(user_ws)
    runner = _AsyncRunner(
        poller, trader, comp_manager, user_ws, condition_ids, strategy_mode
    )
    runner.start()

    ui_interval = min(1.0, interval)
    refresh_rate = max(2.0, 1.0 / ui_interval)
    input_buffer = ""
    use_tty = sys.stdin.isatty()
    if use_tty:
        fd = sys.stdin.fileno()
        old_term_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    try:
        with Live(layout, console=console, refresh_per_second=refresh_rate, screen=True) as _live:
            while True:
                if use_tty and select.select([sys.stdin], [], [], 0)[0]:
                    data = os.read(fd, 1024)
                    for ch in data.decode(errors="ignore"):
                        if ch in ("p", "P"):
                            if paused_event.is_set():
                                paused_event.clear()
                                log_buffer.add("Triggers resumed (unpaused)")
                            else:
                                paused_event.set()
                                log_buffer.add("Triggers paused (P)")
                            continue
                        if ch in ("\r", "\n"):
                            command_line = input_buffer.strip()
                            input_buffer = ""
                            if not command_line:
                                continue
                            tokens = command_line.split()
                            command = tokens[0].lower()
                            if command in {"q", "quit"}:
                                raise KeyboardInterrupt
                            if command in {"r", "reselect"}:
                                runner.stop()
                                selection = _prompt_match_selection()
                                if not selection:
                                    raise KeyboardInterrupt
                                mapping, op_fixture, pm_fixtures, league_name = selection
                                mapping = _prompt_orientation_confirmation(mapping, op_fixture, pm_fixtures)
                                if mapping is None:
                                    log_buffer.add("Orientation cancelled; reselect match.")
                                    continue
                                strategy_mode = _prompt_strategy_mode()
                                apply_strategy_mode_to_layout(layout, strategy_mode)
                                pm_fixture = pm_fixtures[0]
                                pm_question = (pm_fixture.raw_json or {}).get("question")
                                pm_condition_id = (pm_fixture.raw_json or {}).get("conditionId")
                                condition_ids = [
                                    str((fixture.raw_json or {}).get("conditionId"))
                                    for fixture in pm_fixtures
                                    if (fixture.raw_json or {}).get("conditionId")
                                ]
                                poller = SingleMatchPoller(
                                    mapping=mapping,
                                    op_fixture=op_fixture,
                                    pm_fixtures=pm_fixtures,
                                    league_name=league_name,
                                    oddspapi=oddspapi,
                                    polymarket=polymarket,
                                    ws_manager=ws_manager,
                                    log_buffer=log_buffer,
                                    spread_factor=spread_factor,
                                    allow_rest_fallback=trade_mode != "live",
                                )
                                trader = TradeManager(
                                    poller=poller,
                                    ws_manager=ws_manager,
                                    user_ws=None,
                                    fixture_states=fixture_states,
                                    mapping=mapping,
                                    op_fixture=op_fixture,
                                    pm_fixtures=pm_fixtures,
                                    trade_mode=trade_mode,
                                    edge_threshold=edge_threshold,
                                    spread_factor=spread_factor,
                                    log_buffer=log_buffer,
                                    trade_buffer=trade_buffer,
                                    live_private_key=live_private_key,
                                    paused_event=paused_event,
                                )
                                comp_manager = ComplementArbManager(
                                    poller=poller,
                                    ws_manager=ws_manager,
                                    mapping=mapping,
                                    pm_fixtures=pm_fixtures,
                                    trade_mode=trade_mode,
                                    log_buffer=log_buffer,
                                    trade_buffer=comp_buffer,
                                    executor=trader.executor,
                                    paused_event=paused_event,
                                )
                                if trade_mode == "live" and settings.user_ws_enabled and trader.executor:
                                    user_ws = PolymarketUserWSManager(auth=trader.executor.api_creds)
                                    trader.set_user_ws(user_ws)
                                runner = _AsyncRunner(
                                    poller, trader, comp_manager, user_ws, condition_ids, strategy_mode
                                )
                                runner.start()
                        elif ch in ("\x7f", "\b"):
                            input_buffer = input_buffer[:-1]
                        elif ch == "\x03":
                            raise KeyboardInterrupt
                        elif ch.isprintable():
                            input_buffer += ch
                elif not use_tty and select.select([sys.stdin], [], [], 0)[0]:
                    raw_line = sys.stdin.readline().strip()
                    if raw_line:
                        log_buffer.add(f"Command ignored (non-tty): {raw_line}")

                snapshots = poller.get_all_snapshots()
                perf = poller.get_perf()
                positions = _get_completed_positions_for_mapping(
                    str(mapping.id), int(settings.live_positions_limit)
                )
                render_layout(
                    layout=layout,
                    snapshots=snapshots,
                    trade_buffer=trade_buffer,
                    comp_buffer=comp_buffer,
                    log_buffer=log_buffer,
                    spread_factor=spread_factor,
                    perf=perf,
                    ws_connected=ws_manager.is_connected(),
                    mode_label=trade_mode,
                    input_text=input_buffer,
                    paused=paused_event.is_set(),
                    pm_question=pm_question,
                    pm_condition_id=pm_condition_id,
                    positions=positions,
                )
                time.sleep(ui_interval)
    finally:
        if use_tty:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_term_settings)
        runner.stop()


def _resolve_trade_mode(mode: str | None) -> str:
    if mode:
        normalized = mode.strip().lower()
        if normalized not in {"paper", "live"}:
            raise typer.BadParameter("mode must be paper or live")
        return normalized
    if not sys.stdin.isatty():
        return "paper"
    response = typer.prompt("Trading mode (paper/live)", default="paper")
    normalized = response.strip().lower()
    if normalized not in {"paper", "live"}:
        raise typer.BadParameter("mode must be paper or live")
    return normalized


def _prompt_match_selection(
    limit: int = 20,
    min_confidence: float | None = None,
) -> tuple[Mapping, Fixture, list[Fixture], str] | None:
    if not sys.stdin.isatty():
        return None
    if min_confidence is None:
        min_confidence = settings.discovery_min_display_confidence
    now = datetime.now(tz=timezone.utc)
    with SessionLocal() as db:
        op_fixture = aliased(Fixture)
        pm_fixture = aliased(Fixture)
        rows = (
            db.execute(
                sa_select(Mapping, op_fixture, pm_fixture)
                .where(Mapping.confidence >= min_confidence)
                .join(op_fixture, Mapping.oddspapi_fixture_id == op_fixture.id)
                .join(pm_fixture, Mapping.polymarket_fixture_id == pm_fixture.id)
                .where(op_fixture.start_time.is_not(None))
                .where(op_fixture.start_time >= now - timedelta(hours=settings.discovery_lookback_hours))
                .where(op_fixture.status != "cancelled")
                .order_by(op_fixture.start_time.asc())
                .limit(limit)
            )
            .all()
        )
        if not rows:
            return None

        # Deduplicate by OddsPapi fixture — keep highest-confidence
        # mapping when multiple PM fixtures map to the same match.
        seen_op_ids: dict[str, int] = {}
        unique_rows: list[tuple[Mapping, Fixture, Fixture]] = []
        for mapping, op_fix, pm_fix in rows:
            op_id = str(op_fix.id)
            if op_id in seen_op_ids:
                prev_idx = seen_op_ids[op_id]
                if mapping.confidence > unique_rows[prev_idx][0].confidence:
                    unique_rows[prev_idx] = (mapping, op_fix, pm_fix)
                continue
            seen_op_ids[op_id] = len(unique_rows)
            unique_rows.append((mapping, op_fix, pm_fix))

        display_rows: list[tuple[int, Mapping, Fixture, Fixture, str, str]] = []
        for idx, (mapping, op_fix, pm_fix) in enumerate(unique_rows, start=1):
            league_name = op_fix.league.name if op_fix.league else ""
            teams = f"{op_fix.team_a_name} vs {op_fix.team_b_name}"
            start_str = (
                op_fix.start_time.astimezone(timezone.utc).strftime("%b %d %H:%M")
                if op_fix.start_time
                else "--"
            )
            # Tag matches that have already started
            live_tag = ""
            if op_fix.start_time and op_fix.start_time <= now:
                live_tag = " [LIVE]"
            label = f"{league_name} {teams} ({start_str} UTC){live_tag}".strip()
            sport = _fixture_sport_label(op_fix)
            display_rows.append((idx, mapping, op_fix, pm_fix, label, sport))

    typer.echo("")
    typer.echo("Select match (next mapped).")
    grouped = {"LoL": [], "CS2": [], "Other": []}
    for row in display_rows:
        sport = row[5]
        if sport in grouped:
            grouped[sport].append(row)
        else:
            grouped["Other"].append(row)
    for sport in ("LoL", "CS2", "Other"):
        rows_for_sport = grouped[sport]
        if not rows_for_sport:
            continue
        typer.echo(f"  -- {sport} --")
        for idx, _, _, _, label, _ in rows_for_sport:
            typer.echo(f"  {idx}. {label}")
    while True:
        response = typer.prompt(f"Match [1-{len(display_rows)}]", default="", show_default=False)
        response = response.strip()
        if not response:
            return None
        try:
            idx = int(response)
        except ValueError:
            typer.echo("Enter a number from the list.")
            continue
        chosen = next((row for row in display_rows if row[0] == idx), None)
        if not chosen:
            typer.echo("Invalid selection; choose from the list.")
            continue
        mapping, op_fix, pm_fix = chosen[1], chosen[2], chosen[3]
        with SessionLocal() as db2:
            pm_fixtures = _resolve_all_markets(db2, pm_fix)
        if not pm_fixtures:
            typer.echo("No match_winner market found for this mapping.")
            return None
        n_games = sum(1 for f in pm_fixtures if f.market_type == "game_winner")
        n_totals = sum(1 for f in pm_fixtures if f.market_type == "totals")
        typer.echo(
            f"  → match_winner + {n_games} game market(s) + {n_totals} totals market(s) loaded"
        )
        league_name = op_fix.league.name if op_fix.league else ""
        return mapping, op_fix, pm_fixtures, league_name


def _fixture_sport_label(fixture: Fixture) -> str:
    league = fixture.league
    sport_code = (getattr(league, "sport", None) or "").lower()
    if sport_code == "lol":
        return "LoL"
    if sport_code == "cs2":
        return "CS2"
    league_name = (league.name if league else "") or ""
    normalized = league_name.lower()
    if "cs2" in normalized or "counter strike" in normalized:
        return "CS2"
    return "LoL" if "lol" in normalized else "Other"


def _prompt_orientation_confirmation(
    mapping: Mapping,
    op_fixture: Fixture,
    pm_fixtures: list[Fixture],
) -> Mapping | None:
    """
    Prompt user to confirm Pinnacle vs Polymarket team order; persist as manual_focus_confirm.
    Returns updated mapping (with new match_details) or None on cancel.
    """
    if not sys.stdin.isatty():
        return mapping
    details = mapping.match_details if isinstance(mapping.match_details, dict) else {}
    if (
        settings.orientation_manual_confirm_skip_if_set
        and details.get("orientation_anchor_source") == "manual_focus_confirm"
        and details.get("orientation_locked")
    ):
        return mapping
    pm_match = pm_fixtures[0]
    pin_1 = op_fixture.team_a_name or "?"
    pin_2 = op_fixture.team_b_name or "?"
    pm_1 = pm_match.team_a_name or "?"
    pm_2 = pm_match.team_b_name or "?"
    typer.echo("")
    typer.echo("Confirm team order (as shown on the books):")
    typer.echo(f"  Pinnacle:  1. {pin_1}  2. {pin_2}")
    typer.echo(f"  Polymarket: 1. {pm_1}  2. {pm_2}")
    while True:
        response = typer.prompt(
            "Do these match the books? [Y]es  [S]wap Pinnacle  [C]ancel",
            default="y",
            show_default=False,
        )
        raw = (response or "").strip().lower()
        if raw in ("y", "yes"):
            team_a_is_home = True
            break
        if raw in ("s", "swap"):
            team_a_is_home = False
            break
        if raw in ("c", "cancel"):
            return None
        typer.echo("Enter Y, S, or C.")
    home_team = op_fixture.team_a_name if team_a_is_home else op_fixture.team_b_name
    away_team = op_fixture.team_b_name if team_a_is_home else op_fixture.team_a_name
    new_details = {**(details or {}), "orientation_locked": True, "team_a_is_home": team_a_is_home}
    new_details["orientation_anchor_source"] = "manual_focus_confirm"
    new_details["home_team"] = home_team
    new_details["away_team"] = away_team
    with SessionLocal() as db:
        m = db.get(Mapping, mapping.id)
        if not m:
            return mapping
        m.match_details = new_details
        db.commit()
        db.refresh(m)
        return m


StrategyMode = Literal["lead_lag", "binary", "both"]


def _prompt_strategy_mode() -> StrategyMode:
    """Prompt for strategy: Lead Lag, Binary, or Both. Non-tty uses default or STRATEGY_MODE env."""
    if not sys.stdin.isatty():
        raw = os.environ.get("STRATEGY_MODE", "both").strip().lower()
        if raw in ("lead_lag", "l", "leadlag"):
            return "lead_lag"
        if raw in ("binary", "b"):
            return "binary"
        return "both"
    typer.echo("")
    typer.echo("Strategy: [L]ead Lag  [B]inary  [A]ll (both)")
    while True:
        response = typer.prompt("Choice", default="a", show_default=False)
        raw = (response or "").strip().lower()
        if raw in ("l", "lead_lag", "leadlag"):
            return "lead_lag"
        if raw in ("b", "binary"):
            return "binary"
        if raw in ("a", "all", "both"):
            return "both"
        typer.echo("Enter L, B, or A.")


def _get_completed_positions_for_mapping(mapping_id: str, limit: int) -> list:
    """Return recent completed positions (confirmed entry + closed exit)."""
    with SessionLocal() as db:
        rows = (
            db.execute(
                sa_select(Position)
                .where(Position.mapping_id == mapping_id)
                .where(Position.status == "confirmed")
                .where(Position.closed_at.is_not(None))
                .where(Position.exit_price.is_not(None))
                .order_by(
                    Position.closed_at.desc(),
                    Position.opened_at.desc(),
                )
                .limit(limit)
            )
            .scalars().all()
        )
        return [r for r in rows]


def _resolve_all_markets(db, pm_fixture: Fixture) -> list[Fixture]:
    """Return [match_winner, game..., totals...] for the mapping.

    Queries the event's children to find the match_winner, game_winner,
    and totals fixtures.
    """
    if pm_fixture.market_type == "match_winner":
        parent_id = pm_fixture.parent_fixture_id
    elif pm_fixture.market_type == "event":
        parent_id = pm_fixture.id
    else:
        return []

    if not parent_id:
        if pm_fixture.market_type == "match_winner":
            return [pm_fixture]
        return []

    children = (
        db.execute(
            sa_select(Fixture)
            .where(Fixture.parent_fixture_id == parent_id)
            .order_by(Fixture.game_number.asc().nulls_first())
        )
        .scalars()
        .all()
    )

    match_winner = next(
        (c for c in children if c.market_type == "match_winner"), None
    )
    games = sorted(
        [c for c in children if c.market_type == "game_winner"],
        key=lambda g: g.game_number or 0,
    )
    totals = sorted(
        [c for c in children if c.market_type == "totals"],
        key=lambda t: (t.game_number or 0, t.line_value or 0.0),
    )

    result: list[Fixture] = []
    if match_winner:
        result.append(match_winner)
    result.extend(games)
    result.extend(totals)
    return result


class _AsyncRunner:
    def __init__(
        self,
        poller: SingleMatchPoller,
        trader: TradeManager,
        comp_manager: ComplementArbManager,
        user_ws: PolymarketUserWSManager | None,
        condition_ids: list[str],
        strategy_mode: StrategyMode,
    ) -> None:
        self._poller = poller
        self._trader = trader
        self._comp_manager = comp_manager
        self._user_ws = user_ws
        self._condition_ids = condition_ids
        self._strategy_mode = strategy_mode
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    def start(self) -> None:
        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stop_event = asyncio.Event()
            self._loop = loop
            self._stop_event = stop_event

            async def _main() -> None:
                user_ws_task: asyncio.Task | None = None
                if self._user_ws:
                    user_ws_task = asyncio.create_task(self._user_ws.run())
                    await self._user_ws.update_subscriptions(self._condition_ids)
                await self._poller.start()
                if self._strategy_mode in ("lead_lag", "both"):
                    await self._trader.start()
                if self._strategy_mode in ("binary", "both"):
                    await self._comp_manager.start()
                await stop_event.wait()
                await self._comp_manager.stop()
                await self._trader.stop()
                await self._poller.stop()
                if self._user_ws:
                    await self._user_ws.stop()
                if user_ws_task:
                    user_ws_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await user_ws_task

            loop.run_until_complete(_main())

        self._thread = Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5)
