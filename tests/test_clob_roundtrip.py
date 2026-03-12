from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from sqlalchemy import or_, select

from py_clob_client.clob_types import MarketOrderArgs, OrderType

from services.shared.clob_executor import ClobExecutor
from services.shared.config import settings
from services.shared.db import SessionLocal
from services.shared.models import Fixture
from services.shared.secret_utils import decrypt_age_keyfile

DEFAULT_TEAM_A = "Solary"
DEFAULT_TEAM_B = "BK Rog"
DEFAULT_MIN_SHARES = 5.0
STATUS_ALLOWLIST = {"upcoming", "live"}


@dataclass
class OrderBookView:
    best_bid: float | None
    best_ask: float | None
    min_order_size: float | None


def _prompt_yes_no(prompt: str, *, default: bool = False, interactive: bool = True) -> bool:
    if not interactive:
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        response = input(prompt + suffix).strip().lower()
    except EOFError:
        return default
    if not response:
        return default
    return response in {"y", "yes"}


def _parse_token_ids(raw: dict) -> list[str]:
    token_ids = raw.get("clobTokenIds") or []
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except (json.JSONDecodeError, TypeError):
            token_ids = []
    return [str(t) for t in token_ids if t]


def _parse_outcomes(raw: dict) -> list[str]:
    outcomes = raw.get("outcomes") or []
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    return [str(o) for o in outcomes if o]


def _extract_outcome_token_pairs(raw: dict) -> list[tuple[str, str]]:
    tokens = raw.get("tokens") or []
    if isinstance(tokens, str):
        try:
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


def _pair_outcomes_with_tokens(outcomes: list[str], token_ids: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for idx, outcome in enumerate(outcomes):
        token_id = token_ids[idx] if idx < len(token_ids) else ""
        pairs.append((outcome, token_id))
    return pairs


def _resolve_token_pairs(raw: dict) -> list[tuple[str, str]]:
    pairs = _extract_outcome_token_pairs(raw)
    if pairs:
        return pairs
    outcomes = _parse_outcomes(raw)
    token_ids = _parse_token_ids(raw)
    return _pair_outcomes_with_tokens(outcomes, token_ids)


def _best_price(levels, *, reverse: bool) -> float | None:
    best = None
    for level in levels or []:
        try:
            price = float(getattr(level, "price", None))
        except (TypeError, ValueError):
            continue
        if best is None:
            best = price
        elif reverse and price > best:
            best = price
        elif not reverse and price < best:
            best = price
    return best


def _parse_book(book) -> OrderBookView:
    best_bid = _best_price(getattr(book, "bids", None), reverse=True)
    best_ask = _best_price(getattr(book, "asks", None), reverse=False)
    min_order_size = None
    try:
        min_order_size = float(getattr(book, "min_order_size", None))
    except (TypeError, ValueError):
        min_order_size = None
    return OrderBookView(best_bid=best_bid, best_ask=best_ask, min_order_size=min_order_size)


def _parse_price(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("price")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_fixture(fixture: Fixture) -> str:
    start = fixture.start_time.isoformat() if fixture.start_time else "unknown"
    return f"{fixture.team_a_name} vs {fixture.team_b_name} @ {start} [{fixture.market_type}]"


def _extract_order_id(raw: dict | None, fallback: str | None) -> str | None:
    if fallback:
        return fallback
    if not raw:
        return None
    for key in ("orderId", "orderID", "id"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _poll_order(
    executor: ClobExecutor,
    order_id: str,
    *,
    label: str,
    attempts: int = 5,
    delay_seconds: float = 2.0,
) -> dict | None:
    last = None
    for _ in range(attempts):
        try:
            last = executor.get_order(order_id)
        except Exception:
            last = None
        if isinstance(last, dict) and last.get("status"):
            return last
        time.sleep(delay_seconds)
    print(f"{label} status unavailable after {attempts} attempts.")
    return last


def _fetch_fixtures(session, team_a: str, team_b: str) -> list[Fixture]:
    pattern_a = f"%{team_a}%"
    pattern_b = f"%{team_b}%"
    stmt = (
        select(Fixture)
        .where(Fixture.source == "polymarket")
        .where(Fixture.market_type == "match_winner")
        .where(Fixture.status.in_(STATUS_ALLOWLIST))
        .where(
            or_(
                (Fixture.team_a_name.ilike(pattern_a) & Fixture.team_b_name.ilike(pattern_b)),
                (Fixture.team_a_name.ilike(pattern_b) & Fixture.team_b_name.ilike(pattern_a)),
            )
        )
        .order_by(Fixture.start_time.asc())
    )
    return list(session.execute(stmt).scalars().all())


def _choose_fixture(fixtures: list[Fixture]) -> Fixture | None:
    if not fixtures:
        return None
    if len(fixtures) == 1:
        return fixtures[0]
    print("Multiple fixtures found:")
    for idx, fixture in enumerate(fixtures, start=1):
        print(f"  {idx}) {_format_fixture(fixture)}")
    while True:
        choice = input("Choose fixture [1]: ").strip()
        if not choice:
            return fixtures[0]
        try:
            idx = int(choice)
        except ValueError:
            print("Enter a number.")
            continue
        if 1 <= idx <= len(fixtures):
            return fixtures[idx - 1]
        print("Out of range.")


def _choose_token(
    pairs: list[tuple[str, str]],
    *,
    side: str | None,
    interactive: bool,
) -> tuple[str, str]:
    if side in {"a", "b"} and len(pairs) >= 2:
        return pairs[0] if side == "a" else pairs[1]
    print("Available outcomes:")
    for idx, (outcome, token_id) in enumerate(pairs, start=1):
        print(f"  {idx}) {outcome} (token_id={token_id})")
    if not interactive:
        return pairs[0]
    while True:
        try:
            choice = input("Choose outcome [1]: ").strip()
        except EOFError:
            return pairs[0]
        if not choice:
            return pairs[0]
        try:
            idx = int(choice)
        except ValueError:
            print("Enter a number.")
            continue
        if 1 <= idx <= len(pairs):
            return pairs[idx - 1]
        print("Out of range.")


def _clamp_order_size(size: float, price: float, *, enforce_max_usd: bool) -> float:
    if size > settings.live_max_shares_per_order:
        size = settings.live_max_shares_per_order
    if enforce_max_usd:
        max_usd = settings.live_max_usd_per_order
        if max_usd > 0 and (price * size) > max_usd:
            size = max_usd / price
    return size


def _round_size(size: float, *, decimals: int = 2) -> float:
    factor = 10**decimals
    return math.floor(size * factor) / factor


def _to_decimal(value: float | str) -> Decimal:
    return Decimal(str(value))


def _round_down_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket CLOB buy+sell round trip test.")
    parser.add_argument("--team-a", default=DEFAULT_TEAM_A)
    parser.add_argument("--team-b", default=DEFAULT_TEAM_B)
    parser.add_argument("--side", choices=["a", "b"], default=None)
    parser.add_argument("--buy-usd", type=float, default=None)
    parser.add_argument("--shares", type=float, default=None)
    parser.add_argument("--skip-sell", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--force-min", action="store_true")
    args = parser.parse_args()
    interactive = sys.stdin.isatty()
    if not interactive and not args.non_interactive:
        print("No interactive stdin detected. Run in a real terminal or pass --non-interactive.")
        return 1
    interactive = interactive or args.non_interactive

    print("Decrypting keyfile for live signing.")
    private_key = decrypt_age_keyfile(settings.polymarket_keyfile_path)
    executor = ClobExecutor(
        private_key=private_key,
        funder=settings.polymarket_funder_address,
        signature_type=settings.polymarket_signature_type,
        chain_id=settings.polymarket_chain_id,
        clob_url=settings.polymarket_clob_url,
    )

    with SessionLocal() as session:
        fixtures = _fetch_fixtures(session, args.team_a, args.team_b)
        fixture = _choose_fixture(fixtures)
        if fixture is None:
            print("No matching Polymarket fixture found in DB.")
            return 1
        print(f"Selected fixture: {_format_fixture(fixture)}")

    raw = fixture.raw_json or {}
    pairs = _resolve_token_pairs(raw)
    pairs = [(outcome, token_id) for outcome, token_id in pairs if token_id]
    if not pairs:
        print("No token ids found for fixture.")
        return 1
    outcome, token_id = _choose_token(pairs, side=args.side, interactive=interactive)
    print(f"Using outcome: {outcome} (token_id={token_id})")

    book = executor.client.get_order_book(token_id)
    book_view = _parse_book(book)
    buy_price = book_view.best_ask
    sell_price = book_view.best_bid
    if buy_price is None:
        buy_price = _parse_price(executor.client.get_price(token_id, "BUY"))
    if sell_price is None:
        sell_price = _parse_price(executor.client.get_price(token_id, "SELL"))
    tick_size = executor.client.get_tick_size(token_id)
    print(f"Best bid: {book_view.best_bid} | Best ask: {book_view.best_ask}")
    print(f"Fallback BUY price: {buy_price} | Fallback SELL price: {sell_price}")
    print(f"Min order size: {book_view.min_order_size}")
    if buy_price is None or sell_price is None:
        print("Missing bid/ask pricing; aborting.")
        return 1

    tick_size_dec = _to_decimal(tick_size)
    buy_price_dec = _round_down_step(_to_decimal(buy_price), tick_size_dec)
    sell_price_dec = _round_down_step(_to_decimal(sell_price), tick_size_dec)
    if buy_price_dec <= 0 or sell_price_dec <= 0:
        print("Invalid price after tick size rounding; aborting.")
        return 1

    min_order_size = book_view.min_order_size or DEFAULT_MIN_SHARES
    min_order_size_dec = _to_decimal(min_order_size)
    if args.buy_usd is not None and args.buy_usd > 0:
        base_size = _to_decimal(args.buy_usd) / buy_price_dec
    elif args.shares:
        base_size = _to_decimal(args.shares)
    else:
        base_size = min_order_size_dec
    enforce_max_usd = args.buy_usd is not None and args.buy_usd > 0
    base_size = _to_decimal(
        _clamp_order_size(float(base_size), float(buy_price_dec), enforce_max_usd=enforce_max_usd)
    )
    if base_size <= 0:
        print("Computed order size is invalid.")
        return 1

    max_usd = _to_decimal(settings.live_max_usd_per_order)
    buy_usd = buy_price_dec * base_size
    if enforce_max_usd and max_usd > 0 and buy_usd > max_usd:
        buy_usd = max_usd
    buy_usd = buy_usd.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    min_usd = (buy_price_dec * min_order_size_dec).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    if buy_usd < min_usd and not args.force_min:
        print(
            f"Buy notional {buy_usd} below minimum {min_usd}. "
            "Increase live_max_usd_per_order, pass --shares >= min_order_size, "
            "or use --force-min to attempt anyway."
        )
        return 1

    size_dec = (buy_usd / buy_price_dec).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    if size_dec < min_order_size_dec and not args.force_min:
        size_dec = min_order_size_dec
        buy_usd = (size_dec * buy_price_dec).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    allowance = executor.check_allowance_buy(float(buy_usd))
    if not allowance.ok:
        print(f"USDC allowance check failed: {allowance.reason}")
        return 1

    if executor.check_kill_switch():
        print("Kill switch is active; aborting.")
        return 1

    if not _prompt_yes_no(
        f"Place BUY {float(size_dec):.4f} @ {float(buy_price_dec):.4f} "
        f"(~${float(buy_usd):.2f})?",
        interactive=interactive,
    ):
        print("Cancelled.")
        return 0

    if args.buy_usd is not None and args.buy_usd > 0:
        market_args = MarketOrderArgs(
            token_id=token_id,
            amount=float(buy_usd),
            side="BUY",
            price=float(buy_price_dec),
            order_type=OrderType.FAK,
        )
        signed = executor.client.create_market_order(market_args)
        response = executor.client.post_order(signed, OrderType.FAK)
        buy_success = bool(response.get("success", True))
        buy_order_id = response.get("orderId")
        buy_raw = response
    else:
        buy_response = executor.place_fak_order(
            token_id=token_id,
            side="BUY",
            price=float(buy_price_dec),
            amount=float(buy_usd),
            tick_size=float(tick_size_dec),
        )
        buy_success = buy_response.success
        buy_order_id = buy_response.order_id
        buy_raw = buy_response.raw
    buy_order_id = _extract_order_id(buy_raw, buy_order_id)
    print(f"BUY response: success={buy_success} order_id={buy_order_id}")
    if buy_raw:
        print(f"BUY raw: {buy_raw}")
    if not buy_success or not buy_order_id:
        print("Buy failed or missing order id; aborting.")
        return 1

    buy_details = _poll_order(executor, buy_order_id, label="BUY")
    print(f"BUY status: {buy_details}")

    if args.skip_sell:
        print("Skipping sell per flag.")
        return 0

    book = executor.client.get_order_book(token_id)
    book_view = _parse_book(book)
    sell_price = book_view.best_bid
    if sell_price is None:
        sell_price = _parse_price(executor.client.get_price(token_id, "SELL"))
    if sell_price is None:
        print("Missing bid pricing; aborting sell.")
        return 1
    sell_price_dec = _round_down_step(_to_decimal(sell_price), tick_size_dec)
    if sell_price_dec <= 0:
        print("Invalid sell price after tick size rounding; aborting.")
        return 1

    sell_allowance = executor.check_allowance_sell(token_id, float(size_dec))
    if not sell_allowance.ok:
        print(f"Token allowance check failed: {sell_allowance.reason}")
        return 1

    if executor.check_kill_switch():
        print("Kill switch is active; aborting.")
        return 1

    if not _prompt_yes_no(
        f"Place SELL {float(size_dec):.4f} @ {float(sell_price_dec):.4f}?",
        interactive=interactive,
    ):
        print("Cancelled.")
        return 0

    sell_response = executor.place_fak_order(
        token_id=token_id,
        side="SELL",
        price=float(sell_price_dec),
        amount=float(size_dec),
        tick_size=float(tick_size_dec),
    )
    sell_order_id = _extract_order_id(sell_response.raw, sell_response.order_id)
    print(f"SELL response: success={sell_response.success} order_id={sell_order_id}")
    if sell_response.raw:
        print(f"SELL raw: {sell_response.raw}")
    if not sell_response.success or not sell_order_id:
        print("Sell failed or missing order id.")
        return 1

    sell_details = _poll_order(executor, sell_order_id, label="SELL")
    print(f"SELL status: {sell_details}")

    pnl = (sell_price_dec - buy_price_dec) * size_dec
    print(f"Round-trip estimate: {float(pnl):+.4f} (spread-based, not fees)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
