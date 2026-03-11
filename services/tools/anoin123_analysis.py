"""
Analysis of Polymarket user @anoin123 (Whimsical-Terminal)
Wallet: 0x96489abcb9f583d6835c8ef95ffc923d05a86825

Pulls positions + trades from the Data API, then summarizes:
  - Portfolio overview (total invested, current value, unrealized PnL)
  - Position breakdown by theme/market
  - Win rate on resolved positions
  - Concentration / risk analysis
  - Recent trading activity
"""

import json
import requests
from collections import defaultdict
from datetime import datetime, timezone

WALLET = "0x96489abcb9f583d6835c8ef95ffc923d05a86825"
DATA_API = "https://data-api.polymarket.com"


def fetch_all_positions():
    """Paginate through all positions."""
    all_positions = []
    offset = 0
    limit = 500
    while True:
        resp = requests.get(
            f"{DATA_API}/positions",
            params={
                "user": WALLET,
                "limit": limit,
                "offset": offset,
                "sortBy": "CURRENT",
                "sortDirection": "DESC",
            },
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_positions.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return all_positions


def fetch_recent_trades(limit=500):
    """Fetch most recent trades."""
    resp = requests.get(
        f"{DATA_API}/trades",
        params={
            "user": WALLET,
            "limit": limit,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def analyze_positions(positions):
    """Core position analysis."""
    total_invested = 0.0
    total_current = 0.0
    total_pnl = 0.0
    total_realized = 0.0

    # Group by event
    by_event = defaultdict(list)
    by_outcome = defaultdict(list)
    redeemable_count = 0
    active_count = 0

    for p in positions:
        initial = p.get("initialValue", 0) or 0
        current = p.get("currentValue", 0) or 0
        cash_pnl = p.get("cashPnl", 0) or 0
        realized = p.get("realizedPnl", 0) or 0
        size = p.get("size", 0) or 0

        total_invested += initial
        total_current += current
        total_pnl += cash_pnl
        total_realized += realized

        if p.get("redeemable"):
            redeemable_count += 1
        if size > 0:
            active_count += 1

        event_slug = p.get("eventSlug", "unknown")
        by_event[event_slug].append(p)
        by_outcome[p.get("outcome", "unknown")].append(p)

    return {
        "total_invested": total_invested,
        "total_current": total_current,
        "unrealized_pnl": total_pnl,
        "realized_pnl": total_realized,
        "total_pnl": total_pnl,
        "pnl_pct": (total_pnl / total_invested * 100) if total_invested else 0,
        "num_positions": len(positions),
        "active_positions": active_count,
        "redeemable": redeemable_count,
        "by_event": by_event,
        "by_outcome": by_outcome,
    }


def print_portfolio_summary(analysis):
    """Print high-level portfolio stats."""
    print("=" * 70)
    print("  @anoin123 (Whimsical-Terminal) — Portfolio Analysis")
    print("=" * 70)
    print()
    print(f"  Total Invested (cost basis):  ${analysis['total_invested']:>14,.2f}")
    print(f"  Current Portfolio Value:      ${analysis['total_current']:>14,.2f}")
    print(f"  Unrealized PnL:              ${analysis['unrealized_pnl']:>+14,.2f}")
    print(f"  Realized PnL:                ${analysis['realized_pnl']:>+14,.2f}")
    print(f"  Total PnL:                   ${analysis['total_pnl']:>+14,.2f}")
    print(f"  Return on Investment:         {analysis['pnl_pct']:>+13.2f}%")
    print()
    print(f"  Total Positions:              {analysis['num_positions']}")
    print(f"  Active (size > 0):            {analysis['active_positions']}")
    print(f"  Redeemable (won/resolved):    {analysis['redeemable']}")
    print()


def print_top_positions(positions, n=15):
    """Print top N positions by current value."""
    sorted_pos = sorted(
        positions, key=lambda p: p.get("currentValue", 0) or 0, reverse=True
    )
    print("-" * 70)
    print(f"  Top {n} Positions by Current Value")
    print("-" * 70)
    print(
        f"  {'Market':<42} {'Side':<5} {'Invested':>10} {'Current':>10} {'PnL%':>7}"
    )
    print("  " + "-" * 66)
    for p in sorted_pos[:n]:
        title = (p.get("title") or "")[:40]
        outcome = p.get("outcome", "?")
        initial = p.get("initialValue", 0) or 0
        current = p.get("currentValue", 0) or 0
        pct = p.get("percentPnl", 0) or 0
        print(
            f"  {title:<42} {outcome:<5} ${initial:>9,.0f} ${current:>9,.0f} {pct:>+6.1f}%"
        )
    print()


def print_theme_concentration(by_event):
    """Show concentration by event theme."""
    print("-" * 70)
    print("  Position Concentration by Event")
    print("-" * 70)

    event_totals = []
    for slug, positions in by_event.items():
        total_current = sum(p.get("currentValue", 0) or 0 for p in positions)
        total_invested = sum(p.get("initialValue", 0) or 0 for p in positions)
        total_pnl = sum(p.get("cashPnl", 0) or 0 for p in positions)
        num = len(positions)
        event_totals.append(
            {
                "slug": slug,
                "current": total_current,
                "invested": total_invested,
                "pnl": total_pnl,
                "count": num,
            }
        )

    event_totals.sort(key=lambda x: x["current"], reverse=True)
    grand_total = sum(e["current"] for e in event_totals)

    print(
        f"  {'Event':<45} {'Current':>10} {'PnL':>10} {'% Port':>7} {'#Pos':>5}"
    )
    print("  " + "-" * 66)
    for e in event_totals[:20]:
        slug_short = e["slug"][:43]
        pct = (e["current"] / grand_total * 100) if grand_total else 0
        print(
            f"  {slug_short:<45} ${e['current']:>9,.0f} ${e['pnl']:>+9,.0f} {pct:>6.1f}% {e['count']:>4}"
        )

    # Summary stats
    top5_pct = (
        sum(e["current"] for e in event_totals[:5]) / grand_total * 100
        if grand_total
        else 0
    )
    print()
    print(f"  Total unique events: {len(event_totals)}")
    print(f"  Top 5 events = {top5_pct:.1f}% of portfolio")
    print()


def print_outcome_bias(by_outcome, total_current):
    """Show Yes vs No positioning."""
    print("-" * 70)
    print("  Outcome Bias (Yes vs No)")
    print("-" * 70)
    for outcome in ["Yes", "No"]:
        positions = by_outcome.get(outcome, [])
        val = sum(p.get("currentValue", 0) or 0 for p in positions)
        pct = (val / total_current * 100) if total_current else 0
        count = len(positions)
        print(f"  {outcome}: ${val:>12,.2f}  ({pct:.1f}% of portfolio, {count} positions)")
    print()


def print_trade_activity(trades):
    """Summarize recent trading patterns."""
    print("-" * 70)
    print("  Recent Trading Activity (last 500 trades)")
    print("-" * 70)

    if not trades:
        print("  No trades found.")
        return

    buy_count = sum(1 for t in trades if t.get("side") == "BUY")
    sell_count = sum(1 for t in trades if t.get("side") == "SELL")
    buy_vol = sum(
        (t.get("size", 0) or 0) * (t.get("price", 0) or 0)
        for t in trades
        if t.get("side") == "BUY"
    )
    sell_vol = sum(
        (t.get("size", 0) or 0) * (t.get("price", 0) or 0)
        for t in trades
        if t.get("side") == "SELL"
    )

    # Time range
    timestamps = [t.get("timestamp", 0) for t in trades if t.get("timestamp")]
    if timestamps:
        earliest = datetime.fromtimestamp(min(timestamps), tz=timezone.utc)
        latest = datetime.fromtimestamp(max(timestamps), tz=timezone.utc)
        span_days = (latest - earliest).total_seconds() / 86400
    else:
        earliest = latest = None
        span_days = 0

    # Unique markets traded
    unique_markets = set(t.get("title", "") for t in trades)

    print(f"  Period: {earliest} → {latest}  ({span_days:.1f} days)")
    print(f"  Buys:  {buy_count:>5}  (${buy_vol:>12,.2f} notional)")
    print(f"  Sells: {sell_count:>5}  (${sell_vol:>12,.2f} notional)")
    print(f"  Unique markets traded: {len(unique_markets)}")
    print()

    # Most traded markets
    market_counts = defaultdict(lambda: {"buys": 0, "sells": 0, "volume": 0.0})
    for t in trades:
        title = t.get("title", "unknown")
        side = t.get("side", "BUY")
        vol = (t.get("size", 0) or 0) * (t.get("price", 0) or 0)
        if side == "BUY":
            market_counts[title]["buys"] += 1
        else:
            market_counts[title]["sells"] += 1
        market_counts[title]["volume"] += vol

    sorted_markets = sorted(
        market_counts.items(), key=lambda x: x[1]["volume"], reverse=True
    )
    print("  Most Traded Markets (by $ volume):")
    print(f"  {'Market':<48} {'Buys':>5} {'Sells':>5} {'Volume':>10}")
    print("  " + "-" * 66)
    for title, stats in sorted_markets[:10]:
        t_short = title[:46]
        print(
            f"  {t_short:<48} {stats['buys']:>5} {stats['sells']:>5} ${stats['volume']:>9,.0f}"
        )
    print()


def print_risk_analysis(positions, analysis):
    """Key risk metrics."""
    print("-" * 70)
    print("  Risk Analysis")
    print("-" * 70)

    # Avg price paid vs current price
    avg_prices = []
    cur_prices = []
    for p in positions:
        if (p.get("size") or 0) > 0:
            avg_prices.append(p.get("avgPrice", 0) or 0)
            cur_prices.append(p.get("curPrice", 0) or 0)

    if avg_prices:
        mean_avg = sum(avg_prices) / len(avg_prices)
        mean_cur = sum(cur_prices) / len(cur_prices)
        print(f"  Avg entry price across positions: {mean_avg:.4f}")
        print(f"  Avg current price across positions: {mean_cur:.4f}")
        print()

    # Positions near expiry
    now = datetime.now(tz=timezone.utc)
    near_expiry = []
    for p in positions:
        end = p.get("endDate")
        if end and (p.get("size") or 0) > 0:
            try:
                end_dt = datetime.fromisoformat(end + "T00:00:00+00:00")
                days_left = (end_dt - now).days
                if days_left <= 30:
                    near_expiry.append((p, days_left))
            except (ValueError, TypeError):
                pass

    if near_expiry:
        near_expiry.sort(key=lambda x: x[1])
        print(f"  Positions expiring within 30 days: {len(near_expiry)}")
        print(f"  {'Market':<42} {'Days':>5} {'Current':>10} {'Outcome':<5}")
        print("  " + "-" * 60)
        for p, days in near_expiry[:10]:
            title = (p.get("title") or "")[:40]
            current = p.get("currentValue", 0) or 0
            outcome = p.get("outcome", "?")
            print(f"  {title:<42} {days:>5} ${current:>9,.0f} {outcome}")
    print()

    # Largest single-position risk
    sorted_by_size = sorted(
        positions, key=lambda p: p.get("currentValue", 0) or 0, reverse=True
    )
    if sorted_by_size:
        biggest = sorted_by_size[0]
        total = analysis["total_current"]
        pct = (
            (biggest.get("currentValue", 0) or 0) / total * 100 if total else 0
        )
        print(
            f"  Largest single position: ${biggest.get('currentValue', 0):,.0f} "
            f"({pct:.1f}% of portfolio)"
        )
        print(f"    → {biggest.get('title', 'unknown')}")
    print()


def main():
    print("Fetching positions...")
    positions = fetch_all_positions()
    print(f"  → {len(positions)} positions loaded")

    print("Fetching recent trades...")
    trades = fetch_recent_trades(limit=500)
    print(f"  → {len(trades)} trades loaded")
    print()

    analysis = analyze_positions(positions)

    print_portfolio_summary(analysis)
    print_top_positions(positions, n=15)
    print_theme_concentration(analysis["by_event"])
    print_outcome_bias(analysis["by_outcome"], analysis["total_current"])
    print_trade_activity(trades)
    print_risk_analysis(positions, analysis)

    # Save raw data for further analysis
    with open("services/tools/anoin123_positions.json", "w") as f:
        json.dump(positions, f, indent=2)
    with open("services/tools/anoin123_trades.json", "w") as f:
        json.dump(trades, f, indent=2)
    print("Raw data saved to anoin123_positions.json and anoin123_trades.json")


if __name__ == "__main__":
    main()
