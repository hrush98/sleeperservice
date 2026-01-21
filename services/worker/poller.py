import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from shared.models import Market, Outcome, QuoteSnapshot, SettlementSpec
from worker.polymarket_client import fetch_markets
from worker.spec_hash import compute_spec_version_hash

logger = logging.getLogger(__name__)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        cleaned = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None
    return None


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_platform_market_id(payload: dict) -> str | None:
    for key in ("id", "marketId", "slug"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _get_title(payload: dict) -> str:
    return payload.get("question") or payload.get("title") or ""


def _get_outcomes(payload: dict) -> list[dict[str, Any]]:
    outcomes = payload.get("outcomes")
    if isinstance(outcomes, list) and outcomes:
        if all(isinstance(item, str) for item in outcomes):
            return [{"name": item} for item in outcomes]
        if all(isinstance(item, dict) for item in outcomes):
            return outcomes
    if payload.get("isBinary") is True:
        return [{"name": "Yes"}, {"name": "No"}]
    return []


def upsert_market(session: Session, payload: dict) -> str | None:
    platform_market_id = _get_platform_market_id(payload)
    if not platform_market_id:
        return None

    stmt = (
        insert(Market)
        .values(
            platform="polymarket",
            platform_market_id=platform_market_id,
            title=_get_title(payload),
            description=payload.get("description"),
            url=payload.get("url") or payload.get("link"),
            status=payload.get("status"),
            open_time=_parse_datetime(payload.get("startTime")),
            close_time=_parse_datetime(payload.get("endTime") or payload.get("closeTime")),
            raw_json=payload,
            updated_at=_now_utc(),
        )
        .on_conflict_do_update(
            constraint="uq_markets_platform_market",
            set_={
                "title": _get_title(payload),
                "description": payload.get("description"),
                "url": payload.get("url") or payload.get("link"),
                "status": payload.get("status"),
                "open_time": _parse_datetime(payload.get("startTime")),
                "close_time": _parse_datetime(payload.get("endTime") or payload.get("closeTime")),
                "raw_json": payload,
                "updated_at": _now_utc(),
            },
        )
        .returning(Market.id)
    )
    result = session.execute(stmt)
    market_id = result.scalar_one()
    return str(market_id)


def upsert_outcomes(session: Session, market_id: str, payload: dict) -> dict[str, str]:
    outcomes = _get_outcomes(payload)
    if not outcomes:
        return {}

    outcome_ids: dict[str, str] = {}
    for outcome in outcomes:
        name = outcome.get("name") or outcome.get("label") or outcome.get("outcome")
        if not name:
            continue
        platform_outcome_id = outcome.get("id") or outcome.get("tokenId")
        stmt = (
            insert(Outcome)
            .values(
                market_id=market_id,
                outcome_name=str(name),
                platform_outcome_id=str(platform_outcome_id) if platform_outcome_id else None,
                raw_json=outcome,
            )
            .on_conflict_do_update(
                constraint="uq_outcomes_market_name",
                set_={
                    "platform_outcome_id": str(platform_outcome_id) if platform_outcome_id else None,
                    "raw_json": outcome,
                },
            )
            .returning(Outcome.id)
        )
        result = session.execute(stmt)
        outcome_ids[str(name)] = str(result.scalar_one())

    return outcome_ids


def upsert_settlement_spec(session: Session, market_id: str, payload: dict) -> None:
    criteria_text = payload.get("rules") or payload.get("resolutionCriteria") or payload.get("question")
    source = payload.get("resolutionSource") or payload.get("source")
    resolution_time = _parse_datetime(payload.get("resolutionTime"))
    spec_version_hash = compute_spec_version_hash(source, resolution_time, criteria_text)

    stmt = insert(SettlementSpec).values(
        market_id=market_id,
        source=source,
        resolution_time=resolution_time,
        criteria_text=criteria_text,
        spec_version_hash=spec_version_hash,
        raw_json={
            "source": source,
            "criteria_text": criteria_text,
            "resolution_time": resolution_time.isoformat() if resolution_time else None,
            "raw": payload,
        },
    )
    stmt = stmt.on_conflict_do_nothing(constraint="uq_settlement_spec_version")
    session.execute(stmt)


def _quote_price_candidates(payload: dict) -> float | None:
    for key in ("lastTradePrice", "price", "probability"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def insert_quote_snapshots(
    session: Session,
    market_id: str,
    payload: dict,
    outcome_ids_by_name: dict[str, str],
) -> int:
    inserted = 0
    ts = _parse_datetime(payload.get("lastUpdated") or payload.get("updatedAt")) or _now_utc()
    volume_24h = payload.get("volume24h") or payload.get("volume")
    liquidity = payload.get("liquidity")
    outcome_prices = payload.get("outcomePrices")

    if isinstance(outcome_prices, list) and outcome_ids_by_name:
        outcome_names = list(outcome_ids_by_name.keys())
        for name, price in zip(outcome_names, outcome_prices):
            outcome_id = outcome_ids_by_name.get(name)
            if outcome_id is None:
                continue
            stmt = insert(QuoteSnapshot).values(
                market_id=market_id,
                outcome_id=outcome_id,
                ts=ts,
                price=float(price) if price is not None else None,
                volume_24h=float(volume_24h) if isinstance(volume_24h, (int, float)) else None,
                liquidity=float(liquidity) if isinstance(liquidity, (int, float)) else None,
                raw_json={"market": payload, "outcome": name, "price": price},
            )
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["market_id", "outcome_id", "ts"],
                index_where=QuoteSnapshot.outcome_id.is_not(None),
            )
            result = session.execute(stmt)
            inserted += result.rowcount or 0
        return inserted

    price = _quote_price_candidates(payload)
    if price is None:
        return inserted

    stmt = insert(QuoteSnapshot).values(
        market_id=market_id,
        outcome_id=None,
        ts=ts,
        price=price,
        volume_24h=float(volume_24h) if isinstance(volume_24h, (int, float)) else None,
        liquidity=float(liquidity) if isinstance(liquidity, (int, float)) else None,
        raw_json={"market": payload, "price": price},
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["market_id", "ts"],
        index_where=QuoteSnapshot.outcome_id.is_(None),
    )
    result = session.execute(stmt)
    inserted += result.rowcount or 0
    return inserted


def poll_once(session: Session, base_url: str) -> None:
    markets = fetch_markets(base_url=base_url)
    for payload in markets:
        market_id = upsert_market(session, payload)
        if market_id is None:
            logger.warning("Skipping market without platform id")
            continue
        outcome_ids = upsert_outcomes(session, market_id, payload)
        upsert_settlement_spec(session, market_id, payload)
        inserted = insert_quote_snapshots(session, market_id, payload, outcome_ids)
        logger.info("Market %s: outcomes=%s quotes_inserted=%s", market_id, len(outcome_ids), inserted)


def run_poll_loop(session_factory, base_url: str, interval_seconds: int) -> None:
    while True:
        try:
            with session_factory() as session:
                poll_once(session, base_url)
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Polling iteration failed")
        logger.info("Sleeping for %s seconds", interval_seconds)
        from time import sleep

        sleep(interval_seconds)
