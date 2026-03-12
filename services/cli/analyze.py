"""
Simple DB analysis for logged trades and positions.
"""

# pylint: disable=not-callable

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from services.shared.db import SessionLocal
from services.shared.models import Fixture, Position, TradeEvent


def analyze_command(
    *,
    days: int,
    mode: str,
    market_type: str,
    limit: int,
    include_events: bool,
) -> None:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    filters = [Position.opened_at >= cutoff]
    if mode != "any":
        filters.append(Position.mode == mode)
    if market_type != "any":
        filters.append(Position.market_type == market_type)

    with SessionLocal() as db:
        total = db.execute(select(func.count()).select_from(Position).where(*filters)).scalar_one()
        closed = db.execute(
            select(func.count())
            .select_from(Position)
            .where(*filters, Position.closed_at.is_not(None))
        ).scalar_one()
        wins = db.execute(
            select(func.count())
            .select_from(Position)
            .where(*filters, Position.pnl_percent.is_not(None), Position.pnl_percent > 0)
        ).scalar_one()
        pnl_total = db.execute(
            select(func.coalesce(func.sum(Position.pnl_absolute), 0.0))
            .select_from(Position)
            .where(*filters)
        ).scalar_one()
        pnl_avg = db.execute(
            select(func.avg(Position.pnl_percent)).select_from(Position).where(*filters)
        ).scalar_one()
        hold_avg = db.execute(
            select(func.avg(Position.hold_seconds)).select_from(Position).where(*filters)
        ).scalar_one()
        conv_avg = db.execute(
            select(func.avg(Position.convergence_seconds)).select_from(Position).where(*filters)
        ).scalar_one()
        edge_capture_avg = db.execute(
            select(func.avg(Position.edge_capture)).select_from(Position).where(*filters)
        ).scalar_one()

        breakdown = db.execute(
            select(
                Position.market_type,
                func.count(),
                func.count().filter(Position.closed_at.is_not(None)),
                func.coalesce(func.sum(Position.pnl_absolute), 0.0),
                func.avg(Position.pnl_percent),
            )
            .where(*filters)
            .group_by(Position.market_type)
            .order_by(Position.market_type)
        ).all()

        top_rows = db.execute(
            select(Position, Fixture)
            .join(Fixture, Position.pm_fixture_id == Fixture.id, isouter=True)
            .where(*filters, Position.closed_at.is_not(None), Position.pnl_absolute.is_not(None))
            .order_by(Position.pnl_absolute.desc())
            .limit(limit)
        ).all()

        event_rows = []
        if include_events:
            event_filters = [TradeEvent.ts >= cutoff]
            if mode != "any":
                event_filters.append(TradeEvent.mode == mode)
            event_rows = db.execute(
                select(TradeEvent.event_type, func.count())
                .where(*event_filters)
                .group_by(TradeEvent.event_type)
                .order_by(func.count().desc())
            ).all()

    open_positions = max(total - closed, 0)
    win_rate = (wins / closed) if closed else None

    print("\n📈 Trade Analysis\n")
    print(f"Window: last {days} days (since {cutoff.isoformat()})")
    print(f"Mode: {mode} | Market: {market_type}")
    print(f"Positions: {total} total | {closed} closed | {open_positions} open")
    print(f"Win rate: {_format_pct(win_rate)}")
    print(f"Total P&L: {pnl_total:.4f}")
    print(f"Avg P&L%: {_format_pct(pnl_avg)}")
    print(f"Avg hold: {_format_seconds(hold_avg)}")
    print(f"Avg convergence: {_format_seconds(conv_avg)}")
    print(f"Avg edge capture: {_format_number(edge_capture_avg)}")

    if breakdown:
        print("\nBy market type:")
        for market, count, closed_count, pnl_sum, pnl_mean in breakdown:
            print(
                f"- {market or 'unknown'}: "
                f"{count} total | {closed_count} closed | "
                f"P&L {pnl_sum:.4f} | Avg P&L% {_format_pct(pnl_mean)}"
            )

    if top_rows:
        print("\nTop positions:")
        for position, fixture in top_rows:
            match = _format_match(fixture)
            market_label = _format_market(position.market_type, position.game_number)
            pnl_abs = position.pnl_absolute or 0.0
            pnl_pct = _format_pct(position.pnl_percent)
            print(
                f"- {pnl_abs:>8.4f} ({pnl_pct}) | {market_label} | {position.side} | {match}"
            )

    if include_events and event_rows:
        print("\nTrade events:")
        for event_type, count in event_rows:
            print(f"- {event_type}: {count}")


def _format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _format_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}s"


def _format_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _format_market(market_type: str | None, game_number: int | None) -> str:
    if market_type == "game_winner" and game_number:
        return f"game_winner G{game_number}"
    return market_type or "unknown"


def _format_match(fixture: Fixture | None) -> str:
    if not fixture:
        return "-"
    team_a = fixture.team_a_name or "Team A"
    team_b = fixture.team_b_name or "Team B"
    return f"{team_a} vs {team_b}"
