from datetime import datetime, timedelta, timezone

# pylint: disable=import-error
from cli.monitor_types import FocusSnapshot
from cli.trader import _build_entry_candidate, _is_market_endgame, _orientation_entry_block_reason
from shared.config import settings
from shared.edge import NetEdgeResult
from shared.polymarket_ws import BookState


def _snapshot(*, bid_a: float | None, ask_a: float | None, bid_b: float | None, ask_b: float | None) -> FocusSnapshot:
    now = datetime.now(tz=timezone.utc)
    return FocusSnapshot(
        mapping_id="m",
        league="LCK",
        match="A vs B",
        start_time=None,
        market_type="match_winner",
        game_number=None,
        tick_size=0.01,
        pm_resolution_status="",
        pm_winner=None,
        p_ref_a=0.55,
        p_ref_b=0.45,
        odds_a=1.8,
        odds_b=2.0,
        bid_a=bid_a,
        ask_a=ask_a,
        bid_b=bid_b,
        ask_b=ask_b,
        mid_a=None,
        mid_b=None,
        token_id_a="a",
        token_id_b="b",
        orientation_locked=True,
        orientation_source="goalserve_pre",
        orientation_conflict=False,
        edge=NetEdgeResult(None, None, None, None),
        updated_at=now,
        ws_connected=True,
        last_odds_update=now,
        last_gamma_update=now,
    )


def test_is_market_endgame_true_at_thresholds() -> None:
    snap = _snapshot(bid_a=0.97, ask_a=0.98, bid_b=0.02, ask_b=0.03)
    assert _is_market_endgame(snap) is True


def test_build_entry_candidate_rejects_wide_spread(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_spread", 0.05)
    now = datetime.now(tz=timezone.utc)
    book = BookState(
        asset_id="tok",
        best_bid=0.50,
        best_ask=0.60,
        spread=0.10,
        bids=[(0.50, 1000.0)],
        asks=[(0.60, 1000.0)],
        timestamp=now,
        hash=None,
    )
    candidate = _build_entry_candidate("buy_a", 0.70, book, "tok", 0.01, now)
    assert candidate is None


def test_build_entry_candidate_rejects_stale_book(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pm_book_stale_seconds", 5.0)
    monkeypatch.setattr(settings, "max_spread", 0.20)
    now = datetime.now(tz=timezone.utc)
    book = BookState(
        asset_id="tok",
        best_bid=0.50,
        best_ask=0.51,
        spread=0.01,
        bids=[(0.50, 1000.0)],
        asks=[(0.51, 1000.0)],
        timestamp=now - timedelta(seconds=10),
        hash=None,
    )
    candidate = _build_entry_candidate("buy_a", 0.70, book, "tok", 0.01, now)
    assert candidate is None


def test_build_entry_candidate_passes_with_fresh_tight_book(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pm_book_stale_seconds", 5.0)
    monkeypatch.setattr(settings, "max_spread", 0.20)
    now = datetime.now(tz=timezone.utc)
    book = BookState(
        asset_id="tok",
        best_bid=0.50,
        best_ask=0.51,
        spread=0.01,
        bids=[(0.50, 1000.0)],
        asks=[(0.51, 1000.0)],
        timestamp=now,
        hash=None,
    )
    candidate = _build_entry_candidate("buy_a", 0.70, book, "tok", 0.01, now)
    assert candidate is not None


def test_orientation_entry_block_reason_requires_locked(monkeypatch) -> None:
    monkeypatch.setattr(settings, "orientation_anchor_require_lock_for_entry", True)
    snap = _snapshot(bid_a=0.50, ask_a=0.51, bid_b=0.49, ask_b=0.50)
    snap.orientation_locked = False
    assert _orientation_entry_block_reason(snap) == "orientation_unlocked"


def test_orientation_entry_block_reason_blocks_conflict(monkeypatch) -> None:
    monkeypatch.setattr(settings, "orientation_anchor_require_lock_for_entry", True)
    snap = _snapshot(bid_a=0.50, ask_a=0.51, bid_b=0.49, ask_b=0.50)
    snap.orientation_conflict = True
    assert _orientation_entry_block_reason(snap) == "orientation_conflict"


def test_build_entry_candidate_uses_totals_strict_spread(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pm_book_stale_seconds", 5.0)
    monkeypatch.setattr(settings, "max_spread", 0.20)
    monkeypatch.setattr(settings, "totals_max_spread", 0.02)
    now = datetime.now(tz=timezone.utc)
    book = BookState(
        asset_id="tok",
        best_bid=0.50,
        best_ask=0.53,
        spread=0.03,
        bids=[(0.50, 1000.0)],
        asks=[(0.53, 1000.0)],
        timestamp=now,
        hash=None,
    )
    candidate = _build_entry_candidate(
        "buy_a",
        0.70,
        book,
        "tok",
        0.01,
        now,
        None,
        "totals",
    )
    assert candidate is None
