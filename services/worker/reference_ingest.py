import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from shared.models import ExternalMatch, ExternalOddsSnapshot
from worker.oddspapi_client import (
    extract_moneyline_prices_pinnacle,
    fetch_active_fixtures,
    fetch_odds,
)

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        cleaned = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None
    return None


def _implied_prob(odds_decimal: float | None) -> float | None:
    if odds_decimal is None:
        return None
    if odds_decimal <= 0:
        return None
    return 1.0 / odds_decimal


def upsert_external_match(
    session: Session,
    source: str,
    external_match_id: str,
    league: str | None,
    start_time: datetime | None,
    team_a: str,
    team_b: str,
    raw_json: dict,
) -> str:
    stmt = (
        insert(ExternalMatch)
        .values(
            source=source,
            external_match_id=external_match_id,
            league=league,
            start_time=start_time,
            team_a=team_a,
            team_b=team_b,
            raw_json=raw_json,
            updated_at=_now_utc(),
        )
        .on_conflict_do_update(
            constraint="uq_external_matches_source_match",
            set_={
                "league": league,
                "start_time": start_time,
                "team_a": team_a,
                "team_b": team_b,
                "raw_json": raw_json,
                "updated_at": _now_utc(),
            },
        )
        .returning(ExternalMatch.id)
    )
    result = session.execute(stmt)
    return str(result.scalar_one())


def insert_external_odds_snapshot(
    session: Session,
    external_match_pk: str,
    ts: datetime,
    market_type: str,
    selection: str,
    odds_decimal: float | None,
    raw_json: dict,
) -> bool:
    stmt = insert(ExternalOddsSnapshot).values(
        external_match_id=external_match_pk,
        ts=ts,
        market_type=market_type,
        selection=selection,
        odds_decimal=odds_decimal,
        odds_american=None,
        implied_prob=_implied_prob(odds_decimal),
        raw_json=raw_json,
    )
    stmt = stmt.on_conflict_do_nothing(constraint="uq_external_odds_snapshots_natural")
    result = session.execute(stmt)
    return bool(result.rowcount)


def poll_fixtures_and_odds(
    session: Session,
    base_url: str,
    api_key: str | None,
    source: str = "oddspapi_pinnacle",
    sport: str = "Esports",
    max_fixtures: int = 25,
) -> None:
    fixtures = fetch_active_fixtures(base_url=base_url, api_key=api_key, sport=sport)
    if max_fixtures and len(fixtures) > max_fixtures:
        fixtures = fixtures[:max_fixtures]

    logger.info("OddsPapi: fetched %s active fixtures (sport=%s)", len(fixtures), sport)

    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        fixture_id = fixture.get("id")
        team_a = fixture.get("home")
        team_b = fixture.get("away")
        if fixture_id is None or not team_a or not team_b:
            continue

        external_match_pk = upsert_external_match(
            session=session,
            source=source,
            external_match_id=str(fixture_id),
            league=fixture.get("league"),
            start_time=_parse_datetime(fixture.get("start_date")),
            team_a=str(team_a),
            team_b=str(team_b),
            raw_json=fixture,
        )

        odds_payload = fetch_odds(base_url=base_url, api_key=api_key, fixture_id=str(fixture_id))
        rows = extract_moneyline_prices_pinnacle(odds_payload)
        inserted = 0
        for row in rows:
            selection_key = row.get("selection")
            odds_decimal = row.get("odds_decimal")
            changed_at = row.get("changed_at") or _now_utc()

            if selection_key == "home":
                selection = str(team_a)
            elif selection_key == "away":
                selection = str(team_b)
            else:
                selection = str(selection_key) if selection_key is not None else "unknown"

            did_insert = insert_external_odds_snapshot(
                session=session,
                external_match_pk=external_match_pk,
                ts=changed_at,
                market_type="match_winner_moneyline",
                selection=selection,
                odds_decimal=odds_decimal,
                raw_json={
                    "fixture": fixture,
                    "odds": odds_payload,
                    "row": row,
                },
            )
            if did_insert:
                inserted += 1

        logger.info(
            "OddsPapi fixture %s (%s vs %s): odds_rows=%s inserted=%s",
            fixture_id,
            team_a,
            team_b,
            len(rows),
            inserted,
        )


