import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from shared.models import ExternalMatch, ExternalPolymarketMapping, Market, Outcome

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _norm(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "", text.lower())
    return cleaned


def _title_has_team(title: str, team: str) -> bool:
    if not title or not team:
        return False
    return _norm(team) in _norm(title)


def _best_market_candidate(
    external: ExternalMatch, candidates: list[Market]
) -> tuple[Market | None, float, dict]:
    best: Market | None = None
    best_score = 0.0
    debug: dict = {"candidates": []}

    ext_league = _norm(external.league or "")
    for market in candidates:
        title = market.title or ""
        score = 0.0
        score += 1.0 if _title_has_team(title, external.team_a) else 0.0
        score += 1.0 if _title_has_team(title, external.team_b) else 0.0
        if ext_league and ext_league in _norm(title):
            score += 0.25

        debug["candidates"].append({"market_id": str(market.id), "title": title, "score": score})

        if score > best_score:
            best = market
            best_score = score

    confidence = min(1.0, best_score / 2.25) if best_score else 0.0
    return best, confidence, debug


def _upsert_mapping(
    session: Session,
    source: str,
    external_match_pk: str,
    polymarket_market_id: str,
    polymarket_outcome_id: str | None,
    confidence: float,
    method: str,
    raw_json: dict,
) -> None:
    stmt = (
        insert(ExternalPolymarketMapping)
        .values(
            source=source,
            external_match_id=external_match_pk,
            polymarket_market_id=polymarket_market_id,
            polymarket_outcome_id=polymarket_outcome_id,
            mapping_confidence=confidence,
            mapping_method=method,
            raw_json=raw_json,
            updated_at=_now_utc(),
        )
        .on_conflict_do_update(
            constraint="uq_external_polymarket_mapping",
            set_={
                "mapping_confidence": confidence,
                "mapping_method": method,
                "raw_json": raw_json,
                "updated_at": _now_utc(),
            },
        )
    )
    session.execute(stmt)


def resolve_mappings(
    session: Session,
    source: str = "oddspapi_pinnacle",
    lookback_hours: int = 24,
    min_confidence: float = 0.75,
) -> int:
    """
    Conservative heuristic mapper:
    - Match external.team_a + external.team_b as substrings in Polymarket market.title
    - Use league as a weak tie-breaker
    Writes market-level mapping + outcome-level mappings when outcomes match team names.
    """
    since = _now_utc() - timedelta(hours=lookback_hours)

    externals = session.execute(
        select(ExternalMatch)
        .where(ExternalMatch.source == source)
        .where(ExternalMatch.updated_at >= since)
        .order_by(ExternalMatch.updated_at.desc())
        .limit(200)
    ).scalars().all()

    markets = session.execute(
        select(Market).where(Market.platform == "polymarket").order_by(Market.updated_at.desc()).limit(2000)
    ).scalars().all()

    inserted_or_updated = 0
    for external in externals:
        candidates = [
            m for m in markets if _title_has_team(m.title or "", external.team_a) and _title_has_team(m.title or "", external.team_b)
        ]
        if not candidates:
            continue

        best, confidence, debug = _best_market_candidate(external, candidates)
        if best is None or confidence < min_confidence:
            continue

        method = "heuristic_title_contains_v1"
        _upsert_mapping(
            session=session,
            source=source,
            external_match_pk=str(external.id),
            polymarket_market_id=str(best.id),
            polymarket_outcome_id=None,
            confidence=confidence,
            method=method,
            raw_json={"external": external.raw_json, "market": best.raw_json, "debug": debug},
        )
        inserted_or_updated += 1

        outcomes = session.execute(
            select(Outcome).where(Outcome.market_id == best.id).order_by(Outcome.outcome_name.asc())
        ).scalars().all()

        for outcome in outcomes:
            if _norm(outcome.outcome_name) == _norm(external.team_a) or _title_has_team(
                outcome.outcome_name, external.team_a
            ):
                _upsert_mapping(
                    session=session,
                    source=source,
                    external_match_pk=str(external.id),
                    polymarket_market_id=str(best.id),
                    polymarket_outcome_id=str(outcome.id),
                    confidence=min(1.0, confidence),
                    method=method,
                    raw_json={"side": "team_a", "outcome": outcome.raw_json, "debug": debug},
                )
                inserted_or_updated += 1
            elif _norm(outcome.outcome_name) == _norm(external.team_b) or _title_has_team(
                outcome.outcome_name, external.team_b
            ):
                _upsert_mapping(
                    session=session,
                    source=source,
                    external_match_pk=str(external.id),
                    polymarket_market_id=str(best.id),
                    polymarket_outcome_id=str(outcome.id),
                    confidence=min(1.0, confidence),
                    method=method,
                    raw_json={"side": "team_b", "outcome": outcome.raw_json, "debug": debug},
                )
                inserted_or_updated += 1

    logger.info("Mapping resolver: upserted %s mapping rows", inserted_or_updated)
    return inserted_or_updated


