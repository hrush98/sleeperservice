"""
Trade signal processing and execution for single-match monitor (v3).
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal, ROUND_DOWN
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from shared.config import settings
from shared.db import SessionLocal
from shared.edge import (
    NetEdgeResult,
    check_hard_stop,
    check_thesis_death,
    compute_alpha_entry,
    compute_entry_edge,
    compute_exit_signal,
)
from shared.fixture_state import FixtureStateManager, TriggerEvent
from shared.models import Fixture, Mapping, OrderAttempt, Position, TradeEvent
from shared.polymarket_user_ws import PolymarketUserWSManager
from shared.polymarket_ws import BookState, PolymarketWSManager
from shared.clob_executor import CancelSubmission, ClobExecutor, OrderSubmission

from .monitor_types import FocusSnapshot, LogBuffer, PaperTrade, TriggerRecord, format_market_label
from .poller import SingleMatchPoller

logger = logging.getLogger("cli.live")

PROBE_QUANTITY = 100.0
TRIGGER_TTL_MINUTES = 30


@dataclass
class TradeState:
    open_trades: dict[str, PaperTrade]


class TradeManager:
    """Evaluate signals and manage positions for one match (all markets)."""

    def __init__(
        self,
        poller: SingleMatchPoller,
        ws_manager: PolymarketWSManager,
        user_ws: PolymarketUserWSManager | None,
        fixture_states: FixtureStateManager,
        mapping: Mapping,
        op_fixture: Fixture,
        pm_fixtures: list[Fixture],
        trade_mode: str,
        edge_threshold: float,
        spread_factor: float,
        log_buffer: LogBuffer,
        trade_buffer: LogBuffer,
        live_private_key: str | None = None,
    ) -> None:
        self._poller = poller
        self._ws_manager = ws_manager
        self._user_ws = user_ws
        self._fixture_states = fixture_states
        self._mapping = mapping
        self._op_fixture = op_fixture
        self._pm_fixtures = pm_fixtures
        self._pm_fixture = pm_fixtures[0]  # primary (match_winner)
        self._trade_mode = trade_mode
        self._edge_threshold = edge_threshold
        self._spread_factor = spread_factor
        self._log_buffer = log_buffer
        self._trade_buffer = trade_buffer
        self._open_trades: dict[str, PaperTrade] = {}
        self._open_trades_lock = Lock()
        self._latest_snapshots: dict[str, FocusSnapshot] = {}
        self._triggers: dict[str, TriggerRecord] = {}
        self._run_id = uuid4()
        self._mode_label = "real" if trade_mode == "live" else "paper"
        self._executor: ClobExecutor | None = None
        self._last_live_order_ts: float | None = None
        self._last_balance_reconcile_ts: datetime | None = None
        self._balance_zero_poll_counts: dict[str, int] = {}
        self._last_stale_sweep_ts: datetime | None = None
        self._stop: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []

        if trade_mode == "live":
            if not live_private_key:
                raise ValueError("Live mode requires private key")
            self._executor = ClobExecutor(
                private_key=live_private_key,
                funder=settings.polymarket_funder_address,
                signature_type=settings.polymarket_signature_type,
                chain_id=settings.polymarket_chain_id,
            )

    async def start(self) -> None:
        if self._stop is None:
            self._stop = asyncio.Event()
        if self._trade_mode == "live":
            now = datetime.now(tz=timezone.utc)
            self._sweep_stale_open_positions(now)
            self._last_balance_reconcile_ts = _seed_balance_reconcile_last_ts(
                now=now,
                interval_seconds=float(settings.balance_poll_interval_seconds),
                first_delay_seconds=float(settings.balance_first_poll_delay_seconds),
            )
            self._last_stale_sweep_ts = now
        self._tasks = [asyncio.create_task(self._trade_loop())]
        if self._trade_mode == "live":
            self._tasks.append(asyncio.create_task(self._reconcile_open_orders_loop()))

    async def stop(self) -> None:
        if self._stop:
            self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def get_open_trades(self) -> list[PaperTrade]:
        with self._open_trades_lock:
            return list(self._open_trades.values())

    @property
    def executor(self) -> ClobExecutor | None:
        return self._executor

    def set_user_ws(self, user_ws: PolymarketUserWSManager | None) -> None:
        self._user_ws = user_ws

    def _next_attempt_seq(self, db: SessionLocal, position_id: str, phase: str) -> int:
        stmt = (
            select(func.max(OrderAttempt.attempt_seq))
            .where(OrderAttempt.position_id == position_id)
            .where(OrderAttempt.phase == phase)
        )
        current = db.execute(stmt).scalar()
        return int(current or 0) + 1

    def _build_stub_snapshot(self, trade: PaperTrade, now: datetime) -> FocusSnapshot:
        return FocusSnapshot(
            mapping_id=trade.mapping_id,
            league="",
            match="",
            start_time=None,
            market_type=trade.market_type,
            game_number=trade.game_number,
            tick_size=None,
            pm_resolution_status="",
            pm_winner=None,
            p_ref_a=None,
            p_ref_b=None,
            odds_a=None,
            odds_b=None,
            bid_a=None,
            ask_a=None,
            bid_b=None,
            ask_b=None,
            mid_a=None,
            mid_b=None,
            token_id_a=None,
            token_id_b=None,
            edge=NetEdgeResult(None, None, None, None),
            updated_at=now,
            ws_connected=False,
            last_odds_update=None,
            last_gamma_update=None,
        )

    @staticmethod
    def _is_delayed_status(status: str | None) -> bool:
        return (status or "").strip().lower() == "delayed"

    @staticmethod
    def _attempt_is_delayed(attempt: OrderAttempt) -> bool:
        if TradeManager._is_delayed_status(attempt.external_status):
            return True
        raw = attempt.raw_json if isinstance(attempt.raw_json, dict) else {}
        nested = raw.get("response") if isinstance(raw.get("response"), dict) else {}
        return TradeManager._is_delayed_status(nested.get("status") or raw.get("status"))

    def _delayed_retry_cooldown_seconds(self, *, for_entry: bool) -> float:
        base = max(float(settings.delayed_retry_cooldown_seconds), 0.0)
        if for_entry:
            return max(base, float(settings.live_min_seconds_between_orders))
        return base

    def _delayed_retry_ready(self, trade: PaperTrade, now: datetime, *, for_entry: bool) -> bool:
        if trade.delayed_retries >= max(int(settings.delayed_max_retries), 0):
            return False
        age_seconds = (now - trade.entry_ts).total_seconds()
        if age_seconds < float(settings.delayed_grace_seconds):
            return False
        if trade.last_retry_ts is None:
            return True
        delta = (now - trade.last_retry_ts).total_seconds()
        return delta >= self._delayed_retry_cooldown_seconds(for_entry=for_entry)

    @staticmethod
    def _merge_dict(base: dict | None, extra: dict) -> dict:
        payload = dict(base or {})
        payload.update(extra)
        return payload

    def _cancel_existing_order(self, order_id: str | None) -> CancelSubmission:
        if not order_id or not self._executor:
            return CancelSubmission(
                success=False,
                canceled=[],
                not_canceled={order_id or "": "missing_order_id"},
                error_msg="missing_order_id",
                raw={"order_id": order_id, "reason": "missing_order_id"},
            )
        return self._executor.cancel_order(order_id)

    def _ensure_trade_for_position(self, position: Position) -> PaperTrade | None:
        pm_fixture = next(
            (
                fix
                for fix in self._pm_fixtures
                if position.pm_fixture_id and str(fix.id) == str(position.pm_fixture_id)
            ),
            self._pm_fixture,
        )
        if not pm_fixture:
            return None
        side = "buy_a" if position.side == "A" else "buy_b"
        key = _trade_key(self._mapping, pm_fixture, side)
        with self._open_trades_lock:
            existing = self._open_trades.get(key)
        if existing:
            return existing
        raw = position.raw_json or {}
        trade = PaperTrade(
            key=key,
            mapping_id=str(position.mapping_id),
            market_id=str(position.pm_fixture_id),
            token_id=raw.get("token_id"),
            market_type=position.market_type,
            game_number=position.game_number,
            side=side,
            trigger_type=position.trigger_type,
            trigger_ts=_parse_dt(raw.get("trigger_ts")),
            entry_ts=position.opened_at,
            entry_price=position.entry_price,
            limit_price=raw.get("limit_price"),
            size_available=raw.get("size_available"),
            quantity=position.quantity,
            alpha=position.entry_alpha or 0.0,
            net_edge=position.entry_edge,
            p_ref_entry=position.entry_p_ref,
            status=position.status,
            external_order_id=position.external_order_id,
            external_status=position.external_status,
            db_position_id=str(position.id),
        )
        with self._open_trades_lock:
            self._open_trades[key] = trade
        return trade

    def _pm_fixture_for_snapshot(self, snapshot: FocusSnapshot) -> Fixture | None:
        for pm_fix in self._pm_fixtures:
            if (pm_fix.market_type == snapshot.market_type
                    and pm_fix.game_number == snapshot.game_number):
                return pm_fix
        return self._pm_fixture  # fallback to primary

    @staticmethod
    def _snapshot_cache_key(pm_fixture: Fixture) -> str:
        return str(pm_fixture.id)

    def _snapshot_for_trade(
        self,
        *,
        pm_fixture: Fixture,
        trade: PaperTrade,
        now: datetime,
    ) -> FocusSnapshot:
        cached = self._latest_snapshots.get(self._snapshot_cache_key(pm_fixture))
        if cached is not None:
            return cached
        return self._build_stub_snapshot(trade, now)

    def _handle_exit_timeout_with_balance_fallback(
        self,
        *,
        db: SessionLocal,
        attempt: OrderAttempt,
        position: Position,
        trade: PaperTrade,
        now: datetime,
        pm_fixture: Fixture,
        snapshot: FocusSnapshot,
        timeout_seconds: float,
        requested: float,
        eps: float,
        allow_partial_fill: bool,
    ) -> bool:
        if not _is_order_not_found_timeout(attempt.submitted_at, now, timeout_seconds):
            return False
        if attempt.final_reason == "order_not_found_timeout":
            db.add(attempt)
            db.commit()
            return True

        attempt.final_reason = "order_not_found_timeout"
        balance = None
        bal_raw: dict | None = None
        if self._executor and attempt.token_id:
            try:
                balance, bal_raw = self._executor.get_conditional_balance(attempt.token_id)
            except Exception:
                balance, bal_raw = None, None

        outcome, filled_qty = _classify_exit_timeout_balance_outcome(
            balance=balance,
            requested=requested,
            eps=eps,
            trade_status=trade.status,
            allow_partial_fill=allow_partial_fill,
        )
        if outcome == "balance_zero":
            attempt.finalized_at = now
            attempt.final_state = "confirmed"
            attempt.final_reason = "balance_zero"
            attempt.matched_size = requested if requested > 0 else None
            position.closed_at = now
            position.exit_reason = attempt.raw_json.get("exit_reason", "exit")
            position.exit_price = attempt.limit_price
            position.hold_seconds = (now - position.opened_at).total_seconds()
            db.add(position)
            event = self._build_trade_event(
                event_type="EXIT_CONFIRMED",
                now=now,
                mapping=self._mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=trade.side,
                reason=position.exit_reason,
                details="balance_reconcile",
                position_id=str(position.id),
                exit_price=position.exit_price,
                external_order_id=attempt.external_order_id,
                external_status=attempt.external_status,
                raw_json={"balance": balance, "balance_raw": bal_raw},
            )
            db.add(event)
            trade.status = "closed"
            with self._open_trades_lock:
                self._open_trades.pop(trade.key, None)
            self._trade_buffer.add(
                _format_trade_line(
                    event="EXIT_CONFIRMED",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=trade.side,
                    details=f"@ {_format_price(position.exit_price)} reason={position.exit_reason}",
                )
            )
            db.add(attempt)
            db.commit()
            return True

        if outcome == "partial_fill_balance" and balance is not None:
            attempt.finalized_at = now
            attempt.final_state = "confirmed"
            attempt.final_reason = "partial_fill_balance"
            if filled_qty > eps:
                attempt.matched_size = filled_qty
            trade.quantity = balance
            position.quantity = balance
            trade.status = "open"
            db.add(position)
            self._trade_buffer.add(
                f"PARTIAL_EXIT {trade.key}: filled={filled_qty:.2f} remaining={max(balance, 0):.2f}",
            )

        event = self._build_trade_event(
            event_type="EXIT_ERROR",
            now=now,
            mapping=self._mapping,
            pm_fixture=pm_fixture,
            snapshot=snapshot,
            side=trade.side,
            reason="order_not_found",
            details=f"timeout={timeout_seconds:.0f}s",
            position_id=str(position.id),
            external_order_id=attempt.external_order_id,
            external_status=attempt.external_status,
            raw_json={"timeout_seconds": timeout_seconds},
        )
        db.add(event)
        if outcome == "timeout_reopen" and balance is not None:
            attempt.finalized_at = now
            attempt.final_state = "failed"
            attempt.final_reason = "timeout_reopen"
            if filled_qty > eps:
                attempt.matched_size = filled_qty
            trade.quantity = balance
            position.quantity = balance
            trade.status = "open"
            db.add(position)
            retry_event = self._build_trade_event(
                event_type="EXIT_RETRY",
                now=now,
                mapping=self._mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=trade.side,
                reason="order_not_found",
                details="timeout_reopen",
                position_id=str(position.id),
                external_order_id=attempt.external_order_id,
                external_status=attempt.external_status,
                raw_json={"balance": balance, "balance_raw": bal_raw},
            )
            db.add(retry_event)
        db.add(attempt)
        db.commit()
        return True

    def _reconcile_open_trade_balances(self, now: datetime) -> None:
        if self._trade_mode != "live" or not self._executor:
            return
        with self._open_trades_lock:
            open_trades = list(self._open_trades.values())
        if not open_trades:
            return
        eps = max(float(getattr(settings, "live_share_step", 0.0001) or 0.0001), 0.000001)
        required_zero_polls = max(int(settings.balance_reconcile_zero_polls_required), 1)
        to_close: list[str] = []
        active_position_ids = {str(t.db_position_id) for t in open_trades if t.db_position_id}
        with SessionLocal() as db:
            for trade in open_trades:
                if not trade.db_position_id or not trade.token_id:
                    continue
                position = db.get(Position, trade.db_position_id)
                if not position or position.closed_at is not None:
                    continue
                position_id = str(position.id)
                try:
                    balance, bal_raw = self._executor.get_conditional_balance(trade.token_id)
                except Exception:
                    continue
                if balance is None:
                    continue
                pm_fixture = next(
                    (
                        fix
                        for fix in self._pm_fixtures
                        if position.pm_fixture_id and str(fix.id) == str(position.pm_fixture_id)
                    ),
                    self._pm_fixture,
                )
                snapshot = self._snapshot_for_trade(
                    pm_fixture=pm_fixture,
                    trade=trade,
                    now=now,
                )
                prior_zero_count = self._balance_zero_poll_counts.get(position_id, 0)
                zero_count = _next_balance_zero_poll_count(
                    balance=balance,
                    eps=eps,
                    current_count=prior_zero_count,
                )
                if zero_count > 0:
                    self._balance_zero_poll_counts[position_id] = zero_count
                    if zero_count < required_zero_polls:
                        event = self._build_trade_event(
                            event_type="EXIT_RETRY",
                            now=now,
                            mapping=self._mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=trade.side,
                            reason="balance_reconcile_pending",
                            details=f"zero_poll={zero_count}/{required_zero_polls}",
                            position_id=position_id,
                            external_order_id=position.external_order_id,
                            external_status=position.external_status,
                            raw_json={
                                "balance": balance,
                                "balance_raw": bal_raw,
                                "zero_poll_count": zero_count,
                                "required_zero_polls": required_zero_polls,
                            },
                        )
                        db.add(event)
                        continue
                    position.closed_at = now
                    position.status = "closed"
                    position.exit_reason = position.exit_reason or "balance_reconciled"
                    position.hold_seconds = (now - position.opened_at).total_seconds()
                    db.add(position)
                    event = self._build_trade_event(
                        event_type="EXIT_CONFIRMED",
                        now=now,
                        mapping=self._mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=trade.side,
                        reason=position.exit_reason,
                        details="manual_or_external_close_reconciled",
                        position_id=position_id,
                        external_order_id=position.external_order_id,
                        external_status=position.external_status,
                        raw_json={
                            "balance": balance,
                            "balance_raw": bal_raw,
                            "zero_poll_count": zero_count,
                            "required_zero_polls": required_zero_polls,
                        },
                    )
                    db.add(event)
                    self._trade_buffer.add(
                        _format_trade_line(
                            event="EXIT_CONFIRMED",
                            match=snapshot.match,
                            market_type=snapshot.market_type,
                            game_number=snapshot.game_number,
                            side=trade.side,
                            details=f"balance_reconciled reason={position.exit_reason}",
                        )
                    )
                    self._balance_zero_poll_counts.pop(position_id, None)
                    to_close.append(trade.key)
                    continue
                self._balance_zero_poll_counts.pop(position_id, None)
                if abs(balance - trade.quantity) > eps:
                    old_qty = trade.quantity
                    trade.quantity = balance
                    position.quantity = balance
                    db.add(position)
                    event = self._build_trade_event(
                        event_type="EXIT_RETRY",
                        now=now,
                        mapping=self._mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=trade.side,
                        reason="balance_sync",
                        details=f"qty_sync {old_qty:.2f}->{balance:.2f}",
                        position_id=position_id,
                        external_order_id=position.external_order_id,
                        external_status=position.external_status,
                        raw_json={"balance": balance, "balance_raw": bal_raw},
                    )
                    db.add(event)
            db.commit()
        stale_ids = [
            position_id
            for position_id in self._balance_zero_poll_counts
            if position_id not in active_position_ids
        ]
        for position_id in stale_ids:
            self._balance_zero_poll_counts.pop(position_id, None)
        if to_close:
            with self._open_trades_lock:
                for key in to_close:
                    trade = self._open_trades.get(key)
                    if trade:
                        trade.status = "closed"
                        self._open_trades.pop(key, None)

    def _sweep_stale_open_positions(self, now: datetime) -> None:
        if self._trade_mode != "live" or not self._executor:
            return
        eps = max(float(getattr(settings, "live_share_step", 0.0001) or 0.0001), 0.000001)
        with SessionLocal() as db:
            positions = (
                db.execute(
                    select(Position)
                    .where(Position.mapping_id == self._mapping.id)
                    .where(Position.mode == self._mode_label)
                    .where(Position.closed_at.is_(None))
                )
                .scalars()
                .all()
            )
            for position in positions:
                pm_fixture = next(
                    (
                        fix
                        for fix in self._pm_fixtures
                        if position.pm_fixture_id and str(fix.id) == str(position.pm_fixture_id)
                    ),
                    self._pm_fixture,
                )
                trade = self._ensure_trade_for_position(position)
                if not trade or not trade.token_id:
                    continue
                snapshot = self._snapshot_for_trade(
                    pm_fixture=pm_fixture,
                    trade=trade,
                    now=now,
                )
                if not (_fixture_is_resolved(pm_fixture) or _is_market_ended(snapshot)):
                    continue
                try:
                    balance, bal_raw = self._executor.get_conditional_balance(trade.token_id)
                except Exception:
                    continue
                if balance is None:
                    continue
                if balance <= eps:
                    position.closed_at = now
                    position.status = "closed"
                    position.exit_reason = "stale_reconciled"
                    position.hold_seconds = (now - position.opened_at).total_seconds()
                    db.add(position)
                    event = self._build_trade_event(
                        event_type="EXIT_CONFIRMED",
                        now=now,
                        mapping=self._mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=trade.side,
                        reason="stale_reconciled",
                        details="resolved_market_balance_zero",
                        position_id=str(position.id),
                        external_order_id=position.external_order_id,
                        external_status=position.external_status,
                        raw_json={"balance": balance, "balance_raw": bal_raw},
                    )
                    db.add(event)
                    self._trade_buffer.add(
                        _format_trade_line(
                            event="EXIT_CONFIRMED",
                            match=snapshot.match,
                            market_type=snapshot.market_type,
                            game_number=snapshot.game_number,
                            side=trade.side,
                            details="stale_reconciled",
                        )
                    )
                    with self._open_trades_lock:
                        self._open_trades.pop(trade.key, None)
                else:
                    event = self._build_trade_event(
                        event_type="EXIT_ERROR",
                        now=now,
                        mapping=self._mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=trade.side,
                        reason="stale_position_balance_nonzero",
                        details=f"resolved_market_shares={balance:.2f}",
                        position_id=str(position.id),
                        external_order_id=position.external_order_id,
                        external_status=position.external_status,
                        raw_json={"balance": balance, "balance_raw": bal_raw},
                    )
                    db.add(event)
            db.commit()

    async def _trade_loop(self) -> None:
        while self._stop and not self._stop.is_set():
            try:
                snapshots = await self._poller.build_all_snapshots()
                if not snapshots:
                    await asyncio.sleep(0.5)
                    continue
                now = datetime.now(tz=timezone.utc)
                if self._trade_mode == "live":
                    if (
                        self._last_balance_reconcile_ts is None
                        or (now - self._last_balance_reconcile_ts).total_seconds()
                        >= float(settings.balance_poll_interval_seconds)
                    ):
                        self._reconcile_open_trade_balances(now)
                        self._last_balance_reconcile_ts = now
                    if (
                        self._last_stale_sweep_ts is None
                        or (now - self._last_stale_sweep_ts).total_seconds()
                        >= float(settings.stale_position_sweep_seconds)
                    ):
                        self._sweep_stale_open_positions(now)
                        self._last_stale_sweep_ts = now
                fixture_id = str(self._op_fixture.source_id)

                # Trigger detection uses match_winner p_ref (the leading signal)
                match_snap = next(
                    (s for s in snapshots if s.market_type == "match_winner"),
                    snapshots[0],
                )
                trigger = self._fixture_states.update_p_ref(
                    fixture_id, match_snap.p_ref_a, match_snap.p_ref_b, now=now
                )

                # If no delta trigger, check edge-based triggers
                if trigger is None:
                    match_tkey = _trigger_key(fixture_id, match_snap)
                    existing = self._triggers.get(match_tkey)
                    has_active_entry = existing and not existing.entry_done
                    if not has_active_entry:
                        # Spike check first (immediate, single poll, ≥4%)
                        trigger = self._fixture_states.check_edge_spike(
                            fixture_id,
                            match_snap.edge.best_edge,
                            match_snap.edge.best_side,
                            now=now,
                        )
                        # Persist check second (2 consecutive polls ≥3%)
                        if trigger is None:
                            trigger = self._fixture_states.check_edge_trigger(
                                fixture_id,
                                match_snap.edge.best_edge,
                                match_snap.edge.best_side,
                                now=now,
                            )
                        if trigger and existing and existing.entry_done:
                            # Clear stale done triggers to make room
                            for snap in snapshots:
                                tkey = _trigger_key(fixture_id, snap)
                                self._triggers.pop(tkey, None)

                if trigger:
                    # Fire trigger for markets that have a reference price or
                    # are not at price-certainty (no point evaluating dead books).
                    for snap in snapshots:
                        if snap.p_ref_a is None and snap.p_ref_b is None:
                            continue
                        if _is_market_ended(snap):
                            continue
                        tkey = _trigger_key(fixture_id, snap)
                        if tkey not in self._triggers:
                            self._triggers[tkey] = TriggerRecord(trigger=trigger, ts=now)

                ws_state = await self._ws_manager.get_state_snapshot()

                has_active_trigger = False
                for snap in snapshots:
                    pm_fix = self._pm_fixture_for_snapshot(snap)
                    if not pm_fix:
                        continue
                    self._latest_snapshots[self._snapshot_cache_key(pm_fix)] = snap
                    tkey = _trigger_key(fixture_id, snap)
                    ended = _is_market_ended(snap)
                    await self._process_trade_signals(
                        mapping=self._mapping,
                        op_fixture=self._op_fixture,
                        pm_fixture=pm_fix,
                        snapshot=snap,
                        ws_state=ws_state,
                        now=now,
                        ended=ended,
                        trigger_key=tkey,
                    )
                    tr = self._triggers.get(tkey)
                    if tr and not tr.entry_done:
                        has_active_trigger = True

                await asyncio.sleep(
                    settings.trade_loop_hot_seconds
                    if has_active_trigger
                    else settings.trade_loop_idle_seconds
                )
            except Exception:
                logger.exception("Trade loop iteration failed")
                await asyncio.sleep(1.0)

    def _build_trade_event(
        self,
        event_type: str,
        now: datetime,
        mapping: Mapping,
        pm_fixture: Fixture,
        snapshot: FocusSnapshot,
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
        convergence_seconds: float | None = None,
        external_order_id: str | None = None,
        external_fill_id: str | None = None,
        external_status: str | None = None,
        raw_json: dict | None = None,
    ) -> TradeEvent:
        payload = dict(raw_json or {})
        return TradeEvent(
            ts=now,
            run_id=self._run_id,
            event_type=event_type,
            mode=self._mode_label,
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
            convergence_seconds=convergence_seconds,
            external_order_id=external_order_id,
            external_fill_id=external_fill_id,
            external_status=external_status,
            raw_json=payload,
        )

    async def _process_trade_signals(
        self,
        mapping: Mapping,
        op_fixture: Fixture,
        pm_fixture: Fixture,
        snapshot: FocusSnapshot,
        ws_state: dict[str, BookState],
        now: datetime,
        ended: bool,
        trigger_key: str | None = None,
    ) -> None:
        fixture_id = str(op_fixture.source_id)
        if trigger_key is None:
            trigger_key = fixture_id
        trigger_record = self._triggers.get(trigger_key)
        if ended:
            self._check_for_exits(mapping, pm_fixture, snapshot, now, ended, trigger_record)
            return
        if _is_market_endgame(snapshot):
            self._check_for_exits(mapping, pm_fixture, snapshot, now, False, trigger_record)
            return
        if trigger_record and (now - trigger_record.ts) > timedelta(minutes=TRIGGER_TTL_MINUTES):
            self._triggers.pop(trigger_key, None)
            trigger_record = None

        if trigger_record and not trigger_record.logged:
            trigger = trigger_record.trigger
            if trigger.trigger_type in ("edge_persist", "edge_spike"):
                trigger_details = f"edge={_format_pct(trigger.best_edge)} ({trigger.trigger_type})"
                raw_json = {
                    "trigger_type": trigger.trigger_type,
                    "best_edge": trigger.best_edge,
                }
            else:
                delta = _select_trigger_delta(trigger)
                trigger_details = f"delta={_format_pct(delta)} ({trigger.trigger_type})"
                raw_json = {
                    "trigger_type": trigger.trigger_type,
                    "delta_p_ref_a": trigger.delta_p_ref_a,
                    "delta_p_ref_b": trigger.delta_p_ref_b,
                }
            self._trade_buffer.add(
                _format_trade_line(
                    event="TRIGGER",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=None,
                    details=trigger_details,
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
                    details=trigger_details,
                    raw_json=raw_json,
                )
                db.add(event)
                db.commit()
            trigger_record.logged = True

        orientation_block_reason = _orientation_entry_block_reason(snapshot)
        entry_candidates = [] if orientation_block_reason else _build_entry_candidates(snapshot, ws_state)
        entry_window_open = False
        if trigger_record and not trigger_record.entry_done:
            if trigger_record.entry_checked_at is None:
                trigger_record.entry_checked_at = now
                entry_window_open = True
            elif (now - trigger_record.entry_checked_at).total_seconds() < settings.entry_reeval_seconds:
                entry_window_open = True
            else:
                trigger_record.entry_done = True
        if entry_window_open:
            is_first_check = (now - trigger_record.entry_checked_at).total_seconds() < 0.1
            if orientation_block_reason:
                trigger_record.entry_done = True
                if is_first_check:
                    details = f"skip {orientation_block_reason}"
                    self._trade_buffer.add(
                        _format_trade_line(
                            event="ENTRY_SKIP",
                            match=snapshot.match,
                            market_type=snapshot.market_type,
                            game_number=snapshot.game_number,
                            side=None,
                            details=details,
                        )
                    )
                    with SessionLocal() as db:
                        event = self._build_trade_event(
                            event_type="ENTRY_SKIP",
                            now=now,
                            mapping=mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=None,
                            reason=orientation_block_reason,
                            details=details,
                        )
                        db.add(event)
                        db.commit()
                self._check_for_exits(mapping, pm_fixture, snapshot, now, ended, trigger_record)
                return
            best = _select_best_candidate(entry_candidates)
            if not best:
                if is_first_check:
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
                token_id = best.get("token_id")
                key = _trade_key(mapping, pm_fixture, side)
                with self._open_trades_lock:
                    existing_trade = self._open_trades.get(key)
                    already_open = existing_trade is not None
                if already_open:
                    can_retry_delayed = (
                        self._trade_mode == "live"
                        and existing_trade is not None
                        and existing_trade.status == "submitted"
                        and self._is_delayed_status(existing_trade.external_status)
                        and entry["actionable"]
                        and self._delayed_retry_ready(existing_trade, now, for_entry=True)
                    )
                    if can_retry_delayed:
                        retried = await self._retry_delayed_live_entry(
                            mapping=mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=side,
                            token_id=token_id,
                            entry=entry,
                            size_available=float(entry["size_available"] or 0.0),
                            quantity=(
                                min(
                                    PROBE_QUANTITY,
                                    float(entry["size_available"] or 0.0),
                                )
                                if float(entry["size_available"] or 0.0)
                                else PROBE_QUANTITY
                            ),
                            now=now,
                            trade=existing_trade,
                            alpha=float(best["alpha"]),
                            p_ref=best["p_ref"],
                        )
                        if retried:
                            trigger_record.entry_done = True
                    else:
                        trigger_record.entry_done = True
                        details = "skip already_open"
                        reason = "already_open"
                        if (
                            self._trade_mode == "live"
                            and existing_trade is not None
                            and existing_trade.status == "submitted"
                            and self._is_delayed_status(existing_trade.external_status)
                        ):
                            reason = "delayed_grace_or_cooldown"
                            details = "skip delayed_wait"
                        self._trade_buffer.add(
                            _format_trade_line(
                                event="ENTRY_SKIP",
                                match=snapshot.match,
                                market_type=snapshot.market_type,
                                game_number=snapshot.game_number,
                                side=side,
                                details=details,
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
                                reason=reason,
                                details=details,
                                raw_json=(
                                    {
                                        "delayed_retries": existing_trade.delayed_retries,
                                    }
                                    if existing_trade is not None
                                    else {}
                                ),
                            )
                            db.add(event)
                            db.commit()
                elif entry["actionable"]:
                    size_available = float(entry["size_available"] or 0.0)
                    quantity = min(PROBE_QUANTITY, size_available) if size_available else PROBE_QUANTITY
                    if self._trade_mode == "live":
                        trade = await self._submit_live_entry(
                            mapping=mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=side,
                            token_id=token_id,
                            entry=entry,
                            size_available=size_available,
                            quantity=quantity,
                            now=now,
                            trigger_record=trigger_record,
                            alpha=best["alpha"],
                            p_ref=best["p_ref"],
                        )
                        if trade:
                            trigger_record.entry_done = True
                            with self._open_trades_lock:
                                self._open_trades[key] = trade
                    else:
                        trade = PaperTrade(
                            key=key,
                            mapping_id=str(mapping.id),
                            market_id=str(pm_fixture.id),
                            token_id=token_id,
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
                            "token_id": token_id,
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
                                mode=self._mode_label,
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
                            )
                            db.add(event)
                            db.commit()
                            db.refresh(position)
                            trade.db_position_id = str(position.id)
                        trigger_record.entry_done = True
                        with self._open_trades_lock:
                            self._open_trades[key] = trade
                        self._trade_buffer.add(
                            _format_trade_line(
                                event="ENTRY",
                                match=snapshot.match,
                                market_type=snapshot.market_type,
                                game_number=snapshot.game_number,
                                side=side,
                                details=f"@ {trade.entry_price:.3f} (edge={_format_pct(trade.net_edge)})",
                            )
                        )
                else:
                    if is_first_check:
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
                                    f"size={size_available:.0f}"
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
                                    f"size={size_available:.0f}"
                                ),
                                net_edge=entry["net_edge"],
                                size_available=size_available,
                                limit_price=entry["limit_price"],
                            )
                            db.add(event)
                            db.commit()

        self._check_for_exits(mapping, pm_fixture, snapshot, now, ended, trigger_record)

    async def _submit_live_entry(
        self,
        *,
        mapping: Mapping,
        pm_fixture: Fixture,
        snapshot: FocusSnapshot,
        side: str,
        token_id: str | None,
        entry: dict,
        size_available: float,
        quantity: float,
        now: datetime,
        trigger_record: TriggerRecord | None,
        alpha: float,
        p_ref: float | None,
    ) -> PaperTrade | None:
        if not self._executor:
            return None
        if not token_id:
            self._trade_buffer.add(
                _format_trade_line(
                    event="ENTRY_SKIP",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=side,
                    details="no_token_id",
                )
            )
            return None

        limit_price = float(entry.get("limit_price") or entry.get("avg_fill") or 0.0)
        if limit_price <= 0:
            return None

        if len(self._open_trades) >= settings.live_max_open_positions:
            self._trade_buffer.add(
                _format_trade_line(
                    event="BLOCKED",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=side,
                    details="max_open_positions",
                )
            )
            with SessionLocal() as db:
                event = self._build_trade_event(
                    event_type="BLOCKED",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=side,
                    reason="max_open_positions",
                    details="max_open_positions",
                )
                db.add(event)
                db.commit()
            return None

        if self._last_live_order_ts is not None:
            delta = now.timestamp() - self._last_live_order_ts
            if delta < settings.live_min_seconds_between_orders:
                self._trade_buffer.add(
                    _format_trade_line(
                        event="BLOCKED",
                        match=snapshot.match,
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side=side,
                        details="throttle",
                    )
                )
                with SessionLocal() as db:
                    event = self._build_trade_event(
                        event_type="BLOCKED",
                        now=now,
                        mapping=mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=side,
                        reason="throttle",
                        details="throttle",
                    )
                    db.add(event)
                    db.commit()
                return None

        if quantity > settings.live_max_shares_per_order:
            quantity = settings.live_max_shares_per_order
        max_usd = settings.live_max_usd_per_order
        if max_usd > 0 and (limit_price * quantity) > max_usd:
            quantity = max_usd / limit_price if limit_price else 0.0
        if quantity <= 0:
            with SessionLocal() as db:
                event = self._build_trade_event(
                    event_type="BLOCKED",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=side,
                    reason="order_size_zero",
                    details="order_size_zero",
                )
                db.add(event)
                db.commit()
            return None

        buy_usdc = _quantize_usdc(limit_price * quantity)
        if buy_usdc <= 0:
            with SessionLocal() as db:
                event = self._build_trade_event(
                    event_type="BLOCKED",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=side,
                    reason="order_notional_zero",
                    details="order_notional_zero",
                )
                db.add(event)
                db.commit()
            return None

        if settings.live_require_allowance_check:
            allowance = self._executor.check_allowance_buy(buy_usdc)
            if not allowance.ok:
                self._trade_buffer.add(
                    _format_trade_line(
                        event="BLOCKED",
                        match=snapshot.match,
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side=side,
                        details=f"allowance={allowance.reason}",
                    )
                )
                with SessionLocal() as db:
                    event = self._build_trade_event(
                        event_type="BLOCKED",
                        now=now,
                        mapping=mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=side,
                        reason=allowance.reason,
                        details="allowance_check_failed",
                        raw_json={"allowance": allowance.raw},
                    )
                    db.add(event)
                    db.commit()
                return None

        side_label = "A" if side == "buy_a" else "B"
        trigger_type = trigger_record.trigger.trigger_type if trigger_record else None
        trigger_ts = trigger_record.ts if trigger_record else None
        entry_price = float(entry.get("avg_fill") or limit_price)
        reservation_raw = {
            "mapping_id": str(mapping.id),
            "market_id": str(pm_fixture.id),
            "token_id": token_id,
            "market_type": snapshot.market_type,
            "game_number": snapshot.game_number,
            "side": side_label,
            "trigger_type": trigger_type,
            "trigger_ts": trigger_ts.isoformat() if trigger_ts else None,
            "entry_ts": now.isoformat(),
            "entry_price": entry_price,
            "limit_price": limit_price,
            "size_available": size_available,
            "quantity": quantity,
            "buy_usdc": buy_usdc,
            "alpha": float(alpha or 0.0),
            "net_edge": entry.get("net_edge"),
            "p_ref_entry": p_ref,
            "reservation": True,
        }
        with SessionLocal() as db:
            stmt = (
                insert(Position)
                .values(
                    mapping_id=mapping.id,
                    pm_fixture_id=pm_fixture.id,
                    mode=self._mode_label,
                    venue="polymarket_clob",
                    market_type=snapshot.market_type or "unknown",
                    game_number=snapshot.game_number,
                    side=side_label,
                    opened_at=now,
                    entry_price=entry_price,
                    entry_p_ref=p_ref,
                    entry_alpha=float(alpha or 0.0),
                    entry_edge=entry.get("net_edge"),
                    quantity=quantity,
                    trigger_type=trigger_type,
                    status="reserved",
                    raw_json=reservation_raw,
                )
                .on_conflict_do_nothing(
                    index_elements=["mode", "pm_fixture_id", "side"],
                    index_where=Position.closed_at.is_(None),
                )
                .returning(Position.id)
            )
            row = db.execute(stmt).first()
            if not row:
                db.commit()
                if trigger_record:
                    trigger_record.entry_done = True
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
                return None
            position_id = str(row[0])
            db.commit()

        response = self._executor.place_fak_order(
            token_id=token_id,
            side="BUY",
            price=limit_price,
            amount=buy_usdc,
            tick_size=snapshot.tick_size,
        )
        if not response.success or response.error_msg:
            self._trade_buffer.add(
                _format_trade_line(
                    event="ENTRY_ERROR",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=side,
                    details=response.error_msg or "submit_failed",
                )
            )
            with SessionLocal() as db:
                position = db.get(Position, position_id)
                if position:
                    position.status = "cancelled"
                    position.closed_at = now
                    position.exit_reason = "entry_submit_failed"
                    db.add(position)
                event = self._build_trade_event(
                    event_type="ENTRY_ERROR",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=side,
                    reason=response.error_msg,
                    details="entry_submit_failed",
                    external_order_id=response.order_id,
                    external_status=response.status,
                    raw_json=response.raw,
                )
                db.add(event)
                db.commit()
            return None
        self._last_live_order_ts = now.timestamp()

        trade = PaperTrade(
            key=_trade_key(mapping, pm_fixture, side),
            mapping_id=str(mapping.id),
            market_id=str(pm_fixture.id),
            token_id=token_id,
            market_type=snapshot.market_type,
            game_number=snapshot.game_number,
            side=side,
            trigger_type=trigger_type,
            trigger_ts=trigger_ts,
            entry_ts=now,
            entry_price=entry_price,
            limit_price=limit_price,
            size_available=size_available,
            quantity=quantity,
            alpha=float(alpha or 0.0),
            net_edge=entry.get("net_edge"),
            p_ref_entry=p_ref,
            status="submitted",
            external_order_id=response.order_id,
            external_status=response.status,
        )

        entry_raw = {
            "mapping_id": str(mapping.id),
            "market_id": str(pm_fixture.id),
            "token_id": token_id,
            "market_type": snapshot.market_type,
            "game_number": snapshot.game_number,
            "side": side_label,
            "trigger_type": trigger_type,
            "trigger_ts": trigger_ts.isoformat() if trigger_ts else None,
            "entry_ts": trade.entry_ts.isoformat(),
            "entry_price": trade.entry_price,
            "limit_price": trade.limit_price,
            "size_available": trade.size_available,
            "quantity": trade.quantity,
            "buy_usdc": buy_usdc,
            "net_edge": trade.net_edge,
            "p_ref_entry": trade.p_ref_entry,
            "external_order_id": response.order_id,
            "external_status": response.status,
        }
        with SessionLocal() as db:
            position = db.get(Position, position_id)
            if not position:
                return None
            position.entry_price = trade.entry_price
            position.entry_p_ref = trade.p_ref_entry
            position.entry_alpha = trade.alpha
            position.entry_edge = trade.net_edge
            position.quantity = trade.quantity
            position.trigger_type = trade.trigger_type
            position.status = "submitted"
            position.external_order_id = response.order_id
            position.external_status = response.status
            position.raw_json = entry_raw
            db.add(position)
            attempt = OrderAttempt(
                position_id=position.id,
                run_id=self._run_id,
                mode=self._mode_label,
                phase="entry",
                side="BUY",
                token_id=token_id,
                attempt_seq=self._next_attempt_seq(db, position.id, "entry"),
                submitted_at=now,
                limit_price=trade.limit_price,
                requested_size=trade.quantity,
                external_order_id=response.order_id,
                external_status=response.status,
            raw_json={"response": response.raw, "buy_usdc": buy_usdc},
            )
            db.add(attempt)
            event = self._build_trade_event(
                event_type="ENTRY_SUBMIT",
                now=now,
                mapping=mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=side,
                reason="entry",
                details=f"status={response.status or 'unknown'}",
                position_id=position.id,
                quantity=trade.quantity,
                limit_price=trade.limit_price,
                avg_fill_price=trade.entry_price,
                size_available=size_available,
                net_edge=trade.net_edge,
                external_order_id=response.order_id,
                external_status=response.status,
                raw_json=response.raw,
            )
            db.add(event)
            db.commit()
            db.refresh(position)
            trade.db_position_id = str(position.id)

        self._trade_buffer.add(
            _format_trade_line(
                event="ENTRY_SUBMIT",
                match=snapshot.match,
                market_type=snapshot.market_type,
                game_number=snapshot.game_number,
                side=side,
                details=f"@ {trade.entry_price:.3f} (edge={_format_pct(trade.net_edge)})",
            )
        )
        return trade

    async def _retry_delayed_live_entry(
        self,
        *,
        mapping: Mapping,
        pm_fixture: Fixture,
        snapshot: FocusSnapshot,
        side: str,
        token_id: str | None,
        entry: dict,
        size_available: float,
        quantity: float,
        now: datetime,
        trade: PaperTrade,
        alpha: float,
        p_ref: float | None,
    ) -> bool:
        if not self._executor or not trade.db_position_id or not token_id:
            return False
        limit_price = float(entry.get("limit_price") or entry.get("avg_fill") or 0.0)
        if limit_price <= 0:
            return False
        if quantity > settings.live_max_shares_per_order:
            quantity = settings.live_max_shares_per_order
        max_usd = settings.live_max_usd_per_order
        if max_usd > 0 and (limit_price * quantity) > max_usd:
            quantity = max_usd / limit_price if limit_price else 0.0
        buy_usdc = _quantize_usdc(limit_price * quantity)
        if buy_usdc <= 0:
            return False
        if settings.live_require_allowance_check:
            allowance = self._executor.check_allowance_buy(buy_usdc)
            if not allowance.ok:
                return False
        with SessionLocal() as db:
            position = db.get(Position, trade.db_position_id)
            if not position or position.closed_at is not None:
                return False
            pending_attempt = db.execute(
                select(OrderAttempt)
                .where(OrderAttempt.position_id == position.id)
                .where(OrderAttempt.phase == "entry")
                .where(OrderAttempt.finalized_at.is_(None))
                .order_by(OrderAttempt.attempt_seq.desc())
            ).scalars().first()
            if not pending_attempt or not self._attempt_is_delayed(pending_attempt):
                return False
            cancel_response = self._cancel_existing_order(pending_attempt.external_order_id)
            pending_attempt.finalized_at = now
            pending_attempt.final_state = "cancelled"
            pending_attempt.final_reason = "superseded_delayed"
            pending_attempt.raw_json = self._merge_dict(
                pending_attempt.raw_json if isinstance(pending_attempt.raw_json, dict) else {},
                {
                    "superseded_delayed": True,
                    "cancel_response": cancel_response.raw,
                },
            )
            response = self._executor.place_fak_order(
                token_id=token_id,
                side="BUY",
                price=limit_price,
                amount=buy_usdc,
                tick_size=snapshot.tick_size,
            )
            if not response.success or response.error_msg:
                position.status = "cancelled"
                position.closed_at = now
                position.exit_reason = "entry_retry_submit_failed"
                db.add(position)
                db.add(pending_attempt)
                event = self._build_trade_event(
                    event_type="ENTRY_ERROR",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=side,
                    reason=response.error_msg or "retry_submit_failed",
                    details="entry_retry_submit_failed",
                    position_id=str(position.id),
                    external_order_id=response.order_id,
                    external_status=response.status,
                    raw_json={
                        "retry": True,
                        "cancel_response": cancel_response.raw,
                        "submit_response": response.raw,
                    },
                )
                db.add(event)
                db.commit()
                with self._open_trades_lock:
                    self._open_trades.pop(trade.key, None)
                return False
            side_label = "A" if side == "buy_a" else "B"
            entry_raw = {
                "mapping_id": str(mapping.id),
                "market_id": str(pm_fixture.id),
                "token_id": token_id,
                "market_type": snapshot.market_type,
                "game_number": snapshot.game_number,
                "side": side_label,
                "trigger_type": trade.trigger_type,
                "trigger_ts": trade.trigger_ts.isoformat() if trade.trigger_ts else None,
                "entry_ts": trade.entry_ts.isoformat(),
                "entry_price": float(entry.get("avg_fill") or limit_price),
                "limit_price": limit_price,
                "size_available": size_available,
                "quantity": quantity,
                "buy_usdc": buy_usdc,
                "net_edge": entry.get("net_edge"),
                "p_ref_entry": p_ref,
                "external_order_id": response.order_id,
                "external_status": response.status,
            }
            position.entry_price = float(entry.get("avg_fill") or limit_price)
            position.entry_p_ref = p_ref
            position.entry_alpha = float(alpha or 0.0)
            position.entry_edge = entry.get("net_edge")
            position.quantity = quantity
            position.status = "submitted"
            position.external_order_id = response.order_id
            position.external_status = response.status
            position.raw_json = entry_raw
            db.add(position)
            new_attempt = OrderAttempt(
                position_id=position.id,
                run_id=self._run_id,
                mode=self._mode_label,
                phase="entry",
                side="BUY",
                token_id=token_id,
                attempt_seq=self._next_attempt_seq(db, position.id, "entry"),
                submitted_at=now,
                limit_price=limit_price,
                requested_size=quantity,
                external_order_id=response.order_id,
                external_status=response.status,
                raw_json={
                    "response": response.raw,
                    "buy_usdc": buy_usdc,
                    "retry": True,
                    "superseded_attempt_id": str(pending_attempt.id),
                    "cancel_response": cancel_response.raw,
                },
            )
            db.add(new_attempt)
            event = self._build_trade_event(
                event_type="ENTRY_RETRY",
                now=now,
                mapping=mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=side,
                reason="retry_delayed",
                details=f"status={response.status or 'unknown'}",
                position_id=str(position.id),
                quantity=quantity,
                limit_price=limit_price,
                avg_fill_price=float(entry.get("avg_fill") or limit_price),
                size_available=size_available,
                net_edge=entry.get("net_edge"),
                external_order_id=response.order_id,
                external_status=response.status,
                raw_json={
                    "cancel_response": cancel_response.raw,
                    "submit_response": response.raw,
                    "retry_count": trade.delayed_retries + 1,
                },
            )
            db.add(event)
            db.commit()
            trade.entry_price = float(entry.get("avg_fill") or limit_price)
            trade.limit_price = limit_price
            trade.size_available = size_available
            trade.quantity = quantity
            trade.alpha = float(alpha or 0.0)
            trade.net_edge = entry.get("net_edge")
            trade.p_ref_entry = p_ref
            trade.status = "submitted"
            trade.external_order_id = response.order_id
            trade.external_status = response.status
            trade.delayed_retries += 1
            trade.last_retry_ts = now
            self._last_live_order_ts = now.timestamp()
            self._trade_buffer.add(
                _format_trade_line(
                    event="ENTRY_RETRY",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=side,
                    details=f"retry={trade.delayed_retries} @ {trade.entry_price:.3f}",
                )
            )
            return True

    def _phantom_retry_entry(
        self,
        *,
        db: SessionLocal,
        attempt: OrderAttempt,
        position: Position,
        trade: PaperTrade,
        now: datetime,
        pm_fixture: Fixture,
    ) -> bool:
        """Fast resubmit when an entry order is not found on the book.

        Mirrors the phantom-ID retry logic used for exits: cancel the ghost
        order, finalize the current attempt, and immediately place a new FAK
        order at the same limit price.
        """
        if not self._executor:
            return False
        if trade.delayed_retries >= max(int(settings.entry_phantom_max_retries), 0):
            return False
        age = (now - attempt.submitted_at).total_seconds()
        if age < float(settings.entry_phantom_retry_seconds):
            return False

        phantom_id = attempt.external_order_id
        token_id = attempt.token_id
        limit_price = float(attempt.limit_price or 0.0)
        buy_usdc = _quantize_usdc(limit_price * float(attempt.requested_size or 0.0))
        if limit_price <= 0 or buy_usdc <= 0 or not token_id:
            return False

        # --- cancel the phantom order (best-effort) ---
        cancel_response = self._cancel_existing_order(phantom_id)

        # --- finalize current attempt ---
        attempt.finalized_at = now
        attempt.final_state = "failed"
        attempt.final_reason = "phantom_entry_retry"
        attempt.raw_json = self._merge_dict(
            attempt.raw_json if isinstance(attempt.raw_json, dict) else {},
            {
                "phantom_order_id": phantom_id,
                "phantom_not_found_count": attempt.not_found_count,
                "cancel_response": cancel_response.raw,
            },
        )
        db.add(attempt)

        # --- place new FAK order at same price ---
        response = self._executor.place_fak_order(
            token_id=token_id,
            side="BUY",
            price=limit_price,
            amount=buy_usdc,
        )
        if not response.success or response.error_msg:
            position.status = "cancelled"
            position.closed_at = now
            position.exit_reason = "phantom_retry_submit_failed"
            db.add(position)
            snapshot = self._build_stub_snapshot(trade, now)
            event = self._build_trade_event(
                event_type="ENTRY_ERROR",
                now=now,
                mapping=self._mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=trade.side,
                reason=response.error_msg or "phantom_retry_submit_failed",
                details="phantom_retry_submit_failed",
                position_id=str(position.id),
                external_order_id=response.order_id,
                external_status=response.status,
                raw_json={
                    "phantom_order_id": phantom_id,
                    "submit_response": response.raw,
                    "cancel_response": cancel_response.raw,
                },
            )
            db.add(event)
            db.commit()
            with self._open_trades_lock:
                self._open_trades.pop(trade.key, None)
            return True

        # --- record new attempt ---
        new_attempt = OrderAttempt(
            position_id=position.id,
            run_id=self._run_id,
            mode=self._mode_label,
            phase="entry",
            side="BUY",
            token_id=token_id,
            attempt_seq=self._next_attempt_seq(db, position.id, "entry"),
            submitted_at=now,
            limit_price=limit_price,
            requested_size=attempt.requested_size,
            external_order_id=response.order_id,
            external_status=response.status,
            raw_json={
                "response": response.raw,
                "buy_usdc": buy_usdc,
                "phantom_retry": True,
                "phantom_order_id": phantom_id,
            },
        )
        db.add(new_attempt)

        position.external_order_id = response.order_id
        position.external_status = response.status
        db.add(position)

        trade.external_order_id = response.order_id
        trade.external_status = response.status
        trade.delayed_retries += 1
        trade.last_retry_ts = now

        snapshot = self._build_stub_snapshot(trade, now)
        event = self._build_trade_event(
            event_type="ENTRY_RETRY",
            now=now,
            mapping=self._mapping,
            pm_fixture=pm_fixture,
            snapshot=snapshot,
            side=trade.side,
            reason="phantom_entry_retry",
            details=f"retry={trade.delayed_retries} @ {limit_price:.3f}",
            position_id=str(position.id),
            external_order_id=response.order_id,
            external_status=response.status,
            raw_json={
                "phantom_order_id": phantom_id,
                "cancel_response": cancel_response.raw,
            },
        )
        db.add(event)
        self._trade_buffer.add(
            _format_trade_line(
                event="ENTRY_RETRY",
                match=snapshot.match,
                market_type=snapshot.market_type,
                game_number=snapshot.game_number,
                side=trade.side,
                details=f"phantom_retry={trade.delayed_retries} @ {limit_price:.3f}",
            )
        )
        db.commit()
        return True

    def _retry_delayed_live_exit(
        self,
        *,
        db: SessionLocal,
        attempt: OrderAttempt,
        position: Position,
        trade: PaperTrade,
        now: datetime,
        snapshot: FocusSnapshot,
        pm_fixture: Fixture,
    ) -> bool:
        if not self._delayed_retry_ready(trade, now, for_entry=False):
            return False
        if not self._attempt_is_delayed(attempt):
            return False
        cancel_response = self._cancel_existing_order(attempt.external_order_id)
        attempt.finalized_at = now
        attempt.final_state = "cancelled"
        attempt.final_reason = "superseded_delayed"
        attempt.raw_json = self._merge_dict(
            attempt.raw_json if isinstance(attempt.raw_json, dict) else {},
            {
                "superseded_delayed": True,
                "cancel_response": cancel_response.raw,
            },
        )
        position.external_order_id = None
        position.external_status = "retry_pending"
        db.add(position)
        trade.status = "open"
        trade.external_order_id = None
        trade.external_status = None
        trade.delayed_retries += 1
        trade.last_retry_ts = now
        event = self._build_trade_event(
            event_type="EXIT_RETRY",
            now=now,
            mapping=self._mapping,
            pm_fixture=pm_fixture,
            snapshot=snapshot,
            side=trade.side,
            reason="retry_delayed",
            details=f"retry={trade.delayed_retries}",
            position_id=str(position.id),
            external_order_id=attempt.external_order_id,
            external_status=attempt.external_status,
            raw_json={"cancel_response": cancel_response.raw},
        )
        db.add(event)
        self._trade_buffer.add(
            _format_trade_line(
                event="EXIT_RETRY",
                match=snapshot.match,
                market_type=snapshot.market_type,
                game_number=snapshot.game_number,
                side=trade.side,
                details=f"retry={trade.delayed_retries}",
            )
        )
        db.add(attempt)
        db.commit()
        return True

    def _cancel_and_reprice_exit(
        self,
        *,
        db: SessionLocal,
        attempt: OrderAttempt,
        position: Position,
        trade: PaperTrade,
        now: datetime,
        snapshot: FocusSnapshot,
        pm_fixture: Fixture,
        requested: float,
        eps: float,
        filled_qty: float | None,
    ) -> bool:
        cancel_response = self._cancel_existing_order(attempt.external_order_id)
        balance = None
        bal_raw: dict | None = None
        if self._executor and attempt.token_id:
            try:
                balance, bal_raw = self._executor.get_conditional_balance(attempt.token_id)
            except Exception:
                balance, bal_raw = None, None
        if balance is not None and balance <= eps:
            attempt.finalized_at = now
            attempt.final_state = "confirmed"
            attempt.final_reason = "balance_zero"
            if requested > 0:
                attempt.matched_size = requested
            attempt.raw_json = self._merge_dict(
                attempt.raw_json if isinstance(attempt.raw_json, dict) else {},
                {
                    "cancel_response": cancel_response.raw,
                    "balance": balance,
                    "balance_raw": bal_raw,
                },
            )
            position.closed_at = now
            position.exit_reason = attempt.raw_json.get("exit_reason", "exit")
            position.exit_price = attempt.limit_price
            position.hold_seconds = (now - position.opened_at).total_seconds()
            db.add(position)
            event = self._build_trade_event(
                event_type="EXIT_CONFIRMED",
                now=now,
                mapping=self._mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=trade.side,
                reason=position.exit_reason,
                details="cancel_reprice_balance_zero",
                position_id=str(position.id),
                exit_price=position.exit_price,
                external_order_id=attempt.external_order_id,
                external_status=attempt.external_status,
                raw_json={"cancel_response": cancel_response.raw, "balance": balance, "balance_raw": bal_raw},
            )
            db.add(event)
            trade.status = "closed"
            with self._open_trades_lock:
                self._open_trades.pop(trade.key, None)
            self._trade_buffer.add(
                _format_trade_line(
                    event="EXIT_CONFIRMED",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=trade.side,
                    details=f"@ {_format_price(position.exit_price)} reason={position.exit_reason}",
                )
            )
            db.add(attempt)
            db.commit()
            return True

        remaining = balance if balance is not None else requested
        if filled_qty is not None and requested > 0:
            remaining = min(remaining, max(requested - filled_qty, 0.0))
        attempt.finalized_at = now
        attempt.final_state = "failed"
        attempt.final_reason = "cancel_reprice"
        if requested > 0 and remaining >= 0:
            maybe_filled = max(requested - remaining, 0.0)
            if maybe_filled > eps:
                attempt.matched_size = maybe_filled
        attempt.raw_json = self._merge_dict(
            attempt.raw_json if isinstance(attempt.raw_json, dict) else {},
            {
                "cancel_response": cancel_response.raw,
                "balance": balance,
                "balance_raw": bal_raw,
                "filled_qty": filled_qty,
            },
        )
        trade.quantity = max(remaining, 0.0)
        position.quantity = max(remaining, 0.0)
        trade.status = "open"
        db.add(position)
        retry_event = self._build_trade_event(
            event_type="EXIT_RETRY",
            now=now,
            mapping=self._mapping,
            pm_fixture=pm_fixture,
            snapshot=snapshot,
            side=trade.side,
            reason="cancel_reprice",
            details=f"remaining={max(remaining, 0.0):.2f}",
            position_id=str(position.id),
            external_order_id=attempt.external_order_id,
            external_status=attempt.external_status,
            raw_json={"cancel_response": cancel_response.raw, "balance": balance, "balance_raw": bal_raw},
        )
        db.add(retry_event)
        db.add(attempt)
        db.commit()
        return True

    def _submit_live_exit(
        self,
        *,
        mapping: Mapping,
        pm_fixture: Fixture,
        snapshot: FocusSnapshot,
        trade: PaperTrade,
        bid: float | None,
        now: datetime,
        exit_reason: str,
    ) -> bool | None:
        if not self._executor:
            return False
        if not trade.token_id:
            return False
        if bid is None or bid <= 0:
            return False
        sell_shares = _quantize_market_shares(trade.quantity)
        degraded_chunk_mode = False
        degraded_attempt_count: int | None = None
        exit_attempt_count = 0
        if sell_shares <= 0:
            self._trade_buffer.add(
                _format_trade_line(
                    event="BLOCKED",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=trade.side,
                    details="exit_size_zero",
                )
            )
            with SessionLocal() as db:
                event = self._build_trade_event(
                    event_type="BLOCKED",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=trade.side,
                    reason="exit_size_zero",
                    details="exit_size_zero",
                )
                db.add(event)
                db.commit()
            return None  # exhausted: remove from open trades
        if trade.db_position_id:
            with SessionLocal() as db:
                attempt_count = db.scalar(
                    select(func.count())
                    .where(OrderAttempt.position_id == trade.db_position_id)
                    .where(OrderAttempt.phase == "exit")
                )
                exit_attempt_count = int(attempt_count or 0)
                if attempt_count is not None and attempt_count >= settings.exit_max_attempts:
                    degraded_chunk_mode = True
                    degraded_attempt_count = int(attempt_count)
                    chunk_shares = _compute_degraded_exit_shares(
                        trade.quantity,
                        settings.exit_degraded_chunk_fraction,
                    )
                    if chunk_shares <= 0:
                        self._trade_buffer.add(
                            _format_trade_line(
                                event="BLOCKED",
                                match=snapshot.match,
                                market_type=snapshot.market_type,
                                game_number=snapshot.game_number,
                                side=trade.side,
                                details="exit_degraded_chunk_zero",
                            )
                        )
                        event = self._build_trade_event(
                            event_type="BLOCKED",
                            now=now,
                            mapping=mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=trade.side,
                            reason="exit_degraded_chunk_zero",
                            details="exit_degraded_chunk_zero",
                            position_id=trade.db_position_id,
                            raw_json={"attempt_count": attempt_count},
                        )
                        db.add(event)
                        db.commit()
                        return None  # exhausted: remove from open trades
                    sell_shares = min(sell_shares, chunk_shares)
                last_submitted = db.scalar(
                    select(func.max(OrderAttempt.submitted_at))
                    .where(OrderAttempt.position_id == trade.db_position_id)
                    .where(OrderAttempt.phase == "exit")
                )
                if last_submitted is not None:
                    delta_seconds = (now - last_submitted).total_seconds()
                    if delta_seconds < settings.exit_retry_cooldown_seconds:
                        return False
        limit_price = _compute_exit_limit_price(
            bid=bid,
            tick_size=snapshot.tick_size,
            attempt_count=exit_attempt_count,
            exit_reason=exit_reason,
        )
        if settings.live_require_allowance_check:
            allowance = self._executor.check_allowance_sell(trade.token_id, sell_shares)
            if not allowance.ok:
                self._trade_buffer.add(
                    _format_trade_line(
                        event="BLOCKED",
                        match=snapshot.match,
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side=trade.side,
                        details=f"allowance={allowance.reason}",
                    )
                )
                with SessionLocal() as db:
                    event = self._build_trade_event(
                        event_type="BLOCKED",
                        now=now,
                        mapping=mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=trade.side,
                        reason=allowance.reason,
                        details="allowance_check_failed",
                        raw_json={"allowance": allowance.raw},
                    )
                    db.add(event)
                    db.commit()
                return False

        response = self._executor.place_gtc_order(
            token_id=trade.token_id,
            side="SELL",
            price=limit_price,
            amount=sell_shares,
            tick_size=snapshot.tick_size,
        )
        if not response.success or response.error_msg:
            self._trade_buffer.add(
                _format_trade_line(
                    event="EXIT_ERROR",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=trade.side,
                    details=response.error_msg or "submit_failed",
                )
            )
            with SessionLocal() as db:
                event = self._build_trade_event(
                    event_type="EXIT_ERROR",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=trade.side,
                    reason=response.error_msg,
                    details="exit_submit_failed",
                    external_order_id=response.order_id,
                    external_status=response.status,
                    raw_json=response.raw,
                )
                db.add(event)
                db.commit()
            return False

        trade.external_order_id = response.order_id
        trade.external_status = response.status
        trade.limit_price = limit_price
        trade.status = "exit_submitted"
        if degraded_chunk_mode:
            self._trade_buffer.add(
                _format_trade_line(
                    event="EXIT_RETRY",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=trade.side,
                    details=f"degraded_chunk={sell_shares:.2f}",
                )
            )
        with SessionLocal() as db:
            if trade.db_position_id:
                attempt = OrderAttempt(
                    position_id=trade.db_position_id,
                    run_id=self._run_id,
                    mode=self._mode_label,
                    phase="exit",
                    side="SELL",
                    token_id=trade.token_id or "",
                    attempt_seq=self._next_attempt_seq(db, trade.db_position_id, "exit"),
                    submitted_at=now,
                    limit_price=limit_price,
                    requested_size=sell_shares,
                    external_order_id=response.order_id,
                    external_status=response.status,
                    raw_json={
                        "exit_reason": exit_reason,
                        "limit_price": limit_price,
                        "bid_at_submit": bid,
                        "sell_shares": sell_shares,
                        "order_type": "GTC",
                        "attempt_count": exit_attempt_count,
                        "degraded_chunk_mode": degraded_chunk_mode,
                        "degraded_attempt_count": degraded_attempt_count,
                        "degraded_chunk_fraction": settings.exit_degraded_chunk_fraction,
                        "response": response.raw,
                    },
                )
                db.add(attempt)
            event = self._build_trade_event(
                event_type="EXIT_SUBMIT",
                now=now,
                mapping=mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=trade.side,
                reason="exit",
                details=(
                    f"@ {limit_price:.3f}"
                    if not degraded_chunk_mode
                    else f"@ {limit_price:.3f} chunk={sell_shares:.2f}"
                ),
                position_id=trade.db_position_id,
                quantity=sell_shares,
                limit_price=limit_price,
                external_order_id=response.order_id,
                external_status=response.status,
                raw_json={
                    **(response.raw if isinstance(response.raw, dict) else {"raw": response.raw}),
                    "order_type": "GTC",
                    "attempt_count": exit_attempt_count,
                    "bid_at_submit": bid,
                    "limit_price": limit_price,
                    "degraded_chunk_mode": degraded_chunk_mode,
                    "degraded_attempt_count": degraded_attempt_count,
                    "degraded_chunk_fraction": settings.exit_degraded_chunk_fraction,
                    "degraded_sell_shares": sell_shares,
                },
            )
            db.add(event)
            db.commit()
        if not degraded_chunk_mode:
            self._trade_buffer.add(
                _format_trade_line(
                    event="EXIT_SUBMIT",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=trade.side,
                    details=f"@ {limit_price:.3f} reason={exit_reason}",
                )
            )
        return True

    async def _reconcile_entry_attempt(
        self,
        db: SessionLocal,
        attempt: OrderAttempt,
        position: Position,
        trade: PaperTrade,
        now: datetime,
        pm_fixture: Fixture,
    ) -> None:
        snapshot = self._build_stub_snapshot(trade, now)
        attempt.last_checked_at = now
        if attempt.external_order_id:
            response = self._executor.get_order(attempt.external_order_id)
            if not isinstance(response, dict) or not response.get("status"):
                attempt.not_found_count += 1
                # Fast phantom retry: resubmit before the full timeout
                if self._phantom_retry_entry(
                    db=db,
                    attempt=attempt,
                    position=position,
                    trade=trade,
                    now=now,
                    pm_fixture=pm_fixture,
                ):
                    return
                if (now - attempt.submitted_at).total_seconds() > settings.user_ws_untracked_timeout_seconds:
                    attempt.finalized_at = now
                    attempt.final_state = "failed"
                    attempt.final_reason = "order_not_found_timeout"
                    position.status = "cancelled"
                    position.closed_at = now
                    position.exit_reason = "entry_timeout"
                    db.add(position)
                    event = self._build_trade_event(
                        event_type="ENTRY_CANCELLED",
                        now=now,
                        mapping=self._mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=trade.side,
                        reason="entry_timeout",
                        details="order_not_found_timeout",
                        position_id=str(position.id),
                        external_order_id=attempt.external_order_id,
                        external_status=attempt.external_status,
                        raw_json={"timeout_seconds": settings.user_ws_untracked_timeout_seconds},
                    )
                    db.add(event)
                    db.commit()
                    with self._open_trades_lock:
                        self._open_trades.pop(trade.key, None)
                return
            attempt.raw_json = response
            status = response.get("status", "")
            attempt.external_status = status
            filled_qty = _parse_filled_quantity(response)
            if filled_qty is None and status in {"canceled", "cancelled", "rejected"}:
                filled_qty = 0.0
            if filled_qty is None:
                db.add(attempt)
                db.commit()
                return
            if filled_qty > 0:
                attempt.finalized_at = now
                attempt.final_state = "confirmed"
                attempt.final_reason = "filled"
                attempt.matched_size = filled_qty
                position.status = "confirmed"
                position.quantity = filled_qty
                position.external_order_id = attempt.external_order_id
                position.external_status = status
                trade.status = "confirmed"
                trade.quantity = filled_qty
                db.add(position)
                event = self._build_trade_event(
                    event_type="ENTRY_CONFIRMED",
                    now=now,
                    mapping=self._mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=trade.side,
                    reason="entry_confirmed",
                    details=f"filled={filled_qty:.2f}",
                    position_id=str(position.id),
                    quantity=filled_qty,
                    limit_price=trade.limit_price,
                    avg_fill_price=trade.entry_price,
                    size_available=trade.size_available,
                    net_edge=trade.net_edge,
                    external_order_id=attempt.external_order_id,
                    external_status=attempt.external_status,
                    raw_json=response,
                )
                db.add(event)
                self._trade_buffer.add(
                    _format_trade_line(
                        event="ENTRY_CONFIRMED",
                        match=snapshot.match,
                        market_type=snapshot.market_type,
                        game_number=snapshot.game_number,
                        side=trade.side,
                        details=f"filled={filled_qty:.2f} @ {trade.entry_price:.3f}",
                    )
                )
            else:
                attempt.finalized_at = now
                attempt.final_state = "cancelled"
                attempt.final_reason = "zero_fill"
                position.status = "cancelled"
                position.closed_at = now
                position.exit_reason = "entry_cancelled"
                db.add(position)
                event = self._build_trade_event(
                    event_type="ENTRY_CANCELLED",
                    now=now,
                    mapping=self._mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=trade.side,
                    reason="entry_cancelled",
                    details=f"status={status}",
                    position_id=str(position.id),
                    external_order_id=attempt.external_order_id,
                    external_status=attempt.external_status,
                    raw_json=response,
                )
                db.add(event)
                with self._open_trades_lock:
                    self._open_trades.pop(trade.key, None)
            db.add(attempt)
            db.commit()
            return
        if self._user_ws and settings.user_ws_enabled and attempt.token_id:
            ws_order = await self._user_ws.get_latest_order_by_asset(attempt.token_id, attempt.side)
            if ws_order and ws_order.status in {"PLACEMENT", "UPDATE"}:
                attempt.external_order_id = ws_order.order_id
                attempt.external_status = ws_order.status.lower()
                attempt.raw_json = ws_order.raw
                position.external_order_id = attempt.external_order_id
                position.external_status = attempt.external_status
                db.add(position)
                db.add(attempt)
                db.commit()
                return
        if (now - attempt.submitted_at).total_seconds() > settings.user_ws_untracked_timeout_seconds:
            attempt.finalized_at = now
            attempt.final_state = "failed"
            attempt.final_reason = "entry_timeout"
            position.status = "cancelled"
            position.closed_at = now
            position.exit_reason = "entry_timeout"
            db.add(position)
            event = self._build_trade_event(
                event_type="ENTRY_CANCELLED",
                now=now,
                mapping=self._mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=trade.side,
                reason="entry_timeout",
                details="missing_order_id",
                position_id=str(position.id),
                raw_json={"timeout_seconds": settings.user_ws_untracked_timeout_seconds},
            )
            db.add(event)
            db.add(attempt)
            db.commit()
            with self._open_trades_lock:
                self._open_trades.pop(trade.key, None)

    async def _reconcile_exit_attempt(
        self,
        db: SessionLocal,
        attempt: OrderAttempt,
        position: Position,
        trade: PaperTrade,
        now: datetime,
        pm_fixture: Fixture,
    ) -> None:
        snapshot = self._snapshot_for_trade(
            pm_fixture=pm_fixture,
            trade=trade,
            now=now,
        )
        attempt.last_checked_at = now
        eps = max(float(getattr(settings, "live_share_step", 0.0001) or 0.0001), 0.000001)
        requested = float(attempt.requested_size or trade.quantity or 0.0)
        is_gtc = _is_gtc_exit_attempt(attempt)
        timeout_seconds = (
            float(settings.exit_gtc_reprice_seconds)
            if is_gtc
            else float(settings.exit_order_not_found_seconds)
        )
        if not attempt.external_order_id:
            if self._user_ws and settings.user_ws_enabled and attempt.token_id:
                ws_order = await self._user_ws.get_latest_order_by_asset(attempt.token_id, attempt.side)
                if ws_order and ws_order.status in {"PLACEMENT", "UPDATE"}:
                    attempt.external_order_id = ws_order.order_id
                    attempt.external_status = ws_order.status.lower()
                    attempt.raw_json = ws_order.raw
                    db.add(attempt)
                    db.commit()
                    return
            handled = self._handle_exit_timeout_with_balance_fallback(
                db=db,
                attempt=attempt,
                position=position,
                trade=trade,
                now=now,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                timeout_seconds=timeout_seconds,
                requested=requested,
                eps=eps,
                allow_partial_fill=False,
            )
            if handled:
                return
            return
        response = self._executor.get_order(attempt.external_order_id)
        if not isinstance(response, dict) or not response.get("status"):
            attempt.not_found_count += 1
            # If REST status is unavailable, try to infer fill progress via WS trades.
            ws_filled_qty: float | None = None
            if self._user_ws and settings.user_ws_enabled and attempt.external_order_id:
                trades = await self._user_ws.get_trades_for_order(attempt.external_order_id)
                total = 0.0
                for tr in trades:
                    try:
                        total += float(tr.size)
                    except (TypeError, ValueError):
                        continue
                if total > 0:
                    ws_filled_qty = total
            if ws_filled_qty is not None and requested > 0:
                filled_qty = ws_filled_qty
                remaining = max(requested - filled_qty, 0.0)
                attempt.finalized_at = now
                attempt.final_state = "confirmed"
                attempt.final_reason = "filled_ws" if remaining <= eps else "partial_fill_ws"
                attempt.matched_size = filled_qty
                if remaining <= eps:
                    position.closed_at = now
                    position.exit_reason = attempt.raw_json.get("exit_reason", "exit")
                    position.exit_price = attempt.limit_price
                    position.hold_seconds = (now - position.opened_at).total_seconds()
                    db.add(position)
                    event = self._build_trade_event(
                        event_type="EXIT_CONFIRMED",
                        now=now,
                        mapping=self._mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=trade.side,
                        reason=position.exit_reason,
                        details="ws_reconcile",
                        position_id=str(position.id),
                        exit_price=position.exit_price,
                        external_order_id=attempt.external_order_id,
                        external_status=attempt.external_status,
                        raw_json={"filled_qty": filled_qty},
                    )
                    db.add(event)
                    trade.status = "closed"
                    with self._open_trades_lock:
                        self._open_trades.pop(trade.key, None)
                    self._trade_buffer.add(
                        _format_trade_line(
                            event="EXIT_CONFIRMED",
                            match=snapshot.match,
                            market_type=snapshot.market_type,
                            game_number=snapshot.game_number,
                            side=trade.side,
                            details=f"@ {_format_price(position.exit_price)} reason={position.exit_reason}",
                        )
                    )
                else:
                    trade.quantity = remaining
                    position.quantity = remaining
                    trade.status = "open"
                    db.add(position)
                    self._trade_buffer.add(
                        f"PARTIAL_EXIT {trade.key}: filled={filled_qty:.2f} remaining={max(remaining, 0):.2f}",
                    )
                db.add(attempt)
                db.commit()
                return
            handled = self._handle_exit_timeout_with_balance_fallback(
                db=db,
                attempt=attempt,
                position=position,
                trade=trade,
                now=now,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                timeout_seconds=timeout_seconds,
                requested=requested,
                eps=eps,
                allow_partial_fill=True,
            )
            if handled:
                return
            if (
                attempt.external_order_id
                and attempt.not_found_count >= settings.exit_phantom_id_null_threshold
            ):
                phantom_id = attempt.external_order_id
                attempt.external_order_id = None
                attempt.external_status = None
                if isinstance(attempt.raw_json, dict):
                    attempt.raw_json = {
                        **attempt.raw_json,
                        "phantom_order_id": phantom_id,
                        "phantom_not_found_count": attempt.not_found_count,
                    }
                else:
                    attempt.raw_json = {
                        "phantom_order_id": phantom_id,
                        "phantom_not_found_count": attempt.not_found_count,
                    }
                event = self._build_trade_event(
                    event_type="EXIT_ERROR",
                    now=now,
                    mapping=self._mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=trade.side,
                    reason="phantom_order_id",
                    details=(
                        "cleared_phantom_id "
                        f"count={attempt.not_found_count} "
                        f"threshold={settings.exit_phantom_id_null_threshold}"
                    ),
                    position_id=str(position.id),
                    external_order_id=phantom_id,
                    raw_json={"phantom_order_id": phantom_id},
                )
                db.add(event)
                _finalize_phantom_exit_for_retry(attempt=attempt, trade=trade, now=now)
                retry_event = self._build_trade_event(
                    event_type="EXIT_RETRY",
                    now=now,
                    mapping=self._mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=trade.side,
                    reason="phantom_order_id",
                    details="phantom_reopen",
                    position_id=str(position.id),
                    external_order_id=phantom_id,
                    raw_json={"phantom_order_id": phantom_id},
                )
                db.add(retry_event)
            db.add(attempt)
            db.commit()
            return
        attempt.raw_json = response
        status = response.get("status", "")
        attempt.external_status = status
        filled_qty = _parse_filled_quantity(response)
        if (
            is_gtc
            and not self._is_delayed_status(status)
            and status not in {"filled", "canceled", "cancelled", "rejected", "partially_filled"}
            and (now - attempt.submitted_at).total_seconds() >= float(settings.exit_gtc_reprice_seconds)
        ):
            retried = self._cancel_and_reprice_exit(
                db=db,
                attempt=attempt,
                position=position,
                trade=trade,
                now=now,
                snapshot=snapshot,
                pm_fixture=pm_fixture,
                requested=requested,
                eps=eps,
                filled_qty=filled_qty,
            )
            if retried:
                return
        if self._is_delayed_status(status):
            age_seconds = (now - attempt.submitted_at).total_seconds()
            if age_seconds >= float(settings.delayed_grace_seconds):
                retried = self._retry_delayed_live_exit(
                    db=db,
                    attempt=attempt,
                    position=position,
                    trade=trade,
                    now=now,
                    snapshot=snapshot,
                    pm_fixture=pm_fixture,
                )
                if retried:
                    return
        if filled_qty is None and status in {"filled", "partially_filled"}:
            # Some responses omit size_matched; treat missing as unknown rather than crashing.
            filled_qty = 0.0

        effectively_filled = (
            filled_qty is not None
            and requested > 0
            and filled_qty >= max(requested - eps, 0.0)
            and filled_qty > 0
        )
        if status == "filled" or effectively_filled:
            attempt.finalized_at = now
            attempt.final_state = "confirmed"
            attempt.final_reason = "filled"
            attempt.matched_size = filled_qty if filled_qty is not None else requested
            exit_price = _parse_float(response.get("price"))
            if exit_price is None:
                exit_price = attempt.raw_json.get("limit_price")
            position.closed_at = now
            position.exit_price = exit_price
            position.exit_p_ref = None
            position.exit_reason = attempt.raw_json.get("exit_reason", "exit")
            if exit_price is not None:
                position.pnl_absolute = exit_price - position.entry_price
                position.pnl_percent = (
                    position.pnl_absolute / position.entry_price
                    if position.entry_price
                    else None
                )
                if position.entry_edge and position.pnl_percent is not None:
                    position.edge_capture = position.pnl_percent / position.entry_edge
            position.hold_seconds = (now - position.opened_at).total_seconds()
            db.add(position)
            event = self._build_trade_event(
                event_type="EXIT_CONFIRMED",
                now=now,
                mapping=self._mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=trade.side,
                reason=position.exit_reason,
                details=f"pnl={_format_pct(position.pnl_percent)}",
                position_id=str(position.id),
                exit_price=exit_price,
                pnl_percent=position.pnl_percent,
                external_order_id=attempt.external_order_id,
                external_status=attempt.external_status,
                raw_json=response,
            )
            db.add(event)
            self._trade_buffer.add(
                _format_trade_line(
                    event="EXIT_CONFIRMED",
                    match=snapshot.match,
                    market_type=snapshot.market_type,
                    game_number=snapshot.game_number,
                    side=trade.side,
                    details=(
                        f"@ {_format_price(exit_price)} "
                        f"| P&L: {_format_pct(position.pnl_percent)} "
                        f"| reason={position.exit_reason}"
                    ),
                )
            )
            trade.status = "closed"
            with self._open_trades_lock:
                self._open_trades.pop(trade.key, None)
        elif filled_qty is not None and filled_qty > eps:
            remaining = max(requested - filled_qty, 0.0)
            attempt.finalized_at = now
            attempt.final_state = "confirmed"
            attempt.final_reason = "partial_fill"
            attempt.matched_size = filled_qty
            if remaining <= eps:
                trade.status = "closed"
                position.closed_at = now
            else:
                trade.quantity = remaining
                position.quantity = remaining
                trade.status = "open"
            db.add(position)
            self._trade_buffer.add(
                f"PARTIAL_EXIT {trade.key}: filled={filled_qty:.2f} remaining={max(remaining, 0):.2f}",
            )
        elif status in {"canceled", "cancelled", "rejected"}:
            attempt.finalized_at = now
            attempt.final_state = "failed"
            attempt.final_reason = status
            trade.status = "open"
            event = self._build_trade_event(
                event_type="EXIT_ERROR",
                now=now,
                mapping=self._mapping,
                pm_fixture=pm_fixture,
                snapshot=snapshot,
                side=trade.side,
                reason=status,
                details="exit_order_failed",
                position_id=str(position.id),
                external_order_id=attempt.external_order_id,
                external_status=attempt.external_status,
                raw_json=response,
            )
            db.add(event)
        db.add(attempt)
        db.commit()

    async def _reconcile_open_orders_loop(self) -> None:
        while self._stop and not self._stop.is_set():
            if not self._executor:
                await asyncio.sleep(2.0)
                continue
            now = datetime.now(tz=timezone.utc)
            with SessionLocal() as db:
                cutoff = now - timedelta(seconds=settings.user_ws_untracked_timeout_seconds)
                stale_positions = (
                    db.execute(
                        select(Position)
                        .where(Position.mode == self._mode_label)
                        .where(Position.status == "reserved")
                        .where(Position.closed_at.is_(None))
                        .where(Position.opened_at < cutoff)
                    )
                    .scalars()
                    .all()
                )
                for position in stale_positions:
                    position.status = "cancelled"
                    position.closed_at = now
                    position.exit_reason = "reservation_timeout"
                    db.add(position)
                    side = "buy_a" if position.side == "A" else "buy_b"
                    pm_fixture = next(
                        (
                            fix
                            for fix in self._pm_fixtures
                            if position.pm_fixture_id and str(fix.id) == str(position.pm_fixture_id)
                        ),
                        self._pm_fixture,
                    )
                    if pm_fixture:
                        raw = position.raw_json or {}
                        trade = PaperTrade(
                            key=_trade_key(self._mapping, pm_fixture, side),
                            mapping_id=str(position.mapping_id),
                            market_id=str(position.pm_fixture_id),
                            token_id=raw.get("token_id"),
                            market_type=position.market_type,
                            game_number=position.game_number,
                            side=side,
                            trigger_type=position.trigger_type,
                            trigger_ts=_parse_dt(raw.get("trigger_ts")),
                            entry_ts=position.opened_at,
                            entry_price=position.entry_price,
                            limit_price=raw.get("limit_price"),
                            size_available=raw.get("size_available"),
                            quantity=position.quantity,
                            alpha=position.entry_alpha or 0.0,
                            net_edge=position.entry_edge,
                            p_ref_entry=position.entry_p_ref,
                            status=position.status,
                            external_order_id=position.external_order_id,
                            external_status=position.external_status,
                            db_position_id=str(position.id),
                        )
                        snapshot = self._build_stub_snapshot(trade, now)
                        event = self._build_trade_event(
                            event_type="ENTRY_CANCELLED",
                            now=now,
                            mapping=self._mapping,
                            pm_fixture=pm_fixture,
                            snapshot=snapshot,
                            side=side,
                            reason="reservation_timeout",
                            details="reservation_timeout",
                            position_id=str(position.id),
                            external_order_id=position.external_order_id,
                            external_status=position.external_status,
                            raw_json={"timeout_seconds": settings.user_ws_untracked_timeout_seconds},
                        )
                        db.add(event)
                stmt = (
                    select(OrderAttempt.id)
                    .join(Position, OrderAttempt.position_id == Position.id)
                    .where(OrderAttempt.finalized_at.is_(None))
                    .where(OrderAttempt.mode == self._mode_label)
                    .where(Position.mapping_id == self._mapping.id)
                )
                attempt_ids = [row[0] for row in db.execute(stmt).all()]
            for attempt_id in attempt_ids:
                with SessionLocal() as db:
                    attempt = db.get(OrderAttempt, attempt_id)
                    if not attempt:
                        continue
                    position = db.get(Position, attempt.position_id)
                    if not position:
                        continue
                    trade = self._ensure_trade_for_position(position)
                    if not trade:
                        continue
                    pm_fixture = next(
                        (
                            fix
                            for fix in self._pm_fixtures
                            if position.pm_fixture_id and str(fix.id) == str(position.pm_fixture_id)
                        ),
                        self._pm_fixture,
                    )
                    if attempt.phase == "entry":
                        await self._reconcile_entry_attempt(
                            db,
                            attempt,
                            position,
                            trade,
                            now,
                            pm_fixture,
                        )
                    else:
                        trade.status = "exit_submitted"
                        trade.external_order_id = attempt.external_order_id
                        trade.external_status = attempt.external_status
                        await self._reconcile_exit_attempt(
                            db,
                            attempt,
                            position,
                            trade,
                            now,
                            pm_fixture,
                        )
            await asyncio.sleep(2.0)

    def _check_for_exits(
        self,
        mapping: Mapping,
        pm_fixture: Fixture,
        snapshot: FocusSnapshot,
        now: datetime,
        ended: bool,
        trigger_record: TriggerRecord | None,
    ) -> None:
        with self._open_trades_lock:
            open_keys = [
                key
                for key, trade in self._open_trades.items()
                if trade.mapping_id == str(mapping.id) and trade.market_id == str(pm_fixture.id)
            ]
        for key in open_keys:
            trade = self._open_trades[key]
            if trade.status not in {"open", "confirmed"}:
                continue
            p_ref, bid = _select_p_ref_and_bid(trade.side, snapshot)

            # -- Stop-loss guards (checked before convergence) --
            reason: str | None = None
            if ended:
                reason = "market_ended"
            elif settings.stop_thesis_death_enabled and check_thesis_death(
                p_ref, trade.entry_price
            ):
                reason = "thesis_death"
            elif settings.stop_hard_enabled and check_hard_stop(
                bid, trade.entry_price, settings.stop_hard_pct
            ):
                reason = "hard_stop"
            elif compute_exit_signal(p_ref, bid, settings.exit_epsilon):
                reason = "convergence"

            if reason is None:
                continue
            if self._trade_mode == "live":
                result = self._submit_live_exit(
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    trade=trade,
                    bid=bid,
                    now=now,
                    exit_reason=reason,
                )
                if result is None:
                    with self._open_trades_lock:
                        self._open_trades.pop(trade.key, None)
                    continue
                if not result:
                    continue
                continue
            exit_price = bid if bid is not None else trade.entry_price
            pnl = (exit_price - trade.entry_price) if exit_price is not None else 0.0
            pnl_percent = pnl / trade.entry_price if trade.entry_price else None
            hold_seconds = (now - trade.entry_ts).total_seconds()
            convergence_seconds = (
                (now - trade.trigger_ts).total_seconds() if trade.trigger_ts else None
            )
            trade.status = "closed"

            exit_raw = {
                "exit_ts": now.isoformat(),
                "exit_price": exit_price,
                "exit_reason": reason,
                "pnl_percent": pnl_percent,
                "hold_seconds": hold_seconds,
            }
            with SessionLocal() as db:
                position = None
                if trade.db_position_id:
                    position = db.get(Position, trade.db_position_id)
                if position:
                    position.closed_at = now
                    position.exit_price = exit_price
                    position.exit_p_ref = p_ref
                    position.exit_reason = reason
                    position.pnl_percent = pnl_percent
                    position.hold_seconds = hold_seconds
                    if exit_price is not None:
                        position.pnl_absolute = exit_price - position.entry_price
                    if position.entry_edge and position.pnl_percent is not None:
                        position.edge_capture = position.pnl_percent / position.entry_edge
                    if reason == "convergence":
                        position.convergence_seconds = convergence_seconds
                    db.add(position)
                event = self._build_trade_event(
                    event_type="EXIT",
                    now=now,
                    mapping=mapping,
                    pm_fixture=pm_fixture,
                    snapshot=snapshot,
                    side=trade.side,
                    reason=reason,
                    details=f"pnl={_format_pct(pnl_percent)}",
                    position_id=trade.db_position_id,
                    exit_price=exit_price,
                    pnl_percent=pnl_percent,
                    convergence_seconds=convergence_seconds,
                    raw_json=exit_raw,
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
                        f"@ {_format_price(exit_price)} "
                        f"| P&L: {_format_pct(pnl_percent)} "
                        f"| hold={_format_seconds(timedelta(seconds=hold_seconds))} "
                        f"| reason={reason}"
                    ),
                )
            )
            with self._open_trades_lock:
                self._open_trades.pop(trade.key, None)

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
                    convergence_seconds = (now - trigger_record.ts).total_seconds()
                    event = self._build_trade_event(
                        event_type="CATCHUP",
                        now=now,
                        mapping=mapping,
                        pm_fixture=pm_fixture,
                        snapshot=snapshot,
                        side=side,
                        reason="catchup",
                        details=f"time={_format_seconds(now - trigger_record.ts)}",
                        convergence_seconds=convergence_seconds,
                        raw_json={
                            "catchup_seconds": convergence_seconds,
                        },
                    )
                    db.add(event)
                    db.commit()


def _is_market_ended(snapshot: FocusSnapshot) -> bool:
    if snapshot.pm_resolution_status in {"PROPOSED", "DISPUTED", "RESOLVED", "SETTLED"}:
        return True
    if snapshot.pm_winner:
        return True
    # Price-at-certainty: book effectively closed (one side pinned near 1.0,
    # other near 0.0).  Mirrors _pm_price_finished in monitor_core.
    if _pm_price_finished(
        snapshot.bid_a,
        snapshot.ask_a,
        snapshot.bid_b,
        snapshot.ask_b,
        settings.pm_done_threshold_high,
        settings.pm_done_threshold_low,
    ):
        return True
    return False


def _pm_price_finished(
    bid_a: float | None,
    ask_a: float | None,
    bid_b: float | None,
    ask_b: float | None,
    high_threshold: float,
    low_threshold: float,
) -> bool:
    """Return True when prices indicate the market outcome is decided."""
    a_vals = [v for v in (bid_a, ask_a) if v is not None]
    b_vals = [v for v in (bid_b, ask_b) if v is not None]
    a_max = max(a_vals, default=None)
    b_min = min(b_vals, default=None)
    b_max = max(b_vals, default=None)
    a_min = min(a_vals, default=None)
    if a_max is not None and a_max >= high_threshold and b_min is not None and b_min <= low_threshold:
        return True
    if b_max is not None and b_max >= high_threshold and a_min is not None and a_min <= low_threshold:
        return True
    return False


def _build_entry_candidates(
    snapshot: FocusSnapshot,
    ws_state: dict[str, BookState],
) -> list[dict]:
    """Build entry candidates using the snapshot's authoritative token-to-side mapping.

    Previously this called ``_resolve_books`` which used a different (weaker)
    name-matching algorithm than the poller's snapshot builder.  That mismatch
    caused p_ref_a to be paired with the wrong token, producing phantom edges
    and wrong exit signals.  Now we use the token IDs that the poller already
    resolved via its price-based swap detection.
    """
    candidates: list[dict] = []
    for side, p_ref, token_id in (
        ("buy_a", snapshot.p_ref_a, snapshot.token_id_a),
        ("buy_b", snapshot.p_ref_b, snapshot.token_id_b),
    ):
        if not token_id:
            continue
        book = ws_state.get(token_id)
        candidate = _build_entry_candidate(
            side,
            p_ref,
            book,
            token_id,
            snapshot.tick_size,
            snapshot.updated_at,
            snapshot.p_ref_source,
            snapshot.market_type,
        )
        if candidate:
            candidates.append(candidate)
    return candidates


def _resolve_books(
    op_fixture: Fixture,
    pm_fixture: Fixture,
    ws_state: dict[str, BookState],
) -> dict[str, dict | None]:
    raw = pm_fixture.raw_json or {}
    outcome_pairs = _extract_outcome_token_pairs(raw)
    if not outcome_pairs:
        outcomes = _parse_outcomes(raw)
        token_ids = _parse_token_ids(raw)
        outcome_pairs = _pair_outcomes_with_tokens(op_fixture, outcomes, token_ids)
    books: dict[str, dict | None] = {"buy_a": None, "buy_b": None}
    for outcome_name, token_id in outcome_pairs:
        if not token_id:
            continue
        book = ws_state.get(token_id)
        if not book:
            continue
        if _is_team_a(op_fixture, outcome_name):
            books["buy_a"] = {"book": book, "token_id": token_id}
        elif _is_team_b(op_fixture, outcome_name):
            books["buy_b"] = {"book": book, "token_id": token_id}
    if len(outcome_pairs) >= 2:
        left_outcome, left_token = outcome_pairs[0]
        right_outcome, right_token = outcome_pairs[1]
        swap_hint = (
            _similarity(left_outcome, op_fixture.team_b_name)
            > _similarity(left_outcome, op_fixture.team_a_name)
            and _similarity(right_outcome, op_fixture.team_a_name)
            > _similarity(right_outcome, op_fixture.team_b_name)
        )
        if books["buy_a"] is None:
            token_id = right_token if swap_hint else left_token
            book = ws_state.get(token_id)
            if book:
                books["buy_a"] = {"book": book, "token_id": token_id}
        if books["buy_b"] is None:
            token_id = left_token if swap_hint else right_token
            book = ws_state.get(token_id)
            if book:
                books["buy_b"] = {"book": book, "token_id": token_id}
    return books


def _bid_depth_usd(bids: list[tuple[float, float]]) -> float:
    """Total USD liquidity on the bid side: sum(price * size) for each level."""
    return sum(price * size for price, size in bids)


def _build_entry_candidate(
    side: str,
    p_ref: float | None,
    book: BookState | None,
    token_id: str,
    tick_size: float | None,
    now: datetime | None = None,
    p_ref_source: str | None = None,
    market_type: str | None = None,
) -> dict | None:
    if not book or p_ref is None:
        return None
    if _is_book_stale(book=book, now=now, max_age_seconds=float(settings.pm_book_stale_seconds)):
        logger.debug("stale_book: %s age too high - skipping", side)
        return None
    bid = book.best_bid
    ask = book.best_ask
    if bid is None or ask is None:
        return None

    use_totals_gates = market_type == "totals"
    use_derived_gates = p_ref_source == "derived_series"
    min_book_depth = (
        float(settings.totals_min_book_depth_usd)
        if use_totals_gates
        else (
        float(settings.derived_game_min_book_depth_usd)
        if use_derived_gates
        else float(settings.min_book_depth_usd)
        )
    )
    max_spread = (
        float(settings.totals_max_spread)
        if use_totals_gates
        else (
        float(settings.derived_game_max_spread)
        if use_derived_gates
        else float(settings.max_spread)
        )
    )
    alpha_min = (
        float(settings.totals_alpha_min)
        if use_totals_gates
        else (
        float(settings.derived_game_alpha_min)
        if use_derived_gates
        else float(settings.alpha_min)
        )
    )
    alpha_spread_factor = (
        float(settings.totals_alpha_spread_factor)
        if use_totals_gates
        else (
        float(settings.derived_game_alpha_spread_factor)
        if use_derived_gates
        else float(settings.alpha_spread_factor)
        )
    )

    # Gate: skip markets where bid-side liquidity is too thin to exit
    bid_usd = _bid_depth_usd(book.bids)
    if bid_usd < min_book_depth:
        logger.debug(
            "thin_bids: %s bid depth $%.2f < min $%.2f – skipping",
            side,
            bid_usd,
            min_book_depth,
        )
        return None

    spread = max(ask - bid, 0.0)
    if spread > max_spread:
        logger.debug(
            "wide_spread: %s spread %.3f > max %.3f - skipping",
            side,
            spread,
            max_spread,
        )
        return None
    alpha = compute_alpha_entry(spread, alpha_min, alpha_spread_factor)
    entry = compute_entry_edge(p_ref, book.asks, PROBE_QUANTITY, alpha, tick_size=tick_size or 0.01)
    return {
        "side": side,
        "p_ref": p_ref,
        "alpha": alpha,
        "entry": entry,
        "token_id": token_id,
    }


def _select_best_candidate(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None

    def _score(candidate: dict) -> float:
        net_edge = candidate["entry"].get("net_edge")
        return net_edge if isinstance(net_edge, (int, float)) else -1.0

    return max(candidates, key=_score)


def _select_p_ref_and_bid(side: str, snapshot: FocusSnapshot) -> tuple[float | None, float | None]:
    if side == "buy_a":
        return snapshot.p_ref_a, snapshot.bid_a
    return snapshot.p_ref_b, snapshot.bid_b


def _parse_filled_quantity(order_response: dict) -> float | None:
    """Extract filled share quantity from a Polymarket get_order response.

    The CLOB returns ``size_matched`` (string) representing the number of
    shares that have been matched/filled for the order.
    """
    raw = order_response.get("size_matched")
    if raw is None:
        return None
    try:
        value = float(raw)
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _effective_tick_size(value: float | None) -> float:
    tick = _parse_float(value)
    if tick is None or tick <= 0:
        return 0.01
    return tick


def _quantize_price_to_tick(price: float, tick_size: float) -> float:
    if price <= 0:
        return 0.0
    tick = Decimal(str(tick_size if tick_size > 0 else 0.01))
    rounded = (Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_DOWN) * tick
    return float(rounded)


def _compute_exit_limit_price(
    *,
    bid: float,
    tick_size: float | None,
    attempt_count: int,
    exit_reason: str,
) -> float:
    tick = _effective_tick_size(tick_size)
    attempt_num = max(int(attempt_count), 0) + 1
    if attempt_num <= 2:
        improve_ticks = 0
    elif attempt_num <= 4:
        improve_ticks = 1
    else:
        improve_ticks = 2
    if exit_reason in {"thesis_death", "hard_stop", "market_ended"}:
        improve_ticks = max(improve_ticks, 1)
    step_ticks = max(int(settings.exit_price_step_ticks), 1)
    adjusted = bid - (improve_ticks * step_ticks * tick)
    bounded = max(adjusted, tick)
    return _quantize_price_to_tick(bounded, tick)


def _is_gtc_exit_attempt(attempt: OrderAttempt) -> bool:
    raw = attempt.raw_json if isinstance(attempt.raw_json, dict) else {}
    return str(raw.get("order_type") or "").upper() == "GTC"


def _quantize_usdc(value: float) -> float:
    if value <= 0:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _quantize_market_shares(value: float) -> float:
    if value <= 0:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _compute_degraded_exit_shares(quantity: float, chunk_fraction: float) -> float:
    if quantity <= 0:
        return 0.0
    fraction = min(max(chunk_fraction, 0.0), 1.0)
    return _quantize_market_shares(quantity * fraction)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _is_order_not_found_timeout(
    submitted_at: datetime, now: datetime, timeout_seconds: float
) -> bool:
    return (now - submitted_at).total_seconds() > timeout_seconds


def _classify_exit_timeout_balance_outcome(
    *,
    balance: float | None,
    requested: float,
    eps: float,
    trade_status: str,
    allow_partial_fill: bool,
) -> tuple[str, float]:
    if balance is None:
        return "timeout_error", 0.0
    if balance <= eps:
        return "balance_zero", max(requested, 0.0)
    filled_qty = max(requested - balance, 0.0) if requested > 0 else 0.0
    if allow_partial_fill and requested > 0 and balance < max(requested - eps, 0.0):
        return "partial_fill_balance", filled_qty
    if trade_status != "open":
        return "timeout_reopen", filled_qty
    return "timeout_error", filled_qty


def _finalize_phantom_exit_for_retry(*, attempt: OrderAttempt, trade: PaperTrade, now: datetime) -> None:
    attempt.finalized_at = now
    attempt.final_state = "failed"
    attempt.final_reason = "phantom_order_id_reopen"
    trade.status = "open"


def _seed_balance_reconcile_last_ts(
    *,
    now: datetime,
    interval_seconds: float,
    first_delay_seconds: float,
) -> datetime:
    interval = max(float(interval_seconds), 0.1)
    first_delay = min(max(float(first_delay_seconds), 0.0), interval)
    return now - timedelta(seconds=interval - first_delay)


def _next_balance_zero_poll_count(*, balance: float, eps: float, current_count: int) -> int:
    if balance <= eps:
        return max(int(current_count), 0) + 1
    return 0


def _fixture_is_resolved(fixture: Fixture | None) -> bool:
    if not fixture:
        return False
    status = (fixture.status or "").strip().lower()
    return status in {"finished", "resolved", "settled", "closed", "ended"}


def _is_market_endgame(snapshot: FocusSnapshot) -> bool:
    return _pm_price_finished(
        snapshot.bid_a,
        snapshot.ask_a,
        snapshot.bid_b,
        snapshot.ask_b,
        float(settings.pm_endgame_threshold_high),
        float(settings.pm_endgame_threshold_low),
    )


def _orientation_entry_block_reason(snapshot: FocusSnapshot) -> str | None:
    if not settings.orientation_anchor_require_lock_for_entry:
        return None
    if not snapshot.orientation_locked:
        return "orientation_unlocked"
    if snapshot.orientation_conflict:
        return "orientation_conflict"
    return None


def _is_book_stale(*, book: BookState, now: datetime | None, max_age_seconds: float) -> bool:
    if max_age_seconds <= 0:
        return False
    current = now or datetime.now(tz=timezone.utc)
    age = (current - book.timestamp).total_seconds()
    return age > max_age_seconds


def _trigger_key(fixture_id: str, snapshot: FocusSnapshot) -> str:
    return f"{fixture_id}:{snapshot.market_type}:{snapshot.game_number}"


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

    if event in {"ENTRY", "ENTRY_SKIP", "ENTRY_CHECK", "ENTRY_SUBMIT", "ENTRY_CONFIRMED"}:
        return f"BUY {token}"
    if event in {"EXIT", "EXIT_SUBMIT", "EXIT_CONFIRMED"}:
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
    if delta is None:
        return "n/a"
    return f"{delta.total_seconds():.1f}s"


def _extract_outcome_token_pairs(raw: dict) -> list[tuple[str, str]]:
    """Extract (outcome, token_id) pairs from the rich ``tokens`` array."""
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


def _similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def _is_team_a(fixture: Fixture, outcome: str) -> bool:
    out = _normalize(outcome)
    team = _normalize(fixture.team_a_name)
    return bool(out and team and (out == team or out in team or team in out))


def _is_team_b(fixture: Fixture, outcome: str) -> bool:
    out = _normalize(outcome)
    team = _normalize(fixture.team_b_name)
    return bool(out and team and (out == team or team in out))
