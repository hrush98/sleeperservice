import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from shared.models import (
    DisagreementEvent,
    ExternalMatch,
    ExternalOddsSnapshot,
    ExternalPolymarketMapping,
    QuoteSnapshot,
    ShadowOrder,
)

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def emit_shadow_signals(
    session: Session,
    source: str = "oddspapi_pinnacle",
    gap_threshold: float = 0.05,
    min_event_interval_seconds: int = 5,
    min_gap_delta: float = 0.01,
) -> tuple[int, int]:
    """
    Minimal shadow execution:
    - For each outcome-level mapping, compare latest reference implied_prob vs latest Polymarket price.
    - If abs(gap) >= threshold: record disagreement_event + shadow_order.

    This is intentionally conservative and deduped to avoid spamming.
    """
    mappings = session.execute(
        select(ExternalPolymarketMapping)
        .where(ExternalPolymarketMapping.source == source)
        .where(ExternalPolymarketMapping.polymarket_outcome_id.is_not(None))
        .order_by(ExternalPolymarketMapping.updated_at.desc())
        .limit(500)
    ).scalars().all()

    inserted_events = 0
    inserted_orders = 0
    now = _now_utc()

    for mapping in mappings:
        ext = session.get(ExternalMatch, mapping.external_match_id)
        if ext is None:
            continue

        side_hint = None
        if isinstance(mapping.raw_json, dict):
            side_hint = mapping.raw_json.get("side")

        if side_hint == "team_a":
            selection = ext.team_a
        elif side_hint == "team_b":
            selection = ext.team_b
        else:
            selection = ext.team_a

        ref = session.execute(
            select(ExternalOddsSnapshot)
            .where(ExternalOddsSnapshot.external_match_id == ext.id)
            .where(ExternalOddsSnapshot.market_type == "match_winner_moneyline")
            .where(ExternalOddsSnapshot.selection == selection)
            .order_by(ExternalOddsSnapshot.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        if ref is None or ref.implied_prob is None:
            continue

        poly = session.execute(
            select(QuoteSnapshot)
            .where(QuoteSnapshot.outcome_id == mapping.polymarket_outcome_id)
            .order_by(QuoteSnapshot.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        if poly is None or poly.price is None:
            continue

        gap = float(ref.implied_prob) - float(poly.price)
        if abs(gap) < gap_threshold:
            continue

        last = session.execute(
            select(DisagreementEvent)
            .where(DisagreementEvent.external_match_id == ext.id)
            .where(DisagreementEvent.polymarket_market_id == mapping.polymarket_market_id)
            .where(DisagreementEvent.polymarket_outcome_id == mapping.polymarket_outcome_id)
            .order_by(DisagreementEvent.ts.desc())
            .limit(1)
        ).scalar_one_or_none()

        if last is not None:
            if (now - last.ts) < timedelta(seconds=min_event_interval_seconds) and abs(gap - last.gap) < min_gap_delta:
                continue

        event = {
            "ts": now,
            "external_match_id": ext.id,
            "polymarket_market_id": mapping.polymarket_market_id,
            "polymarket_outcome_id": mapping.polymarket_outcome_id,
            "ref_implied_prob": float(ref.implied_prob),
            "poly_mid": float(poly.price),
            "poly_best_bid": None,
            "poly_best_ask": None,
            "gap": gap,
            "edge": None,
            "raw_json": {
                "external_odds_snapshot_id": str(ref.id),
                "polymarket_quote_snapshot_id": str(poly.id),
                "selection": selection,
                "source": source,
            },
        }
        session.execute(insert(DisagreementEvent).values(**event))
        inserted_events += 1

        order_side = "buy" if gap > 0 else "sell"
        order = {
            "ts": now,
            "external_match_id": ext.id,
            "polymarket_market_id": mapping.polymarket_market_id,
            "polymarket_outcome_id": mapping.polymarket_outcome_id,
            "side": order_side,
            "price": float(poly.price),
            "size": None,
            "reason": "gap_threshold",
            "raw_json": {
                "gap": gap,
                "gap_threshold": gap_threshold,
                "ref_implied_prob": float(ref.implied_prob),
                "poly_price": float(poly.price),
            },
        }
        session.execute(insert(ShadowOrder).values(**order))
        inserted_orders += 1

    logger.info("Shadow engine: inserted events=%s orders=%s", inserted_events, inserted_orders)
    return inserted_events, inserted_orders


