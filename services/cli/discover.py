"""
Discovery command for LoL Lead-Lag Arbitrage Bot.

Collects upcoming matches from OddsPapi and Polymarket,
then builds mappings between them.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from uuid import uuid4

import typer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from zoneinfo import ZoneInfo

from services.shared.config import settings
from services.shared.db import SessionLocal
from services.shared.goalserve_client import GoalserveClient
from services.shared.models import Fixture, League, Mapping, Team
from services.shared.oddspapi_client import OddsPapiClient
from services.shared.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

# Target leagues (normalized names for matching)
LOL_TARGET_LEAGUE_PATTERNS = settings.target_league_patterns
CS2_TARGET_LEAGUE_PATTERNS = settings.target_cs2_league_patterns
ALL_TARGET_LEAGUE_PATTERNS = list(
    dict.fromkeys([*LOL_TARGET_LEAGUE_PATTERNS, *CS2_TARGET_LEAGUE_PATTERNS])
)
TEAM_SUFFIXES = {"esports", "e-sports", "gaming", "team"}


@dataclass(frozen=True)
class SportConfig:
    display_name: str
    sport_code: str
    oddspapi_sport_id: int
    oddspapi_league_patterns: list[str]
    polymarket_team_league: str


SPORT_CONFIGS: tuple[SportConfig, ...] = (
    SportConfig(
        display_name="LoL",
        sport_code="lol",
        oddspapi_sport_id=settings.oddspapi_lol_sport_id,
        oddspapi_league_patterns=LOL_TARGET_LEAGUE_PATTERNS,
        polymarket_team_league="lol",
    ),
    SportConfig(
        display_name="CS2",
        sport_code="cs2",
        oddspapi_sport_id=settings.oddspapi_cs2_sport_id,
        oddspapi_league_patterns=CS2_TARGET_LEAGUE_PATTERNS,
        polymarket_team_league="counter-strike",
    ),
)


def _format_display_time(dt: datetime | None) -> str:
    """
    Render a timestamp for humans.

    We always store UTC in the DB; this converts for display only.
    """
    if not dt:
        return "TBD"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz_name = (getattr(settings, "display_timezone", None) or "UTC").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return dt.astimezone(tz).strftime("%b %d %H:%M %Z")


def normalize_name(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    normalized = "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in name)
    tokens = [t for t in normalized.split() if t and t not in TEAM_SUFFIXES]
    return " ".join(tokens)


def is_target_league(name: str, patterns: list[str] | None = None) -> bool:
    """Check if a league name matches our target leagues."""
    if patterns is None:
        patterns = ALL_TARGET_LEAGUE_PATTERNS
    normalized = normalize_name(name)
    return any(pattern in normalized for pattern in patterns)


def similarity(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def _extract_polymarket_league(market: dict) -> str | None:
    """Best-effort extraction of league label from a Polymarket market."""
    candidates = [
        market.get("groupItemTitle"),
        market.get("groupTitle"),
        market.get("league"),
        market.get("tournament"),
        market.get("question"),
        market.get("title"),
    ]
    for value in candidates:
        if not value:
            continue
        normalized = normalize_name(str(value))
        for pattern in ALL_TARGET_LEAGUE_PATTERNS:
            if pattern in normalized:
                return pattern
    return None


def _extract_event_start(event: dict, markets: list[dict], match_market: dict | None) -> datetime | None:
    """Infer event start time using market gameStartTime when available."""
    candidates: list[str | None] = []
    if match_market:
        candidates.extend(
            [
                match_market.get("gameStartTime"),
                match_market.get("game_start_time"),
            ]
        )
    for market in markets:
        if market.get("sportsMarketType") == "moneyline":
            candidates.append(market.get("gameStartTime") or market.get("game_start_time"))
            break
    for market in markets:
        candidates.append(market.get("gameStartTime") or market.get("game_start_time"))
    candidates.extend([event.get("startDateIso"), event.get("startDate")])
    for value in candidates:
        parsed = _parse_datetime(value)
        if parsed:
            return parsed
    return None


def _extract_oddspapi_league(fixture: Fixture) -> str | None:
    """Extract league label from OddsPapi fixture payload."""
    league_name = fixture.raw_json.get("tournamentName") if fixture.raw_json else None
    if not league_name:
        return None
    normalized = normalize_name(str(league_name))
    for pattern in ALL_TARGET_LEAGUE_PATTERNS:
        if pattern in normalized:
            return pattern
    return None


def _date_match_gate(op_fix: Fixture, pm_fix: Fixture) -> bool:
    """Hard date gate: require both start times and same UTC calendar date."""
    if not op_fix.start_time or not pm_fix.start_time:
        return False
    return op_fix.start_time.date() == pm_fix.start_time.date()


def discover_command(days: int, dry_run: bool = False, include_past: bool = False) -> None:
    """
    Main discovery flow:
    1. Fetch OddsPapi tournaments → filter to target leagues → store
    2. Fetch OddsPapi participants → store teams
    3. Fetch OddsPapi fixtures for each league → store
    4. Fetch Polymarket events/markets → store
    5. Build mappings by matching league + teams + date
    """
    typer.echo(f"\n🔍 Discovering LoL + CS2 matches for next {days} days...\n")

    polymarket = PolymarketClient()

    now = datetime.now(tz=timezone.utc)
    from_date = now - timedelta(hours=settings.discovery_lookback_hours)
    to_date = now + timedelta(days=days)

    stats = {
        "leagues_oddspapi": 0,
        "leagues_polymarket": 0,
        "teams_oddspapi": 0,
        "teams_polymarket": 0,
        "fixtures_oddspapi": 0,
        "fixtures_polymarket": 0,
        "leagues_oddspapi_by_sport": {cfg.sport_code: 0 for cfg in SPORT_CONFIGS},
        "leagues_polymarket_by_sport": {cfg.sport_code: 0 for cfg in SPORT_CONFIGS},
        "teams_oddspapi_by_sport": {cfg.sport_code: 0 for cfg in SPORT_CONFIGS},
        "teams_polymarket_by_sport": {cfg.sport_code: 0 for cfg in SPORT_CONFIGS},
        "fixtures_oddspapi_by_sport": {cfg.sport_code: 0 for cfg in SPORT_CONFIGS},
        "fixtures_polymarket_by_sport": {cfg.sport_code: 0 for cfg in SPORT_CONFIGS},
        "mappings_created": 0,
        "mappings_high_confidence": 0,
    }

    with SessionLocal() as db:
        oddspapi_fixtures = []
        for sport_cfg in SPORT_CONFIGS:
            typer.echo(f"\n📋 Fetching OddsPapi tournaments ({sport_cfg.display_name})...")
            oddspapi = OddsPapiClient(
                global_cooldown_ms=settings.oddspapi_global_cooldown_ms_discovery,
                sport_id=sport_cfg.oddspapi_sport_id,
            )
            tournaments = oddspapi.get_tournaments()
            target_tournaments = []
            for t in tournaments:
                name = t.get("tournamentName", "")
                if is_target_league(name, sport_cfg.oddspapi_league_patterns):
                    target_tournaments.append(t)
                    logger.info(
                        "Target league found (%s): %s (id=%s)",
                        sport_cfg.display_name,
                        name,
                        t.get("tournamentId"),
                    )

            stats["leagues_oddspapi_by_sport"][sport_cfg.sport_code] = len(target_tournaments)
            stats["leagues_oddspapi"] += len(target_tournaments)
            typer.echo(
                f"   {sport_cfg.display_name}: {len(target_tournaments)} target leagues from {len(tournaments)} total"
            )

            if not dry_run:
                for t in target_tournaments:
                    stmt = insert(League).values(
                        id=uuid4(),
                        source="oddspapi",
                        source_id=str(t.get("tournamentId")),
                        sport=sport_cfg.sport_code,
                        name=t.get("tournamentName", "Unknown"),
                        slug=t.get("tournamentSlug"),
                        raw_json=t,
                    ).on_conflict_do_update(
                        index_elements=["source", "source_id"],
                        set_={
                            "sport": sport_cfg.sport_code,
                            "name": t.get("tournamentName"),
                            "raw_json": t,
                        },
                    )
                    db.execute(stmt)
                db.commit()

            typer.echo(f"\n👥 Fetching OddsPapi participants ({sport_cfg.display_name})...")
            participants = oddspapi.get_participants()
            stats["teams_oddspapi_by_sport"][sport_cfg.sport_code] = len(participants)
            stats["teams_oddspapi"] += len(participants)
            typer.echo(f"   {sport_cfg.display_name}: {len(participants)} teams")

            if not dry_run and participants:
                for pid, pname in participants.items():
                    stmt = insert(Team).values(
                        id=uuid4(),
                        source="oddspapi",
                        source_id=str(pid),
                        name=pname,
                        raw_json={
                            "participantId": pid,
                            "name": pname,
                            "sport": sport_cfg.sport_code,
                        },
                    ).on_conflict_do_update(
                        index_elements=["source", "source_id"],
                        set_={"name": pname},
                    )
                    db.execute(stmt)
                db.commit()

            typer.echo(f"\n🎮 Fetching OddsPapi fixtures ({sport_cfg.display_name})...")
            sport_fixture_count = 0
            for t in target_tournaments:
                tid = t.get("tournamentId")
                tname = t.get("tournamentName", "Unknown")

                league = db.execute(
                    select(League).where(League.source == "oddspapi", League.source_id == str(tid))
                ).scalar_one_or_none()

                fixtures = oddspapi.get_fixtures(tid, from_date, to_date)
                typer.echo(f"   {tname}: {len(fixtures)} fixtures")

                for f in fixtures:
                    fixture_data = {
                        "source": "oddspapi",
                        "source_id": str(f.get("fixtureId")),
                        "league_id": league.id if league else None,
                        "team_a_name": f.get("participant1Name"),
                        "team_b_name": f.get("participant2Name"),
                        "start_time": _parse_datetime(f.get("startTime")),
                        "status": OddsPapiClient.infer_fixture_status(f),
                        "has_odds": bool(f.get("hasOdds")),
                        "line_value": None,
                        "raw_json": f,
                    }
                    oddspapi_fixtures.append(fixture_data)
                    sport_fixture_count += 1

                    if not dry_run:
                        stmt = insert(Fixture).values(id=uuid4(), **fixture_data).on_conflict_do_update(
                            index_elements=["source", "source_id"],
                            set_={
                                "team_a_name": fixture_data["team_a_name"],
                                "team_b_name": fixture_data["team_b_name"],
                                "start_time": fixture_data["start_time"],
                                "status": fixture_data["status"],
                                "has_odds": fixture_data["has_odds"],
                                "line_value": fixture_data["line_value"],
                                "raw_json": fixture_data["raw_json"],
                            },
                        )
                        db.execute(stmt)

                if not dry_run:
                    db.commit()

            stats["fixtures_oddspapi_by_sport"][sport_cfg.sport_code] = sport_fixture_count
            stats["fixtures_oddspapi"] += sport_fixture_count
            typer.echo(f"   {sport_cfg.display_name} total: {sport_fixture_count} OddsPapi fixtures")

        typer.echo(f"\n   Overall OddsPapi fixtures: {len(oddspapi_fixtures)}")

        # ============================================
        # Step 4a: Polymarket Sports → Leagues
        # ============================================
        typer.echo("\n🔮 Fetching Polymarket sports/leagues...")
        sports = polymarket.get_sports()
        sport_config_by_code = {cfg.sport_code: cfg for cfg in SPORT_CONFIGS}
        pm_target_leagues: list[tuple[SportConfig, dict]] = []
        for s in sports:
            sport_code = (s.get("sport") or "").lower()
            if sport_code in sport_config_by_code:
                cfg = sport_config_by_code[sport_code]
                pm_target_leagues.append((cfg, s))
                logger.info(
                    "Polymarket %s found: sport=%s, series=%s",
                    cfg.display_name,
                    sport_code,
                    s.get("series"),
                )

        for cfg in SPORT_CONFIGS:
            count = sum(1 for league_cfg, _ in pm_target_leagues if league_cfg.sport_code == cfg.sport_code)
            stats["leagues_polymarket_by_sport"][cfg.sport_code] = count
            stats["leagues_polymarket"] += count
            typer.echo(f"   {cfg.display_name}: {count} league(s)")

        if not dry_run:
            for cfg, s in pm_target_leagues:
                series_id = s.get("series") or s.get("id") or ""
                sport_name = s.get("sport") or "Unknown"
                stmt = insert(League).values(
                    id=uuid4(),
                    source="polymarket",
                    source_id=str(series_id),
                    sport=cfg.sport_code,
                    name=f"{cfg.display_name} ({sport_name.upper()})",
                    slug=sport_name,
                    raw_json=s,
                ).on_conflict_do_update(
                    index_elements=["source", "source_id"],
                    set_={
                        "sport": cfg.sport_code,
                        "name": f"{cfg.display_name} ({sport_name.upper()})",
                        "raw_json": s,
                    },
                )
                db.execute(stmt)
            db.commit()

        # ============================================
        # Step 4b: Polymarket Teams
        # ============================================
        typer.echo("\n👥 Fetching Polymarket teams...")
        for cfg in SPORT_CONFIGS:
            teams = polymarket.get_teams(league=cfg.polymarket_team_league)
            stats["teams_polymarket_by_sport"][cfg.sport_code] = len(teams)
            stats["teams_polymarket"] += len(teams)
            typer.echo(f"   {cfg.display_name}: {len(teams)} teams")

            if not dry_run and teams:
                for t in teams:
                    team_id = t.get("teamId") or t.get("id") or ""
                    stmt = insert(Team).values(
                        id=uuid4(),
                        source="polymarket",
                        source_id=str(team_id),
                        name=t.get("name") or t.get("teamName") or "Unknown",
                        abbreviation=t.get("abbreviation") or t.get("alias"),
                        raw_json=t,
                    ).on_conflict_do_update(
                        index_elements=["source", "source_id"],
                        set_={"name": t.get("name") or t.get("teamName"), "abbreviation": t.get("abbreviation")},
                    )
                    db.execute(stmt)
                db.commit()

        # ============================================
        # Step 4c: Polymarket Events → Fixtures
        # ============================================
        typer.echo("\n🎮 Fetching Polymarket events...")

        polymarket_fixtures = []
        for cfg in SPORT_CONFIGS:
            sport_leagues = [league for league_cfg, league in pm_target_leagues if league_cfg.sport_code == cfg.sport_code]
            sport_fixture_count = 0
            typer.echo(f"\n   {cfg.display_name}:")
            for league in sport_leagues:
                series_id = league.get("series") or league.get("id")

                # Get league from DB for linking
                pm_league_db = db.execute(
                    select(League).where(League.source == "polymarket", League.source_id == str(series_id))
                ).scalar_one_or_none()

                # Fetch open events with game bets tag (closed=False means not resolved yet)
                events = polymarket.get_events(
                    series_id=str(series_id) if series_id else None,
                    tag_id=settings.polymarket_game_bets_tag_id,
                    active=True,
                    closed=False,
                )
                typer.echo(f"     {len(events)} open events")

                # Extract markets from events
                for event in events:
                    markets = event.get("markets") or []
                    if not markets:
                        # If no nested markets, the event itself might be the market
                        markets = [event]

                    match_market: dict | None = None
                    game_markets: list[tuple[dict, int | None]] = []
                    totals_markets: list[tuple[dict, int | None, float | None]] = []

                    for m in markets:
                        market_class = _classify_polymarket_market(m)
                        if not market_class:
                            continue
                        market_type, game_number, line_value = market_class
                        if market_type == "match_winner":
                            match_market = m
                        elif market_type == "game_winner":
                            game_markets.append((m, game_number))
                        elif market_type == "totals":
                            totals_markets.append((m, game_number, line_value))

                    event_fixture_id = None
                    series_type = _extract_series_type(match_market) if match_market else None
                    event_start = _extract_event_start(event, markets, match_market)
                    event_team_a: str | None = None
                    event_team_b: str | None = None

                    if match_market:
                        team_a, team_b = _extract_polymarket_teams(match_market)
                        event_team_a, event_team_b = team_a, team_b
                        if team_a and team_b:
                            event_source_id = str(
                                event.get("id") or event.get("slug") or event.get("eventId") or ""
                            )
                            if event_source_id:
                                fixture_data = {
                                    "source": "polymarket",
                                    "source_id": event_source_id,
                                    "league_id": pm_league_db.id if pm_league_db else None,
                                    "team_a_name": team_a,
                                    "team_b_name": team_b,
                                    "start_time": event_start,
                                    "status": "upcoming"
                                    if event.get("active") and not event.get("closed")
                                    else "finished",
                                    "has_odds": False,
                                    "market_type": "event",
                                    "game_number": None,
                                    "line_value": None,
                                    "series_type": series_type,
                                    "parent_fixture_id": None,
                                    "raw_json": event,
                                }
                                polymarket_fixtures.append(fixture_data)
                                sport_fixture_count += 1

                                if not dry_run:
                                    stmt = insert(Fixture).values(
                                        id=uuid4(), **fixture_data
                                    ).on_conflict_do_update(
                                        index_elements=["source", "source_id"],
                                        set_={
                                            "league_id": fixture_data["league_id"],
                                            "team_a_name": fixture_data["team_a_name"],
                                            "team_b_name": fixture_data["team_b_name"],
                                            "start_time": fixture_data["start_time"],
                                            "status": fixture_data["status"],
                                            "market_type": fixture_data["market_type"],
                                            "game_number": fixture_data["game_number"],
                                            "line_value": fixture_data["line_value"],
                                            "series_type": fixture_data["series_type"],
                                            "parent_fixture_id": fixture_data["parent_fixture_id"],
                                            "raw_json": fixture_data["raw_json"],
                                        },
                                    )
                                    db.execute(stmt)
                                    parent = db.execute(
                                        select(Fixture).where(
                                            Fixture.source == "polymarket",
                                            Fixture.source_id == event_source_id,
                                        )
                                    ).scalar_one_or_none()
                                    if parent:
                                        event_fixture_id = parent.id

                    if match_market:
                        team_a, team_b = _extract_polymarket_teams(match_market)
                        if team_a and team_b:
                            fixture_data = {
                                "source": "polymarket",
                                "source_id": str(
                                    match_market.get("id")
                                    or match_market.get("conditionId")
                                    or event.get("id")
                                ),
                                "league_id": pm_league_db.id if pm_league_db else None,
                                "team_a_name": team_a,
                                "team_b_name": team_b,
                                "start_time": _parse_datetime(
                                    match_market.get("gameStartTime")
                                    or match_market.get("game_start_time")
                                    or event.get("startDateIso")
                                    or event.get("startDate")
                                    or match_market.get("startDateIso")
                                    or match_market.get("startDate")
                                ),
                                "status": "upcoming"
                                if (match_market.get("active") or event.get("active"))
                                and not (match_market.get("closed") or event.get("closed"))
                                else "finished",
                                "has_odds": True,
                                "market_type": "match_winner",
                                "game_number": None,
                                "line_value": None,
                                "series_type": series_type,
                                "parent_fixture_id": event_fixture_id,
                                "raw_json": match_market,
                            }
                            polymarket_fixtures.append(fixture_data)
                            sport_fixture_count += 1

                            if not dry_run:
                                stmt = insert(Fixture).values(
                                    id=uuid4(), **fixture_data
                                ).on_conflict_do_update(
                                    index_elements=["source", "source_id"],
                                    set_={
                                        "league_id": fixture_data["league_id"],
                                        "team_a_name": fixture_data["team_a_name"],
                                        "team_b_name": fixture_data["team_b_name"],
                                        "start_time": fixture_data["start_time"],
                                        "status": fixture_data["status"],
                                        "market_type": fixture_data["market_type"],
                                        "game_number": fixture_data["game_number"],
                                        "line_value": fixture_data["line_value"],
                                        "series_type": fixture_data["series_type"],
                                        "parent_fixture_id": fixture_data["parent_fixture_id"],
                                        "raw_json": fixture_data["raw_json"],
                                    },
                                )
                                db.execute(stmt)
                                parent = db.execute(
                                    select(Fixture).where(
                                        Fixture.source == "polymarket",
                                        Fixture.source_id == fixture_data["source_id"],
                                    )
                                ).scalar_one_or_none()
                                if parent and not event_fixture_id:
                                    event_fixture_id = parent.parent_fixture_id

                    for m, game_number in game_markets:
                        team_a, team_b = _extract_polymarket_teams(m)
                        if not team_a or not team_b:
                            continue
                        game_series_type = _extract_series_type(m) or series_type
                        fixture_data = {
                            "source": "polymarket",
                            "source_id": str(m.get("id") or m.get("conditionId") or event.get("id")),
                            "league_id": pm_league_db.id if pm_league_db else None,
                            "team_a_name": team_a,
                            "team_b_name": team_b,
                            "start_time": _parse_datetime(
                                m.get("gameStartTime")
                                or m.get("game_start_time")
                                or m.get("startDateIso")
                                or m.get("startDate")
                                or event.get("startDateIso")
                                or event.get("startDate")
                            ),
                            "status": "upcoming"
                            if (m.get("active") or event.get("active"))
                            and not (m.get("closed") or event.get("closed"))
                            else "finished",
                            "has_odds": True,
                            "market_type": "game_winner",
                            "game_number": game_number,
                            "line_value": None,
                            "series_type": game_series_type,
                            "parent_fixture_id": event_fixture_id,
                            "raw_json": m,
                        }
                        polymarket_fixtures.append(fixture_data)
                        sport_fixture_count += 1

                        if not dry_run:
                            stmt = insert(Fixture).values(
                                id=uuid4(), **fixture_data
                            ).on_conflict_do_update(
                                index_elements=["source", "source_id"],
                                set_={
                                    "league_id": fixture_data["league_id"],
                                    "team_a_name": fixture_data["team_a_name"],
                                    "team_b_name": fixture_data["team_b_name"],
                                    "start_time": fixture_data["start_time"],
                                    "status": fixture_data["status"],
                                    "market_type": fixture_data["market_type"],
                                    "game_number": fixture_data["game_number"],
                                    "line_value": fixture_data["line_value"],
                                    "series_type": fixture_data["series_type"],
                                    "parent_fixture_id": fixture_data["parent_fixture_id"],
                                    "raw_json": fixture_data["raw_json"],
                                },
                            )
                            db.execute(stmt)

                    for m, game_number, line_value in totals_markets:
                        team_a = event_team_a
                        team_b = event_team_b
                        if not team_a or not team_b:
                            fallback_a, fallback_b = _extract_polymarket_teams(m)
                            team_a = fallback_a or team_a
                            team_b = fallback_b or team_b
                        if not team_a or not team_b:
                            continue
                        fixture_data = {
                            "source": "polymarket",
                            "source_id": str(m.get("id") or m.get("conditionId") or event.get("id")),
                            "league_id": pm_league_db.id if pm_league_db else None,
                            "team_a_name": team_a,
                            "team_b_name": team_b,
                            "start_time": _parse_datetime(
                                m.get("gameStartTime")
                                or m.get("game_start_time")
                                or m.get("startDateIso")
                                or m.get("startDate")
                                or event.get("startDateIso")
                                or event.get("startDate")
                            ),
                            "status": "upcoming"
                            if (m.get("active") or event.get("active"))
                            and not (m.get("closed") or event.get("closed"))
                            else "finished",
                            "has_odds": True,
                            "market_type": "totals",
                            "game_number": game_number,
                            "line_value": line_value,
                            "series_type": _extract_series_type(m) or series_type,
                            "parent_fixture_id": event_fixture_id,
                            "raw_json": m,
                        }
                        polymarket_fixtures.append(fixture_data)
                        sport_fixture_count += 1

                        if not dry_run:
                            stmt = insert(Fixture).values(
                                id=uuid4(), **fixture_data
                            ).on_conflict_do_update(
                                index_elements=["source", "source_id"],
                                set_={
                                    "league_id": fixture_data["league_id"],
                                    "team_a_name": fixture_data["team_a_name"],
                                    "team_b_name": fixture_data["team_b_name"],
                                    "start_time": fixture_data["start_time"],
                                    "status": fixture_data["status"],
                                    "market_type": fixture_data["market_type"],
                                    "game_number": fixture_data["game_number"],
                                    "line_value": fixture_data["line_value"],
                                    "series_type": fixture_data["series_type"],
                                    "parent_fixture_id": fixture_data["parent_fixture_id"],
                                    "raw_json": fixture_data["raw_json"],
                                },
                            )
                            db.execute(stmt)

                if not dry_run:
                    db.commit()

            stats["fixtures_polymarket_by_sport"][cfg.sport_code] = sport_fixture_count
            stats["fixtures_polymarket"] += sport_fixture_count
            typer.echo(f"     {cfg.display_name} total fixtures: {sport_fixture_count}")

        typer.echo(f"   Total: {len(polymarket_fixtures)} Polymarket fixtures")

        # ============================================
        # Step 5: Build Mappings
        # ============================================
        typer.echo("\n🔗 Building mappings...")

        # Reload fixtures from DB for mapping
        oddspapi_db_fixtures = db.execute(
            select(Fixture).where(
                Fixture.source == "oddspapi",
                Fixture.start_time.is_not(None),
                Fixture.start_time >= from_date,
                Fixture.start_time <= to_date,
            )
        ).scalars().all()

        polymarket_db_fixtures = db.execute(
            select(Fixture).where(
                Fixture.source == "polymarket",
                Fixture.start_time.is_not(None),
                Fixture.start_time >= from_date,
                Fixture.start_time <= to_date,
            )
        ).scalars().all()

        mappings_created = 0
        mappings_high = 0
        created_mappings: list[dict] = []  # Track for overview
        goalserve_orientations = _load_goalserve_orientations()
        odds_payload_cache: dict[str, dict] = {}
        orientation_oddspapi = OddsPapiClient(
            global_cooldown_ms=settings.oddspapi_global_cooldown_ms_discovery
        )

        for op_fix in oddspapi_db_fixtures:
            for pm_fix in polymarket_db_fixtures:
                if pm_fix.market_type != "event":
                    continue

                confidence, details = _calculate_match_confidence(op_fix, pm_fix)
                if confidence < 0.5:
                    continue

                if not dry_run:
                    existing = db.execute(
                        select(Mapping).where(
                            Mapping.oddspapi_fixture_id == op_fix.id,
                            Mapping.polymarket_fixture_id == pm_fix.id,
                        )
                    ).scalar_one_or_none()

                    if not existing:
                        details_enriched = dict(details)
                        teams_swapped = bool(details_enriched.get("teams_swapped"))
                        side_map = _build_market_side_map(pm_fix.raw_json or {}, teams_swapped=teams_swapped)
                        if side_map:
                            details_enriched["market_side_map"] = side_map
                            mw = side_map.get("match_winner")
                            if isinstance(mw, dict):
                                details_enriched["pm_token_id_a"] = mw.get("token_id_a")
                                details_enriched["pm_token_id_b"] = mw.get("token_id_b")
                        orientation_anchor = _resolve_orientation_anchor(
                            op_fix=op_fix,
                            goalserve_orientations=goalserve_orientations,
                            oddspapi=orientation_oddspapi,
                            odds_payload_cache=odds_payload_cache,
                        )
                        _apply_orientation_anchor(details_enriched, orientation_anchor)
                        mapping = Mapping(
                            id=uuid4(),
                            oddspapi_fixture_id=op_fix.id,
                            polymarket_fixture_id=pm_fix.id,
                            confidence=confidence,
                            method="auto",
                            match_details=details_enriched,
                        )
                        db.add(mapping)
                        mappings_created += 1

                        if confidence >= 0.8:
                            mappings_high += 1

                        league_name = op_fix.raw_json.get("tournamentName", "Unknown")
                        market_label = pm_fix.market_type
                        if pm_fix.game_number:
                            market_label = f"{market_label} G{pm_fix.game_number}"

                        created_mappings.append({
                            "league": league_name,
                            "match": f"{op_fix.team_a_name} vs {op_fix.team_b_name}",
                            "pm_match": f"{pm_fix.team_a_name} vs {pm_fix.team_b_name}",
                            "start_time": op_fix.start_time,
                            "confidence": confidence,
                            "is_high": confidence >= 0.8,
                            "market": market_label,
                        })

                        logger.info(
                            "Mapping: %s vs %s <-> %s vs %s (%s, confidence=%.2f)",
                            op_fix.team_a_name,
                            op_fix.team_b_name,
                            pm_fix.team_a_name,
                            pm_fix.team_b_name,
                            market_label,
                            confidence,
                        )

        if not dry_run:
            db.commit()
        orientation_oddspapi.close()

        stats["mappings_created"] = mappings_created
        stats["mappings_high_confidence"] = mappings_high
        stats["_created_mappings"] = created_mappings  # Pass to summary

    # ============================================
    # Summary
    # ============================================
    typer.echo("\n" + "=" * 60)
    typer.echo("📊 Discovery Summary")
    typer.echo("=" * 60)
    for cfg in SPORT_CONFIGS:
        typer.echo(f"{cfg.display_name}:")
        typer.echo(
            "  Leagues:    "
            f"OddsPapi={stats['leagues_oddspapi_by_sport'][cfg.sport_code]}, "
            f"Polymarket={stats['leagues_polymarket_by_sport'][cfg.sport_code]}"
        )
        typer.echo(
            "  Teams:      "
            f"OddsPapi={stats['teams_oddspapi_by_sport'][cfg.sport_code]}, "
            f"Polymarket={stats['teams_polymarket_by_sport'][cfg.sport_code]}"
        )
        typer.echo(
            "  Fixtures:   "
            f"OddsPapi={stats['fixtures_oddspapi_by_sport'][cfg.sport_code]}, "
            f"Polymarket={stats['fixtures_polymarket_by_sport'][cfg.sport_code]}"
        )
    typer.echo("")
    typer.echo(
        f"Totals:       "
        f"Leagues O={stats['leagues_oddspapi']} P={stats['leagues_polymarket']} | "
        f"Teams O={stats['teams_oddspapi']} P={stats['teams_polymarket']} | "
        f"Fixtures O={stats['fixtures_oddspapi']} P={stats['fixtures_polymarket']}"
    )
    typer.echo(f"Mappings:     {stats['mappings_created']} new ({stats['mappings_high_confidence']} high confidence)")

    # ============================================
    # Mapping Overview (all mappings from DB)
    # ============================================
    if not dry_run:
        window_start = now - timedelta(hours=1)
        _print_mapping_overview(window_start=window_start, include_past=include_past)

    if dry_run:
        typer.secho("\n⚠️  Dry run - no data written to database", fg=typer.colors.YELLOW)

    typer.echo("\n✅ Discovery complete!")


def _print_mapping_overview(window_start: datetime, include_past: bool) -> None:
    """Print an overview of mapped matches from the database.

    Only shows mappings at or above ``discovery_min_display_confidence``
    and ranks them by Polymarket liquidity (descending).
    """
    min_conf = settings.discovery_min_display_confidence

    with SessionLocal() as db:
        # Get all mappings
        all_mappings = db.execute(select(Mapping)).scalars().all()

        if not all_mappings:
            typer.echo("\n📭 No mappings found.")
            return

        # Build display data
        mapping_data = []
        skipped_low = 0
        for m in all_mappings:
            # ---------- confidence gate ----------
            if m.confidence < min_conf:
                skipped_low += 1
                continue

            op_fix = db.execute(
                select(Fixture).where(Fixture.id == m.oddspapi_fixture_id)
            ).scalar_one_or_none()
            pm_fix = db.execute(
                select(Fixture).where(Fixture.id == m.polymarket_fixture_id)
            ).scalar_one_or_none()

            if op_fix and pm_fix:
                start_ref = op_fix.start_time or pm_fix.start_time
                if not include_past:
                    if not start_ref or start_ref < window_start:
                        continue
                market_label = pm_fix.market_type
                if pm_fix.game_number:
                    market_label = f"{market_label} G{pm_fix.game_number}"

                liquidity = _extract_market_liquidity(pm_fix.raw_json)

                mapping_data.append({
                    "league": op_fix.raw_json.get("tournamentName", "Unknown"),
                    "match": f"{op_fix.team_a_name} vs {op_fix.team_b_name}",
                    "pm_match": f"{pm_fix.team_a_name} vs {pm_fix.team_b_name}",
                    "start_time": op_fix.start_time,
                    "pm_start_time": pm_fix.start_time,
                    "confidence": m.confidence,
                    "is_high": m.confidence >= 0.8,
                    "market": market_label or "",
                    "liquidity": liquidity,
                })

        typer.echo("\n" + "=" * 60)
        conf_pct_label = int(min_conf * 100)
        typer.echo(
            f"🎯 Mapped Matches Overview "
            f"({len(mapping_data)} shown, {skipped_low} below {conf_pct_label}% hidden)"
        )
        typer.echo("=" * 60)

        # Primary sort: liquidity descending, then start time, then confidence
        sorted_mappings = sorted(
            mapping_data,
            key=lambda x: (
                -x["liquidity"],
                x["start_time"] or datetime.max,
                -x["confidence"],
            ),
        )

        for m in sorted_mappings:
            # Display times in configured local timezone (DB/storage remains UTC)
            op_time_str = _format_display_time(m.get("start_time"))
            pm_time_str = _format_display_time(m.get("pm_start_time"))

            # Confidence indicator with color
            conf_pct = int(m["confidence"] * 100)
            if m["is_high"]:
                conf_str = typer.style(f"[{conf_pct}%]", fg=typer.colors.GREEN, bold=True)
            elif conf_pct >= 65:
                conf_str = typer.style(f"[{conf_pct}%]", fg=typer.colors.YELLOW)
            else:
                conf_str = typer.style(f"[{conf_pct}%]", fg=typer.colors.RED)

            # Liquidity display
            liq = m["liquidity"]
            if liq >= 1_000_000:
                liq_str = f"${liq / 1_000_000:.1f}M"
            elif liq >= 1_000:
                liq_str = f"${liq / 1_000:.1f}K"
            elif liq > 0:
                liq_str = f"${liq:.0f}"
            else:
                liq_str = "$–"

            market_str = f" ({m['market']})" if m.get("market") else ""
            typer.echo(f"\n  {conf_str} {m['league']}{market_str}  💰 {liq_str}")
            typer.echo(f"      📅 OddsPapi: {op_time_str}")
            typer.echo(f"      📅 Polymarket: {pm_time_str}")
            typer.echo(f"      🎮 {m['match']}")
            if m["match"].lower() != m["pm_match"].lower():
                typer.echo(f"      🔮 {m['pm_match']} (Polymarket)")


def _parse_datetime(value) -> datetime | None:
    """Parse a datetime string or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            cleaned = value.strip()
            # Handle ISO format with Z suffix
            cleaned = cleaned.replace("Z", "+00:00")
            # Handle space instead of T separator (e.g., "2026-01-24 21:00:00+00")
            if " " in cleaned and "T" not in cleaned:
                cleaned = cleaned.replace(" ", "T", 1)
            # Handle short timezone format +00 -> +00:00
            if cleaned.endswith("+00"):
                cleaned = cleaned + ":00"
            elif cleaned.endswith("-00"):
                cleaned = cleaned + ":00"
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    return None


def _extract_polymarket_teams(market: dict) -> tuple[str | None, str | None]:
    """
    Extract team names from a Polymarket market.

    Tries multiple fields:
    - teamA/teamB (from sports markets)
    - outcomes array
    - question/title parsing
    """
    import json

    # Try direct team fields (sports markets have these)
    team_a = market.get("teamA") or market.get("teamAName")
    team_b = market.get("teamB") or market.get("teamBName")
    if team_a and team_b:
        return str(team_a), str(team_b)

    # Try outcomes array
    outcomes = market.get("outcomes")
    if outcomes:
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except (json.JSONDecodeError, TypeError):
                outcomes = []
        if isinstance(outcomes, list) and len(outcomes) >= 2:
            return str(outcomes[0]), str(outcomes[1])

    # Try parsing from question/title
    question = market.get("question") or market.get("title") or ""
    if " vs " in question.lower():
        parts = question.split(" vs ", 1)
        if len(parts) == 2:
            # Clean up - remove "Will X win?" patterns
            team_a = parts[0].strip().split(":")[-1].strip()
            team_b = parts[1].strip().split("?")[0].strip()
            # Remove common prefixes
            for prefix in ["Will ", "will "]:
                if team_a.startswith(prefix):
                    team_a = team_a[len(prefix):]
            return team_a, team_b

    return None, None


def _calculate_match_confidence(op_fix: Fixture, pm_fix: Fixture) -> tuple[float, dict]:
    """
    Calculate confidence score for a mapping between OddsPapi and Polymarket fixtures.

    Returns (confidence: float, details: dict)
    """
    confidence = 0.0
    details = {
        "team_a_similarity": 0.0,
        "team_b_similarity": 0.0,
        "date_match": False,
        "teams_match": False,
        "league_match": False,
        "teams_swapped": False,
        "op_team_a": op_fix.team_a_name,
        "op_team_b": op_fix.team_b_name,
        "pm_team_a": pm_fix.team_a_name,
        "pm_team_b": pm_fix.team_b_name,
    }

    # Hard date gate (UTC date match required)
    if not _date_match_gate(op_fix, pm_fix):
        return 0.0, details
    details["date_match"] = True

    # League matching (best effort; gate if both sides known)
    op_league = _extract_oddspapi_league(op_fix)
    pm_league = _extract_polymarket_league(pm_fix.raw_json or {})
    if op_league and pm_league:
        if op_league != pm_league:
            return 0.0, details
        details["league_match"] = True
        confidence += 0.2

    # Team matching (both ways - teams might be in different order)
    if op_fix.team_a_name and op_fix.team_b_name and pm_fix.team_a_name and pm_fix.team_b_name:
        # Try direct match
        sim_aa = similarity(op_fix.team_a_name, pm_fix.team_a_name)
        sim_bb = similarity(op_fix.team_b_name, pm_fix.team_b_name)
        direct_score = (sim_aa + sim_bb) / 2

        # Try swapped match
        sim_ab = similarity(op_fix.team_a_name, pm_fix.team_b_name)
        sim_ba = similarity(op_fix.team_b_name, pm_fix.team_a_name)
        swapped_score = (sim_ab + sim_ba) / 2

        if direct_score >= swapped_score:
            details["team_a_similarity"] = sim_aa
            details["team_b_similarity"] = sim_bb
            details["teams_swapped"] = False
            team_score = direct_score
        else:
            details["team_a_similarity"] = sim_ab
            details["team_b_similarity"] = sim_ba
            details["teams_swapped"] = True
            team_score = swapped_score

        # Teams match if average similarity > 0.6
        details["teams_match"] = team_score > 0.6
        confidence += team_score * 0.6  # Teams are 60% of confidence

    # Time matching (exact time is strong signal, same-day already gated)
    if op_fix.start_time and pm_fix.start_time:
        time_diff = abs((op_fix.start_time - pm_fix.start_time).total_seconds())

        if time_diff < 60:  # Within 1 minute = exact match
            details["time_exact"] = True
            confidence += 0.2
        elif time_diff < 3600:  # Within 1 hour
            details["time_exact"] = False
            confidence += 0.1

    return confidence, details


def _parse_outcomes(raw: dict) -> list[str]:
    outcomes = raw.get("outcomes") or []
    if isinstance(outcomes, str):
        try:
            import json

            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    return [str(o) for o in outcomes if o]


def _parse_token_ids(raw: dict) -> list[str]:
    token_ids = raw.get("clobTokenIds") or []
    if isinstance(token_ids, str):
        try:
            import json

            token_ids = json.loads(token_ids)
        except (json.JSONDecodeError, TypeError):
            token_ids = []
    return [str(t) for t in token_ids if t]


def _extract_outcome_token_pairs(raw: dict) -> list[tuple[str, str]]:
    tokens = raw.get("tokens") or []
    if isinstance(tokens, str):
        try:
            import json

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
    if pairs:
        return pairs
    outcomes = _parse_outcomes(raw)
    token_ids = _parse_token_ids(raw)
    if not outcomes:
        return []
    return [
        (str(outcomes[idx]), str(token_ids[idx]) if idx < len(token_ids) else "")
        for idx in range(len(outcomes))
    ]


def _line_key(line_value: float | None) -> str:
    if line_value is None:
        return "na"
    formatted = f"{float(line_value):.3f}".rstrip("0").rstrip(".")
    return formatted or "0"


def _market_key(market_type: str, game_number: int | None, line_value: float | None = None) -> str:
    if market_type == "game_winner" and game_number:
        return f"game_winner:{game_number}"
    if market_type == "totals":
        if game_number:
            return f"totals:game{game_number}:{_line_key(line_value)}"
        return f"totals:{_line_key(line_value)}"
    return "match_winner"


def _build_market_side_map(event_raw: dict, *, teams_swapped: bool) -> dict[str, dict]:
    market_side_map: dict[str, dict] = {}
    markets = event_raw.get("markets") or []
    if not isinstance(markets, list):
        return market_side_map
    for market in markets:
        if not isinstance(market, dict):
            continue
        classified = _classify_polymarket_market(market)
        if not classified:
            continue
        market_type, game_number, line_value = classified
        key = _market_key(market_type, game_number, line_value)
        pairs = _extract_outcome_token_pairs(market)
        if len(pairs) < 2:
            continue
        if market_type == "totals":
            over_pair = next((pair for pair in pairs if _outcome_side(pair[0]) == "over"), None)
            under_pair = next((pair for pair in pairs if _outcome_side(pair[0]) == "under"), None)
            if not over_pair or not under_pair:
                continue
            token_id_a, token_id_b = over_pair[1], under_pair[1]
            pm_team_for_a, pm_team_for_b = "OVER", "UNDER"
        else:
            left_name, left_token = pairs[0]
            right_name, right_token = pairs[1]
            if teams_swapped:
                token_id_a, token_id_b = right_token, left_token
                pm_team_for_a, pm_team_for_b = right_name, left_name
            else:
                token_id_a, token_id_b = left_token, right_token
                pm_team_for_a, pm_team_for_b = left_name, right_name
        market_side_map[key] = {
            "token_id_a": token_id_a,
            "token_id_b": token_id_b,
            "pm_team_for_a": pm_team_for_a,
            "pm_team_for_b": pm_team_for_b,
            "line_value": line_value,
        }
    return market_side_map


def _classify_polymarket_market(market: dict) -> tuple[str, int | None, float | None] | None:
    """
    Classify a Polymarket market as match winner or game winner.

    Returns (market_type, game_number) or None if not relevant.
    """
    question = (market.get("question") or market.get("title") or "").lower()
    group_title = (market.get("groupItemTitle") or "").lower()
    sports_type = (market.get("sportsMarketType") or "").lower()

    if sports_type == "moneyline" and "vs" in question:
        return "match_winner", None, None

    if sports_type == "child_moneyline":
        game_number = _extract_game_number(question) or _extract_game_number(group_title)
        if game_number:
            return "game_winner", game_number, None

    if sports_type in {"totals", "child_totals"} or _looks_like_totals_market(market):
        game_number = _extract_game_number(question) or _extract_game_number(group_title)
        line_value = _extract_totals_line(market)
        if line_value is not None:
            return "totals", game_number, line_value

    return None


def _outcome_side(outcome: str | None) -> str | None:
    text = (outcome or "").strip().lower()
    if "over" in text:
        return "over"
    if "under" in text:
        return "under"
    return None


def _looks_like_totals_market(market: dict) -> bool:
    sports_type = str(market.get("sportsMarketType") or "").lower()
    if "total" in sports_type:
        return True
    outcomes = _parse_outcomes(market)
    if outcomes and any(_outcome_side(outcome) for outcome in outcomes):
        return True
    question = str(market.get("question") or market.get("title") or "").lower()
    return "over" in question and "under" in question


def _extract_totals_line(market: dict) -> float | None:
    outcomes = _parse_outcomes(market)
    lines: list[float] = []
    for outcome in outcomes:
        if not _outcome_side(outcome):
            continue
        match = re.search(r"(\d+(?:\.\d+)?)", str(outcome))
        if match:
            try:
                lines.append(float(match.group(1)))
            except ValueError:
                continue
    if lines:
        return lines[0]

    question = str(market.get("question") or market.get("title") or "")
    match = re.search(r"(\d+(?:\.\d+)?)", question)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _extract_game_number(text: str) -> int | None:
    normalized = text.lower()
    for n in (1, 2, 3, 4, 5):
        if f"game {n} winner" in normalized:
            return n
        if f"game {n}" in normalized and "winner" in normalized:
            return n
        if f"map {n} winner" in normalized:
            return n
        if f"map {n}" in normalized and "winner" in normalized:
            return n
    return None


def _extract_market_liquidity(raw_json: dict | None) -> float:
    """
    Extract total volume (USD proxy for liquidity) from a Polymarket
    event or market raw payload.

    Tries event-level ``volume`` first, then sums across nested markets.
    """
    if not raw_json:
        return 0.0

    # Event-level volume (string or numeric)
    event_vol = raw_json.get("volume")
    if event_vol is not None:
        try:
            return float(event_vol)
        except (ValueError, TypeError):
            pass

    # Sum market-level volumes
    markets = raw_json.get("markets") or []
    if not isinstance(markets, list):
        return 0.0

    total = 0.0
    for m in markets:
        if not isinstance(m, dict):
            continue
        for key in ("volume", "liquidity"):
            val = m.get(key)
            if val is not None:
                try:
                    total += float(val)
                except (ValueError, TypeError):
                    continue
                break  # use first found per market
    return total


def _extract_series_type(market: dict | None) -> str | None:
    if not market:
        return None
    question = (market.get("question") or market.get("title") or "").lower()
    group_title = (market.get("groupItemTitle") or "").lower()
    combined = f"{question} {group_title}"
    if "bo1" in combined or "best of 1" in combined:
        return "bo1"
    if "bo3" in combined or "best of 3" in combined:
        return "bo3"
    if "bo5" in combined or "best of 5" in combined:
        return "bo5"
    return None


def _load_goalserve_orientations() -> list[dict]:
    if not settings.orientation_anchor_use_goalserve:
        return []
    client = GoalserveClient()
    try:
        payload = client.get_home()
    except Exception as exc:
        logger.warning("Goalserve orientation preload failed: %s", exc)
        client.close()
        return []
    rows: list[dict] = []
    try:
        matches = client.filter_lol(client.extract_matches(payload))
        for match in matches:
            local = str((match.get("localteam") or {}).get("@name") or "").strip()
            away = str((match.get("awayteam") or {}).get("@name") or "").strip()
            if not local or not away:
                continue
            rows.append(
                {
                    "match_id": str(match.get("@id") or ""),
                    "date": _parse_goalserve_date(match.get("@date")),
                    "home_team": local,
                    "away_team": away,
                }
            )
    finally:
        client.close()
    return rows


def _parse_goalserve_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        return None


def _default_orientation_anchor() -> dict:
    return {
        "orientation_locked": False,
        "team_a_is_home": None,
        "home_team": None,
        "away_team": None,
        "orientation_anchor_source": None,
        "orientation_anchor_confidence": 0.0,
        "orientation_anchor_reason": "no_anchor",
        "orientation_anchor_ts": datetime.now(tz=timezone.utc).isoformat(),
    }


def _resolve_orientation_anchor(
    *,
    op_fix: Fixture,
    goalserve_orientations: list[dict],
    oddspapi: OddsPapiClient,
    odds_payload_cache: dict[str, dict],
) -> dict:
    anchor = _anchor_from_goalserve(op_fix, goalserve_orientations)
    if anchor.get("orientation_locked"):
        return anchor
    if not settings.orientation_anchor_use_oddspapi_ids:
        return anchor
    anchor_from_ids = _anchor_from_oddspapi_ids(
        op_fix=op_fix,
        oddspapi=oddspapi,
        odds_payload_cache=odds_payload_cache,
    )
    if anchor_from_ids.get("orientation_locked"):
        return anchor_from_ids
    if anchor.get("orientation_anchor_reason") == "no_anchor":
        return anchor_from_ids
    return anchor


def _anchor_from_goalserve(op_fix: Fixture, goalserve_orientations: list[dict]) -> dict:
    anchor = _default_orientation_anchor()
    if not goalserve_orientations or not op_fix.team_a_name or not op_fix.team_b_name:
        anchor["orientation_anchor_reason"] = "goalserve_unavailable"
        return anchor

    best_score = 0.0
    best_row: dict | None = None
    best_is_direct = True
    best_margin = 0.0
    op_date = op_fix.start_time.date() if op_fix.start_time else None

    for row in goalserve_orientations:
        row_date = row.get("date")
        if op_date and row_date and op_date != row_date:
            continue
        home_team = str(row.get("home_team") or "")
        away_team = str(row.get("away_team") or "")
        direct = (similarity(op_fix.team_a_name, home_team) + similarity(op_fix.team_b_name, away_team)) / 2
        swapped = (similarity(op_fix.team_a_name, away_team) + similarity(op_fix.team_b_name, home_team)) / 2
        score = max(direct, swapped)
        if score > best_score:
            best_score = score
            best_row = row
            best_is_direct = direct >= swapped
            best_margin = abs(direct - swapped)

    anchor["orientation_anchor_confidence"] = best_score
    anchor["orientation_anchor_source"] = "goalserve_pre"
    if best_row is None:
        anchor["orientation_anchor_reason"] = "goalserve_no_match"
        return anchor

    min_similarity = float(settings.orientation_anchor_min_similarity)
    min_margin = float(settings.orientation_anchor_min_margin)
    if best_score < min_similarity:
        anchor["orientation_anchor_reason"] = "goalserve_low_similarity"
        return anchor
    if best_margin < min_margin:
        anchor["orientation_anchor_reason"] = "goalserve_ambiguous_orientation"
        return anchor

    anchor["orientation_locked"] = True
    anchor["team_a_is_home"] = best_is_direct
    anchor["home_team"] = best_row.get("home_team")
    anchor["away_team"] = best_row.get("away_team")
    anchor["orientation_anchor_reason"] = "goalserve_locked"
    return anchor


def _anchor_from_oddspapi_ids(
    *,
    op_fix: Fixture,
    oddspapi: OddsPapiClient,
    odds_payload_cache: dict[str, dict],
) -> dict:
    anchor = _default_orientation_anchor()
    source_id = str(op_fix.source_id)
    payload = odds_payload_cache.get(source_id)
    if payload is None:
        try:
            payload = oddspapi.get_odds(source_id)
        except Exception:
            anchor["orientation_anchor_reason"] = "oddspapi_odds_unavailable"
            return anchor
        odds_payload_cache[source_id] = payload

    odds = OddsPapiClient.extract_pinnacle_moneyline(payload)
    home = odds.get("home") if isinstance(odds, dict) else None
    away = odds.get("away") if isinstance(odds, dict) else None
    if not isinstance(home, dict) or not isinstance(away, dict):
        anchor["orientation_anchor_reason"] = "oddspapi_no_moneyline"
        return anchor

    p1 = payload.get("participant1Id")
    p2 = payload.get("participant2Id")
    home_id = home.get("player_id")
    away_id = away.get("player_id")
    if None in (p1, p2, home_id, away_id):
        anchor["orientation_anchor_reason"] = "oddspapi_missing_player_ids"
        return anchor

    p1s = str(p1)
    p2s = str(p2)
    homes = str(home_id)
    aways = str(away_id)
    if homes == p1s and aways == p2s:
        team_a_is_home = True
    elif homes == p2s and aways == p1s:
        team_a_is_home = False
    else:
        anchor["orientation_anchor_reason"] = "oddspapi_id_mismatch"
        return anchor

    anchor["orientation_locked"] = True
    anchor["team_a_is_home"] = team_a_is_home
    anchor["home_team"] = op_fix.team_a_name if team_a_is_home else op_fix.team_b_name
    anchor["away_team"] = op_fix.team_b_name if team_a_is_home else op_fix.team_a_name
    anchor["orientation_anchor_source"] = "oddspapi_ids"
    anchor["orientation_anchor_confidence"] = 1.0
    anchor["orientation_anchor_reason"] = "oddspapi_locked"
    return anchor


def _apply_orientation_anchor(details_enriched: dict, orientation_anchor: dict) -> None:
    details_enriched["orientation_locked"] = bool(orientation_anchor.get("orientation_locked"))
    details_enriched["team_a_is_home"] = orientation_anchor.get("team_a_is_home")
    details_enriched["home_team"] = orientation_anchor.get("home_team")
    details_enriched["away_team"] = orientation_anchor.get("away_team")
    details_enriched["orientation_anchor_source"] = orientation_anchor.get("orientation_anchor_source")
    details_enriched["orientation_anchor_confidence"] = orientation_anchor.get(
        "orientation_anchor_confidence"
    )
    details_enriched["orientation_anchor_reason"] = orientation_anchor.get("orientation_anchor_reason")
    details_enriched["orientation_anchor_ts"] = orientation_anchor.get("orientation_anchor_ts")
