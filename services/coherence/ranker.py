"""M3 orderbook-aware ranking for coherence violations."""

from __future__ import annotations

from coherence.models import RankedOpportunity, Violation
from shared.polymarket_client import PolymarketClient


def rank_violations_with_orderbook(
    violations: list[Violation],
    target_sizes_usd: list[float],
    min_edge_cents: float = 0.0,
    client: PolymarketClient | None = None,
) -> list[RankedOpportunity]:
    """Price both legs at target sizes using ask ladders, then rank by edge."""
    if not violations or not target_sizes_usd:
        return []

    owned_client = client is None
    client = client or PolymarketClient()
    try:
        token_ids = _collect_token_ids(violations)
        books = client.get_orderbooks_batch(token_ids)
        opportunities: list[RankedOpportunity] = []
        for violation in violations:
            for size in target_sizes_usd:
                short_no_price, short_ok = _avg_fill_price(
                    books.get(violation.short_market.no_token_id, {}),
                    side="no",
                    target_size_usd=size,
                )
                long_yes_price, long_ok = _avg_fill_price(
                    books.get(violation.long_market.yes_token_id, {}),
                    side="yes",
                    target_size_usd=size,
                )
                filled = short_ok and long_ok
                pair_cost = short_no_price + long_yes_price if filled else 2.0
                edge_cents = (1.0 - pair_cost) * 100.0
                if edge_cents < min_edge_cents:
                    continue
                opportunities.append(
                    RankedOpportunity(
                        violation=violation,
                        target_size_usd=size,
                        short_no_avg_price=short_no_price,
                        long_yes_avg_price=long_yes_price,
                        edge_cents_post_slippage=edge_cents,
                        filled=filled,
                    )
                )
        opportunities.sort(key=lambda item: item.edge_cents_post_slippage, reverse=True)
        return opportunities
    finally:
        if owned_client:
            client.close()


def _collect_token_ids(violations: list[Violation]) -> list[str]:
    token_ids: list[str] = []
    seen: set[str] = set()
    for violation in violations:
        for token_id in (violation.short_market.no_token_id, violation.long_market.yes_token_id):
            if token_id and token_id not in seen:
                seen.add(token_id)
                token_ids.append(token_id)
    return token_ids


def _avg_fill_price(book: dict, side: str, target_size_usd: float) -> tuple[float, bool]:
    asks = book.get("asks") if isinstance(book, dict) else None
    if not isinstance(asks, list) or not asks or target_size_usd <= 0:
        return 1.0, False

    remaining = target_size_usd
    spent = 0.0
    shares = 0.0
    for level in asks:
        try:
            ask_price = float(level.get("price", 0))
            ask_size = float(level.get("size", 0))
        except (TypeError, ValueError):
            continue
        if ask_price <= 0 or ask_price >= 1 or ask_size <= 0:
            continue
        # Buying No at displayed Yes ask costs 1 - ask_yes.
        unit_price = ask_price if side == "yes" else (1.0 - ask_price)
        if unit_price <= 0:
            continue
        level_capacity = unit_price * ask_size
        take_value = min(level_capacity, remaining)
        take_shares = take_value / unit_price
        spent += take_value
        shares += take_shares
        remaining -= take_value
        if remaining <= 1e-9:
            break
    if remaining > 1e-9 or shares <= 0:
        return 1.0, False
    return spent / shares, True

