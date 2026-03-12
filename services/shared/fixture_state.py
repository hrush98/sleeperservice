"""
Fixture state manager for event-driven trigger detection.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.shared.config import settings


@dataclass
class TriggerEvent:
    fixture_id: str
    trigger_type: str  # primary | burst | adaptive | lock_change | edge_persist
    delta_p_ref_a: float | None
    delta_p_ref_b: float | None
    urgency: str  # normal | high
    best_edge: float | None = None


@dataclass
class FixtureState:
    fixture_id: str
    p_ref_a: float | None
    p_ref_b: float | None
    p_ref_history: deque[tuple[datetime, float, float]]
    sigma_a: float
    sigma_b: float
    hot_until: datetime | None
    last_polled: datetime | None
    locked: bool
    pending_sign: int | None
    pending_count: int
    edge_above_count: int = 0
    edge_above_side: str | None = None


class TriggerConfig:
    PRIMARY_THRESHOLD = settings.trigger_primary_threshold
    BURST_THRESHOLD = settings.trigger_burst_threshold
    ADAPTIVE_MULTIPLIER = settings.trigger_adaptive_multiplier
    MIN_ADAPTIVE = 0.02
    ANTI_FLICKER_POLLS = 2
    HOT_TTL_SECONDS = settings.hot_fixture_ttl_seconds
    HOT_TTL_BURST_SECONDS = 90
    EWMA_ALPHA = 0.3
    HISTORY_MAXLEN = 240
    EDGE_PERSIST_THRESHOLD = settings.trigger_edge_persist_threshold
    EDGE_PERSIST_POLLS = settings.trigger_edge_persist_polls
    EDGE_SPIKE_THRESHOLD = settings.trigger_edge_spike_threshold


class FixtureStateManager:
    """Track fixture states and emit trigger events."""

    def __init__(self) -> None:
        self._states: dict[str, FixtureState] = {}

    def get_state(self, fixture_id: str) -> FixtureState:
        state = self._states.get(fixture_id)
        if state:
            return state
        state = FixtureState(
            fixture_id=fixture_id,
            p_ref_a=None,
            p_ref_b=None,
            p_ref_history=deque(maxlen=TriggerConfig.HISTORY_MAXLEN),
            sigma_a=0.0,
            sigma_b=0.0,
            hot_until=None,
            last_polled=None,
            locked=False,
            pending_sign=None,
            pending_count=0,
        )
        self._states[fixture_id] = state
        return state

    def update_p_ref(
        self,
        fixture_id: str,
        p_ref_a: float | None,
        p_ref_b: float | None,
        now: datetime | None = None,
        locked: bool | None = None,
    ) -> TriggerEvent | None:
        """Update p_ref values and emit trigger event if thresholds met."""
        now = now or datetime.now(tz=timezone.utc)
        state = self.get_state(fixture_id)

        lock_changed = False
        if locked is not None and locked != state.locked:
            lock_changed = True
            state.locked = locked

        delta_a = _delta(p_ref_a, state.p_ref_a)
        delta_b = _delta(p_ref_b, state.p_ref_b)
        delta_abs = max(abs(delta_a or 0.0), abs(delta_b or 0.0))

        if p_ref_a is not None and p_ref_b is not None:
            state.p_ref_history.append((now, p_ref_a, p_ref_b))

        state.sigma_a = _ewma(state.sigma_a, abs(delta_a or 0.0))
        state.sigma_b = _ewma(state.sigma_b, abs(delta_b or 0.0))

        state.p_ref_a = p_ref_a
        state.p_ref_b = p_ref_b
        state.last_polled = now

        if lock_changed:
            self.escalate_to_hot(fixture_id, TriggerConfig.HOT_TTL_BURST_SECONDS, now)
            return TriggerEvent(
                fixture_id=fixture_id,
                trigger_type="lock_change",
                delta_p_ref_a=delta_a,
                delta_p_ref_b=delta_b,
                urgency="high",
            )

        adaptive = self.compute_adaptive_threshold(fixture_id)
        threshold = max(TriggerConfig.PRIMARY_THRESHOLD, adaptive)
        urgency = "normal"
        trigger_type = "primary"

        if delta_abs >= TriggerConfig.BURST_THRESHOLD:
            self.escalate_to_hot(fixture_id, TriggerConfig.HOT_TTL_BURST_SECONDS, now)
            return TriggerEvent(
                fixture_id=fixture_id,
                trigger_type="burst",
                delta_p_ref_a=delta_a,
                delta_p_ref_b=delta_b,
                urgency="high",
            )

        if delta_abs >= threshold:
            sign = 1 if (delta_a or delta_b or 0.0) > 0 else -1
            if state.pending_sign == sign:
                state.pending_count += 1
            else:
                state.pending_sign = sign
                state.pending_count = 1

            if state.pending_count >= TriggerConfig.ANTI_FLICKER_POLLS:
                if adaptive > TriggerConfig.PRIMARY_THRESHOLD:
                    trigger_type = "adaptive"
                self.escalate_to_hot(fixture_id, TriggerConfig.HOT_TTL_SECONDS, now)
                return TriggerEvent(
                    fixture_id=fixture_id,
                    trigger_type=trigger_type,
                    delta_p_ref_a=delta_a,
                    delta_p_ref_b=delta_b,
                    urgency=urgency,
                )
        else:
            state.pending_sign = None
            state.pending_count = 0

        return None

    def check_edge_spike(
        self,
        fixture_id: str,
        best_edge: float | None,
        best_side: str | None,
        now: datetime | None = None,
    ) -> TriggerEvent | None:
        """Fire immediately when absolute edge exceeds spike threshold (single poll)."""
        if best_edge is not None and best_edge >= TriggerConfig.EDGE_SPIKE_THRESHOLD:
            now = now or datetime.now(tz=timezone.utc)
            self.escalate_to_hot(fixture_id, TriggerConfig.HOT_TTL_BURST_SECONDS, now)
            return TriggerEvent(
                fixture_id=fixture_id,
                trigger_type="edge_spike",
                delta_p_ref_a=None,
                delta_p_ref_b=None,
                urgency="high",
                best_edge=best_edge,
            )
        return None

    def check_edge_trigger(
        self,
        fixture_id: str,
        best_edge: float | None,
        best_side: str | None,
        now: datetime | None = None,
    ) -> TriggerEvent | None:
        """Fire trigger when absolute edge stays above threshold for N consecutive polls."""
        now = now or datetime.now(tz=timezone.utc)
        state = self.get_state(fixture_id)

        if best_edge is not None and best_edge >= TriggerConfig.EDGE_PERSIST_THRESHOLD:
            if state.edge_above_side == best_side:
                state.edge_above_count += 1
            else:
                state.edge_above_side = best_side
                state.edge_above_count = 1

            if state.edge_above_count >= TriggerConfig.EDGE_PERSIST_POLLS:
                state.edge_above_count = 0
                state.edge_above_side = None
                self.escalate_to_hot(fixture_id, TriggerConfig.HOT_TTL_SECONDS, now)
                return TriggerEvent(
                    fixture_id=fixture_id,
                    trigger_type="edge_persist",
                    delta_p_ref_a=None,
                    delta_p_ref_b=None,
                    urgency="normal",
                    best_edge=best_edge,
                )
        else:
            state.edge_above_count = 0
            state.edge_above_side = None

        return None

    def compute_adaptive_threshold(self, fixture_id: str) -> float:
        state = self.get_state(fixture_id)
        sigma = max(state.sigma_a, state.sigma_b, 0.0)
        return max(TriggerConfig.MIN_ADAPTIVE, TriggerConfig.ADAPTIVE_MULTIPLIER * sigma)

    def is_hot(self, fixture_id: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(tz=timezone.utc)
        state = self.get_state(fixture_id)
        return state.hot_until is not None and now < state.hot_until

    def get_hot_fixtures(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(tz=timezone.utc)
        return [
            fixture_id
            for fixture_id, state in self._states.items()
            if state.hot_until and now < state.hot_until
        ]

    def escalate_to_hot(self, fixture_id: str, ttl_seconds: int, now: datetime | None = None) -> None:
        now = now or datetime.now(tz=timezone.utc)
        state = self.get_state(fixture_id)
        state.hot_until = now + timedelta(seconds=ttl_seconds)


def _delta(new: float | None, old: float | None) -> float | None:
    if new is None or old is None:
        return None
    return new - old


def _ewma(previous: float, value: float) -> float:
    alpha = TriggerConfig.EWMA_ALPHA
    return (alpha * value) + ((1 - alpha) * previous)
