"""
Live TUI monitor for LoL Lead-Lag Arbitrage Bot (read-only).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from sqlalchemy import select

from types import SimpleNamespace

from shared.config import settings
from shared.db import SessionLocal
from shared.edge import NetEdgeResult, compute_net_edges, devig_two_way_decimal
from shared.models import Fixture, Mapping
from shared.oddspapi_client import OddsPapiClient
from shared.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


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


@dataclass
class LiveState:
    snapshots: list[MatchSnapshot]
    recently_ended: list[MatchSnapshot]
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

    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        self._buffer.add(message)


def _configure_logging_for_live(system_log_buffer: LogBuffer) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = LogBufferHandler(system_log_buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    root.setLevel(logging.INFO)


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
    oddspapi = OddsPapiClient(
        global_cooldown_ms=settings.oddspapi_global_cooldown_ms_live
    )
    polymarket = PolymarketClient()

    alert_cooldown = 30
    last_alert: dict[str, float] = {}
    alerts_buffer = LogBuffer(max_lines=6)
    log_buffer = LogBuffer(max_lines=8)
    log_buffer.add("Live monitor started")
    system_log_buffer = LogBuffer(max_lines=8)
    _configure_logging_for_live(system_log_buffer)
    recently_ended: dict[str, MatchSnapshot] = {}
    ended_ttl_seconds = 15 * 60
    pm_status_cache: dict[str, tuple[datetime, dict]] = {}
    pm_status_ttl = timedelta(seconds=5)

    layout = _build_layout()

    refresh_rate = max(10.0, 1.0 / interval)
    state_lock = Lock()
    live_state = LiveState(
        snapshots=[],
        recently_ended=[],
        focus_match=None,
        focus_game1=None,
        last_update=None,
        last_loop_duration=None,
        perf=None,
    )

    def data_loop() -> None:
        last_candidates_refresh = 0.0
        cached_candidates: list[tuple[Mapping, Fixture, Fixture, str]] = []
        candidates_refresh_seconds = 120.0
        while True:
            loop_start = time.perf_counter()
            now = datetime.now(tz=timezone.utc)
            for key in list(recently_ended.keys()):
                if (now - recently_ended[key].updated_at).total_seconds() > ended_ttl_seconds:
                    recently_ended.pop(key, None)
            with SessionLocal() as db:
                refresh_due = (time.time() - last_candidates_refresh) >= candidates_refresh_seconds
                if refresh_due or not cached_candidates:
                    cached_candidates = _load_candidate_mappings(
                        db, now, lookahead_minutes, min_confidence, live_only=False
                    )
                    last_candidates_refresh = time.time()
                candidates = list(cached_candidates)
            if not candidates:
                log_buffer.add(
                    f"No matches (min_confidence={min_confidence:.2f})",
                    key="no-candidates",
                    cooldown_seconds=30,
                )

            token_ids: list[str] = []
            for _, _, pm_fixture, _ in candidates:
                token_ids.extend(_parse_token_ids(pm_fixture.raw_json or {}))
            perf = PerfStats()
            books_start = time.perf_counter()
            books_by_token = (
                polymarket.get_orderbooks_batch(token_ids) if token_ids else {}
            )
            books_ms = (time.perf_counter() - books_start) * 1000
            logger.info("perf clob_batch_ms=%.1f tokens=%d", books_ms, len(token_ids))
            perf.clob_batch_ms = books_ms

            live_snapshots: list[MatchSnapshot] = []
            for mapping, op_fixture, pm_fixture, league_name in candidates:
                try:
                    gamma_start = time.perf_counter()
                    pm_latest = _get_pm_latest_cached(
                        polymarket,
                        pm_status_cache,
                        str(pm_fixture.source_id),
                        now,
                        pm_status_ttl,
                    )
                    gamma_ms = (time.perf_counter() - gamma_start) * 1000
                    perf.gamma_ms_total += gamma_ms
                    perf.gamma_calls += 1
                    snapshot, ended = _build_snapshot(
                        mapping=mapping,
                        op_fixture=op_fixture,
                        pm_fixture=pm_fixture,
                        oddspapi=oddspapi,
                        pm_latest=pm_latest,
                        books_by_token=books_by_token,
                        spread_factor=spread_factor,
                        now=now,
                        league_name=league_name,
                        stale_minutes=stale_minutes,
                        log_buffer=log_buffer,
                        require_live=True,
                        perf=perf,
                    )
                    if snapshot:
                        if ended:
                            recently_ended[mapping.id] = snapshot
                        else:
                            live_snapshots.append(snapshot)
                except Exception as exc:  # pragma: no cover - defensive
                    log_buffer.add(
                        f"Error for {op_fixture.source_id}: {exc}",
                        key=f"error:{op_fixture.source_id}",
                        cooldown_seconds=30,
                    )

            focus_match, focus_game1 = _select_focuses(
                live_snapshots, list(recently_ended.values())
            )

            # Alerting
            if (
                focus_match
                and focus_match.edge.best_edge is not None
                and focus_match.edge.best_edge >= edge_threshold
            ):
                last_ts = last_alert.get(focus_match.mapping_id, 0.0)
                if time.time() - last_ts >= alert_cooldown:
                    alert_text = (
                        f"{focus_match.match}: edge {focus_match.edge.best_edge:+.2%} "
                        f"({focus_match.edge.best_side})"
                    )
                    last_alert[focus_match.mapping_id] = time.time()
                    logger.info("ALERT %s", alert_text)
                    alerts_buffer.add(alert_text)
                    log_buffer.add(f"ALERT {alert_text}")

            loop_end = time.perf_counter()
            loop_duration = loop_end - loop_start
            logger.info("Loop duration: %.2fs", loop_duration)
            perf.loop_ms = loop_duration * 1000

            with state_lock:
                live_state.snapshots = live_snapshots
                live_state.recently_ended = list(recently_ended.values())
                live_state.focus_match = focus_match
                live_state.focus_game1 = focus_game1
                live_state.last_update = now
                live_state.last_loop_duration = loop_duration
                live_state.perf = perf

            time.sleep(interval)

    data_thread = Thread(target=data_loop, name="live-data-loop", daemon=True)
    data_thread.start()

    ui_interval = min(1.0, interval)
    refresh_rate = max(2.0, 1.0 / ui_interval)
    with Live(layout, console=console, refresh_per_second=refresh_rate, screen=True):
        while True:
            with state_lock:
                snapshots = list(live_state.snapshots)
                recently_ended_list = list(live_state.recently_ended)
                focus_match = live_state.focus_match
                focus_game1 = live_state.focus_game1
                perf = live_state.perf
            _render_layout(
                layout=layout,
                snapshots=snapshots,
                recently_ended=recently_ended_list,
                focus_match=focus_match,
                focus_game1=focus_game1,
                edge_threshold=edge_threshold,
                alerts_buffer=alerts_buffer,
                log_buffer=log_buffer,
                system_log_buffer=system_log_buffer,
                spread_factor=spread_factor,
                perf=perf,
            )
            time.sleep(ui_interval)


def _load_candidate_mappings(
    db,
    now: datetime,
    lookahead_minutes: int,
    min_confidence: float,
    live_only: bool = False,
) -> list[tuple[Mapping, Fixture, Fixture, str]]:
    window_start = now - timedelta(hours=4)
    window_end = now + timedelta(minutes=lookahead_minutes)

    mappings = db.execute(
        select(Mapping).where(Mapping.confidence >= min_confidence)
    ).scalars().all()

    results: list[tuple[Mapping, Fixture, Fixture, str]] = []
    for mapping in mappings:
        op_fixture = db.get(Fixture, mapping.oddspapi_fixture_id)
        pm_fixture = db.get(Fixture, mapping.polymarket_fixture_id)
        if not op_fixture or not pm_fixture:
            continue
        if pm_fixture.market_type not in {"event", "match_winner"}:
            continue
        league_name = op_fixture.league.name if op_fixture.league else ""

        include_match = False
        if op_fixture.status == "live":
            include_match = True
        elif not live_only and op_fixture.start_time and window_start <= op_fixture.start_time <= window_end:
            include_match = True

        if include_match:
            parent_fixture = pm_fixture
            match_market = pm_fixture
            if pm_fixture.market_type == "event":
                child_markets = db.execute(
                    select(Fixture).where(Fixture.parent_fixture_id == pm_fixture.id)
                ).scalars().all()
                match_market = next(
                    (c for c in child_markets if c.market_type == "match_winner"),
                    None,
                )
            else:
                child_markets = db.execute(
                    select(Fixture).where(Fixture.parent_fixture_id == pm_fixture.id)
                ).scalars().all()

            if not match_market:
                continue

            results.append((mapping, op_fixture, match_market, league_name))
            for child in child_markets:
                if child.market_type == "match_winner":
                    continue
                results.append((mapping, op_fixture, child, league_name))

            series_type = match_market.series_type or parent_fixture.series_type
            max_games = _series_length(series_type)
            if max_games:
                has_final_game = any(
                    (c.market_type == "game_winner" and c.game_number == max_games)
                    for c in child_markets
                )
                if not has_final_game:
                    proxy = SimpleNamespace(
                        id=match_market.id,
                        source=match_market.source,
                        source_id=match_market.source_id,
                        league_id=match_market.league_id,
                        team_a_id=match_market.team_a_id,
                        team_b_id=match_market.team_b_id,
                        team_a_name=match_market.team_a_name,
                        team_b_name=match_market.team_b_name,
                        start_time=match_market.start_time,
                        status=match_market.status,
                        has_odds=match_market.has_odds,
                        market_type="game_winner",
                        game_number=max_games,
                        series_type=series_type,
                        parent_fixture_id=parent_fixture.id,
                        raw_json=match_market.raw_json,
                    )
                    results.append((mapping, op_fixture, proxy, league_name))

    return results


def _build_snapshot(
    mapping: Mapping,
    op_fixture: Fixture,
    pm_fixture: Fixture,
    oddspapi: OddsPapiClient,
    pm_latest: dict | None,
    books_by_token: dict[str, dict],
    spread_factor: float,
    now: datetime,
    league_name: str,
    stale_minutes: int,
    log_buffer: LogBuffer,
    require_live: bool = False,
    perf: PerfStats | None = None,
) -> tuple[MatchSnapshot | None, bool]:
    odds_a = None
    odds_b = None
    p_ref_a = None
    p_ref_b = None
    market_type = pm_fixture.market_type or "unknown"
    game_number = pm_fixture.game_number
    ended = False
    pm_closed = False
    pm_active = True
    pm_resolution = ""
    pm_winner = None
    odds_status_label = None
    odds_live = False
    try:
        odds_start = time.perf_counter()
        odds_payload = oddspapi.get_odds(op_fixture.source_id)
        odds_ms = (time.perf_counter() - odds_start) * 1000
        logger.info("perf oddspapi_ms=%.1f fixture=%s", odds_ms, op_fixture.source_id)
        if perf:
            perf.oddspapi_ms_total += odds_ms
            perf.oddspapi_calls += 1
        status_id = odds_payload.get("statusId")
        if require_live and status_id != 1:
            odds_status_label = f"ODDS {status_id}"
            log_buffer.add(
                f"Show non-live {op_fixture.source_id}: statusId={status_id}",
                key=f"show-nonlive:{op_fixture.source_id}",
                cooldown_seconds=60,
            )
        if status_id in (2, 3):
            ended = True
        # Only treat as stale/ended for game markets; match_winner should stay open.
        if status_id == 0 and market_type == "game_winner":
            start_ref = pm_fixture.start_time or op_fixture.start_time
            if start_ref:
                since_start = now - start_ref
            else:
                since_start = None
            if since_start and since_start.total_seconds() > stale_minutes * 60:
                ended = True
        if market_type == "match_winner":
            pinnacle_odds = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
        elif market_type == "game_winner" and game_number:
            pinnacle_odds = OddsPapiClient.extract_pinnacle_game_winner(
                odds_payload,
                game_number,
            )
            if not pinnacle_odds:
                series_len = _series_length(getattr(pm_fixture, "series_type", None))
                moneyline = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
                if moneyline and series_len and game_number == series_len:
                    pinnacle_odds = moneyline
                    log_buffer.add(
                        f"OddsPapi: using match moneyline for final game {game_number} "
                        f"({op_fixture.source_id})",
                        key=f"odds-final-game:{op_fixture.source_id}:{game_number}",
                        cooldown_seconds=120,
                    )
                elif moneyline:
                    log_buffer.add(
                        f"OddsPapi: no game {game_number} market for {op_fixture.source_id}; "
                        "only moneyline available",
                        key=f"odds-no-game:{op_fixture.source_id}:{game_number}",
                        cooldown_seconds=120,
                    )
        else:
            pinnacle_odds = {}
        home = pinnacle_odds.get("home")
        away = pinnacle_odds.get("away")
        if home and away:
            odds_a = home.get("price")
            odds_b = away.get("price")
            if odds_a is not None and odds_b is not None:
                p_ref_a, p_ref_b = devig_two_way_decimal(odds_a, odds_b)
            odds_changed_at = _latest_changed_at([home.get("changed_at"), away.get("changed_at")])
            odds_live = status_id == 1 or _recent_enough(odds_changed_at, now, minutes=10)
        else:
            if not ended and status_id == 1:
                log_buffer.add(
                    f"Odds missing for {op_fixture.source_id} ({market_type}"
                    f"{' G'+str(game_number) if game_number else ''})",
                    key=f"odds-missing:{op_fixture.source_id}:{market_type}:{game_number}",
                    cooldown_seconds=30,
                )
        if status_id == 0 and odds_live:
            log_buffer.add(
                f"Odds live by change time {op_fixture.source_id}: statusId=0",
                key=f"odds-live-by-change:{op_fixture.source_id}",
                cooldown_seconds=60,
            )
        log_buffer.add(
            f"Odds status {op_fixture.source_id}: statusId={status_id}",
            key=f"odds-status:{op_fixture.source_id}",
            cooldown_seconds=60,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log_buffer.add(f"Odds error {op_fixture.source_id}: {exc}")

    # Refresh Polymarket market status to detect resolved/closed markets
    if isinstance(pm_latest, dict):
        pm_closed = bool(pm_latest.get("closed"))
        pm_active = bool(pm_latest.get("active", True))
        pm_resolution = _extract_pm_resolution_status(pm_latest)
        pm_winner = _extract_pm_winner(pm_latest)
        if _pm_is_ended(pm_latest, pm_closed, pm_active, pm_resolution, now, market_type):
            ended = True
        log_buffer.add(
            f"PM status {pm_fixture.source_id}: closed={pm_closed} active={pm_active} uma={pm_resolution or 'n/a'}",
            key=f"pm-status:{pm_fixture.source_id}",
            cooldown_seconds=60,
        )

    outcomes = _parse_outcomes(pm_fixture.raw_json or {})
    token_ids = _parse_token_ids(pm_fixture.raw_json or {})
    outcome_pairs = _pair_outcomes_with_tokens(op_fixture, outcomes, token_ids)

    source_note = "CLOB"
    bid_a = ask_a = bid_b = ask_b = None
    mid_a = mid_b = None

    if outcome_pairs and all(p[1] for p in outcome_pairs):
        for outcome_name, token_id in outcome_pairs:
            book = books_by_token.get(token_id) or {}
            bid = book.get("best_bid")
            ask = book.get("best_ask")
            if _is_team_a(op_fixture, outcome_name):
                bid_a, ask_a = bid, ask
            elif _is_team_b(op_fixture, outcome_name):
                bid_b, ask_b = bid, ask
        if len(outcome_pairs) >= 2:
            if bid_a is None or ask_a is None:
                book = books_by_token.get(outcome_pairs[0][1]) or {}
                bid_a, ask_a = book.get("best_bid"), book.get("best_ask")
            if bid_b is None or ask_b is None:
                book = books_by_token.get(outcome_pairs[1][1]) or {}
                bid_b, ask_b = book.get("best_bid"), book.get("best_ask")
    else:
        source_note = "Gamma outcomePrices"
        outcome_prices = _parse_outcome_prices(pm_fixture.raw_json or {})
        if len(outcome_prices) >= 2:
            ask_a = bid_a = outcome_prices[0]
            ask_b = bid_b = outcome_prices[1]
        else:
            source_note = "No CLOB tokens"
            log_buffer.add(f"No CLOB tokens for {pm_fixture.source_id}")

    if bid_a is not None and ask_a is not None:
        mid_a = (bid_a + ask_a) / 2
    if bid_b is not None and ask_b is not None:
        mid_b = (bid_b + ask_b) / 2

    # If there is active orderbook liquidity, do not treat as ended.
    if ended and (
        (bid_a is not None or ask_a is not None) or (bid_b is not None or ask_b is not None)
    ):
        ended = False
        log_buffer.add(
            f"Market re-opened (liquidity) {op_fixture.source_id} ({market_type}"
            f"{' G'+str(game_number) if game_number else ''})",
            key=f"ended-override:{op_fixture.source_id}:{market_type}:{game_number}",
            cooldown_seconds=60,
        )

    if pm_fixture.raw_json and pm_fixture.raw_json.get("closed") is True:
        ended = True

    if ended:
        log_buffer.add(
            f"Market ended for {op_fixture.source_id} ({market_type}"
            f"{' G'+str(game_number) if game_number else ''})",
            key=f"ended:{op_fixture.source_id}:{market_type}:{game_number}",
            cooldown_seconds=60,
        )
        edge = NetEdgeResult(edge_buy_a=None, edge_buy_b=None, best_edge=None, best_side=None)
        match_name = f"{op_fixture.team_a_name} vs {op_fixture.team_b_name}"
        display_status = odds_status_label or ("LIVE*" if odds_live else None) or pm_resolution
        return MatchSnapshot(
            mapping_id=str(mapping.id),
            league=league_name,
            match=match_name,
            start_time=op_fixture.start_time,
            market_type=market_type,
            game_number=game_number,
            pm_resolution_status=display_status,
            pm_winner=pm_winner,
            p_ref_a=p_ref_a,
            p_ref_b=p_ref_b,
            odds_a=odds_a,
            odds_b=odds_b,
            bid_a=bid_a,
            ask_a=ask_a,
            bid_b=bid_b,
            ask_b=ask_b,
            mid_a=mid_a,
            mid_b=mid_b,
            source_note=source_note,
            edge=edge,
            updated_at=now,
            ended=True,
        ), True

    edge = compute_net_edges(
        p_ref_a=p_ref_a,
        p_ref_b=p_ref_b,
        bid_a=bid_a,
        ask_a=ask_a,
        bid_b=bid_b,
        ask_b=ask_b,
        spread_factor=spread_factor,
    )

    match_name = f"{op_fixture.team_a_name} vs {op_fixture.team_b_name}"

    display_status = odds_status_label or ("LIVE*" if odds_live else None) or pm_resolution
    return MatchSnapshot(
        mapping_id=str(mapping.id),
        league=league_name,
        match=match_name,
        start_time=op_fixture.start_time,
        market_type=market_type,
        game_number=game_number,
        pm_resolution_status=display_status,
        pm_winner=pm_winner,
        p_ref_a=p_ref_a,
        p_ref_b=p_ref_b,
        odds_a=odds_a,
        odds_b=odds_b,
        bid_a=bid_a,
        ask_a=ask_a,
        bid_b=bid_b,
        ask_b=ask_b,
        mid_a=mid_a,
        mid_b=mid_b,
        source_note=source_note,
        edge=edge,
        updated_at=now,
        ended=False,
    ), False


def _build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="main", ratio=4),
        Layout(name="logs", size=22),
    )
    layout["logs"].split_column(
        Layout(name="log_stream", ratio=2),
        Layout(name="system_logs", ratio=2),
        Layout(name="perf", ratio=1),
    )
    layout["main"].split_column(
        Layout(name="matches", ratio=1),
        Layout(name="odds", ratio=3),
        Layout(name="alerts", ratio=1),
    )
    layout["odds"].split_row(
        Layout(name="sharps", ratio=1),
        Layout(name="poly", ratio=1),
    )
    return layout


def _render_layout(
    layout: Layout,
    snapshots: list[MatchSnapshot],
    recently_ended: list[MatchSnapshot],
    focus_match: MatchSnapshot | None,
    focus_game1: MatchSnapshot | None,
    edge_threshold: float,
    alerts_buffer: LogBuffer,
    log_buffer: LogBuffer,
    system_log_buffer: LogBuffer,
    spread_factor: float,
    perf: PerfStats | None,
) -> None:
    layout["matches"].update(
        Panel(
            _build_matches_table(snapshots, recently_ended, edge_threshold),
            title="Live Matches",
        )
    )
    layout["sharps"].update(
        Panel(
            _build_sharps_panel(
                focus_match,
                focus_game1,
                spread_factor,
            ),
            title="Sharps Odds",
        )
    )
    layout["poly"].update(Panel(_build_poly_panel(focus_match, focus_game1), title="Poly Odds"))
    layout["alerts"].update(Panel(_build_alerts_panel(alerts_buffer), title="Alerts"))
    layout["log_stream"].update(Panel(log_buffer.render(), title="Log Stream"))
    layout["system_logs"].update(Panel(system_log_buffer.render(), title="System Logs"))
    layout["perf"].update(Panel(_build_perf_panel(perf), title="Performance"))


def _build_matches_table(
    snapshots: list[MatchSnapshot],
    recently_ended: list[MatchSnapshot],
    edge_threshold: float,
) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("League", style="cyan", no_wrap=True)
    table.add_column("Match")
    table.add_column("Market", no_wrap=True)
    table.add_column("Start", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Edge", justify="right")
    table.add_column("Side", no_wrap=True)
    table.add_column("Result", no_wrap=True)

    if not snapshots:
        if recently_ended:
            table.add_row(
                "-", "No Matches Live and Recently Ended", "-", "-", "-", "-", "-", "-"
            )
        else:
            table.add_row("-", "No Matches Live", "-", "-", "-", "-", "-", "-")

    for ended in recently_ended:
        market_label = _format_market_label(ended.market_type, ended.game_number)
        status_label = ended.pm_resolution_status or "ENDED"
        winner = _format_pm_winner(ended.pm_winner)
        table.add_row(
            ended.league or "-",
            ended.match,
            market_label,
            "--:--",
            status_label,
            "-",
            "-",
            winner,
            style="dim",
        )

    for snap in snapshots:
        edge = snap.edge.best_edge
        edge_str = "-" if edge is None else f"{edge:+.2%}"
        side = snap.edge.best_side or "-"
        start_str = snap.start_time.strftime("%H:%M") if snap.start_time else "--:--"
        market_label = _format_market_label(snap.market_type, snap.game_number)
        status_label = snap.pm_resolution_status or "LIVE"
        winner = _format_pm_winner(snap.pm_winner)

        style = "bold yellow" if edge is not None and edge >= edge_threshold else ""
        table.add_row(
            snap.league or "-",
            snap.match,
            market_label,
            start_str,
            status_label,
            edge_str,
            side,
            winner,
            style=style,
        )
    return table


def _build_sharps_panel(
    focus_match: MatchSnapshot | None,
    focus_game1: MatchSnapshot | None,
    spread_factor: float,
) -> Text:
    text = Text()
    if not focus_match and not focus_game1:
        text.append("No match selected.")
        return text

    header = focus_match.match if focus_match else focus_game1.match
    text.append(f"{header}\n", style="bold")
    _append_sharps_section(text, "Match Winner", focus_match, spread_factor)
    game_label = "Game Winner"
    if focus_game1 and focus_game1.game_number:
        game_label = f"Game {focus_game1.game_number} Winner"
    _append_sharps_section(text, game_label, focus_game1, spread_factor)
    return text


def _build_poly_panel(
    focus_match: MatchSnapshot | None,
    focus_game1: MatchSnapshot | None,
) -> Text:
    text = Text()
    if not focus_match and not focus_game1:
        text.append("No match selected.")
        return text

    header = focus_match.match if focus_match else focus_game1.match
    text.append(f"{header}\n", style="bold")
    _append_poly_section(text, "Match Winner", focus_match)
    game_label = "Game Winner"
    if focus_game1 and focus_game1.game_number:
        game_label = f"Game {focus_game1.game_number} Winner"
    _append_poly_section(text, game_label, focus_game1)
    return text


def _build_alerts_panel(alerts_buffer: LogBuffer) -> Text:
    text = Text()
    if not getattr(alerts_buffer, "_lines", None):
        text.append("No alerts yet.")
        return text
    return alerts_buffer.render()


def _build_perf_panel(perf: PerfStats | None) -> Text:
    text = Text()
    if not perf:
        text.append("No perf data yet.")
        return text
    loop_ms = perf.loop_ms or 0.0
    clob_ms = perf.clob_batch_ms or 0.0
    odds_total = perf.oddspapi_ms_total
    odds_calls = perf.oddspapi_calls
    odds_avg = (odds_total / odds_calls) if odds_calls else 0.0
    gamma_total = perf.gamma_ms_total
    gamma_calls = perf.gamma_calls
    gamma_avg = (gamma_total / gamma_calls) if gamma_calls else 0.0
    text.append(f"Total Loop: {loop_ms/1000:.2f}s\n")
    text.append(f"OddsPapi:  {odds_total:.0f}ms ({odds_calls} calls, {odds_avg:.0f}ms avg)\n")
    text.append(f"CLOB:      {clob_ms:.0f}ms\n")
    text.append(f"Gamma:     {gamma_total:.0f}ms ({gamma_calls} calls, {gamma_avg:.0f}ms avg)\n")
    return text


def _select_focuses(
    live_snapshots: list[MatchSnapshot],
    ended_snapshots: list[MatchSnapshot],
) -> tuple[MatchSnapshot | None, MatchSnapshot | None]:
    if not live_snapshots and not ended_snapshots:
        return None, None
    pool = live_snapshots if live_snapshots else ended_snapshots
    best_match_name = max(
        pool,
        key=lambda s: abs(s.edge.best_edge) if s.edge.best_edge is not None else 0.0,
    ).match

    match_winner = None
    best_game = None
    for snap in pool:
        if snap.match != best_match_name:
            continue
        if snap.market_type == "match_winner":
            match_winner = snap
        if snap.market_type == "game_winner":
            if not best_game:
                best_game = snap
            elif snap.game_number and best_game.game_number:
                if snap.game_number > best_game.game_number:
                    best_game = snap
            elif snap.game_number and not best_game.game_number:
                best_game = snap

    if not match_winner:
        match_winner = next((s for s in pool if s.match == best_match_name), None)
    return match_winner, best_game


def _append_sharps_section(
    text: Text,
    label: str,
    snap: MatchSnapshot | None,
    spread_factor: float,
) -> None:
    text.append(f"\n{label}\n", style="bold")
    if not snap:
        text.append("  unavailable\n")
        return
    _append_compare_focus(text, snap, spread_factor)
    a_odds = f"{snap.odds_a:.2f}" if snap.odds_a is not None else "n/a"
    b_odds = f"{snap.odds_b:.2f}" if snap.odds_b is not None else "n/a"
    a_pref = f"{snap.p_ref_a:.2%}" if snap.p_ref_a is not None else "n/a"
    b_pref = f"{snap.p_ref_b:.2%}" if snap.p_ref_b is not None else "n/a"
    text.append(f"  A odds: {a_odds} | B odds: {b_odds}\n")
    text.append(f"  A p_ref: {a_pref} | B p_ref: {b_pref}\n")
    text.append(f"  Updated: {snap.updated_at.strftime('%H:%M:%S UTC')}\n")



def _append_poly_section(
    text: Text,
    label: str,
    snap: MatchSnapshot | None,
) -> None:
    text.append(f"\n{label}\n", style="bold")
    if not snap:
        text.append("  unavailable\n")
        return
    if snap.pm_resolution_status or snap.pm_winner:
        status = snap.pm_resolution_status or "n/a"
        winner = _format_pm_winner(snap.pm_winner)
        text.append(f"  Status: {status} | Winner: {winner}\n")
    text.append(f"  Source: {snap.source_note}\n")
    if snap.bid_a is not None and snap.ask_a is not None:
        spread_a = max(snap.ask_a - snap.bid_a, 0.0)
        text.append(
            f"  A bid/ask: {snap.bid_a:.3f}/{snap.ask_a:.3f} (spr {spread_a:.3f})\n"
        )
    if snap.bid_b is not None and snap.ask_b is not None:
        spread_b = max(snap.ask_b - snap.bid_b, 0.0)
        text.append(
            f"  B bid/ask: {snap.bid_b:.3f}/{snap.ask_b:.3f} (spr {spread_b:.3f})\n"
        )
    text.append(f"  Updated: {snap.updated_at.strftime('%H:%M:%S UTC')}\n")


def _parse_outcomes(raw: dict) -> list[str]:
    outcomes = raw.get("outcomes") or []
    if isinstance(outcomes, str):
        try:
            import json
            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    return [str(o) for o in outcomes if o]


def _parse_token_ids(raw: dict) -> list[str]:
    token_ids = raw.get("clobTokenIds") or []
    if isinstance(token_ids, str):
        try:
            import json
            token_ids = json.loads(token_ids)
        except (json.JSONDecodeError, TypeError):
            token_ids = []
    return [str(t) for t in token_ids if t]


def _parse_outcome_prices(raw: dict) -> list[float]:
    prices = raw.get("outcomePrices") or []
    if isinstance(prices, str):
        try:
            import json
            prices = json.loads(prices)
        except (json.JSONDecodeError, TypeError):
            prices = []
    parsed: list[float] = []
    for p in prices:
        try:
            parsed.append(float(p))
        except (ValueError, TypeError):
            continue
    return parsed


def _pair_outcomes_with_tokens(
    op_fixture: Fixture,
    outcomes: list[str],
    token_ids: list[str],
) -> list[tuple[str, str]]:
    if not outcomes:
        outcomes = [op_fixture.team_a_name or "Team A", op_fixture.team_b_name or "Team B"]
    pairs = []
    for idx, outcome in enumerate(outcomes):
        token_id = token_ids[idx] if idx < len(token_ids) else ""
        pairs.append((outcome, token_id))
    return pairs


def _normalize(value: str | None) -> str:
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())


def _is_team_a(fixture: Fixture, outcome: str) -> bool:
    return _normalize(outcome) == _normalize(fixture.team_a_name)


def _is_team_b(fixture: Fixture, outcome: str) -> bool:
    return _normalize(outcome) == _normalize(fixture.team_b_name)


def _extract_pm_resolution_status(pm_latest: dict) -> str:
    status = pm_latest.get("umaResolutionStatus")
    if isinstance(status, str):
        return status.upper()
    statuses = pm_latest.get("umaResolutionStatuses")
    if isinstance(statuses, list) and statuses:
        last = statuses[-1]
        if isinstance(last, dict) and "status" in last:
            return str(last.get("status")).upper()
    return ""


def _pm_is_ended(
    pm_latest: dict,
    pm_closed: bool,
    pm_active: bool,
    pm_resolution: str,
    now: datetime,
    market_type: str | None,
) -> bool:
    if pm_closed or not pm_active:
        return True
    # UMA proposed/disputed should be treated as ended for display purposes.
    if pm_resolution in {"PROPOSED", "DISPUTED", "RESOLVED", "SETTLED"}:
        return True
    end_date = pm_latest.get("endDateIso") or pm_latest.get("endDate")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt <= now:
                return market_type == "game_winner"
        except ValueError:
            pass
    return False


def _get_pm_latest_cached(
    polymarket: PolymarketClient,
    cache: dict[str, tuple[datetime, dict]],
    market_id: str,
    now: datetime,
    ttl: timedelta,
) -> dict | None:
    cached = cache.get(market_id)
    if cached and (now - cached[0]) <= ttl:
        return cached[1]
    latest = polymarket.get_market_by_id(market_id)
    if isinstance(latest, dict):
        cache[market_id] = (now, latest)
        return latest
    return None


def _format_market_label(market_type: str | None, game_number: int | None) -> str:
    if market_type == "match_winner":
        return "MATCH (ML)"
    if market_type == "game_winner":
        return f"GAME {game_number}" if game_number else "GAME"
    return market_type or "-"


def _series_length(series_type: str | None) -> int | None:
    if not series_type:
        return None
    normalized = series_type.lower()
    if normalized == "bo1":
        return 1
    if normalized == "bo3":
        return 3
    if normalized == "bo5":
        return 5
    return None


def _format_pm_winner(value: str | None) -> str:
    if not value:
        return "-"
    return value.strip()


def _latest_changed_at(values: list[str | None]) -> datetime | None:
    for value in values:
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


def _recent_enough(value: datetime | None, now: datetime, minutes: int) -> bool:
    if not value:
        return False
    return (now - value).total_seconds() <= minutes * 60


def _append_compare_focus(text: Text, snap: MatchSnapshot, spread_factor: float) -> None:
    if (
        snap.p_ref_a is not None
        and snap.ask_a is not None
        and snap.bid_a is not None
    ):
        spread_a = max(snap.ask_a - snap.bid_a, 0.0)
        edge_a = snap.p_ref_a - snap.ask_a - (spread_factor * spread_a)
        text.append(
            f"  COMPARE A: {snap.p_ref_a:.3f} vs {snap.ask_a:.3f} → {edge_a:+.3f}\n",
            style="bold green" if edge_a > 0 else "bold red",
        )
    if (
        snap.p_ref_b is not None
        and snap.ask_b is not None
        and snap.bid_b is not None
    ):
        spread_b = max(snap.ask_b - snap.bid_b, 0.0)
        edge_b = snap.p_ref_b - snap.ask_b - (spread_factor * spread_b)
        text.append(
            f"  COMPARE B: {snap.p_ref_b:.3f} vs {snap.ask_b:.3f} → {edge_b:+.3f}\n",
            style="bold green" if edge_b > 0 else "bold red",
        )


def _extract_pm_winner(pm_latest: dict) -> str | None:
    candidates = [
        pm_latest.get("resolution"),
        pm_latest.get("resolvedOutcome"),
        pm_latest.get("resolvedOutcomeName"),
        pm_latest.get("winningOutcome"),
        pm_latest.get("winner"),
        pm_latest.get("result"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("outcome") or value.get("name") or value.get("title")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()

    outcomes = _parse_outcomes(pm_latest)
    token_ids = _parse_token_ids(pm_latest)
    tokens = pm_latest.get("tokens") or []
    token_map: dict[str, str] = {}
    if isinstance(tokens, list):
        for token in tokens:
            if not isinstance(token, dict):
                continue
            token_id = token.get("token_id") or token.get("tokenId") or token.get("id")
            outcome = token.get("outcome") or token.get("name") or token.get("title")
            if token_id and outcome:
                token_map[str(token_id)] = str(outcome)

    for key in ("resolvedOutcomeId", "winningOutcomeId", "resolutionOutcomeId"):
        value = pm_latest.get(key)
        if value is None:
            continue
        value_str = str(value)
        if value_str in token_map:
            return token_map[value_str]
        if value_str in token_ids:
            idx = token_ids.index(value_str)
            if 0 <= idx < len(outcomes):
                return outcomes[idx]

    for key in ("resolvedOutcomeIndex", "winningOutcomeIndex", "resolutionOutcomeIndex"):
        value = pm_latest.get(key)
        if value is None:
            continue
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(outcomes):
            return outcomes[idx]

    return None

