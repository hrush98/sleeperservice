import pytest

from py_clob_client.order_builder.builder import OrderBuilder, ROUNDING_CONFIG
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.signer import Signer

from services.shared.clob_executor import (
    ClobExecutor,
    _parse_balance_allowance,
    _quantize_buy_size_for_usdc_cents,
    _quantize_size,
)


def test_quantize_size_rounds_down_to_step() -> None:
    assert _quantize_size(5.294116) == pytest.approx(5.2941)


def test_quantize_size_under_step_is_zero() -> None:
    assert _quantize_size(0.00005) == 0.0


def test_quantize_buy_size_respects_usdc_cents() -> None:
    # Example from live logs: size is derived from max_usd / price and can
    # create repeating decimals (e.g. 2 / 0.45 = 4.444...).
    price = 0.45
    size = 4.444444444444445
    adjusted = _quantize_buy_size_for_usdc_cents(price=price, size=size)
    assert adjusted <= size
    # shares should still be 4dp step-aligned by default
    assert adjusted == pytest.approx(_quantize_size(adjusted))
    # maker notional must be cents-quantized after adjustment
    maker = price * adjusted
    assert maker == pytest.approx((maker // 0.01) * 0.01)


def test_parse_balance_allowance_normalizes_raw_units() -> None:
    raw = {"balance": "5264064", "allowance": "9999999"}
    balance, allowance = _parse_balance_allowance(raw, decimals=6)
    assert balance == pytest.approx(5.264064)
    assert allowance == pytest.approx(9.999999)


def test_parse_balance_allowance_no_decimals() -> None:
    raw = {"balance": "5264064", "allowance": "9999999"}
    balance, allowance = _parse_balance_allowance(raw)
    assert balance == pytest.approx(5264064.0)
    assert allowance == pytest.approx(9999999.0)


def test_market_buy_amounts_respect_precision() -> None:
    signer = Signer("0x" + "1" * 64, 137)
    builder = OrderBuilder(signer)
    _side, maker_amount, taker_amount = builder.get_market_order_amounts(
        BUY, amount=2.13, price=0.457, round_config=ROUNDING_CONFIG["0.01"]
    )
    assert maker_amount % 10000 == 0
    assert taker_amount % 100 == 0


def test_market_sell_amounts_respect_precision() -> None:
    signer = Signer("0x" + "2" * 64, 137)
    builder = OrderBuilder(signer)
    _side, maker_amount, taker_amount = builder.get_market_order_amounts(
        SELL, amount=12.3456, price=0.543, round_config=ROUNDING_CONFIG["0.01"]
    )
    assert maker_amount % 10000 == 0
    assert taker_amount % 100 == 0


def test_cancel_order_success() -> None:
    class _Client:
        @staticmethod
        def cancel(order_id: str) -> dict:
            return {"canceled": [order_id], "not_canceled": {}}

    executor = ClobExecutor.__new__(ClobExecutor)
    executor._client = _Client()  # type: ignore[attr-defined]  # pylint: disable=protected-access
    result = executor.cancel_order("0xabc")
    assert result.success is True
    assert result.canceled == ["0xabc"]
    assert result.error_msg is None


def test_cancel_order_handles_invalid_response() -> None:
    class _Client:
        @staticmethod
        def cancel(order_id: str) -> list[str]:
            return [order_id]

    executor = ClobExecutor.__new__(ClobExecutor)
    executor._client = _Client()  # type: ignore[attr-defined]  # pylint: disable=protected-access
    result = executor.cancel_order("0xabc")
    assert result.success is False
    assert result.error_msg == "invalid_cancel_response"


def test_place_gtc_order_uses_gtc_order_type() -> None:
    class _Client:
        captured: dict = {}

        @staticmethod
        def create_market_order(order_args, _options):  # type: ignore[no-untyped-def]
            _Client.captured["order_type"] = order_args.order_type
            _Client.captured["side"] = order_args.side
            _Client.captured["amount"] = order_args.amount
            _Client.captured["price"] = order_args.price
            return {"signed": True}

        @staticmethod
        def post_order(_signed, order_type):  # type: ignore[no-untyped-def]
            _Client.captured["post_order_type"] = order_type
            return {"success": True, "orderId": "0x123", "status": "live"}

    executor = ClobExecutor.__new__(ClobExecutor)
    executor._client = _Client()  # type: ignore[attr-defined]  # pylint: disable=protected-access
    executor.check_kill_switch = lambda: False  # type: ignore[method-assign]
    result = executor.place_gtc_order(
        token_id="1",
        side="SELL",
        price=0.31,
        amount=5.67,
        tick_size=0.01,
    )
    assert result.success is True
    assert result.order_id == "0x123"
    assert str(_Client.captured.get("post_order_type")) == "GTC"
