from datetime import datetime, timedelta, timezone

# pylint: disable=import-error
from cli.monitor_types import PaperTrade
from cli.trader import (
    TradeManager,
    _compute_degraded_exit_shares,
    _compute_exit_limit_price,
    _is_order_not_found_timeout,
)
from shared.config import settings


def test_order_not_found_timeout() -> None:
    now = datetime.now(tz=timezone.utc)
    submitted_recent = now - timedelta(seconds=5)
    submitted_old = now - timedelta(seconds=15)

    assert not _is_order_not_found_timeout(submitted_recent, now, 10.0)
    assert _is_order_not_found_timeout(submitted_old, now, 10.0)


def test_exit_order_timeout_setting() -> None:
    now = datetime.now(tz=timezone.utc)
    submitted_old = now - timedelta(seconds=settings.exit_order_not_found_seconds + 1)
    assert _is_order_not_found_timeout(
        submitted_old,
        now,
        settings.exit_order_not_found_seconds,
    )


def test_delayed_retry_ready_after_grace() -> None:
    manager = TradeManager.__new__(TradeManager)
    now = datetime.now(tz=timezone.utc)
    trade = PaperTrade(
        key="k",
        mapping_id="m",
        market_id="pm",
        token_id="t",
        market_type="match_winner",
        game_number=None,
        side="buy_a",
        trigger_type=None,
        trigger_ts=None,
        entry_ts=now - timedelta(seconds=settings.delayed_grace_seconds + 1),
        entry_price=0.4,
        limit_price=0.4,
        size_available=10.0,
        quantity=10.0,
        alpha=0.03,
        net_edge=0.04,
        p_ref_entry=0.45,
        status="submitted",
        external_order_id="0x1",
        external_status="delayed",
    )
    assert manager._delayed_retry_ready(trade, now, for_entry=True) is True  # pylint: disable=protected-access


def test_delayed_retry_blocked_by_max_retries() -> None:
    manager = TradeManager.__new__(TradeManager)
    now = datetime.now(tz=timezone.utc)
    trade = PaperTrade(
        key="k",
        mapping_id="m",
        market_id="pm",
        token_id="t",
        market_type="match_winner",
        game_number=None,
        side="buy_a",
        trigger_type=None,
        trigger_ts=None,
        entry_ts=now - timedelta(seconds=settings.delayed_grace_seconds + 10),
        entry_price=0.4,
        limit_price=0.4,
        size_available=10.0,
        quantity=10.0,
        alpha=0.03,
        net_edge=0.04,
        p_ref_entry=0.45,
        status="submitted",
        external_order_id="0x1",
        external_status="delayed",
        delayed_retries=settings.delayed_max_retries,
    )
    assert manager._delayed_retry_ready(trade, now, for_entry=True) is False  # pylint: disable=protected-access


def test_compute_degraded_exit_shares_uses_fraction() -> None:
    shares = _compute_degraded_exit_shares(34.78, settings.exit_degraded_chunk_fraction)
    assert shares == 8.69


def test_compute_degraded_exit_shares_clamps_fraction() -> None:
    assert _compute_degraded_exit_shares(10.0, -1.0) == 0.0
    assert _compute_degraded_exit_shares(10.0, 2.0) == 10.0


def test_compute_exit_limit_price_improves_with_attempts() -> None:
    price_1 = _compute_exit_limit_price(
        bid=0.50,
        tick_size=0.01,
        attempt_count=0,
        exit_reason="convergence",
    )
    price_3 = _compute_exit_limit_price(
        bid=0.50,
        tick_size=0.01,
        attempt_count=2,
        exit_reason="convergence",
    )
    price_6 = _compute_exit_limit_price(
        bid=0.50,
        tick_size=0.01,
        attempt_count=5,
        exit_reason="convergence",
    )
    assert price_1 == 0.50
    assert price_3 == 0.49
    assert price_6 == 0.48


def test_compute_exit_limit_price_stop_loss_starts_aggressive() -> None:
    price = _compute_exit_limit_price(
        bid=0.50,
        tick_size=0.01,
        attempt_count=0,
        exit_reason="hard_stop",
    )
    assert price == 0.49
