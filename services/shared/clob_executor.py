"""
Lightweight execution wrapper for Polymarket CLOB (py-clob-client).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

_IMPORT_ERROR: Exception | None = None
try:  # pragma: no cover - optional dependency in paper mode
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        ApiCreds,
        AssetType,
        BalanceAllowanceParams,
        MarketOrderArgs,
        OrderType,
        PartialCreateOrderOptions,
    )
    from py_clob_client.exceptions import PolyApiException
    from py_clob_client.order_builder.constants import BUY, SELL
except Exception as exc:  # pragma: no cover - handled at runtime
    ClobClient = None
    ApiCreds = None
    AssetType = None
    BalanceAllowanceParams = None
    MarketOrderArgs = None
    OrderType = None
    PartialCreateOrderOptions = None
    PolyApiException = None
    BUY = None
    SELL = None
    _IMPORT_ERROR = exc

from shared.config import settings


@dataclass
class AllowanceCheck:
    ok: bool
    balance: float | None
    allowance: float | None
    reason: str | None = None
    raw: dict | None = None


@dataclass
class OrderSubmission:
    success: bool
    order_id: str | None
    status: str | None
    error_msg: str | None
    raw: dict


class ClobExecutor:
    """Small wrapper for placing and checking orders via py-clob-client."""

    def __init__(
        self,
        *,
        private_key: str,
        funder: str | None = None,
        signature_type: int | None = None,
        chain_id: int | None = None,
        clob_url: str | None = None,
        kill_switch_path: str | None = None,
    ) -> None:
        if ClobClient is None:
            raise RuntimeError(
                "py-clob-client is required for live trading."
            ) from _IMPORT_ERROR
        self._private_key = private_key
        self._funder = funder
        self._signature_type = (
            signature_type if signature_type is not None else settings.polymarket_signature_type
        )
        self._chain_id = chain_id if chain_id is not None else settings.polymarket_chain_id
        self._clob_url = clob_url or settings.polymarket_clob_url
        self._kill_switch_path = kill_switch_path or settings.live_kill_switch_path

        self._client = ClobClient(
            host=self._clob_url,
            key=self._private_key,
            chain_id=self._chain_id,
            signature_type=self._signature_type,
            funder=self._funder,
        )
        creds = self._client.create_or_derive_api_creds()
        if not isinstance(creds, ApiCreds):
            raise RuntimeError("Failed to derive API credentials.")
        self._creds = creds
        self._client.set_api_creds(creds)

    @property
    def client(self) -> ClobClient:
        return self._client

    @property
    def api_creds(self) -> dict[str, str]:
        return {
            "apiKey": self._creds.api_key,
            "secret": self._creds.api_secret,
            "passphrase": self._creds.api_passphrase,
        }

    def check_kill_switch(self) -> bool:
        path = Path(str(self._kill_switch_path)).expanduser()
        return path.exists()

    def check_allowance_buy(self, required_usdc: float) -> AllowanceCheck:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        raw = self._client.get_balance_allowance(params)
        balance, allowance = _parse_balance_allowance(
            raw,
            decimals=settings.polymarket_token_decimals,
        )
        if balance is None or allowance is None:
            return AllowanceCheck(False, balance, allowance, reason="missing_balance", raw=raw)
        if balance < required_usdc:
            return AllowanceCheck(False, balance, allowance, reason="insufficient_balance", raw=raw)
        if allowance < required_usdc:
            return AllowanceCheck(False, balance, allowance, reason="insufficient_allowance", raw=raw)
        return AllowanceCheck(True, balance, allowance, raw=raw)

    def check_allowance_sell(self, token_id: str, required_shares: float) -> AllowanceCheck:
        params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
        raw = self._client.get_balance_allowance(params)
        balance, allowance = _parse_balance_allowance(
            raw,
            decimals=settings.polymarket_token_decimals,
        )
        if balance is None or allowance is None:
            return AllowanceCheck(False, balance, allowance, reason="missing_balance", raw=raw)
        if balance < required_shares:
            return AllowanceCheck(False, balance, allowance, reason="insufficient_balance", raw=raw)
        if allowance < required_shares:
            return AllowanceCheck(False, balance, allowance, reason="insufficient_allowance", raw=raw)
        return AllowanceCheck(True, balance, allowance, raw=raw)

    def get_conditional_balance(self, token_id: str) -> tuple[float | None, dict]:
        """Return current conditional-token balance (shares) for token_id."""
        params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
        raw = self._client.get_balance_allowance(params)
        balance, _allowance = _parse_balance_allowance(
            raw,
            decimals=settings.polymarket_token_decimals,
        )
        return balance, raw if isinstance(raw, dict) else {"raw": raw}

    def place_fak_order(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        amount: float,
        tick_size: float | None = None,
    ) -> OrderSubmission:
        if self.check_kill_switch():
            return OrderSubmission(
                success=False,
                order_id=None,
                status="blocked",
                error_msg="kill_switch",
                raw={"blocked": True, "reason": "kill_switch"},
            )
        normalized_tick = _normalize_tick_size(tick_size)
        price = _quantize_price(price, normalized_tick)
        if side == "BUY":
            amount = _quantize_usdc_amount(amount)
        else:
            amount = _quantize_market_sell_amount(amount)
        if price <= 0 or amount <= 0:
            return OrderSubmission(
                success=False,
                order_id=None,
                status="blocked",
                error_msg="order_size_zero_after_round",
                raw={"blocked": True, "reason": "order_size_zero_after_round"},
            )
        order_side = BUY if side == "BUY" else SELL
        order_args = MarketOrderArgs(
            token_id=token_id,
            side=order_side,
            amount=amount,
            price=price,
            order_type=OrderType.FAK,
        )
        try:
            options = (
                PartialCreateOrderOptions(tick_size=normalized_tick)
                if normalized_tick and PartialCreateOrderOptions is not None
                else None
            )
            signed = self._client.create_market_order(order_args, options)
            response = self._client.post_order(signed, OrderType.FAK)
        except Exception as exc:  # pragma: no cover - network/venue guardrail
            raw: dict[str, Any] = {
                "exception_type": type(exc).__name__,
                "exception": str(exc),
            }
            if PolyApiException is not None and isinstance(exc, PolyApiException):
                raw["status_code"] = getattr(exc, "status_code", None)
                raw["error_message"] = getattr(exc, "error_message", None)
            error_msg = str(raw.get("error_message") or raw.get("exception") or "submit_failed")
            return OrderSubmission(
                success=False,
                order_id=None,
                status="error",
                error_msg=error_msg,
                raw=raw,
            )
        if not isinstance(response, dict):
            return OrderSubmission(
                success=False,
                order_id=None,
                status="error",
                error_msg="invalid_post_order_response",
                raw={"response_type": type(response).__name__},
            )
        order_id = response.get("orderId") or response.get("orderID")
        return OrderSubmission(
            success=bool(response.get("success", True)),
            order_id=order_id,
            status=response.get("status"),
            error_msg=response.get("errorMsg"),
            raw=response,
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._client.get_order(order_id)


def _quantize_price(value: float, tick_size: str | None = None) -> float:
    """Round price down to the provided tick size (defaults to 0.01)."""
    if value <= 0:
        return 0.0
    step = Decimal(str(tick_size or "0.01"))
    if step <= 0:
        step = Decimal("0.01")
    rounded = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    return float(rounded)


def _quantize_size(value: float) -> float:
    """Round size down to the configured share step."""
    if value <= 0:
        return 0.0
    step = Decimal(str(settings.live_share_step))
    if step <= 0:
        step = Decimal("0.0001")
    rounded = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    return float(rounded)


def _quantize_usdc_amount(value: float) -> float:
    """Round USDC amount down to cents."""
    if value <= 0:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _quantize_market_sell_amount(value: float) -> float:
    """Round market-sell amount down to 2 decimals (client precision)."""
    if value <= 0:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _normalize_tick_size(value: float | str | None) -> str | None:
    """Normalize tick size into a supported CLOB literal."""
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    for option in (0.1, 0.01, 0.001, 0.0001):
        if abs(as_float - option) < 1e-9:
            return f"{option:.4f}".rstrip("0").rstrip(".")
    return None


def _quantize_buy_size_for_usdc_cents(*, price: float, size: float) -> float:
    """Adjust BUY share size so maker notional (USDC) is <= 2 decimals.

    Polymarket enforces precision limits for market buy orders:
    - maker amount (USDC) max accuracy: 2 decimals
    - taker amount (shares) max accuracy: 4 decimals

    We already quantize shares to `settings.live_share_step` (default 0.0001).
    This helper additionally reduces size so `price * size` rounds DOWN to cents.
    """
    if price <= 0 or size <= 0:
        return 0.0
    step = Decimal(str(settings.live_share_step))
    if step <= 0:
        step = Decimal("0.0001")
    price_dec = Decimal(str(price))
    if price_dec <= 0:
        return 0.0
    size_dec = (Decimal(str(size)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    if size_dec <= 0:
        return 0.0

    # First pass: round maker notional to cents, then back out a share size.
    maker_dec = (price_dec * size_dec).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if maker_dec <= 0:
        return 0.0
    size_dec = (maker_dec / price_dec / step).to_integral_value(rounding=ROUND_DOWN) * step

    # Second pass: ensure stability after share-step rounding.
    for _ in range(2):
        maker2 = (price_dec * size_dec).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        size2 = (maker2 / price_dec / step).to_integral_value(rounding=ROUND_DOWN) * step
        if size2 == size_dec:
            break
        size_dec = size2

    return float(size_dec)




def _parse_balance_allowance(
    raw: Any,
    *,
    decimals: int = 0,
) -> tuple[float | None, float | None]:
    if not isinstance(raw, dict):
        return None, None
    balance = raw.get("balance") or raw.get("amount") or raw.get("available")
    allowance = raw.get("allowance") or raw.get("approved")
    if allowance is None and isinstance(raw.get("allowances"), dict):
        # Some responses return allowances per exchange contract address.
        # Use the max allowance as a conservative usable allowance.
        try:
            allowance = max(float(v) for v in raw["allowances"].values())
        except (TypeError, ValueError):
            allowance = None
    try:
        balance_val = float(balance) if balance is not None else None
    except (TypeError, ValueError):
        balance_val = None
    try:
        allowance_val = float(allowance) if allowance is not None else None
    except (TypeError, ValueError):
        allowance_val = None
    if decimals > 0:
        scale = 10 ** decimals
        balance_val = balance_val / scale if balance_val is not None else None
        allowance_val = allowance_val / scale if allowance_val is not None else None
    return balance_val, allowance_val
