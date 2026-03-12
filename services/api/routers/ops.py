"""
Operations endpoints for LoL Lead-Lag Arbitrage Bot.

Only two endpoints:
- GET /ops/status — counts, last update times
- GET /ops/live — current live matches + gaps
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from services.api.dependencies import get_db
from services.shared.models import Fixture, League, Mapping, OddsSnapshot, Position, ShadowOrder, Team, TradeEvent

router = APIRouter(prefix="/ops", tags=["Operations"])


class StatusResponse(BaseModel):
    """System status response."""

    leagues: dict[str, int]
    teams: dict[str, int]
    fixtures: dict[str, int]
    mappings: dict[str, int]
    last_discovery: datetime | None
    live_matches: int
    odds_snapshots: int
    shadow_orders: int
    positions: int
    trade_events: int


class LiveMatchInfo(BaseModel):
    """Info about a live match."""

    mapping_id: str
    teams: str
    league: str | None
    pinnacle_prob_a: float | None
    pinnacle_prob_b: float | None
    polymarket_mid: float | None
    gap: float | None
    last_update: datetime | None


class LiveResponse(BaseModel):
    """Response for live matches."""

    matches: list[LiveMatchInfo]
    timestamp: datetime


@router.get("/status", response_model=StatusResponse)
def get_status(db: Session = Depends(get_db)):
    """
    Get current system status with counts and timestamps.

    Returns:
    - League counts by source
    - Team counts by source
    - Fixture counts by source
    - Mapping counts (total and high confidence)
    - Last discovery timestamp
    - Number of live matches
    - Total odds snapshots
    - Total shadow orders
    """

    def _count_by_source(model, source_col):
        oddspapi = db.execute(
            select(func.count()).select_from(model).where(source_col == "oddspapi")
        ).scalar_one()
        polymarket = db.execute(
            select(func.count()).select_from(model).where(source_col == "polymarket")
        ).scalar_one()
        return {"oddspapi": oddspapi, "polymarket": polymarket}

    leagues = _count_by_source(League, League.source)
    teams = _count_by_source(Team, Team.source)
    fixtures = _count_by_source(Fixture, Fixture.source)

    mappings_total = db.execute(select(func.count()).select_from(Mapping)).scalar_one()
    mappings_high = db.execute(
        select(func.count()).select_from(Mapping).where(Mapping.confidence >= 0.8)
    ).scalar_one()

    live_matches = db.execute(
        select(func.count()).select_from(Fixture).where(Fixture.status == "live")
    ).scalar_one()

    last_discovery = db.execute(select(func.max(Fixture.updated_at))).scalar_one()

    odds_snapshots = db.execute(select(func.count()).select_from(OddsSnapshot)).scalar_one()
    shadow_orders = db.execute(select(func.count()).select_from(ShadowOrder)).scalar_one()
    positions = db.execute(select(func.count()).select_from(Position)).scalar_one()
    trade_events = db.execute(select(func.count()).select_from(TradeEvent)).scalar_one()

    return StatusResponse(
        leagues=leagues,
        teams=teams,
        fixtures=fixtures,
        mappings={"total": mappings_total, "high_confidence": mappings_high},
        last_discovery=last_discovery,
        live_matches=live_matches,
        odds_snapshots=odds_snapshots,
        shadow_orders=shadow_orders,
        positions=positions,
        trade_events=trade_events,
    )


@router.get("/live", response_model=LiveResponse)
def get_live(db: Session = Depends(get_db)):
    """
    Get current live matches with latest odds comparison.

    Returns list of live matches with:
    - Team names and league
    - Latest Pinnacle implied probabilities
    - Latest Polymarket mid price
    - Current gap
    """
    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(hours=4)
    window_end = now + timedelta(minutes=30)

    # Get live or starting-soon fixtures
    live_fixtures = db.execute(
        select(Fixture).where(
            Fixture.source == "oddspapi",
            or_(
                Fixture.status == "live",
                Fixture.start_time.between(window_start, window_end),
            ),
        )
    ).scalars().all()

    live_fixture_ids = [f.id for f in live_fixtures]

    # Get mappings for these fixtures
    if not live_fixture_ids:
        return LiveResponse(matches=[], timestamp=now)

    mappings = db.execute(
        select(Mapping).where(Mapping.oddspapi_fixture_id.in_(live_fixture_ids))
    ).scalars().all()

    matches = []
    for mapping in mappings:
        op_fixture = db.get(Fixture, mapping.oddspapi_fixture_id)
        pm_fixture = db.get(Fixture, mapping.polymarket_fixture_id)

        if not op_fixture:
            continue

        # Get latest OddsPapi snapshot
        op_snapshot = db.execute(
            select(OddsSnapshot)
            .where(
                OddsSnapshot.fixture_id == op_fixture.id,
                OddsSnapshot.source == "oddspapi",
            )
            .order_by(OddsSnapshot.ts.desc())
            .limit(1)
        ).scalar_one_or_none()

        # Get latest Polymarket snapshot
        pm_snapshot = None
        if pm_fixture:
            pm_snapshot = db.execute(
                select(OddsSnapshot)
                .where(
                    OddsSnapshot.fixture_id == pm_fixture.id,
                    OddsSnapshot.source == "polymarket_clob",
                )
                .order_by(OddsSnapshot.ts.desc())
                .limit(1)
            ).scalar_one_or_none()

        # Calculate gap
        pinnacle_prob_a = op_snapshot.team_a_implied_prob if op_snapshot else None
        pinnacle_prob_b = op_snapshot.team_b_implied_prob if op_snapshot else None
        polymarket_mid = pm_snapshot.team_a_implied_prob if pm_snapshot else None

        gap = None
        if pinnacle_prob_a is not None and polymarket_mid is not None:
            gap = pinnacle_prob_a - polymarket_mid

        # Get league name
        league_name = None
        if op_fixture.league:
            league_name = op_fixture.league.name

        matches.append(
            LiveMatchInfo(
                mapping_id=str(mapping.id),
                teams=f"{op_fixture.team_a_name} vs {op_fixture.team_b_name}",
                league=league_name,
                pinnacle_prob_a=pinnacle_prob_a,
                pinnacle_prob_b=pinnacle_prob_b,
                polymarket_mid=polymarket_mid,
                gap=gap,
                last_update=op_snapshot.ts if op_snapshot else None,
            )
        )

    return LiveResponse(matches=matches, timestamp=now)
