import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# pylint: disable=import-error
from cli.monitor_types import LogBuffer, PaperTrade
from cli.trader import (
    _classify_exit_timeout_balance_outcome,
    TradeManager,
    _compute_degraded_exit_shares,
    _compute_exit_limit_price,
    _finalize_phantom_exit_for_retry,
    _is_order_not_found_timeout,
    _next_balance_zero_poll_count,
    _seed_balance_reconcile_last_ts,
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


def test_seed_balance_reconcile_last_ts_uses_first_delay() -> None:
    now = datetime(2026, 2, 13, 18, 0, 0, tzinfo=timezone.utc)
    seeded = _seed_balance_reconcile_last_ts(
        now=now,
        interval_seconds=30.0,
        first_delay_seconds=3.0,
    )
    delta = (now - seeded).total_seconds()
    assert delta == 27.0


def test_seed_balance_reconcile_last_ts_clamps_delay_to_interval() -> None:
    now = datetime(2026, 2, 13, 18, 0, 0, tzinfo=timezone.utc)
    seeded = _seed_balance_reconcile_last_ts(
        now=now,
        interval_seconds=30.0,
        first_delay_seconds=45.0,
    )
    delta = (now - seeded).total_seconds()
    assert delta == 0.0


def test_next_balance_zero_poll_count_increments_on_zero_balance() -> None:
    count = _next_balance_zero_poll_count(balance=0.0, eps=0.0001, current_count=1)
    assert count == 2


def test_next_balance_zero_poll_count_resets_on_positive_balance() -> None:
    count = _next_balance_zero_poll_count(balance=5.0, eps=0.0001, current_count=2)
    assert count == 0


def test_next_balance_zero_poll_count_below_min_sane_quantity_increments() -> None:
    # balance above eps but below floor -> treat as effective zero, increment count
    count = _next_balance_zero_poll_count(
        balance=0.005,
        eps=0.0001,
        current_count=0,
        min_sane_quantity=0.01,
    )
    assert count == 1


def test_next_balance_zero_poll_count_at_or_above_min_sane_quantity_resets() -> None:
    # balance at or above floor and above eps -> not effective zero, reset count
    count = _next_balance_zero_poll_count(
        balance=0.02,
        eps=0.0001,
        current_count=2,
        min_sane_quantity=0.01,
    )
    assert count == 0


def test_classify_exit_timeout_balance_outcome_balance_zero() -> None:
    outcome, filled_qty = _classify_exit_timeout_balance_outcome(
        balance=0.0,
        requested=12.5,
        eps=0.0001,
        trade_status="exit_submitted",
        allow_partial_fill=True,
    )
    assert outcome == "balance_zero"
    assert filled_qty == 12.5


def test_classify_exit_timeout_balance_outcome_partial_fill() -> None:
    outcome, filled_qty = _classify_exit_timeout_balance_outcome(
        balance=3.0,
        requested=10.0,
        eps=0.0001,
        trade_status="exit_submitted",
        allow_partial_fill=True,
    )
    assert outcome == "partial_fill_balance"
    assert filled_qty == 7.0


def test_classify_exit_timeout_balance_outcome_timeout_reopen() -> None:
    outcome, filled_qty = _classify_exit_timeout_balance_outcome(
        balance=6.0,
        requested=10.0,
        eps=0.0001,
        trade_status="exit_submitted",
        allow_partial_fill=False,
    )
    assert outcome == "timeout_reopen"
    assert filled_qty == 4.0


def test_finalize_phantom_exit_for_retry_reopens_trade() -> None:
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
        entry_ts=now,
        entry_price=0.4,
        limit_price=0.4,
        size_available=10.0,
        quantity=10.0,
        alpha=0.03,
        net_edge=0.04,
        p_ref_entry=0.45,
        status="exit_submitted",
    )

    class Attempt:
        finalized_at = None
        final_state = None
        final_reason = None

    attempt = Attempt()
    _finalize_phantom_exit_for_retry(attempt=attempt, trade=trade, now=now)
    assert attempt.finalized_at == now
    assert attempt.final_state == "failed"
    assert attempt.final_reason == "phantom_order_id_reopen"
    assert trade.status == "open"


# ---------------------------------------------------------------------------
# Phantom entry retry tests
# ---------------------------------------------------------------------------


def _make_manager_with_executor(executor=None):
    """Build a minimal TradeManager without __init__ for unit tests."""
    mgr = TradeManager.__new__(TradeManager)
    mgr._executor = executor  # pylint: disable=protected-access
    mgr._run_id = "test-run"  # pylint: disable=protected-access
    mgr._mode_label = "real"  # pylint: disable=protected-access
    mgr._mapping = MagicMock(id="mapping-1")  # pylint: disable=protected-access
    mgr._open_trades = {}  # pylint: disable=protected-access
    mgr._open_trades_lock = threading.Lock()  # pylint: disable=protected-access
    mgr._trade_buffer = LogBuffer()  # pylint: disable=protected-access
    mgr._pm_fixture = MagicMock(id="fixture-1")  # pylint: disable=protected-access
    mgr._pm_fixtures = []  # pylint: disable=protected-access
    mgr._edge_threshold = 0.02  # pylint: disable=protected-access
    mgr._spread_factor = 0.5  # pylint: disable=protected-access
    return mgr


def _make_attempt(submitted_at, not_found_count=1, limit_price=0.45, requested_size=20.0):
    attempt = MagicMock()
    attempt.submitted_at = submitted_at
    attempt.not_found_count = not_found_count
    attempt.limit_price = limit_price
    attempt.requested_size = requested_size
    attempt.external_order_id = "0xabc"
    attempt.token_id = "token-1"
    attempt.raw_json = {"response": {"status": "delayed"}}
    attempt.finalized_at = None
    attempt.position_id = "pos-1"
    attempt.id = "attempt-1"
    return attempt


def _make_trade(delayed_retries=0):
    now = datetime.now(tz=timezone.utc)
    return PaperTrade(
        key="k",
        mapping_id="m",
        market_id="pm",
        token_id="token-1",
        market_type="match_winner",
        game_number=None,
        side="buy_a",
        trigger_type=None,
        trigger_ts=None,
        entry_ts=now,
        entry_price=0.45,
        limit_price=0.45,
        size_available=10.0,
        quantity=20.0,
        alpha=0.03,
        net_edge=0.04,
        p_ref_entry=0.50,
        status="submitted",
        external_order_id="0xabc",
        external_status="delayed",
        delayed_retries=delayed_retries,
    )


def test_phantom_retry_entry_blocked_without_executor() -> None:
    mgr = _make_manager_with_executor(executor=None)
    now = datetime.now(tz=timezone.utc)
    attempt = _make_attempt(now - timedelta(seconds=5))
    trade = _make_trade()
    result = mgr._phantom_retry_entry(  # pylint: disable=protected-access
        db=MagicMock(), attempt=attempt, position=MagicMock(),
        trade=trade, now=now, pm_fixture=MagicMock(),
    )
    assert result is False


def test_phantom_retry_entry_blocked_by_max_retries() -> None:
    mgr = _make_manager_with_executor(executor=MagicMock())
    now = datetime.now(tz=timezone.utc)
    attempt = _make_attempt(now - timedelta(seconds=5))
    trade = _make_trade(delayed_retries=settings.entry_phantom_max_retries)
    result = mgr._phantom_retry_entry(  # pylint: disable=protected-access
        db=MagicMock(), attempt=attempt, position=MagicMock(),
        trade=trade, now=now, pm_fixture=MagicMock(),
    )
    assert result is False


def test_phantom_retry_entry_blocked_too_early() -> None:
    mgr = _make_manager_with_executor(executor=MagicMock())
    now = datetime.now(tz=timezone.utc)
    # Submitted 1s ago — below the 3s threshold
    attempt = _make_attempt(now - timedelta(seconds=1))
    trade = _make_trade()
    result = mgr._phantom_retry_entry(  # pylint: disable=protected-access
        db=MagicMock(), attempt=attempt, position=MagicMock(),
        trade=trade, now=now, pm_fixture=MagicMock(),
    )
    assert result is False


@patch("cli.trader.TradeManager._next_attempt_seq", return_value=2)
@patch("cli.trader.TradeManager._cancel_existing_order")
def test_phantom_retry_entry_succeeds(mock_cancel, _mock_seq) -> None:
    executor = MagicMock()
    submit_resp = MagicMock()
    submit_resp.success = True
    submit_resp.error_msg = ""
    submit_resp.order_id = "0xnew"
    submit_resp.status = "delayed"
    submit_resp.raw = {"status": "delayed", "orderID": "0xnew"}
    executor.place_fak_order.return_value = submit_resp
    mock_cancel.return_value = MagicMock(raw={})

    mgr = _make_manager_with_executor(executor=executor)
    now = datetime.now(tz=timezone.utc)
    # Submitted 4s ago — exceeds the 3s threshold
    attempt = _make_attempt(now - timedelta(seconds=4))
    trade = _make_trade(delayed_retries=0)
    db = MagicMock()
    position = MagicMock()
    position.id = "pos-1"

    result = mgr._phantom_retry_entry(  # pylint: disable=protected-access
        db=db, attempt=attempt, position=position,
        trade=trade, now=now, pm_fixture=MagicMock(),
    )
    assert result is True
    # Old attempt finalized
    assert attempt.finalized_at == now
    assert attempt.final_state == "failed"
    assert attempt.final_reason == "phantom_entry_retry"
    # Trade updated
    assert trade.delayed_retries == 1
    assert trade.external_order_id == "0xnew"
    # New order placed
    executor.place_fak_order.assert_called_once()
    db.commit.assert_called_once()
