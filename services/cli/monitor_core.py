"""
Event-driven live monitor core (polling, triggers, trade simulation).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import aliased

from shared.config import settings
from shared.db import SessionLocal
from shared.edge import (
    NetEdgeResult,
    compute_alpha_entry,
    compute_entry_edge,
    compute_exit_signal,
    compute_net_edges,
    devig_two_way_decimal,
)
from shared.fixture_state import FixtureStateManager, TriggerEvent
from shared.models import Fixture, Mapping, Position, TradeEvent
from shared.oddspapi_client import AsyncOddsPapiClient, OddsPapiClient
from shared.polymarket_client import AsyncPolymarketClient
from shared.polymarket_ws import BookState, PolymarketWSManager
from shared.agent_debug import agent_log

from .monitor_types import (
    LiveState,
    LogBuffer,
    MatchSnapshot,
    PaperTrade,
    PerfStats,
    TriggerRecord,
    format_market_label,
)

logger = logging.getLogger("cli.live")

PROBE_QUANTITY = 100.0
TRIGGER_TTL_MINUTES = 30


class EventDrivenMonitor:
    """Event-driven monitor using OddsPapi polling + Polymarket WS."""

    def __init__(
        self,
        oddspapi: AsyncOddsPapiClient,
        polymarket: AsyncPolymarketClient,
        ws_manager: PolymarketWSManager,
        fixture_states: FixtureStateManager,
        state_lock: Lock,
        live_state: LiveState,
        log_buffer: LogBuffer,
        alerts_buffer: LogBuffer,
        trade_buffer: LogBuffer,
        edge_threshold: float,
        spread_factor: float,
        min_confidence: float,
        lookahead_minutes: int,
        stale_minutes: int,
    ) -> None:
        self._oddspapi = oddspapi
        self._polymarket = polymarket
        self._ws_manager = ws_manager
        self._fixture_states = fixture_states
        self._state_lock = state_lock
        self._live_state = live_state
        self._log_buffer = log_buffer
        self._alerts_buffer = alerts_buffer
        self._trade_buffer = trade_buffer
        self._edge_threshold = edge_threshold
        self._spread_factor = spread_factor
        self._min_confidence = min_confidence
        self._lookahead_minutes = lookahead_minutes
        self._stale_minutes = stale_minutes

        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []
        self._run_id = uuid4()

        self._candidates_lock = Lock()
        self._candidates: list[tuple[Mapping, Fixture, Fixture, str]] = []
        self._tournament_ids: list[int] = []
        self._odds_cache: dict[str, dict] = {}
        self._pm_status_cache: dict[str, tuple[datetime, dict]] = {}
        self._pm_status_ttl = timedelta(seconds=5)
        self._last_alert: dict[str, float] = {}
        self._alert_cooldown = 30
        self._perf_lock = Lock()
        self._perf = PerfStats()
        self._last_perf_odds_ms_total = 0.0
        self._last_perf_odds_calls = 0
        self._league_poll_ready = False
        self._triggers: dict[str, TriggerRecord] = {}
        self._open_trades: dict[str, PaperTrade] = {}
        self._hot_tasks: dict[str, asyncio.Task] = {}

        self._oddspapi_sem = asyncio.Semaphore(settings.oddspapi_max_concurrent_live)
        self._gamma_sem = asyncio.Semaphore(settings.polymarket_gamma_max_concurrent_live)
        self._clob_sem = asyncio.Semaphore(settings.polymarket_clob_max_concurrent_live)

    def start(self) -> Thread:
        thread = Thread(target=self._run_loop, name="event-monitor", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        if self._loop and self._stop and not self._stop.is_set():
            self._loop.call_soon_threadsafe(self._stop.set)
            self._ws_manager.request_stop()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stop = asyncio.Event()
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _main(self) -> None:
        if not self._stop:
            self._stop = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._ws_manager.run()),
            asyncio.create_task(self._refresh_candidates_loop()),
            asyncio.create_task(self._league_poll_loop()),
            asyncio.create_task(self._hot_fixture_poll_loop()),
            asyncio.create_task(self._snapshot_loop()),
        ]
        stop_task = asyncio.create_task(self._stop.wait())
        try:
            done, _ = await asyncio.wait(
                self._tasks + [stop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                for task in self._tasks:
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            for task in self._hot_tasks.values():
                task.cancel()
            self._hot_tasks.clear()
            await self._ws_manager.stop()
            await self._oddspapi.close()
            await self._polymarket.close()

    async def _refresh_candidates_loop(self) -> None:
        while self._stop and not self._stop.is_set():
            now = datetime.now(tz=timezone.utc)
            candidates = await asyncio.to_thread(
                self._load_candidates,
                now,
            )
            tournament_ids: set[int] = set()
            token_ids: set[str] = set()
            for _, op_fixture, pm_fixture, _ in candidates:
                tournament_id = _extract_tournament_id(op_fixture.raw_json or {})
                if tournament_id is not None:
                    tournament_ids.add(tournament_id)
                token_ids.update(_parse_token_ids(pm_fixture.raw_json or {}))

            await self._ws_manager.update_subscriptions(sorted(token_ids))

            with self._candidates_lock:
                self._candidates = candidates
                self._tournament_ids = sorted(tournament_ids)

            await asyncio.sleep(120.0)

    async def _league_poll_loop(self) -> None:
        while self._stop and not self._stop.is_set():
            tournament_ids = self._get_tournament_ids()
            if not tournament_ids:
                await asyncio.sleep(1.0)
                continue
            start = time.perf_counter()
            async with self._oddspapi_sem:
                odds_batch = await self._oddspapi.get_odds_by_tournaments(tournament_ids)
            duration_ms = (time.perf_counter() - start) * 1000
            with self._perf_lock:
                self._perf.oddspapi_ms_total += duration_ms
                self._perf.oddspapi_calls += 1
                self._perf.oddspapi_last_batch = len(odds_batch)
            self._league_poll_ready = True
            for odds_payload in odds_batch:
                fixture_id = str(odds_payload.get("fixtureId") or "")
                if not fixture_id:
                    continue
                self._odds_cache[fixture_id] = odds_payload
                # region agent log (odds cache updates)
                updated_at_val = odds_payload.get("updatedAt")
                agent_log(
                    location="services/cli/monitor_core.py:_league_poll_loop",
                    message="odds cache updated",
                    hypothesis_id="H3",
                    data={
                        "fixture_id": fixture_id,
                        "statusId": odds_payload.get("statusId"),
                        "updatedAt_type": type(updated_at_val).__name__,
                        "updatedAt": str(updated_at_val)[:32] if updated_at_val is not None else None,
                    },
                )
                # endregion
                p_ref_a, p_ref_b, _, _ = _extract_p_refs_from_odds(
                    odds_payload, market_type="match_winner", game_number=None
                )
                trigger = self._fixture_states.update_p_ref(
                    fixture_id,
                    p_ref_a,
                    p_ref_b,
                )
                if trigger:
                    self._log_trigger(trigger)
            await asyncio.sleep(1.0)

    async def _hot_fixture_poll_loop(self) -> None:
        while self._stop and not self._stop.is_set():
            hot_fixtures = set(self._fixture_states.get_hot_fixtures())
            for fixture_id in hot_fixtures:
                if fixture_id in self._hot_tasks:
                    continue
                self._hot_tasks[fixture_id] = asyncio.create_task(
                    self._hot_fixture_worker(fixture_id)
                )
            for fixture_id in list(self._hot_tasks.keys()):
                if fixture_id not in hot_fixtures:
                    task = self._hot_tasks.pop(fixture_id)
                    task.cancel()
            await asyncio.sleep(0.2)

    async def _hot_fixture_worker(self, fixture_id: str) -> None:
        while self._stop and not self._stop.is_set():
            if not self._fixture_states.is_hot(fixture_id):
                return
            start = time.perf_counter()
            async with self._oddspapi_sem:
                odds_payload = await self._oddspapi.get_odds(fixture_id)
            duration_ms = (time.perf_counter() - start) * 1000
            with self._perf_lock:
                self._perf.oddspapi_ms_total += duration_ms
                self._perf.oddspapi_calls += 1
            if isinstance(odds_payload, dict):
                self._odds_cache[fixture_id] = odds_payload
                # region agent log (odds cache updates - hot)
                updated_at_val = odds_payload.get("updatedAt")
                agent_log(
                    location="services/cli/monitor_core.py:_hot_fixture_worker",
                    message="odds cache updated (hot)",
                    hypothesis_id="H3",
                    data={
                        "fixture_id": fixture_id,
                        "statusId": odds_payload.get("statusId"),
                        "updatedAt_type": type(updated_at_val).__name__,
                        "updatedAt": str(updated_at_val)[:32] if updated_at_val is not None else None,
                    },
                )
                # endregion
                p_ref_a, p_ref_b, _, _ = _extract_p_refs_from_odds(
                    odds_payload, market_type="match_winner", game_number=None
                )
                trigger = self._fixture_states.update_p_ref(
                    fixture_id,
                    p_ref_a,
                    p_ref_b,
                )
                if trigger:
                    self._log_trigger(trigger)
            await asyncio.sleep(settings.hot_fixture_poll_ms / 1000)

    async def _snapshot_loop(self) -> None:
        while self._stop and not self._stop.is_set():
            loop_start = time.perf_counter()
            now = datetime.now(tz=timezone.utc)
            with self._candidates_lock:
                candidates = list(self._candidates)
            if not candidates:
                await asyncio.sleep(1.0)
                continue

            ws_state = await self._ws_manager.get_state_snapshot()

            # Fetch latest market objects (Gamma) first, so we can:
            # - use fresh clobTokenIds/tokens (markets can change during pauses/restarts)
            # - compute accurate required token set for WS coverage + HTTP fallback
            pm_latest_by_market_id: dict[str, dict] = {}
            required_tokens: set[str] = set()
            for _, _, pm_fixture, _ in candidates:
                market_id = str(pm_fixture.source_id)
                gamma_start = time.perf_counter()
                pm_latest = await _get_pm_latest_cached_async(
                    polymarket=self._polymarket,
                    cache=self._pm_status_cache,
                    market_id=market_id,
                    now=now,
                    ttl=self._pm_status_ttl,
                    semaphore=self._gamma_sem,
                )
                gamma_ms = (time.perf_counter() - gamma_start) * 1000
                with self._perf_lock:
                    self._perf.gamma_ms_total += gamma_ms
                    self._perf.gamma_calls += 1
                if isinstance(pm_latest, dict):
                    pm_latest_by_market_id[market_id] = pm_latest
                    required_tokens.update(_extract_token_ids(pm_latest))
                else:
                    required_tokens.update(_extract_token_ids(getattr(pm_fixture, "raw_json", None) or {}))

            missing_tokens = [t for t in required_tokens if t and t not in ws_state]
            # region agent log (WS snapshot + subscription coverage)
            agent_log(
                location="services/cli/monitor_core.py:_snapshot_loop",
                message="ws snapshot acquired",
                hypothesis_id="H2",
                data={
                    "ws_connected": bool(self._ws_manager.is_connected()),
                    "ws_assets": len(ws_state),
                    "required_tokens": len(required_tokens),
                    "missing_tokens": len(missing_tokens),
                },
            )
            # endregion

            http_books: dict[str, BookState] = {}
            if missing_tokens:
                books_start = time.perf_counter()
                async with self._clob_sem:
                    books_by_token = await self._polymarket.get_orderbooks_batch(missing_tokens)
                books_ms = (time.perf_counter() - books_start) * 1000
                http_books = _convert_books_to_state(books_by_token)
                ws_state.update(http_books)
                with self._perf_lock:
                    self._perf.clob_batch_ms = books_ms
                    self._perf.ws_books_fallback = len(http_books)
                # region agent log (HTTP fallback books)
                agent_log(
                    location="services/cli/monitor_core.py:_snapshot_loop",
                    message="http book fallback used",
                    hypothesis_id="H2",
                    data={"missing_tokens": len(missing_tokens), "fetched_books": len(http_books)},
                )
                # endregion

            perf = self._snapshot_perf(len(ws_state))
            live_snapshots: list[MatchSnapshot] = []
            upcoming_snapshots: list[MatchSnapshot] = []
            recently_ended: list[MatchSnapshot] = []
            for mapping, op_fixture, pm_fixture, league_name in candidates:
                odds_payload = self._odds_cache.get(str(op_fixture.source_id))
                pm_latest = pm_latest_by_market_id.get(str(pm_fixture.source_id))
                snapshot, ended = _build_snapshot_from_cache(
                    mapping=mapping,
                    op_fixture=op_fixture,
                    pm_fixture=pm_fixture,
                    odds_payload=odds_payload,
                    pm_latest=pm_latest,
                    ws_state=ws_state,
                    spread_factor=self._spread_factor,
                    now=now,
                    league_name=league_name,
                    stale_minutes=self._stale_minutes,
                    log_buffer=self._log_buffer,
                    league_poll_ready=self._league_poll_ready,
                )
                if snapshot:
                    self._process_trade_signals(
                        mapping=mapping,
                        op_fixture=op_fixture,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        ws_state=ws_state,
                        now=now,
                        ended=ended,
                    )
                    if ended:
                        recently_ended.append(snapshot)
                    elif snapshot.is_live:
                        live_snapshots.append(snapshot)
                    else:
                        upcoming_snapshots.append(snapshot)

            focus_match, focus_game1 = _select_focuses(live_snapshots, recently_ended)
            ws_connected = self._ws_manager.is_connected()
            with self._state_lock:
                self._live_state.snapshots = live_snapshots
                self._live_state.recently_ended = recently_ended
                self._live_state.upcoming = upcoming_snapshots
                self._live_state.focus_match = focus_match
                self._live_state.focus_game1 = focus_game1
                self._live_state.last_update = now
                self._live_state.perf = perf
                self._live_state.ws_connected = ws_connected
            loop_ms = (time.perf_counter() - loop_start) * 1000
            with self._perf_lock:
                self._perf.loop_ms = loop_ms
            await asyncio.sleep(0.5)

    def _load_candidates(
        self,
        now: datetime,
    ) -> list[tuple[Mapping, Fixture, Fixture, str]]:
        with SessionLocal() as db:
            return _load_candidate_mappings(
                db, now, self._lookahead_minutes, self._min_confidence, live_only=False
            )

    def _get_tournament_ids(self) -> list[int]:
        with self._candidates_lock:
            return list(getattr(self, "_tournament_ids", []))

    def _log_trigger(self, trigger: TriggerEvent) -> None:
        now = datetime.now(tz=timezone.utc)
        self._triggers[trigger.fixture_id] = TriggerRecord(trigger=trigger, ts=now)
        message = (
            f"Trigger {trigger.trigger_type} fixture={trigger.fixture_id} "
            f"delta_a={_format_delta(trigger.delta_p_ref_a)} "
            f"delta_b={_format_delta(trigger.delta_p_ref_b)}"
        )
        self._log_buffer.add(message)

    def _snapshot_perf(self, ws_assets: int) -> PerfStats:
        with self._perf_lock:
            # Per-snapshot window deltas (avoid cumulative drift in UI)
            odds_ms_window = self._perf.oddspapi_ms_total - self._last_perf_odds_ms_total
            odds_calls_window = self._perf.oddspapi_calls - self._last_perf_odds_calls
            self._last_perf_odds_ms_total = self._perf.oddspapi_ms_total
            self._last_perf_odds_calls = self._perf.oddspapi_calls

            snapshot = PerfStats(
                loop_ms=self._perf.loop_ms,
                oddspapi_ms_total=self._perf.oddspapi_ms_total,
                oddspapi_calls=self._perf.oddspapi_calls,
                oddspapi_ms_window=odds_ms_window,
                oddspapi_calls_window=odds_calls_window,
                clob_batch_ms=self._perf.clob_batch_ms,
                gamma_ms_total=self._perf.gamma_ms_total,
                gamma_calls=self._perf.gamma_calls,
                oddspapi_last_batch=self._perf.oddspapi_last_batch,
                ws_assets=ws_assets,
                ws_books_fallback=self._perf.ws_books_fallback,
            )
            self._perf.clob_batch_ms = None
            self._perf.ws_books_fallback = 0
        return snapshot

    def _build_trade_event(
        self,
        event_type: str,
        now: datetime,
        mapping: Mapping,
        pm_fixture: Fixture,
        snapshot: MatchSnapshot,
        side: str | None = None,
        reason: str | None = None,
        details: str | None = None,
        position_id: str | None = None,
        quantity: float | None = None,
        limit_price: float | None = None,
        avg_fill_price: float | None = None,
        size_available: float | None = None,
        net_edge: float | None = None,
        exit_price: float | None = None,
        pnl_percent: float | None = None,
        raw_json: dict | None = None,
    ) -> TradeEvent:
        payload = dict(raw_json or {})
        return TradeEvent(
            ts=now,
            run_id=self._run_id,
            event_type=event_type,
            mode="paper",
            mapping_id=mapping.id,
            pm_fixture_id=pm_fixture.id,
            position_id=position_id,
            market_type=snapshot.market_type,
            game_number=snapshot.game_number,
            side=side,
            reason=reason,
            details=details,
            edge_threshold=self._edge_threshold,
            spread_factor=self._spread_factor,
            alpha_min=settings.alpha_min,
            alpha_spread_factor=settings.alpha_spread_factor,
            exit_epsilon=settings.exit_epsilon,
            p_ref_a=snapshot.p_ref_a,
            p_ref_b=snapshot.p_ref_b,
            bid_a=snapshot.bid_a,
            ask_a=snapshot.ask_a,
            mid_a=snapshot.mid_a,
            bid_b=snapshot.bid_b,
            ask_b=snapshot.ask_b,
            mid_b=snapshot.mid_b,
            best_edge=snapshot.edge.best_edge,
            best_side=snapshot.edge.best_side,
            quantity=quantity,
            limit_price=limit_price,
            avg_fill_price=avg_fill_price,
            size_available=size_available,
            net_edge=net_edge,
            exit_price=exit_price,
            pnl_percent=pnl_percent,
            raw_json=payload,
        )

    def _process_trade_signals(
        self,
        mapping: Mapping,
        op_fixture: Fixture,
        pm_fixture: Fixture,
        snapshot: MatchSnapshot,
        ws_state: dict[str, BookState],
        now: datetime,
        ended: bool,
    ) -> None:
        fixture_id = str(op_fixture.source_id)
        trigger_record = self._triggers.get(fixture_id)
        if ended:
            # Market is over: stop new entries; only exit open trades.
            self._check_for_exits(mapping, pm_fixture, snapshot, now, ended, trigger_record)
            return
        if trigger_record and (now - trigger_record.ts) > timedelta(minutes=TRIGGER_TTL_MINUTES):
            self._triggers.pop(fixture_id, None)
            trigger_record = None

        if trigger_record and not trigger_record.logged:
            trigger = trigger_record.trigger
            delta = _select_trigger_delta(trigger)
            self._trade_buffer.add(
                _format_trade_line(
                    event="TRIGGER",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=None,
                    details=f"delta={_format_pct(delta)} ({trigger.trigger_type})",
                )
            )
            with SessionLocal() as db:
                event = self._build_trade_event(
                    event_type="TRIGGER",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    reason=trigger.trigger_type,
                    details=f"delta={_format_pct(delta)}",
                    raw_json={
                        "trigger_type": trigger.trigger_type,
                        "delta_p_ref_a": trigger.delta_p_ref_a,
                        "delta_p_ref_b": trigger.delta_p_ref_b,
                    },
                )
                db.add(event)
                db.commit()
            trigger_record.logged = True

        entry_candidates = _build_entry_candidates(op_fixture, pm_fixture, snapshot, ws_state)
        if trigger_record and not trigger_record.entry_logged:
            trigger_record.entry_logged = True
            best = _select_best_candidate(entry_candidates)
            if not best:
                self._trade_buffer.add(
                    _format_trade_line(
                        event="ENTRY_CHECK",
                        match=snapshot.match,
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side=None,
                        details="no_depth",
                    )
                )
                with SessionLocal() as db:
                    event = self._build_trade_event(
                        event_type="ENTRY_CHECK",
                        now=now,
                        mapping=mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        reason="no_depth",
                        details="no_depth",
                    )
                    db.add(event)
                    db.commit()
            else:
                side = best["side"]
                entry = best["entry"]
                key = _trade_key(mapping, pm_fixture, side)
                if key in self._open_trades:
                    self._trade_buffer.add(
                        _format_trade_line(
                            event="ENTRY_SKIP",
                            match=snapshot.match,
                            market_type=snapshot.market_type,
                            game_number=snapshot.game_number,
                            side=side,
                            details="skip already_open",
                        )
                    )
                    with SessionLocal() as db:
                        event = self._build_trade_event(
                            event_type="ENTRY_SKIP",
                            now=now,
                            mapping=mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=side,
                            reason="already_open",
                            details="skip already_open",
                        )
                        db.add(event)
                        db.commit()
                elif entry["actionable"]:
                    size_available = float(entry["size_available"] or 0.0)
                    quantity = min(PROBE_QUANTITY, size_available) if size_available else PROBE_QUANTITY
                    trade = PaperTrade(
                        key=key,
                        mapping_id=str(mapping.id),
                        market_id=str(pm_fixture.id),
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side=side,
                        trigger_type=trigger_record.trigger.trigger_type if trigger_record else None,
                        trigger_ts=trigger_record.ts if trigger_record else None,
                        entry_ts=now,
                        entry_price=float(entry["avg_fill"]),
                        limit_price=entry["limit_price"],
                        size_available=size_available,
                        quantity=quantity,
                        alpha=float(best["alpha"]),
                        net_edge=entry["net_edge"],
                        p_ref_entry=best["p_ref"],
                    )
                    entry_raw = {
                        "mapping_id": str(mapping.id),
                        "market_id": str(pm_fixture.id),
                        "market_type": snapshot.market_type,
                        "game_number": snapshot.game_number,
                        "side": "A" if side == "buy_a" else "B",
                        "trigger_type": trade.trigger_type,
                        "trigger_ts": trade.trigger_ts.isoformat() if trade.trigger_ts else None,
                        "entry_ts": trade.entry_ts.isoformat(),
                        "entry_price": trade.entry_price,
                        "limit_price": trade.limit_price,
                        "size_available": trade.size_available,
                        "quantity": trade.quantity,
                        "alpha": trade.alpha,
                        "net_edge": trade.net_edge,
                        "p_ref_entry": trade.p_ref_entry,
                    }
                    with SessionLocal() as db:
                        position = Position(
                            mapping_id=mapping.id,
                            pm_fixture_id=pm_fixture.id,
                            mode="paper",
                            venue="paper",
                            market_type=snapshot.market_type or "unknown",
                            game_number=snapshot.game_number,
                            side=entry_raw["side"],
                            opened_at=now,
                            entry_price=trade.entry_price,
                            entry_p_ref=trade.p_ref_entry,
                            entry_alpha=trade.alpha,
                            entry_edge=trade.net_edge,
                            quantity=trade.quantity,
                            trigger_type=trade.trigger_type,
                            raw_json=entry_raw,
                        )
                        db.add(position)
                        db.flush()
                        event = self._build_trade_event(
                            event_type="ENTRY",
                            now=now,
                            mapping=mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=side,
                            reason="entry",
                            details=f"edge={_format_pct(trade.net_edge)}",
                            position_id=position.id,
                            quantity=trade.quantity,
                            limit_price=trade.limit_price,
                            avg_fill_price=trade.entry_price,
                            size_available=size_available,
                            net_edge=trade.net_edge,
                            raw_json={
                                "entry_price": trade.entry_price,
                                "limit_price": trade.limit_price,
                                "alpha": trade.alpha,
                                "p_ref_entry": trade.p_ref_entry,
                                "trigger_type": trade.trigger_type,
                                "trigger_ts": trade.trigger_ts.isoformat()
                                if trade.trigger_ts
                                else None,
                            },
                        )
                        db.add(event)
                        db.commit()
                        db.refresh(position)
                        trade.db_position_id = str(position.id)
                    self._open_trades[key] = trade
                    self._trade_buffer.add(
                        _format_trade_line(
                            event="ENTRY",
                            match=snapshot.match,
                            market_type=snapshot.market_type,
                            game_number=snapshot.game_number,
                            side=side,
                            details=(
                                f"@ {trade.entry_price:.3f} "
                                f"(edge={_format_pct(trade.net_edge)})"
                            ),
                        )
                    )
                else:
                    size_available = float(entry["size_available"] or 0.0)
                    self._trade_buffer.add(
                        _format_trade_line(
                            event="ENTRY_SKIP",
                            match=snapshot.match,
                            market_type=snapshot.market_type,
                            game_number=snapshot.game_number,
                            side=side,
                            details=(
                                f"skip edge={_format_pct(entry['net_edge'])} "
                                f"alpha={best['alpha']:.3f} "
                                f"size={entry['size_available']:.0f}"
                            ),
                        )
                    )
                    with SessionLocal() as db:
                        event = self._build_trade_event(
                            event_type="ENTRY_SKIP",
                            now=now,
                            mapping=mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=side,
                            reason="edge_below_threshold",
                            details=(
                                f"edge={_format_pct(entry['net_edge'])} "
                                f"alpha={best['alpha']:.3f} "
                                f"size={entry['size_available']:.0f}"
                            ),
                            net_edge=entry["net_edge"],
                            size_available=size_available,
                            limit_price=entry["limit_price"],
                        )
                        db.add(event)
                        db.commit()

        self._check_for_exits(mapping, pm_fixture, snapshot, now, ended, trigger_record)

    def _check_for_exits(
        self,
        mapping: Mapping,
        pm_fixture: Fixture,
        snapshot: MatchSnapshot,
        now: datetime,
        ended: bool,
        trigger_record: TriggerRecord | None,
    ) -> None:
        open_keys = [
            key
            for key, trade in self._open_trades.items()
            if trade.mapping_id == str(mapping.id) and trade.market_id == str(pm_fixture.id)
        ]
        for key in open_keys:
            trade = self._open_trades[key]
            p_ref, bid = _select_p_ref_and_bid(trade.side, snapshot)
            exit_signal = compute_exit_signal(p_ref, bid, settings.exit_epsilon) if not ended else True
            if not exit_signal:
                continue
            exit_gap = (p_ref - bid) if p_ref is not None and bid is not None else None
            catchup_s = _format_seconds(now - trade.trigger_ts) if trade.trigger_ts else "n/a"
            hold_s = _format_seconds(now - trade.entry_ts)
            reason = "market_ended" if ended else "convergence"
            pnl_pct = None
            if bid is not None and trade.entry_price:
                pnl_pct = (bid - trade.entry_price) / trade.entry_price
            if trade.db_position_id:
                with SessionLocal() as db:
                    position = db.get(Position, trade.db_position_id)
                    if position:
                        position.closed_at = now
                        position.exit_price = bid
                        position.exit_p_ref = p_ref
                        position.exit_reason = reason
                        if bid is not None:
                            position.pnl_absolute = bid - position.entry_price
                            position.pnl_percent = (
                                (bid - position.entry_price) / position.entry_price
                            )
                        position.hold_seconds = (now - position.opened_at).total_seconds()
                        if position.entry_edge and position.pnl_percent is not None:
                            position.edge_capture = position.pnl_percent / position.entry_edge
                        raw_json = dict(position.raw_json or {})
                        raw_json["exit"] = {
                            "exit_ts": now.isoformat(),
                            "exit_price": bid,
                            "exit_p_ref": p_ref,
                            "exit_reason": reason,
                            "pnl_percent": position.pnl_percent,
                        }
                        position.raw_json = raw_json
                        event = self._build_trade_event(
                            event_type="EXIT",
                            now=now,
                            mapping=mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=trade.side,
                            reason=reason,
                            details=f"pnl={_format_pct(pnl_pct)}",
                            position_id=position.id,
                            exit_price=bid,
                            pnl_percent=position.pnl_percent,
                            raw_json={
                                "exit_reason": reason,
                                "exit_price": bid,
                                "exit_p_ref": p_ref,
                                "pnl_percent": position.pnl_percent,
                            },
                        )
                        db.add(event)
                        db.commit()
            self._trade_buffer.add(
                _format_trade_line(
                    event="EXIT",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=trade.side,
                    details=(
                        f"@ {_format_price(bid)} "
                        f"| P&L: {_format_pct(pnl_pct)} "
                        f"| hold={hold_s} "
                        f"| reason={reason}"
                    ),
                )
            )
            if trigger_record:
                trigger_record.catchup_logged_sides.add(trade.side)
            trade.status = "closed"
            self._open_trades.pop(key, None)

        if trigger_record:
            for side in ("buy_a", "buy_b"):
                if side in trigger_record.catchup_logged_sides:
                    continue
                p_ref, bid = _select_p_ref_and_bid(side, snapshot)
                if not compute_exit_signal(p_ref, bid, settings.exit_epsilon):
                    continue
                trigger_record.catchup_logged_sides.add(side)
                self._trade_buffer.add(
                    _format_trade_line(
                        event="CATCHUP",
                        match=snapshot.match,
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side=side,
                        details=f"time={_format_seconds(now - trigger_record.ts)}",
                    )
                )
                with SessionLocal() as db:
                    event = self._build_trade_event(
                        event_type="CATCHUP",
                        now=now,
                        mapping=mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=side,
                        reason="catchup",
                        details=f"time={_format_seconds(now - trigger_record.ts)}",
                        raw_json={
                            "catchup_seconds": (now - trigger_record.ts).total_seconds(),
                        },
                    )
                    db.add(event)
                    db.commit()


def _load_candidate_mappings(
    db,
    now: datetime,
    lookahead_minutes: int,
    min_confidence: float,
    live_only: bool = False,
) -> list[tuple[Mapping, Fixture, Fixture, str]]:
    window_start = now - timedelta(hours=4)
    window_end = now + timedelta(minutes=lookahead_minutes)

    op_fixture = aliased(Fixture)
    pm_fixture = aliased(Fixture)

    rows = (
        db.execute(
            select(Mapping, op_fixture, pm_fixture)
            .where(Mapping.confidence >= min_confidence)
            .join(op_fixture, Mapping.oddspapi_fixture_id == op_fixture.id)
            .join(pm_fixture, Mapping.polymarket_fixture_id == pm_fixture.id)
        )
        .all()
    )

    parent_ids: set[str] = set()
    for _, _, pm_fix in rows:
        if pm_fix.market_type == "event":
            parent_ids.add(pm_fix.id)
        elif pm_fix.parent_fixture_id:
            parent_ids.add(pm_fix.parent_fixture_id)

    children_by_parent: dict[str, list[Fixture]] = {}
    if parent_ids:
        children = (
            db.execute(
                select(Fixture).where(Fixture.parent_fixture_id.in_(list(parent_ids)))
            )
            .scalars()
            .all()
        )
        for child in children:
            children_by_parent.setdefault(child.parent_fixture_id, []).append(child)

    results: list[tuple[Mapping, Fixture, Fixture, str]] = []
    for mapping, op_fix, pm_fix in rows:
        if pm_fix.market_type not in {"event", "match_winner"}:
            continue
        league_name = op_fix.league.name if op_fix.league else ""

        include_match = False
        if op_fix.status == "live":
            include_match = True
        elif not live_only and op_fix.start_time and window_start <= op_fix.start_time <= window_end:
            include_match = True

        if not include_match:
            continue

        parent_fixture = pm_fix
        match_market = pm_fix
        if pm_fix.market_type == "event":
            child_markets = children_by_parent.get(pm_fix.id, [])
            match_market = next(
                (c for c in child_markets if c.market_type == "match_winner"),
                None,
            )
        else:
            child_markets = children_by_parent.get(pm_fix.id, [])

        if not match_market:
            continue

        results.append((mapping, op_fix, match_market, league_name))
        for child in child_markets:
            if child.market_type == "match_winner":
                continue
            results.append((mapping, op_fix, child, league_name))

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
                results.append((mapping, op_fix, proxy, league_name))

    return results


def _build_snapshot_from_cache(
    mapping: Mapping,
    op_fixture: Fixture,
    pm_fixture: Fixture,
    odds_payload: dict | None,
    pm_latest: dict | None,
    ws_state: dict[str, BookState],
    spread_factor: float,
    now: datetime,
    league_name: str,
    stale_minutes: int,
    log_buffer: LogBuffer,
    league_poll_ready: bool,
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
    pm_ended = False
    odds_status_label = None
    odds_live = False

    if isinstance(odds_payload, dict):
        status_id = odds_payload.get("statusId")
        if status_id in (2, 3):
            ended = True
        if status_id == 0 and market_type == "game_winner":
            start_ref = pm_fixture.start_time or op_fixture.start_time
            if start_ref:
                since_start = now - start_ref
            else:
                since_start = None
            if since_start and since_start.total_seconds() > stale_minutes * 60:
                ended = True
        p_ref_a, p_ref_b, odds_a, odds_b = _extract_p_refs_from_odds(
            odds_payload,
            market_type=market_type,
            game_number=game_number,
            op_fixture=op_fixture,
            pm_fixture=pm_fixture,
            log_buffer=log_buffer,
        )
        odds_changed_at = _latest_changed_at(
            [
                odds_payload.get("updatedAt"),
            ]
        )
        started_recently = False
        if op_fixture.start_time:
            try:
                started_recently = (
                    op_fixture.start_time <= now
                    and (now - op_fixture.start_time).total_seconds() <= stale_minutes * 60
                )
            except TypeError:
                started_recently = False
        odds_live = bool(
            status_id == 1
            or getattr(op_fixture, "status", "") == "live"
            or started_recently
            or _recent_enough(odds_changed_at, now, minutes=10)
        )
        if status_id == 1:
            odds_status_label = "LIVE"
        elif status_id == 0:
            odds_status_label = "PRE"
        elif status_id is not None:
            odds_status_label = f"ODDS {status_id}"
        else:
            odds_status_label = None
    else:
        if not league_poll_ready:
            return None, False
        log_buffer.add(
            f"No OddsPapi cache for {op_fixture.source_id}",
            key=f"odds-cache-miss:{op_fixture.source_id}",
            cooldown_seconds=30,
        )

    if isinstance(pm_latest, dict):
        pm_closed = bool(pm_latest.get("closed"))
        pm_active = bool(pm_latest.get("active", True))
        pm_resolution = _extract_pm_resolution_status(pm_latest)
        pm_winner = _extract_pm_winner(pm_latest)
        pm_ended = _pm_is_ended(pm_latest, pm_closed, pm_active, pm_resolution, now, market_type)
        if pm_ended:
            ended = True

    # Prefer fresh Gamma payload for tokens/outcomes (markets can change during pauses).
    market_raw = pm_latest if isinstance(pm_latest, dict) else (pm_fixture.raw_json or {})
    outcome_pairs = _extract_outcome_token_pairs(market_raw)
    if not outcome_pairs:
        outcomes = _parse_outcomes(market_raw)
        token_ids = _extract_token_ids(market_raw)
        outcome_pairs = _pair_outcomes_with_tokens(op_fixture, outcomes, token_ids)
    else:
        token_ids = [token_id for _, token_id in outcome_pairs if token_id]

    source_note = "WS"
    bid_a = ask_a = bid_b = ask_b = None
    mid_a = mid_b = None
    pm_price_finished = False

    if outcome_pairs and all(p[1] for p in outcome_pairs):
        for outcome_name, token_id in outcome_pairs:
            book = ws_state.get(token_id)
            bid = book.best_bid if book else None
            ask = book.best_ask if book else None
            if _is_team_a(op_fixture, outcome_name):
                bid_a, ask_a = bid, ask
            elif _is_team_b(op_fixture, outcome_name):
                bid_b, ask_b = bid, ask
        if len(outcome_pairs) >= 2:
            if bid_a is None or ask_a is None:
                book = ws_state.get(outcome_pairs[0][1])
                bid_a, ask_a = (book.best_bid, book.best_ask) if book else (None, None)
            if bid_b is None or ask_b is None:
                book = ws_state.get(outcome_pairs[1][1])
                bid_b, ask_b = (book.best_bid, book.best_ask) if book else (None, None)
    else:
        source_note = "No WS tokens"

    # region agent log (live classification + odds/ws merge)
    agent_log(
        location="services/cli/monitor_core.py:_build_snapshot_from_cache",
        message="snapshot merge computed",
        hypothesis_id="H3",
        data={
            "fixture_id": str(op_fixture.source_id),
            "op_status": getattr(op_fixture, "status", None),
            "odds_statusId": odds_payload.get("statusId") if isinstance(odds_payload, dict) else None,
            "odds_live": bool(odds_live),
            "ended": bool(ended),
            "pm_resolution": pm_resolution,
            "pm_closed": bool(pm_closed),
            "pm_active": bool(pm_active),
            "market_type": market_type,
            "game_number": game_number,
            "tokens_n": len([t for t in token_ids if t]),
            "ws_have_a": bid_a is not None or ask_a is not None,
            "ws_have_b": bid_b is not None or ask_b is not None,
            "source_note": source_note,
        },
    )
    # endregion

    if bid_a is not None and ask_a is not None:
        mid_a = (bid_a + ask_a) / 2
    if bid_b is not None and ask_b is not None:
        mid_b = (bid_b + ask_b) / 2

    # If Polymarket is effectively at certainty, treat market as finished.
    a_max = max([v for v in (bid_a, ask_a) if v is not None], default=None)
    b_max = max([v for v in (bid_b, ask_b) if v is not None], default=None)
    pm_price_finished = bool(
        (a_max is not None and a_max >= 0.995) or (b_max is not None and b_max >= 0.995)
    )
    if pm_price_finished:
        ended = True
        if not pm_resolution:
            pm_resolution = "PM_FINISHED"

    if ended and not pm_ended and (not pm_price_finished) and (
        (bid_a is not None or ask_a is not None) or (bid_b is not None or ask_b is not None)
    ):
        ended = False
        log_buffer.add(
            f"Market re-opened (liquidity) {op_fixture.source_id} ({market_type}"
            f"{' G'+str(game_number) if game_number else ''})",
            key=f"ended-override:{op_fixture.source_id}:{market_type}:{game_number}",
            cooldown_seconds=60,
        )

    if market_raw and market_raw.get("closed") is True:
        ended = True

    if ended:
        edge = NetEdgeResult(edge_buy_a=None, edge_buy_b=None, best_edge=None, best_side=None)
        match_name = f"{op_fixture.team_a_name} vs {op_fixture.team_b_name}"
        display_status = pm_resolution or odds_status_label or ("LIVE*" if odds_live else None)
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
            is_live=False,
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
    display_status = pm_resolution or odds_status_label or ("LIVE*" if odds_live else None)
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
        is_live=bool(odds_live),
    ), False


def _parse_outcomes(raw: dict) -> list[str]:
    outcomes = raw.get("outcomes") or []
    if isinstance(outcomes, str):
        try:
            import json

            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    return [str(o) for o in outcomes if o]


def _convert_books_to_state(books_by_token: dict[str, dict]) -> dict[str, BookState]:
    results: dict[str, BookState] = {}
    for token_id, book in books_by_token.items():
        bids = [(float(b["price"]), float(b["size"])) for b in book.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in book.get("asks", [])]
        best_bid = book.get("best_bid")
        best_ask = book.get("best_ask")
        spread = None
        if best_bid is not None and best_ask is not None:
            spread = max(best_ask - best_bid, 0.0)
        results[token_id] = BookState(
            asset_id=token_id,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            bids=bids,
            asks=asks,
            timestamp=datetime.now(tz=timezone.utc),
            hash=None,
        )
    return results


def _extract_tournament_id(raw: dict) -> int | None:
    for key in ("tournamentId", "tournament_id"):
        value = raw.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _extract_p_refs_from_odds(
    odds_payload: dict,
    market_type: str,
    game_number: int | None,
    op_fixture: Fixture | None = None,
    pm_fixture: Fixture | None = None,
    log_buffer: LogBuffer | None = None,
) -> tuple[float | None, float | None, float | None, float | None]:
    odds_a = None
    odds_b = None
    p_ref_a = None
    p_ref_b = None
    if market_type == "match_winner":
        pinnacle_odds = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
    elif market_type == "game_winner" and game_number:
        pinnacle_odds = OddsPapiClient.extract_pinnacle_game_winner(
            odds_payload,
            game_number,
        )
        if not pinnacle_odds and pm_fixture:
            series_len = _series_length(getattr(pm_fixture, "series_type", None))
            moneyline = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
            if moneyline and series_len and game_number == series_len:
                pinnacle_odds = moneyline
                if log_buffer and op_fixture:
                    log_buffer.add(
                        f"OddsPapi: using match moneyline for final game {game_number} "
                        f"({op_fixture.source_id})",
                        key=f"odds-final-game:{op_fixture.source_id}:{game_number}",
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
    return p_ref_a, p_ref_b, odds_a, odds_b


def _format_delta(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.3f}"


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
    out = _normalize(outcome)
    team = _normalize(fixture.team_a_name)
    return bool(out and team and (out == team or out in team or team in out))


def _is_team_b(fixture: Fixture, outcome: str) -> bool:
    out = _normalize(outcome)
    team = _normalize(fixture.team_b_name)
    return bool(out and team and (out == team or out in team or team in out))


def _extract_outcome_token_pairs(raw: dict) -> list[tuple[str, str]]:
    tokens = raw.get("tokens") or []
    if isinstance(tokens, str):
        try:
            import json

            tokens = json.loads(tokens)
        except (json.JSONDecodeError, TypeError):
            tokens = []
    pairs: list[tuple[str, str]] = []
    if isinstance(tokens, list):
        for token in tokens:
            if not isinstance(token, dict):
                continue
            token_id = token.get("token_id") or token.get("tokenId") or token.get("id")
            outcome = token.get("outcome") or token.get("name") or token.get("title")
            if token_id and outcome:
                pairs.append((str(outcome), str(token_id)))
    return pairs


def _extract_token_ids(raw: dict) -> list[str]:
    # Prefer explicit token objects when available, otherwise fall back to clobTokenIds.
    token_ids = [token_id for _, token_id in _extract_outcome_token_pairs(raw) if token_id]
    if token_ids:
        return token_ids
    return _parse_token_ids(raw)


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
    if pm_resolution in {"PROPOSED", "DISPUTED", "RESOLVED", "SETTLED"}:
        return True
    end_date = pm_latest.get("endDateIso") or pm_latest.get("endDate")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt <= now:
                return True
        except ValueError:
            pass
    return False


async def _get_pm_latest_cached_async(
    polymarket: AsyncPolymarketClient,
    cache: dict[str, tuple[datetime, dict]],
    market_id: str,
    now: datetime,
    ttl: timedelta,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    cached = cache.get(market_id)
    if cached and (now - cached[0]) <= ttl:
        return cached[1]
    async with semaphore:
        latest = await polymarket.get_market_by_id(market_id)
    if isinstance(latest, dict):
        cache[market_id] = (now, latest)
        return latest
    return None


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


def _latest_changed_at(values: list[str | None]) -> datetime | None:
    for value in values:
        if not value:
            continue
        if isinstance(value, (int, float)):
            try:
                ts = float(value)
                if ts > 10_000_000_000:  # ms -> s
                    ts = ts / 1000.0
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
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


def _build_entry_candidates(
    op_fixture: Fixture,
    pm_fixture: Fixture,
    snapshot: MatchSnapshot,
    ws_state: dict[str, BookState],
) -> list[dict]:
    books = _resolve_books(op_fixture, pm_fixture, ws_state)
    candidates: list[dict] = []
    for side, p_ref in (("buy_a", snapshot.p_ref_a), ("buy_b", snapshot.p_ref_b)):
        book = books.get(side)
        candidate = _build_entry_candidate(side, p_ref, book)
        if candidate:
            candidates.append(candidate)
    return candidates


def _resolve_books(
    op_fixture: Fixture,
    pm_fixture: Fixture,
    ws_state: dict[str, BookState],
) -> dict[str, BookState | None]:
    outcomes = _parse_outcomes(pm_fixture.raw_json or {})
    token_ids = _parse_token_ids(pm_fixture.raw_json or {})
    outcome_pairs = _pair_outcomes_with_tokens(op_fixture, outcomes, token_ids)
    books: dict[str, BookState | None] = {"buy_a": None, "buy_b": None}
    for outcome_name, token_id in outcome_pairs:
        if not token_id:
            continue
        book = ws_state.get(token_id)
        if not book:
            continue
        if _is_team_a(op_fixture, outcome_name):
            books["buy_a"] = book
        elif _is_team_b(op_fixture, outcome_name):
            books["buy_b"] = book
    if len(outcome_pairs) >= 2:
        if books["buy_a"] is None:
            books["buy_a"] = ws_state.get(outcome_pairs[0][1])
        if books["buy_b"] is None:
            books["buy_b"] = ws_state.get(outcome_pairs[1][1])
    return books


def _build_entry_candidate(
    side: str,
    p_ref: float | None,
    book: BookState | None,
) -> dict | None:
    if not book or p_ref is None:
        return None
    bid = book.best_bid
    ask = book.best_ask
    if bid is None or ask is None:
        return None
    spread = max(ask - bid, 0.0)
    alpha = compute_alpha_entry(spread, settings.alpha_min, settings.alpha_spread_factor)
    entry = compute_entry_edge(p_ref, book.asks, PROBE_QUANTITY, alpha)
    return {"side": side, "p_ref": p_ref, "alpha": alpha, "entry": entry}


def _select_best_candidate(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None

    def _score(candidate: dict) -> float:
        net_edge = candidate["entry"].get("net_edge")
        return net_edge if isinstance(net_edge, (int, float)) else -1.0

    return max(candidates, key=_score)


def _select_p_ref_and_bid(side: str, snapshot: MatchSnapshot) -> tuple[float | None, float | None]:
    if side == "buy_a":
        return snapshot.p_ref_a, snapshot.bid_a
    return snapshot.p_ref_b, snapshot.bid_b


def _trade_key(mapping: Mapping, pm_fixture: Fixture, side: str) -> str:
    return f"{mapping.id}:{pm_fixture.id}:{side}"


def _format_side_label(event: str, side: str | None) -> str:
    if not side:
        return "-"
    if side == "buy_a":
        token = "A"
    elif side == "buy_b":
        token = "B"
    else:
        return side.upper()

    if event in {"ENTRY", "ENTRY_SKIP", "ENTRY_CHECK"}:
        return f"BUY {token}"
    if event == "EXIT":
        return f"SELL {token}"
    return token


def _select_trigger_delta(trigger: TriggerEvent) -> float | None:
    deltas = [d for d in (trigger.delta_p_ref_a, trigger.delta_p_ref_b) if d is not None]
    if not deltas:
        return None
    return max(deltas, key=lambda value: abs(value))


def _format_trade_line(
    event: str,
    match: str,
    market_type: str | None,
    game_number: int | None,
    side: str | None,
    details: str,
) -> str:
    market_label = format_market_label(market_type, game_number)
    side_label = _format_side_label(event, side)
    if side_label == "-" or not details:
        return f"{event:<10} {match} | {market_label} | {details}"
    return f"{event:<10} {match} | {market_label} | {side_label} {details}"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2%}"


def _format_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _format_seconds(delta: timedelta | None) -> str:
    if not delta:
        return "n/a"
    return f"{delta.total_seconds():.1f}s"


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
