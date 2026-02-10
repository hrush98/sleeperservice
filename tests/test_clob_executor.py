import pytest

# pylint: disable=import-error
from py_clob_client.order_builder.builder import OrderBuilder, ROUNDING_CONFIG
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.signer import Signer

from shared.clob_executor import (
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
