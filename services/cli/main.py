"""
CLI entry point for LoL Lead-Lag Arbitrage Bot.

Usage:
    python -m cli discover --days 7
    python -m cli monitor
"""

import logging
import sys

import typer

from cli.discover import discover_command
from cli.live import live_command
from cli.monitor import monitor_command

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = typer.Typer(
    name="lol-arb",
    help="LoL Lead-Lag Arbitrage Bot - Detect inefficiencies between Pinnacle and Polymarket",
    add_completion=False,
)


@app.command()
def discover(
    days: int = typer.Option(7, "--days", "-d", help="Number of days ahead to look for fixtures"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Don't write to database, just print"),
    past: bool = typer.Option(False, "--past", help="Include past mappings in overview output"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    """
    Discover upcoming LoL matches and build mappings.

    Fetches data from OddsPapi and Polymarket, matches fixtures,
    and stores mappings in the database.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        discover_command(days=days, dry_run=dry_run, include_past=past)
    except KeyboardInterrupt:
        typer.echo("\nDiscovery cancelled.")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        logging.exception("Discovery failed")
        raise typer.Exit(code=1)


@app.command()
def monitor(
    interval: int = typer.Option(5, "--interval", "-i", help="Poll interval in seconds"),
    threshold: float = typer.Option(0.05, "--threshold", "-t", help="Gap threshold for shadow orders"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    """
    Monitor live matches and compare odds.

    Watches mapped matches that are live, compares Pinnacle odds
    with Polymarket CLOB prices, and records shadow orders when
    gaps exceed the threshold.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        monitor_command(interval=interval, threshold=threshold)
    except KeyboardInterrupt:
        typer.echo("\nMonitoring stopped.")
        raise typer.Exit(code=0)
    except Exception as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        logging.exception("Monitoring failed")
        raise typer.Exit(code=1)


@app.command()
def live(
    interval: float | None = typer.Option(
        None,
        "--interval",
        "-i",
        help="Poll interval in seconds (overrides --speed)",
    ),
    speed: str = typer.Option(
        "MED",
        "--speed",
        "-s",
        help="Polling speed preset: FAST, MED, SLOW",
        case_sensitive=False,
    ),
    edge_threshold: float = typer.Option(0.03, "--edge-threshold", help="Net edge threshold for alerts"),
    spread_factor: float = typer.Option(1.0, "--spread-factor", help="Spread penalty multiplier"),
    min_confidence: float = typer.Option(0.7, "--min-confidence", help="Minimum mapping confidence"),
    lookahead_minutes: int = typer.Option(30, "--lookahead-minutes", help="How far ahead to show matches"),
    stale_minutes: int = typer.Option(180, "--stale-minutes", help="Minutes after start to treat as ended"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    """
    Live TUI monitor (read-only).

    Renders a stationary console UI with live odds and edge alerts.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        if speed:
            speed = speed.upper()
            if speed not in {"FAST", "MED", "SLOW"}:
                raise typer.BadParameter("speed must be FAST, MED, or SLOW")
        live_command(
            interval=interval,
            speed=speed,
            edge_threshold=edge_threshold,
            spread_factor=spread_factor,
            min_confidence=min_confidence,
            lookahead_minutes=lookahead_minutes,
            stale_minutes=stale_minutes,
        )
    except KeyboardInterrupt:
        typer.echo("\nLive monitor stopped.")
        raise typer.Exit(code=0)
    except Exception as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        logging.exception("Live monitor failed")
        raise typer.Exit(code=1)


@app.command()
def status():
    """Show current system status (counts, last discovery, etc.)."""
    from sqlalchemy import func, select

    from shared.db import SessionLocal
    from shared.models import Fixture, League, Mapping, OddsSnapshot, ShadowOrder, Team

    with SessionLocal() as db:
        leagues_op = db.execute(
            select(func.count()).select_from(League).where(League.source == "oddspapi")
        ).scalar_one()
        leagues_pm = db.execute(
            select(func.count()).select_from(League).where(League.source == "polymarket")
        ).scalar_one()

        teams_op = db.execute(
            select(func.count()).select_from(Team).where(Team.source == "oddspapi")
        ).scalar_one()
        teams_pm = db.execute(
            select(func.count()).select_from(Team).where(Team.source == "polymarket")
        ).scalar_one()

        fixtures_op = db.execute(
            select(func.count()).select_from(Fixture).where(Fixture.source == "oddspapi")
        ).scalar_one()
        fixtures_pm = db.execute(
            select(func.count()).select_from(Fixture).where(Fixture.source == "polymarket")
        ).scalar_one()

        live_fixtures = db.execute(
            select(func.count()).select_from(Fixture).where(Fixture.status == "live")
        ).scalar_one()

        mappings_total = db.execute(select(func.count()).select_from(Mapping)).scalar_one()
        mappings_high = db.execute(
            select(func.count()).select_from(Mapping).where(Mapping.confidence >= 0.8)
        ).scalar_one()

        snapshots = db.execute(select(func.count()).select_from(OddsSnapshot)).scalar_one()
        shadow_orders = db.execute(select(func.count()).select_from(ShadowOrder)).scalar_one()

        last_discovery = db.execute(
            select(func.max(Fixture.updated_at))
        ).scalar_one()

    typer.echo("\n📊 LoL Lead-Lag Arbitrage Bot Status\n")
    typer.echo(f"Leagues:       OddsPapi={leagues_op}, Polymarket={leagues_pm}")
    typer.echo(f"Teams:         OddsPapi={teams_op}, Polymarket={teams_pm}")
    typer.echo(f"Fixtures:      OddsPapi={fixtures_op}, Polymarket={fixtures_pm}")
    typer.echo(f"Live fixtures: {live_fixtures}")
    typer.echo(f"Mappings:      {mappings_total} total ({mappings_high} high confidence)")
    typer.echo(f"Snapshots:     {snapshots}")
    typer.echo(f"Shadow orders: {shadow_orders}")
    typer.echo(f"Last discovery: {last_discovery or 'Never'}")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()

