from datetime import datetime, timezone

from cli.complement_arb import _simulate_fok_fill
from cli.monitor_types import ComplementArbState
from shared.edge import compute_complement_edge, compute_vwap
from shared.polymarket_ws import BookState


def test_compute_vwap_returns_partial_fillable_size() -> None:
    asks = [(0.44, 40.0), (0.46, 20.0), (0.50, 10.0)]
    vwap, fillable = compute_vwap(asks, 100.0)
    expected = (0.44 * 40.0 + 0.46 * 20.0 + 0.50 * 10.0) / 70.0
    assert fillable == 70.0
    assert vwap is not None
    assert abs(vwap - expected) < 1e-9


def test_compute_complement_edge_uses_common_fillable_depth() -> None:
    asks_a = [(0.44, 100.0)]
    asks_b = [(0.52, 20.0), (0.54, 20.0)]
    result = compute_complement_edge(asks_a, asks_b, max_size=50.0)
    assert result.fillable_size == 40.0
    assert result.vwap_a is not None
    assert abs(result.vwap_a - 0.44) < 1e-9
    assert result.vwap_b is not None
    assert abs(result.vwap_b - 0.53) < 1e-9
    assert result.edge is not None
    assert abs(result.edge - (1.0 - 0.44 - 0.53)) < 1e-9


def test_simulate_fok_fill_succeeds_when_depth_at_or_below_limit() -> None:
    now = datetime.now(tz=timezone.utc)
    book = BookState(
        asset_id="a",
        best_bid=0.40,
        best_ask=0.45,
        spread=0.05,
        bids=[(0.40, 100.0)],
        asks=[(0.45, 30.0), (0.46, 30.0)],
        timestamp=now,
        hash=None,
    )
    fill = _simulate_fok_fill(book, size=50.0, limit_price=0.46)
    assert fill is not None
    assert abs(fill - ((0.45 * 30.0 + 0.46 * 20.0) / 50.0)) < 1e-9


def test_simulate_fok_fill_fails_when_limit_too_tight() -> None:
    now = datetime.now(tz=timezone.utc)
    book = BookState(
        asset_id="a",
        best_bid=0.40,
        best_ask=0.45,
        spread=0.05,
        bids=[(0.40, 100.0)],
        asks=[(0.45, 30.0), (0.46, 30.0)],
        timestamp=now,
        hash=None,
    )
    fill = _simulate_fok_fill(book, size=50.0, limit_price=0.45)
    assert fill is None


def test_complement_state_values_are_stable() -> None:
    assert ComplementArbState.BOTH_FILLED.value == "BOTH_FILLED"
    assert ComplementArbState.FAILED.value == "FAILED"
