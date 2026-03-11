"""
Edge math utilities for live monitor (v0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class NetEdgeResult:
    """Net edge values for both outcomes."""

    edge_buy_a: float | None
    edge_buy_b: float | None
    best_edge: float | None
    best_side: str | None


def devig_two_way_decimal(odds_a: float, odds_b: float) -> tuple[float, float]:
    """
    Convert two-way decimal odds into de-vigged probabilities.

    Args:
        odds_a: Decimal odds for outcome A.
        odds_b: Decimal odds for outcome B.
    """
    q_a = 1.0 / odds_a
    q_b = 1.0 / odds_b
    total = q_a + q_b
    if total <= 0:
        return 0.0, 0.0
    return q_a / total, q_b / total


def _bo3_series_prob(game_prob: float) -> float:
    """Probability of winning a Bo3 given per-game win probability."""
    q = min(max(game_prob, 0.0), 1.0)
    return (3.0 * q * q) - (2.0 * q * q * q)


def _bo5_series_prob(game_prob: float) -> float:
    """Probability of winning a Bo5 given per-game win probability."""
    q = min(max(game_prob, 0.0), 1.0)
    return (10.0 * q**3) - (15.0 * q**4) + (6.0 * q**5)


def series_prob_to_game_prob(p_series: float, series_type: str | None) -> float | None:
    """Infer per-game win probability from series moneyline probability.

    Uses binary search over closed-form Bo3/Bo5 series win equations.
    Returns ``None`` for bo1/unknown formats.
    """
    if series_type is None:
        return None

    normalized = series_type.lower().strip()
    if normalized == "bo1":
        return None
    if normalized == "bo3":
        series_prob_fn = _bo3_series_prob
    elif normalized == "bo5":
        series_prob_fn = _bo5_series_prob
    else:
        return None

    target = min(max(float(p_series), 0.0), 1.0)
    if target <= 0.0:
        return 0.0
    if target >= 1.0:
        return 1.0
    low = 0.0
    high = 1.0
    for _ in range(50):
        mid = (low + high) / 2.0
        mid_prob = series_prob_fn(mid)
        if mid_prob < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def compute_net_edges(
    p_ref_a: float | None,
    p_ref_b: float | None,
    bid_a: float | None,
    ask_a: float | None,
    bid_b: float | None,
    ask_b: float | None,
    spread_factor: float = 1.0,
) -> NetEdgeResult:
    """
    Compute conservative net edges using tradeable ask prices.

    edge_net_buy = p_ref - ask - spread_penalty
    spread_penalty = spread_factor * (ask - bid)
    """
    edge_a = None
    edge_b = None

    if p_ref_a is not None and ask_a is not None and bid_a is not None:
        spread_a = max(ask_a - bid_a, 0.0)
        edge_a = p_ref_a - ask_a - (spread_factor * spread_a)

    if p_ref_b is not None and ask_b is not None and bid_b is not None:
        spread_b = max(ask_b - bid_b, 0.0)
        edge_b = p_ref_b - ask_b - (spread_factor * spread_b)

    best_edge = None
    best_side = None
    for side, edge in (("buy_a", edge_a), ("buy_b", edge_b)):
        if edge is None:
            continue
        if best_edge is None or edge > best_edge:
            best_edge = edge
            best_side = side

    return NetEdgeResult(edge_buy_a=edge_a, edge_buy_b=edge_b, best_edge=best_edge, best_side=best_side)


def compute_alpha_entry(spread: float, min_alpha: float = 0.03, spread_factor: float = 1.5) -> float:
    """alpha = max(min_alpha, spread_factor * spread + 0.01)."""
    return max(min_alpha, (spread_factor * spread) + 0.01)


def compute_avg_fill_price(asks: list[tuple[float, float]], quantity: float) -> float | None:
    """Walk the ask ladder to compute expected average fill for quantity shares."""
    if quantity <= 0 or not asks:
        return None
    remaining = quantity
    total_cost = 0.0
    filled = 0.0
    for price, size in asks:
        if remaining <= 0:
            break
        take = min(size, remaining)
        total_cost += price * take
        filled += take
        remaining -= take
    if filled <= 0:
        return None
    return total_cost / filled


def compute_entry_edge(
    p_ref: float | None,
    asks: list[tuple[float, float]],
    quantity: float,
    alpha: float,
    tick_size: float = 0.01,
) -> dict[str, float | None | bool]:
    """
    Compute entry edge using depth-aware average fill.

    Returns:
        actionable: bool
        limit_price: float  (rounded down to tick_size)
        size_available: float
        avg_fill: float | None
        net_edge: float | None
    """
    if p_ref is None:
        return {
            "actionable": False,
            "limit_price": None,
            "size_available": 0.0,
            "avg_fill": None,
            "net_edge": None,
        }
    # round(..., 9) eliminates IEEE-754 noise before flooring and after multiply-back
    limit_price = round(math.floor(round((p_ref - alpha) / tick_size, 9)) * tick_size, 10)
    size_available = 0.0
    for price, size in asks:
        if price <= limit_price:
            size_available += size
        else:
            break
    avg_fill = compute_avg_fill_price(asks, min(quantity, size_available)) if size_available else None
    net_edge = (p_ref - avg_fill) if avg_fill is not None else None
    actionable = avg_fill is not None and net_edge is not None and net_edge >= alpha
    return {
        "actionable": actionable,
        "limit_price": limit_price,
        "size_available": size_available,
        "avg_fill": avg_fill,
        "net_edge": net_edge,
    }


def compute_exit_signal(p_ref: float | None, bid: float | None, epsilon: float = 0.01) -> bool:
    """Exit when p_ref - bid <= epsilon."""
    if p_ref is None or bid is None:
        return False
    return (p_ref - bid) <= epsilon


def check_thesis_death(p_ref: float | None, entry_price: float) -> bool:
    """True when the reference probability drops below entry price (thesis invalidated)."""
    if p_ref is None:
        return False
    return p_ref < entry_price


def check_hard_stop(bid: float | None, entry_price: float, stop_pct: float) -> bool:
    """True when bid falls ``stop_pct`` or more below entry price."""
    if bid is None:
        return False
    return bid <= entry_price * (1.0 - stop_pct)

