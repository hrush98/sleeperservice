"""
Discovery command for LoL Lead-Lag Arbitrage Bot.

Collects upcoming matches from OddsPapi and Polymarket,
then builds mappings between them.
"""

import logging
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from uuid import uuid4

import typer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from shared.config import settings
from shared.db import SessionLocal
from shared.models import Fixture, League, Mapping, Team
from shared.oddspapi_client import OddsPapiClient
from shared.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

# Target leagues (normalized names for matching)
TARGET_LEAGUE_PATTERNS = ["lck", "lpl", "lec", "lcs", "lta", "lcp"]


def normalize_name(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    return name.lower().strip()


def is_target_league(name: str) -> bool:
    """Check if a league name matches our target leagues."""
    normalized = normalize_name(name)
    return any(pattern in normalized for pattern in TARGET_LEAGUE_PATTERNS)


def similarity(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def discover_command(days: int, dry_run: bool = False) -> None:
    """
    Main discovery flow:
    1. Fetch OddsPapi tournaments → filter to target leagues → store
    2. Fetch OddsPapi participants → store teams
    3. Fetch OddsPapi fixtures for each league → store
    4. Fetch Polymarket events/markets → store
    5. Build mappings by matching league + teams + date
    """
    typer.echo(f"\n🔍 Discovering LoL matches for next {days} days...\n")

    oddspapi = OddsPapiClient()
    polymarket = PolymarketClient()

    now = datetime.now(tz=timezone.utc)
    from_date = now
    to_date = now + timedelta(days=days)

    stats = {
        "leagues_oddspapi": 0,
        "leagues_polymarket": 0,
        "teams_oddspapi": 0,
        "teams_polymarket": 0,
        "fixtures_oddspapi": 0,
        "fixtures_polymarket": 0,
        "mappings_created": 0,
        "mappings_high_confidence": 0,
    }

    with SessionLocal() as db:
        # ============================================
        # Step 1: OddsPapi Tournaments → Leagues
        # ============================================
        typer.echo("📋 Fetching OddsPapi tournaments...")
        tournaments = oddspapi.get_tournaments()

        target_tournaments = []
        for t in tournaments:
            name = t.get("tournamentName", "")
            if is_target_league(name):
                target_tournaments.append(t)
                logger.info("Target league found: %s (id=%s)", name, t.get("tournamentId"))

        typer.echo(f"   Found {len(target_tournaments)} target leagues from {len(tournaments)} total")

        if not dry_run:
            for t in target_tournaments:
                stmt = insert(League).values(
                    id=uuid4(),
                    source="oddspapi",
                    source_id=str(t.get("tournamentId")),
                    name=t.get("tournamentName", "Unknown"),
                    slug=t.get("tournamentSlug"),
                    raw_json=t,
                ).on_conflict_do_update(
                    index_elements=["source", "source_id"],
                    set_={"name": t.get("tournamentName"), "raw_json": t},
                )
                db.execute(stmt)
            db.commit()

        stats["leagues_oddspapi"] = len(target_tournaments)

        # ============================================
        # Step 2: OddsPapi Participants → Teams
        # ============================================
        typer.echo("\n👥 Fetching OddsPapi participants...")
        participants = oddspapi.get_participants()
        typer.echo(f"   Found {len(participants)} teams")

        if not dry_run and participants:
            for pid, pname in participants.items():
                stmt = insert(Team).values(
                    id=uuid4(),
                    source="oddspapi",
                    source_id=str(pid),
                    name=pname,
                    raw_json={"participantId": pid, "name": pname},
                ).on_conflict_do_update(
                    index_elements=["source", "source_id"],
                    set_={"name": pname},
                )
                db.execute(stmt)
            db.commit()

        stats["teams_oddspapi"] = len(participants)

        # ============================================
        # Step 3: OddsPapi Fixtures for each league
        # ============================================
        typer.echo("\n🎮 Fetching OddsPapi fixtures...")

        oddspapi_fixtures = []
        for t in target_tournaments:
            tid = t.get("tournamentId")
            tname = t.get("tournamentName", "Unknown")

            # Get league from DB
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
                    "status": OddsPapiClient.parse_fixture_status(f.get("statusId")),
                    "has_odds": bool(f.get("hasOdds")),
                    "raw_json": f,
                }
                oddspapi_fixtures.append(fixture_data)

                if not dry_run:
                    stmt = insert(Fixture).values(id=uuid4(), **fixture_data).on_conflict_do_update(
                        index_elements=["source", "source_id"],
                        set_={
                            "team_a_name": fixture_data["team_a_name"],
                            "team_b_name": fixture_data["team_b_name"],
                            "start_time": fixture_data["start_time"],
                            "status": fixture_data["status"],
                            "has_odds": fixture_data["has_odds"],
                            "raw_json": fixture_data["raw_json"],
                        },
                    )
                    db.execute(stmt)

            if not dry_run:
                db.commit()

        stats["fixtures_oddspapi"] = len(oddspapi_fixtures)
        typer.echo(f"   Total: {len(oddspapi_fixtures)} OddsPapi fixtures")

        # ============================================
        # Step 4a: Polymarket Sports → Leagues
        # ============================================
        typer.echo("\n🔮 Fetching Polymarket sports/leagues...")

        sports = polymarket.get_sports()
        pm_target_leagues = []

        # Look for the "lol" sport entry specifically
        # Note: "lcs" = Leagues Cup (soccer), "lpl" = Lanka Premier League (cricket)
        # The actual LoL umbrella is sport="lol"
        for s in sports:
            sport_code = (s.get("sport") or "").lower()
            if sport_code == "lol":
                pm_target_leagues.append(s)
                logger.info("Polymarket LoL found: sport=%s, series=%s", sport_code, s.get("series"))

        typer.echo(f"   Found {len(pm_target_leagues)} LoL league(s) from {len(sports)} sports")

        # Store Polymarket leagues
        if not dry_run:
            for s in pm_target_leagues:
                series_id = s.get("series") or s.get("id") or ""
                sport_name = s.get("sport") or "Unknown"
                stmt = insert(League).values(
                    id=uuid4(),
                    source="polymarket",
                    source_id=str(series_id),
                    name=f"LoL ({sport_name.upper()})",  # e.g., "LoL (LOL)"
                    slug=sport_name,
                    raw_json=s,
                ).on_conflict_do_update(
                    index_elements=["source", "source_id"],
                    set_={"name": f"LoL ({sport_name.upper()})", "raw_json": s},
                )
                db.execute(stmt)
            db.commit()

        stats["leagues_polymarket"] = len(pm_target_leagues)

        # ============================================
        # Step 4b: Polymarket Teams (for LoL)
        # ============================================
        typer.echo("\n👥 Fetching Polymarket teams...")

        pm_teams_count = 0
        # Try fetching teams for "lol" league
        teams = polymarket.get_teams(league="lol")
        typer.echo(f"   LoL: {len(teams)} teams")

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

        pm_teams_count = len(teams)
        stats["teams_polymarket"] = pm_teams_count

        # ============================================
        # Step 4c: Polymarket Events → Fixtures
        # ============================================
        typer.echo("\n🎮 Fetching Polymarket events...")

        polymarket_fixtures = []
        for league in pm_target_leagues:
            series_id = league.get("series") or league.get("id")
            sport_name = league.get("sport") or "lol"

            # Get league from DB for linking
            pm_league_db = db.execute(
                select(League).where(League.source == "polymarket", League.source_id == str(series_id))
            ).scalar_one_or_none()

            # Fetch open events with game bets tag (closed=False means not resolved yet)
            events = polymarket.get_events(
                series_id=str(series_id) if series_id else None,
                tag_id=settings.polymarket_game_bets_tag_id,
                active=True,
                closed=False,  # Only get events that haven't resolved yet
            )
            typer.echo(f"   LoL: {len(events)} open events")

            # Extract markets from events
            for event in events:
                markets = event.get("markets") or []
                if not markets:
                    # If no nested markets, the event itself might be the market
                    markets = [event]

                for m in markets:
                    # Extract team names from market
                    team_a, team_b = _extract_polymarket_teams(m)

                    # Skip if we can't identify teams
                    if not team_a or not team_b:
                        continue

                    market_class = _classify_polymarket_market(m)
                    if not market_class:
                        continue
                    market_type, game_number = market_class

                    fixture_data = {
                        "source": "polymarket",
                        "source_id": str(m.get("id") or m.get("conditionId") or event.get("id")),
                        "league_id": pm_league_db.id if pm_league_db else None,
                        "team_a_name": team_a,
                        "team_b_name": team_b,
                        "start_time": _parse_datetime(
                            m.get("gameStartTime") or m.get("startDate") or event.get("startDate")
                        ),
                        "status": "upcoming" if (m.get("active") or event.get("active")) and not (m.get("closed") or event.get("closed")) else "finished",
                        "has_odds": True,
                        "market_type": market_type,
                        "game_number": game_number,
                        "raw_json": m,
                    }
                    polymarket_fixtures.append(fixture_data)

                    if not dry_run:
                        stmt = insert(Fixture).values(id=uuid4(), **fixture_data).on_conflict_do_update(
                            index_elements=["source", "source_id"],
                            set_={
                                "league_id": fixture_data["league_id"],
                                "team_a_name": fixture_data["team_a_name"],
                                "team_b_name": fixture_data["team_b_name"],
                                "start_time": fixture_data["start_time"],
                                "status": fixture_data["status"],
                                "market_type": fixture_data["market_type"],
                                "game_number": fixture_data["game_number"],
                                "raw_json": fixture_data["raw_json"],
                            },
                        )
                        db.execute(stmt)

            if not dry_run:
                db.commit()

        typer.echo(f"   Total: {len(polymarket_fixtures)} Polymarket fixtures")
        stats["fixtures_polymarket"] = len(polymarket_fixtures)

        # ============================================
        # Step 5: Build Mappings
        # ============================================
        typer.echo("\n🔗 Building mappings...")

        # Reload fixtures from DB for mapping
        oddspapi_db_fixtures = db.execute(
            select(Fixture).where(Fixture.source == "oddspapi")
        ).scalars().all()

        polymarket_db_fixtures = db.execute(
            select(Fixture).where(Fixture.source == "polymarket")
        ).scalars().all()

        mappings_created = 0
        mappings_high = 0
        created_mappings: list[dict] = []  # Track for overview

        for op_fix in oddspapi_db_fixtures:
            for pm_fix in polymarket_db_fixtures:
                if pm_fix.market_type not in {"match_winner", "game_winner"}:
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
                        mapping = Mapping(
                            id=uuid4(),
                            oddspapi_fixture_id=op_fix.id,
                            polymarket_fixture_id=pm_fix.id,
                            confidence=confidence,
                            method="auto",
                            match_details=details,
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

        stats["mappings_created"] = mappings_created
        stats["mappings_high_confidence"] = mappings_high
        stats["_created_mappings"] = created_mappings  # Pass to summary

    # ============================================
    # Summary
    # ============================================
    typer.echo("\n" + "=" * 60)
    typer.echo("📊 Discovery Summary")
    typer.echo("=" * 60)
    typer.echo(f"Leagues:      OddsPapi={stats['leagues_oddspapi']}, Polymarket={stats['leagues_polymarket']}")
    typer.echo(f"Teams:        OddsPapi={stats['teams_oddspapi']}, Polymarket={stats['teams_polymarket']}")
    typer.echo(f"Fixtures:     OddsPapi={stats['fixtures_oddspapi']}, Polymarket={stats['fixtures_polymarket']}")
    typer.echo(f"Mappings:     {stats['mappings_created']} new ({stats['mappings_high_confidence']} high confidence)")

    # ============================================
    # Mapping Overview (all mappings from DB)
    # ============================================
    if not dry_run:
        _print_mapping_overview()

    if dry_run:
        typer.secho("\n⚠️  Dry run - no data written to database", fg=typer.colors.YELLOW)

    typer.echo("\n✅ Discovery complete!")


def _print_mapping_overview() -> None:
    """Print an overview of all mapped matches from the database."""
    with SessionLocal() as db:
        # Get all mappings
        all_mappings = db.execute(select(Mapping)).scalars().all()

        if not all_mappings:
            typer.echo("\n📭 No mappings found.")
            return

        typer.echo("\n" + "=" * 60)
        typer.echo(f"🎯 Mapped Matches Overview ({len(all_mappings)} total)")
        typer.echo("=" * 60)

        # Build display data
        mapping_data = []
        for m in all_mappings:
            op_fix = db.execute(
                select(Fixture).where(Fixture.id == m.oddspapi_fixture_id)
            ).scalar_one_or_none()
            pm_fix = db.execute(
                select(Fixture).where(Fixture.id == m.polymarket_fixture_id)
            ).scalar_one_or_none()

            if op_fix and pm_fix:
                market_label = pm_fix.market_type
                if pm_fix.game_number:
                    market_label = f"{market_label} G{pm_fix.game_number}"
                mapping_data.append({
                    "league": op_fix.raw_json.get("tournamentName", "Unknown"),
                    "match": f"{op_fix.team_a_name} vs {op_fix.team_b_name}",
                    "pm_match": f"{pm_fix.team_a_name} vs {pm_fix.team_b_name}",
                    "start_time": op_fix.start_time,
                    "confidence": m.confidence,
                    "is_high": m.confidence >= 0.8,
                    "market": market_label or "",
                })

        # Sort by start time
        sorted_mappings = sorted(
            mapping_data,
            key=lambda x: (x["start_time"] or datetime.max, -x["confidence"]),
        )

        for m in sorted_mappings:
            # Format time
            if m["start_time"]:
                time_str = m["start_time"].strftime("%b %d %H:%M UTC")
            else:
                time_str = "TBD"

            # Confidence indicator with color
            conf_pct = int(m["confidence"] * 100)
            if m["is_high"]:
                conf_str = typer.style(f"[{conf_pct}%]", fg=typer.colors.GREEN, bold=True)
            elif conf_pct >= 65:
                conf_str = typer.style(f"[{conf_pct}%]", fg=typer.colors.YELLOW)
            else:
                conf_str = typer.style(f"[{conf_pct}%]", fg=typer.colors.RED)

            market_str = f" ({m['market']})" if m.get("market") else ""
            typer.echo(f"\n  {conf_str} {m['league']}{market_str}")
            typer.echo(f"      📅 {time_str}")
            typer.echo(f"      🎮 {m['match']}")
            if m["match"].lower() != m["pm_match"].lower():
                typer.echo(f"      🔮 {m['pm_match']} (Polymarket)")


def _parse_datetime(value) -> datetime | None:
    """Parse a datetime string or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
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
            return datetime.fromisoformat(cleaned)
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
    }

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
            team_score = direct_score
        else:
            details["team_a_similarity"] = sim_ab
            details["team_b_similarity"] = sim_ba
            team_score = swapped_score

        # Teams match if average similarity > 0.6
        details["teams_match"] = team_score > 0.6
        confidence += team_score * 0.7  # Teams are 70% of confidence

    # Time matching (exact time is strong signal, same day is good)
    if op_fix.start_time and pm_fix.start_time:
        time_diff = abs((op_fix.start_time - pm_fix.start_time).total_seconds())

        if time_diff < 60:  # Within 1 minute = exact match
            details["date_match"] = True
            details["time_exact"] = True
            confidence += 0.3  # Full 30%
        elif time_diff < 3600:  # Within 1 hour
            details["date_match"] = True
            details["time_exact"] = False
            confidence += 0.25
        elif op_fix.start_time.date() == pm_fix.start_time.date():
            details["date_match"] = True
            details["time_exact"] = False
            confidence += 0.2  # Same day
        elif abs((op_fix.start_time - pm_fix.start_time).days) <= 1:
            # Within 1 day - partial credit
            details["date_match"] = False
            details["time_exact"] = False
            confidence += 0.1

    return confidence, details


def _classify_polymarket_market(market: dict) -> tuple[str, int | None] | None:
    """
    Classify a Polymarket market as match winner or game winner.

    Returns (market_type, game_number) or None if not relevant.
    """
    question = (market.get("question") or market.get("title") or "").lower()
    group_title = (market.get("groupItemTitle") or "").lower()
    sports_type = (market.get("sportsMarketType") or "").lower()

    if sports_type == "moneyline" and "vs" in question:
        if "bo3" in question or "match winner" in question or "series winner" in question:
            return "match_winner", None

    if sports_type == "child_moneyline":
        game_number = _extract_game_number(question) or _extract_game_number(group_title)
        if game_number:
            return "game_winner", game_number

    return None


def _extract_game_number(text: str) -> int | None:
    for n in (1, 2, 3):
        if f"game {n} winner" in text:
            return n
        if f"game {n}" in text and "winner" in text:
            return n
    return None

