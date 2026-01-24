"""
Live TUI monitor for LoL Lead-Lag Arbitrage Bot (read-only).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import typer
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from sqlalchemy import select

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
    p_ref_a: float | None
    p_ref_b: float | None
    odds_a: float | None
    odds_b: float | None
    bid_a: float | None
    ask_a: float | None
    bid_b: float | None
    ask_b: float | None
    source_note: str
    edge: NetEdgeResult
    updated_at: datetime


@dataclass
class RecentlyEnded:
    mapping_id: str
    league: str
    match: str
    market_type: str | None
    game_number: int | None
    ended_at: datetime


class LogBuffer:
    """Rolling log buffer for TUI display."""

    def __init__(self, max_lines: int = 8) -> None:
        self._lines: list[str] = []
        self._max_lines = max_lines
        self._last_logged: dict[str, float] = {}

    def add(self, message: str, key: str | None = None, cooldown_seconds: int = 0) -> None:
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
        text = Text()
        if not self._lines:
            text.append("No logs yet.")
            return text
        text.append("\n".join(self._lines))
        return text


def live_command(
    interval: int = 5,
    edge_threshold: float = 0.03,
    spread_factor: float = 1.0,
    min_confidence: float = 0.7,
    lookahead_minutes: int = 30,
    stale_minutes: int = 180,
) -> None:
    """
    Live TUI monitor:
    - Reads mappings from DB
    - Polls OddsPapi + Polymarket CLOB
    - Computes net edge and displays a stationary TUI
    """
    console = Console()
    oddspapi = OddsPapiClient()
    polymarket = PolymarketClient()

    alert_cooldown = 30
    last_alert: dict[str, float] = {}
    last_alert_text = ""
    log_buffer = LogBuffer(max_lines=8)
    log_buffer.add("Live monitor started")
    recently_ended: dict[str, RecentlyEnded] = {}
    ended_ttl_seconds = 15 * 60

    layout = _build_layout()

    with Live(layout, console=console, refresh_per_second=4, screen=True):
        while True:
            now = datetime.now(tz=timezone.utc)
            for key in list(recently_ended.keys()):
                if (now - recently_ended[key].ended_at).total_seconds() > ended_ttl_seconds:
                    recently_ended.pop(key, None)
            with SessionLocal() as db:
                candidates = _load_candidate_mappings(db, now, lookahead_minutes, min_confidence)

            snapshots: list[MatchSnapshot] = []
            for mapping, op_fixture, pm_fixture, league_name in candidates:
                try:
                    snapshot, ended = _build_snapshot(
                        mapping=mapping,
                        op_fixture=op_fixture,
                        pm_fixture=pm_fixture,
                        oddspapi=oddspapi,
                        polymarket=polymarket,
                        spread_factor=spread_factor,
                        now=now,
                        league_name=league_name,
                        stale_minutes=stale_minutes,
                        log_buffer=log_buffer,
                    )
                    if ended:
                        recently_ended[mapping.id] = RecentlyEnded(
                            mapping_id=str(mapping.id),
                            league=league_name,
                            match=f"{op_fixture.team_a_name} vs {op_fixture.team_b_name}",
                            market_type=pm_fixture.market_type,
                            game_number=pm_fixture.game_number,
                            ended_at=now,
                        )
                    elif snapshot:
                        snapshots.append(snapshot)
                except Exception as exc:  # pragma: no cover - defensive
                    log_buffer.add(
                        f"Error for {op_fixture.source_id}: {exc}",
                        key=f"error:{op_fixture.source_id}",
                        cooldown_seconds=30,
                    )

            focus = _select_focus(snapshots)
            _render_layout(
                layout=layout,
                snapshots=snapshots,
                recently_ended=list(recently_ended.values()),
                focus=focus,
                edge_threshold=edge_threshold,
                last_alert_text=last_alert_text,
                log_buffer=log_buffer,
            )

            # Alerting
            if focus and focus.edge.best_edge is not None and focus.edge.best_edge >= edge_threshold:
                last_ts = last_alert.get(focus.mapping_id, 0.0)
                if time.time() - last_ts >= alert_cooldown:
                    last_alert_text = (
                        f"{focus.match}: edge {focus.edge.best_edge:+.2%} "
                        f"({focus.edge.best_side})"
                    )
                    last_alert[focus.mapping_id] = time.time()
                    logger.info("ALERT %s", last_alert_text)
                    log_buffer.add(f"ALERT {last_alert_text}")

            time.sleep(interval)


def _load_candidate_mappings(
    db,
    now: datetime,
    lookahead_minutes: int,
    min_confidence: float,
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
        if pm_fixture.market_type not in {"match_winner", "game_winner"}:
            continue
        league_name = op_fixture.league.name if op_fixture.league else ""
        if op_fixture.status == "live":
            results.append((mapping, op_fixture, pm_fixture, league_name))
            continue
        if op_fixture.start_time and window_start <= op_fixture.start_time <= window_end:
            results.append((mapping, op_fixture, pm_fixture, league_name))

    return results


def _build_snapshot(
    mapping: Mapping,
    op_fixture: Fixture,
    pm_fixture: Fixture,
    oddspapi: OddsPapiClient,
    polymarket: PolymarketClient,
    spread_factor: float,
    now: datetime,
    league_name: str,
    stale_minutes: int,
    log_buffer: LogBuffer,
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
    try:
        odds_payload = oddspapi.get_odds(op_fixture.source_id)
        status_id = odds_payload.get("statusId")
        if status_id in (2, 3):
            ended = True
        if status_id == 0 and op_fixture.start_time:
            since_start = now - op_fixture.start_time
            if since_start.total_seconds() > stale_minutes * 60:
                ended = True
        if market_type == "match_winner":
            pinnacle_odds = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
        elif market_type == "game_winner" and game_number:
            pinnacle_odds = OddsPapiClient.extract_pinnacle_game_winner(
                odds_payload,
                game_number,
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
        else:
            if not ended and status_id == 1:
                log_buffer.add(
                    f"Odds missing for {op_fixture.source_id} ({market_type}"
                    f"{' G'+str(game_number) if game_number else ''})",
                    key=f"odds-missing:{op_fixture.source_id}:{market_type}:{game_number}",
                    cooldown_seconds=30,
                )
        log_buffer.add(
            f"Odds status {op_fixture.source_id}: statusId={status_id}",
            key=f"odds-status:{op_fixture.source_id}",
            cooldown_seconds=60,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log_buffer.add(f"Odds error {op_fixture.source_id}: {exc}")

    # Refresh Polymarket market status to detect resolved/closed markets
    try:
        pm_latest = polymarket.get_market_by_id(str(pm_fixture.source_id))
        if isinstance(pm_latest, dict):
            pm_closed = bool(pm_latest.get("closed"))
            pm_active = bool(pm_latest.get("active", True))
            pm_resolution = _extract_pm_resolution_status(pm_latest)
            if _pm_is_ended(pm_latest, pm_closed, pm_active, pm_resolution, now):
                ended = True
            log_buffer.add(
                f"PM status {pm_fixture.source_id}: closed={pm_closed} active={pm_active} uma={pm_resolution or 'n/a'}",
                key=f"pm-status:{pm_fixture.source_id}",
                cooldown_seconds=60,
            )
    except Exception as exc:  # pragma: no cover - defensive
        log_buffer.add(
            f"Polymarket status error {pm_fixture.source_id}: {exc}",
            key=f"pm-status:{pm_fixture.source_id}",
            cooldown_seconds=60,
        )

    outcomes = _parse_outcomes(pm_fixture.raw_json or {})
    token_ids = _parse_token_ids(pm_fixture.raw_json or {})
    outcome_pairs = _pair_outcomes_with_tokens(op_fixture, outcomes, token_ids)

    source_note = "CLOB"
    bid_a = ask_a = bid_b = ask_b = None

    if outcome_pairs and all(p[1] for p in outcome_pairs):
        books = polymarket.get_orderbooks_batch([p[1] for p in outcome_pairs if p[1]])
        for outcome_name, token_id in outcome_pairs:
            book = books.get(token_id) or {}
            bid = book.get("best_bid")
            ask = book.get("best_ask")
            if _is_team_a(op_fixture, outcome_name):
                bid_a, ask_a = bid, ask
            elif _is_team_b(op_fixture, outcome_name):
                bid_b, ask_b = bid, ask
        if len(outcome_pairs) >= 2:
            if bid_a is None or ask_a is None:
                book = books.get(outcome_pairs[0][1]) or {}
                bid_a, ask_a = book.get("best_bid"), book.get("best_ask")
            if bid_b is None or ask_b is None:
                book = books.get(outcome_pairs[1][1]) or {}
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

    if pm_fixture.raw_json and pm_fixture.raw_json.get("closed") is True:
        ended = True

    if ended:
        log_buffer.add(
            f"Market ended for {op_fixture.source_id} ({market_type}"
            f"{' G'+str(game_number) if game_number else ''})",
            key=f"ended:{op_fixture.source_id}:{market_type}:{game_number}",
            cooldown_seconds=60,
        )
        return None, True

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

    return MatchSnapshot(
        mapping_id=str(mapping.id),
        league=league_name,
        match=match_name,
        start_time=op_fixture.start_time,
        market_type=market_type,
        game_number=game_number,
        p_ref_a=p_ref_a,
        p_ref_b=p_ref_b,
        odds_a=odds_a,
        odds_b=odds_b,
        bid_a=bid_a,
        ask_a=ask_a,
        bid_b=bid_b,
        ask_b=ask_b,
        source_note=source_note,
        edge=edge,
        updated_at=now,
    ), False


def _build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="main", ratio=4),
        Layout(name="logs", size=8),
    )
    layout["main"].split_row(
        Layout(name="matches", ratio=2),
        Layout(name="details", ratio=1),
    )
    layout["details"].split_column(
        Layout(name="sharps", ratio=1),
        Layout(name="poly", ratio=1),
        Layout(name="alerts", ratio=1),
    )
    return layout


def _render_layout(
    layout: Layout,
    snapshots: list[MatchSnapshot],
    recently_ended: list[RecentlyEnded],
    focus: MatchSnapshot | None,
    edge_threshold: float,
    last_alert_text: str,
    log_buffer: LogBuffer,
) -> None:
    layout["matches"].update(
        Panel(
            _build_matches_table(snapshots, recently_ended, edge_threshold),
            title="Live Matches",
        )
    )
    layout["sharps"].update(Panel(_build_sharps_panel(focus), title="Sharps Odds"))
    layout["poly"].update(Panel(_build_poly_panel(focus), title="Poly Odds"))
    layout["alerts"].update(Panel(_build_alerts_panel(last_alert_text), title="Alerts"))
    layout["logs"].update(Panel(log_buffer.render(), title="Log Stream"))


def _build_matches_table(
    snapshots: list[MatchSnapshot],
    recently_ended: list[RecentlyEnded],
    edge_threshold: float,
) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("League", style="cyan", no_wrap=True)
    table.add_column("Match")
    table.add_column("Market", no_wrap=True)
    table.add_column("Start", no_wrap=True)
    table.add_column("Edge", justify="right")
    table.add_column("Side", no_wrap=True)

    if not snapshots:
        if recently_ended:
            table.add_row("-", "No Matches Live and Recently Ended", "-", "-", "-", "-")
        else:
            table.add_row("-", "No Matches Live", "-", "-", "-", "-")

    for ended in recently_ended:
        market_label = ended.market_type or "-"
        if ended.game_number:
            market_label = f"{market_label} G{ended.game_number}"
        table.add_row(
            ended.league or "-",
            ended.match,
            market_label,
            "--:--",
            "ENDED",
            "-",
            style="dim",
        )

    for snap in snapshots:
        edge = snap.edge.best_edge
        edge_str = "-" if edge is None else f"{edge:+.2%}"
        side = snap.edge.best_side or "-"
        start_str = snap.start_time.strftime("%H:%M") if snap.start_time else "--:--"
        market_label = snap.market_type or "-"
        if snap.game_number:
            market_label = f"{market_label} G{snap.game_number}"

        style = "bold yellow" if edge is not None and edge >= edge_threshold else ""
        table.add_row(
            snap.league or "-",
            snap.match,
            market_label,
            start_str,
            edge_str,
            side,
            style=style,
        )
    return table


def _build_sharps_panel(focus: MatchSnapshot | None) -> Text:
    text = Text()
    if not focus:
        text.append("No match selected.")
        return text

    market_label = focus.market_type or "unknown"
    if focus.game_number:
        market_label = f"{market_label} G{focus.game_number}"
    text.append(f"{focus.match}\n", style="bold")
    text.append(f"Market: {market_label}\n")
    if focus.odds_a is not None and focus.odds_b is not None:
        text.append(f"A odds: {focus.odds_a:.2f}\n")
        text.append(f"B odds: {focus.odds_b:.2f}\n")
    else:
        text.append("Odds: unavailable\n")
    if focus.p_ref_a is not None and focus.p_ref_b is not None:
        text.append(f"A p_ref: {focus.p_ref_a:.2%}\n")
        text.append(f"B p_ref: {focus.p_ref_b:.2%}\n")
    else:
        text.append("p_ref: unavailable\n")
    text.append(f"Updated: {focus.updated_at.strftime('%H:%M:%S UTC')}")
    return text


def _build_poly_panel(focus: MatchSnapshot | None) -> Text:
    text = Text()
    if not focus:
        text.append("No match selected.")
        return text

    market_label = focus.market_type or "unknown"
    if focus.game_number:
        market_label = f"{market_label} G{focus.game_number}"
    text.append(f"{focus.match}\n", style="bold")
    text.append(f"Market: {market_label}\n")
    text.append(f"Source: {focus.source_note}\n")

    if focus.bid_a is not None and focus.ask_a is not None:
        spread_a = max(focus.ask_a - focus.bid_a, 0.0)
        text.append(f"A bid/ask: {focus.bid_a:.3f}/{focus.ask_a:.3f} (spr {spread_a:.3f})\n")
    if focus.bid_b is not None and focus.ask_b is not None:
        spread_b = max(focus.ask_b - focus.bid_b, 0.0)
        text.append(f"B bid/ask: {focus.bid_b:.3f}/{focus.ask_b:.3f} (spr {spread_b:.3f})\n")

    text.append(f"Updated: {focus.updated_at.strftime('%H:%M:%S UTC')}")
    return text


def _build_alerts_panel(last_alert_text: str) -> Text:
    text = Text()
    if last_alert_text:
        text.append(last_alert_text, style="bold yellow")
    else:
        text.append("No alerts yet.")
    return text


def _select_focus(snapshots: list[MatchSnapshot]) -> MatchSnapshot | None:
    if not snapshots:
        return None
    return max(
        snapshots,
        key=lambda s: abs(s.edge.best_edge) if s.edge.best_edge is not None else 0.0,
    )


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
) -> bool:
    if pm_closed or not pm_active:
        return True
    if pm_resolution in {"PROPOSED", "DISPUTED", "RESOLVED", "SETTLED"}:
        return True
    end_date = pm_latest.get("endDateIso") or pm_latest.get("endDate")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
            if end_dt <= now:
                return True
        except ValueError:
            pass
    return False

