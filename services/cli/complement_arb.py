"""
Binary complement arbitrage manager for single-match live monitor.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from threading import Event
from datetime import datetime, timezone
from uuid import uuid4

from services.shared.clob_executor import ClobExecutor, OrderSubmission
from services.shared.config import settings
from services.shared.db import SessionLocal
from services.shared.edge import ComplementEdgeResult, compute_complement_edge, compute_vwap
from services.shared.models import ComplementArb, Fixture, Mapping, OrderAttempt, Position, TradeEvent
from services.shared.polymarket_ws import BookState, PolymarketWSManager

from .monitor_types import ComplementArbRecord, ComplementArbState, FocusSnapshot, LogBuffer
from .poller import SingleMatchPoller

logger = logging.getLogger("cli.live")


@dataclass
class _ExecutionOutcome:
    state: ComplementArbState
    fill_a: float | None
    fill_b: float | None
    order_id_a: str | None
    order_id_b: str | None
    detail: str


class ComplementArbManager:
    """Run binary complement strategy alongside lead-lag."""

    def __init__(
        self,
        *,
        poller: SingleMatchPoller,
        ws_manager: PolymarketWSManager,
        mapping: Mapping,
        pm_fixtures: list[Fixture],
        trade_mode: str,
        log_buffer: LogBuffer,
        trade_buffer: LogBuffer,
        executor: ClobExecutor | None = None,
        paused_event: Event | None = None,
    ) -> None:
        self._poller = poller
        self._ws_manager = ws_manager
        self._mapping = mapping
        self._pm_fixtures = pm_fixtures
        self._trade_mode = trade_mode
        self._mode_label = "real" if trade_mode == "live" else "paper"
        self._log_buffer = log_buffer
        self._trade_buffer = trade_buffer
        self._executor = executor
        self._paused_event = paused_event
        self._run_id = uuid4()
        self._edge_first_seen: dict[str, datetime] = {}
        self._records: dict[str, ComplementArbRecord] = {}
        self._stop: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if not settings.complement_arb_enabled:
            return
        if self._stop is None:
            self._stop = asyncio.Event()
        self._tasks = [asyncio.create_task(self._loop())]
        self._trade_buffer.add("COMP_START complement arb enabled")

    async def stop(self) -> None:
        if self._stop:
            self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _loop(self) -> None:
        interval = max(float(settings.complement_scan_interval_ms) / 1000.0, 0.05)
        while self._stop and not self._stop.is_set():
            try:
                await self._scan_once()
            except Exception:  # pragma: no cover - runtime guardrail  # pylint: disable=broad-exception-caught
                logger.exception("Complement arb loop iteration failed")
            await asyncio.sleep(interval)

    async def _scan_once(self) -> None:
        snapshots = self._poller.get_all_snapshots()
        if not snapshots:
            return
        ws_state = await self._ws_manager.get_state_snapshot()
        now = datetime.now(tz=timezone.utc)
        for snapshot in snapshots:
            if snapshot.market_type not in {"match_winner", "game_winner"}:
                continue
            if not snapshot.token_id_a or not snapshot.token_id_b:
                continue
            key = self._record_key(snapshot)
            existing = self._records.get(key)
            if existing and existing.state in {
                ComplementArbState.SUBMITTING,
                ComplementArbState.BOTH_FILLED,
                ComplementArbState.RETRYING_B,
                ComplementArbState.UNWINDING_A,
            }:
                continue
            book_a = ws_state.get(snapshot.token_id_a)
            book_b = ws_state.get(snapshot.token_id_b)
            if not self._books_fresh(book_a, book_b, now):
                self._edge_first_seen.pop(key, None)
                continue
            edge_result = compute_complement_edge(
                asks_a=book_a.asks if book_a else [],
                asks_b=book_b.asks if book_b else [],
                max_size=float(settings.complement_max_size),
            )
            if (
                edge_result.edge is None
                or edge_result.edge < float(settings.complement_min_edge)
                or edge_result.fillable_size <= 0
            ):
                self._edge_first_seen.pop(key, None)
                continue
            first_seen = self._edge_first_seen.get(key)
            if first_seen is None:
                self._edge_first_seen[key] = now
                continue
            seen_ms = (now - first_seen).total_seconds() * 1000.0
            if seen_ms < float(settings.complement_min_edge_duration_ms):
                continue
            self._edge_first_seen.pop(key, None)
            await self._execute(snapshot=snapshot, edge=edge_result, now=now)

    async def _execute(
        self,
        *,
        snapshot: FocusSnapshot,
        edge: ComplementEdgeResult,
        now: datetime,
    ) -> None:
        if self._paused_event and self._paused_event.is_set():
            return
        key = self._record_key(snapshot)
        target_size = min(edge.fillable_size, float(settings.complement_max_size))
        record = ComplementArbRecord(
            key=key,
            mapping_id=str(self._mapping.id),
            market_id=self._fixture_id(snapshot),
            market_type=snapshot.market_type,
            game_number=snapshot.game_number,
            token_id_a=snapshot.token_id_a or "",
            token_id_b=snapshot.token_id_b or "",
            state=ComplementArbState.SUBMITTING,
            created_at=now,
            updated_at=now,
            edge=edge.edge,
            target_size=target_size,
            vwap_a=edge.vwap_a,
            vwap_b=edge.vwap_b,
        )
        self._records[key] = record
        self._trade_buffer.add(
            f"COMP_SIGNAL {snapshot.market_type} g={snapshot.game_number} edge={edge.edge:+.2%} size={target_size:.2f}"
        )
        outcome = await self._submit_pair(snapshot=snapshot, edge=edge, target_size=target_size, now=now)
        record.state = outcome.state
        record.updated_at = now
        await self._persist_outcome(record=record, snapshot=snapshot, outcome=outcome, now=now)

    async def _submit_pair(
        self,
        *,
        snapshot: FocusSnapshot,
        edge: ComplementEdgeResult,
        target_size: float,
        now: datetime,
    ) -> _ExecutionOutcome:
        limit_a = (edge.vwap_a or 0.0) + float(settings.complement_fill_price_buffer)
        limit_b = (edge.vwap_b or 0.0) + float(settings.complement_fill_price_buffer)
        if self._trade_mode != "live":
            ws_state = await self._ws_manager.get_state_snapshot()
            book_a = ws_state.get(snapshot.token_id_a or "")
            book_b = ws_state.get(snapshot.token_id_b or "")
            fill_a = _simulate_fok_fill(book_a, target_size, limit_a)
            fill_b = _simulate_fok_fill(book_b, target_size, limit_b)
            if fill_a is not None and fill_b is not None:
                return _ExecutionOutcome(
                    state=ComplementArbState.BOTH_FILLED,
                    fill_a=fill_a,
                    fill_b=fill_b,
                    order_id_a=None,
                    order_id_b=None,
                    detail="paper_both_filled",
                )
            if fill_a is not None and fill_b is None:
                return _ExecutionOutcome(
                    state=ComplementArbState.LEG_A_ONLY,
                    fill_a=fill_a,
                    fill_b=None,
                    order_id_a=None,
                    order_id_b=None,
                    detail="paper_leg_b_miss",
                )
            return _ExecutionOutcome(
                state=ComplementArbState.FAILED,
                fill_a=None,
                fill_b=None,
                order_id_a=None,
                order_id_b=None,
                detail="paper_no_fill",
            )

        if not self._executor:
            return _ExecutionOutcome(
                state=ComplementArbState.FAILED,
                fill_a=None,
                fill_b=None,
                order_id_a=None,
                order_id_b=None,
                detail="missing_executor",
            )
        responses = self._executor.place_fok_batch(
            [
                {
                    "token_id": snapshot.token_id_a,
                    "side": "BUY",
                    "price": limit_a,
                    "amount": target_size * limit_a,
                    "tick_size": snapshot.tick_size,
                },
                {
                    "token_id": snapshot.token_id_b,
                    "side": "BUY",
                    "price": limit_b,
                    "amount": target_size * limit_b,
                    "tick_size": snapshot.tick_size,
                },
            ]
        )
        resp_a = responses[0] if len(responses) > 0 else None
        resp_b = responses[1] if len(responses) > 1 else None
        ok_a = bool(resp_a and resp_a.success)
        ok_b = bool(resp_b and resp_b.success)
        if ok_a and ok_b:
            return _ExecutionOutcome(
                state=ComplementArbState.BOTH_FILLED,
                fill_a=edge.vwap_a,
                fill_b=edge.vwap_b,
                order_id_a=resp_a.order_id if resp_a else None,
                order_id_b=resp_b.order_id if resp_b else None,
                detail="live_both_filled",
            )
        if ok_a and not ok_b:
            retry = await self._retry_missing_leg(
                token_id=snapshot.token_id_b or "",
                tick_size=snapshot.tick_size,
                target_size=target_size,
                limit_price=limit_b,
                start=now,
            )
            if retry and retry.success:
                return _ExecutionOutcome(
                    state=ComplementArbState.BOTH_FILLED,
                    fill_a=edge.vwap_a,
                    fill_b=edge.vwap_b,
                    order_id_a=resp_a.order_id if resp_a else None,
                    order_id_b=retry.order_id,
                    detail="live_retry_leg_b_filled",
                )
            await self._unwind_leg_a(
                token_id=snapshot.token_id_a or "",
                size=target_size,
                tick_size=snapshot.tick_size,
            )
            return _ExecutionOutcome(
                state=ComplementArbState.FAILED,
                fill_a=edge.vwap_a,
                fill_b=None,
                order_id_a=resp_a.order_id if resp_a else None,
                order_id_b=retry.order_id if retry else None,
                detail="live_leg_b_miss_unwind",
            )
        return _ExecutionOutcome(
            state=ComplementArbState.FAILED,
            fill_a=None,
            fill_b=None,
            order_id_a=resp_a.order_id if resp_a else None,
            order_id_b=resp_b.order_id if resp_b else None,
            detail="live_no_fill",
        )

    async def _retry_missing_leg(
        self,
        *,
        token_id: str,
        tick_size: float | None,
        target_size: float,
        limit_price: float,
        start: datetime,
    ) -> OrderSubmission | None:
        timeout_s = max(float(settings.complement_retry_timeout_secs), 0.0)
        if timeout_s <= 0 or not self._executor:
            return None
        end_ts = start.timestamp() + timeout_s
        while datetime.now(tz=timezone.utc).timestamp() < end_ts:
            response = self._executor.place_fok_order(
                token_id=token_id,
                side="BUY",
                price=limit_price,
                amount=target_size * limit_price,
                tick_size=tick_size,
            )
            if response.success:
                return response
            await asyncio.sleep(0.5)
        return None

    async def _unwind_leg_a(
        self,
        *,
        token_id: str,
        size: float,
        tick_size: float | None,
    ) -> None:
        if not self._executor:
            return
        ws_state = await self._ws_manager.get_state_snapshot()
        book = ws_state.get(token_id)
        best_bid = book.best_bid if book else None
        if best_bid is None:
            return
        buffer = max(float(settings.complement_unwind_cost_threshold), 0.0)
        unwind_price = max(best_bid - buffer, 0.001)
        self._executor.place_fok_order(
            token_id=token_id,
            side="SELL",
            price=unwind_price,
            amount=size,
            tick_size=tick_size,
        )
        self._trade_buffer.add(f"COMP_UNWIND token={token_id} px={unwind_price:.3f} size={size:.2f}")

    async def _persist_outcome(
        self,
        *,
        record: ComplementArbRecord,
        snapshot: FocusSnapshot,
        outcome: _ExecutionOutcome,
        now: datetime,
    ) -> None:
        pm_fixture = self._fixture_for_snapshot(snapshot)
        if pm_fixture is None:
            return
        with SessionLocal() as db:
            pos_a = None
            pos_b = None
            if outcome.fill_a is not None:
                pos_a = Position(
                    mapping_id=self._mapping.id,
                    pm_fixture_id=pm_fixture.id,
                    mode=self._mode_label,
                    strategy="complement_arb",
                    venue="paper" if self._trade_mode != "live" else "polymarket_clob",
                    market_type=snapshot.market_type or "unknown",
                    game_number=snapshot.game_number,
                    side="A",
                    opened_at=now,
                    entry_price=float(outcome.fill_a),
                    entry_edge=record.edge,
                    quantity=float(record.target_size or 0.0),
                    trigger_type="complement_arb",
                    status="confirmed" if record.state == ComplementArbState.BOTH_FILLED else "submitted",
                    external_order_id=outcome.order_id_a,
                    raw_json={"strategy": "complement_arb"},
                )
                db.add(pos_a)
                db.flush()
                record.position_id_a = str(pos_a.id)
            if outcome.fill_b is not None:
                pos_b = Position(
                    mapping_id=self._mapping.id,
                    pm_fixture_id=pm_fixture.id,
                    mode=self._mode_label,
                    strategy="complement_arb",
                    venue="paper" if self._trade_mode != "live" else "polymarket_clob",
                    market_type=snapshot.market_type or "unknown",
                    game_number=snapshot.game_number,
                    side="B",
                    opened_at=now,
                    entry_price=float(outcome.fill_b),
                    entry_edge=record.edge,
                    quantity=float(record.target_size or 0.0),
                    trigger_type="complement_arb",
                    status="confirmed" if record.state == ComplementArbState.BOTH_FILLED else "submitted",
                    external_order_id=outcome.order_id_b,
                    raw_json={"strategy": "complement_arb"},
                )
                db.add(pos_b)
                db.flush()
                record.position_id_b = str(pos_b.id)

            comp = ComplementArb(
                run_id=self._run_id,
                mapping_id=self._mapping.id,
                pm_fixture_id=pm_fixture.id,
                market_type=snapshot.market_type or "unknown",
                game_number=snapshot.game_number,
                mode=self._mode_label,
                state=record.state.value,
                leg_a_position_id=pos_a.id if pos_a else None,
                leg_b_position_id=pos_b.id if pos_b else None,
                vwap_a=record.vwap_a,
                vwap_b=record.vwap_b,
                target_size=record.target_size,
                locked_edge=record.edge if record.state == ComplementArbState.BOTH_FILLED else None,
                actual_cost_a=outcome.fill_a,
                actual_cost_b=outcome.fill_b,
                created_at=record.created_at,
                resolved_at=now if record.state in {ComplementArbState.RESOLVED, ComplementArbState.FAILED} else None,
                resolution_pnl=(1.0 - float(outcome.fill_a) - float(outcome.fill_b))
                if outcome.fill_a is not None and outcome.fill_b is not None
                else None,
                raw_json={"detail": outcome.detail},
            )
            db.add(comp)
            db.flush()
            record.db_id = str(comp.id)

            if pos_a:
                db.add(
                    TradeEvent(
                        ts=now,
                        run_id=self._run_id,
                        event_type="COMP_ENTRY_A",
                        mode=self._mode_label,
                        strategy="complement_arb",
                        mapping_id=self._mapping.id,
                        pm_fixture_id=pm_fixture.id,
                        position_id=pos_a.id,
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side="buy_a",
                        reason=record.state.value,
                        details=outcome.detail,
                        quantity=record.target_size,
                        avg_fill_price=outcome.fill_a,
                        net_edge=record.edge,
                        raw_json={"detail": outcome.detail},
                    )
                )
                db.add(
                    OrderAttempt(
                        position_id=pos_a.id,
                        run_id=self._run_id,
                        mode=self._mode_label,
                        strategy="complement_arb",
                        phase="entry",
                        side="BUY",
                        token_id=record.token_id_a,
                        attempt_seq=1,
                        submitted_at=now,
                        limit_price=(record.vwap_a or 0.0) + float(settings.complement_fill_price_buffer),
                        requested_size=record.target_size,
                        external_order_id=outcome.order_id_a,
                        external_status=record.state.value,
                        finalized_at=now,
                        final_state=record.state.value,
                        final_reason=outcome.detail,
                        raw_json={"detail": outcome.detail},
                    )
                )
            if pos_b:
                db.add(
                    TradeEvent(
                        ts=now,
                        run_id=self._run_id,
                        event_type="COMP_ENTRY_B",
                        mode=self._mode_label,
                        strategy="complement_arb",
                        mapping_id=self._mapping.id,
                        pm_fixture_id=pm_fixture.id,
                        position_id=pos_b.id,
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side="buy_b",
                        reason=record.state.value,
                        details=outcome.detail,
                        quantity=record.target_size,
                        avg_fill_price=outcome.fill_b,
                        net_edge=record.edge,
                        raw_json={"detail": outcome.detail},
                    )
                )
                db.add(
                    OrderAttempt(
                        position_id=pos_b.id,
                        run_id=self._run_id,
                        mode=self._mode_label,
                        strategy="complement_arb",
                        phase="entry",
                        side="BUY",
                        token_id=record.token_id_b,
                        attempt_seq=1,
                        submitted_at=now,
                        limit_price=(record.vwap_b or 0.0) + float(settings.complement_fill_price_buffer),
                        requested_size=record.target_size,
                        external_order_id=outcome.order_id_b,
                        external_status=record.state.value,
                        finalized_at=now,
                        final_state=record.state.value,
                        final_reason=outcome.detail,
                        raw_json={"detail": outcome.detail},
                    )
                )
            db.commit()

        self._trade_buffer.add(
            f"COMP_{record.state.value} {snapshot.market_type} g={snapshot.game_number} edge={(record.edge or 0.0):+.2%}"
        )

    def _books_fresh(
        self,
        book_a: BookState | None,
        book_b: BookState | None,
        now: datetime,
    ) -> bool:
        if not book_a or not book_b:
            return False
        stale_limit = max(float(settings.complement_stale_threshold_ms) / 1000.0, 0.0)
        age_a = (now - book_a.timestamp).total_seconds()
        age_b = (now - book_b.timestamp).total_seconds()
        return age_a <= stale_limit and age_b <= stale_limit

    def _record_key(self, snapshot: FocusSnapshot) -> str:
        return (
            f"{self._mapping.id}:{snapshot.market_type}:{snapshot.game_number}:"
            f"{snapshot.token_id_a}:{snapshot.token_id_b}"
        )

    def _fixture_id(self, snapshot: FocusSnapshot) -> str:
        fixture = self._fixture_for_snapshot(snapshot)
        return str(fixture.id) if fixture else ""

    def _fixture_for_snapshot(self, snapshot: FocusSnapshot) -> Fixture | None:
        for fixture in self._pm_fixtures:
            if fixture.market_type != snapshot.market_type:
                continue
            if (fixture.game_number or None) != (snapshot.game_number or None):
                continue
            return fixture
        return None


def _simulate_fok_fill(book: BookState | None, size: float, limit_price: float) -> float | None:
    """Simulate FOK fill against asks up to limit price."""
    if not book or size <= 0:
        return None
    executable: list[tuple[float, float]] = []
    avail = 0.0
    for price, level_size in book.asks:
        if price > limit_price:
            break
        executable.append((price, level_size))
        avail += level_size
        if avail >= size:
            break
    if avail < size:
        return None
    vwap, filled = compute_vwap(executable, size)
    if vwap is None or filled < size:
        return None
    return vwap
