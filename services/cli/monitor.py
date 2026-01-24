"""
Live monitoring command for LoL Lead-Lag Arbitrage Bot.

Watches mapped matches that are live, compares Pinnacle odds
with Polymarket CLOB prices, and records shadow orders.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import typer
from sqlalchemy import or_, select

from shared.config import settings
from shared.db import SessionLocal
from shared.models import Fixture, Mapping, OddsSnapshot, ShadowOrder
from shared.oddspapi_client import OddsPapiClient
from shared.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


def monitor_command(interval: int = 5, threshold: float = 0.05) -> None:
    """
    Main monitoring loop:
    1. Find mappings where either fixture is live (or starting soon)
    2. Poll OddsPapi odds + Polymarket CLOB
    3. Compare and compute gap
    4. Record shadow order if gap > threshold
    5. Store snapshots
    """
    typer.echo("\n👁️  Starting live monitor...")
    typer.echo(f"   Poll interval: {interval}s")
    typer.echo(f"   Gap threshold: {threshold:.1%}\n")

    oddspapi = OddsPapiClient()
    polymarket = PolymarketClient()

    iteration = 0
    while True:
        iteration += 1
        now = datetime.now(tz=timezone.utc)

        with SessionLocal() as db:
            # Find mappings with live or starting-soon fixtures
            live_mappings = _get_live_mappings(db, now)

            if not live_mappings:
                if iteration == 1:
                    typer.echo("📭 No live matches found. Waiting for matches to go live...")
                    typer.echo("   (Run 'cli discover --days 7' to populate fixtures)\n")

                # Still show a heartbeat every 30 seconds
                if iteration % (30 // interval) == 0:
                    typer.echo(f"[{now.strftime('%H:%M:%S')}] No live matches - waiting...")

                time.sleep(interval)
                continue

            typer.echo(f"[{now.strftime('%H:%M:%S')}] Monitoring {len(live_mappings)} live match(es):\n")

            for mapping in live_mappings:
                _process_mapping(
                    db=db,
                    mapping=mapping,
                    oddspapi=oddspapi,
                    polymarket=polymarket,
                    threshold=threshold,
                    now=now,
                )

            db.commit()
            typer.echo("")

        time.sleep(interval)


def _get_live_mappings(db, now: datetime) -> list[Mapping]:
    """
    Get mappings where fixtures are live or starting within 30 minutes.
    """
    window_start = now - timedelta(hours=4)  # Include matches that started up to 4h ago
    window_end = now + timedelta(minutes=30)  # Include matches starting in next 30min

    # Get OddsPapi fixtures that are live or starting soon
    live_fixture_ids = db.execute(
        select(Fixture.id).where(
            Fixture.source == "oddspapi",
            or_(
                Fixture.status == "live",
                Fixture.start_time.between(window_start, window_end),
            ),
        )
    ).scalars().all()

    if not live_fixture_ids:
        return []

    # Get mappings for these fixtures
    mappings = db.execute(
        select(Mapping).where(Mapping.oddspapi_fixture_id.in_(live_fixture_ids))
    ).scalars().all()

    return list(mappings)


def _process_mapping(
    db,
    mapping: Mapping,
    oddspapi: OddsPapiClient,
    polymarket: PolymarketClient,
    threshold: float,
    now: datetime,
) -> None:
    """
    Process a single mapping:
    1. Get OddsPapi odds
    2. Get Polymarket CLOB prices
    3. Compare and log
    4. Record snapshot and shadow order if needed
    """
    # Load fixtures
    op_fixture = db.get(Fixture, mapping.oddspapi_fixture_id)
    pm_fixture = db.get(Fixture, mapping.polymarket_fixture_id)

    if not op_fixture or not pm_fixture:
        logger.warning("Mapping %s has missing fixtures", mapping.id)
        return

    match_name = f"{op_fixture.team_a_name} vs {op_fixture.team_b_name}"
    league_name = ""
    if op_fixture.league:
        league_name = f" ({op_fixture.league.name})"

    typer.echo(f"   📊 {match_name}{league_name}")

    # Get OddsPapi odds
    try:
        odds_payload = oddspapi.get_odds(op_fixture.source_id)
        pinnacle_odds = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
    except Exception as e:
        logger.error("Failed to get OddsPapi odds for %s: %s", op_fixture.source_id, e)
        typer.echo(f"      ⚠️  Failed to get Pinnacle odds: {e}")
        return

    if not pinnacle_odds:
        typer.echo("      ⚠️  No Pinnacle odds available")
        return

    # Extract team odds
    home_data = pinnacle_odds.get("home", {})
    away_data = pinnacle_odds.get("away", {})

    home_prob = home_data.get("implied_prob")
    away_prob = away_data.get("implied_prob")
    home_price = home_data.get("price")
    away_price = away_data.get("price")

    if home_prob is None or away_prob is None:
        typer.echo("      ⚠️  Incomplete Pinnacle odds")
        return

    typer.echo(
        f"      Pinnacle: {op_fixture.team_a_name} @ {home_price:.2f} ({home_prob:.1%}) | "
        f"{op_fixture.team_b_name} @ {away_price:.2f} ({away_prob:.1%})"
    )

    # Store OddsPapi snapshot
    op_snapshot = OddsSnapshot(
        id=uuid4(),
        fixture_id=op_fixture.id,
        ts=now,
        source="oddspapi",
        team_a_odds=home_price,
        team_b_odds=away_price,
        team_a_implied_prob=home_prob,
        team_b_implied_prob=away_prob,
        raw_json=odds_payload,
    )
    db.add(op_snapshot)

    # Get Polymarket CLOB prices
    pm_raw = pm_fixture.raw_json or {}

    # Try to get token IDs for CLOB
    token_ids = pm_raw.get("clobTokenIds") or []
    if isinstance(token_ids, str):
        try:
            import json
            token_ids = json.loads(token_ids)
        except (json.JSONDecodeError, TypeError):
            token_ids = []

    # If no CLOB tokens, try to use outcome prices from Gamma
    outcome_prices = pm_raw.get("outcomePrices")
    if outcome_prices:
        if isinstance(outcome_prices, str):
            try:
                import json
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                outcome_prices = []

        if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
            try:
                pm_team_a_price = float(outcome_prices[0])
                pm_team_b_price = float(outcome_prices[1])
            except (ValueError, TypeError):
                pm_team_a_price = None
                pm_team_b_price = None
        else:
            pm_team_a_price = None
            pm_team_b_price = None
    else:
        pm_team_a_price = None
        pm_team_b_price = None

    # Try CLOB if we have token IDs
    clob_data = {}
    if token_ids and len(token_ids) >= 1:
        try:
            clob_data = polymarket.get_clob_orderbook(str(token_ids[0]))
            if clob_data.get("mid") is not None:
                pm_team_a_price = clob_data["mid"]
        except Exception as e:
            logger.debug("CLOB fetch failed for %s: %s", token_ids[0], e)

    if pm_team_a_price is None:
        typer.echo("      ⚠️  No Polymarket prices available")
        return

    # Calculate mid if we only have one outcome
    pm_mid = pm_team_a_price
    pm_bid = clob_data.get("best_bid", pm_team_a_price)
    pm_ask = clob_data.get("best_ask", pm_team_a_price)

    typer.echo(
        f"      Polymarket: {pm_fixture.team_a_name or 'Team A'} "
        f"bid={pm_bid:.3f} ask={pm_ask:.3f} mid={pm_mid:.3f}"
    )

    # Store Polymarket snapshot
    pm_snapshot = OddsSnapshot(
        id=uuid4(),
        fixture_id=pm_fixture.id,
        ts=now,
        source="polymarket_clob",
        best_bid=pm_bid,
        best_ask=pm_ask,
        team_a_implied_prob=pm_mid,  # Use mid as implied prob
        raw_json=clob_data or {"outcomePrices": outcome_prices},
    )
    db.add(pm_snapshot)

    # Calculate gap
    gap_a = home_prob - pm_mid
    gap_b = away_prob - (1 - pm_mid) if pm_mid else None

    # Determine which side has the larger gap
    if abs(gap_a) >= abs(gap_b or 0):
        gap = gap_a
        side = "buy_a" if gap > 0 else "sell_a"
        pinnacle_prob_used = home_prob
        poly_price_used = pm_mid
        team_name = op_fixture.team_a_name
    else:
        gap = gap_b
        side = "buy_b" if gap > 0 else "sell_b"
        pinnacle_prob_used = away_prob
        poly_price_used = 1 - pm_mid if pm_mid else None
        team_name = op_fixture.team_b_name

    # Display gap
    if abs(gap) > threshold:
        typer.secho(
            f"      Gap: {gap:+.1%} on {team_name} ⚠️  ABOVE THRESHOLD",
            fg=typer.colors.YELLOW,
        )

        # Record shadow order
        shadow_order = ShadowOrder(
            id=uuid4(),
            mapping_id=mapping.id,
            ts=now,
            side=side,
            pinnacle_prob=pinnacle_prob_used,
            polymarket_price=poly_price_used,
            gap=gap,
            reason=f"Gap {gap:+.1%} exceeds threshold {threshold:.1%}",
            raw_json={
                "pinnacle_odds": pinnacle_odds,
                "polymarket_clob": clob_data,
                "outcome_prices": outcome_prices,
            },
        )
        db.add(shadow_order)
        typer.secho(f"      📝 SHADOW ORDER recorded: {side}", fg=typer.colors.GREEN)
    else:
        typer.echo(f"      Gap: {gap:+.1%} on {team_name} (below threshold)")


def _update_fixture_status(db, fixture: Fixture, odds_payload: dict) -> None:
    """Update fixture status based on odds payload."""
    status_id = odds_payload.get("statusId")
    if status_id is not None:
        new_status = OddsPapiClient.parse_fixture_status(status_id)
        if fixture.status != new_status:
            logger.info(
                "Fixture %s status changed: %s -> %s",
                fixture.source_id,
                fixture.status,
                new_status,
            )
            fixture.status = new_status

