"""
Edge math utilities for live monitor (v0).
"""

from __future__ import annotations

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

