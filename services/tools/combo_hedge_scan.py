"""
Full combinatorial arb hedge analysis.

For each match where CS implied > PM moneyline ask, pull:
  - Moneyline market (series winner) prices
  - Game 1, Game 2, Game 3 (and G4/G5 for BO5) market prices
  - Compute all series paths and hedge P&L

Usage (from repo root):
    python -m services.tools.combo_hedge_scan
"""

import json
import logging
import re
import sys
from dataclasses import dataclass, field

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

PM_GAMMA = "https://gamma-api.polymarket.com"
PM_CLOB = "https://clob.polymarket.com"


@dataclass
class MarketPrices:
    """Prices for one two-outcome market."""
    label: str  # e.g. "Moneyline", "Game 1", "Game 2"
    outcome_a: str
    outcome_b: str
    token_a: str
    token_b: str
    ask_a: float | None = None
    bid_a: float | None = None
    ask_b: float | None = None
    bid_b: float | None = None


@dataclass
class MatchBundle:
    """All markets for one match."""
    event_title: str
    event_id: str
    moneyline: MarketPrices | None = None
    games: list[MarketPrices] = field(default_factory=list)


def fetch_lol_match_bundles() -> list[MatchBundle]:
    """Fetch all LoL match events with moneyline + game markets."""
    client = httpx.Client(timeout=30)
    bundles: list[MatchBundle] = []

    try:
        resp = client.get(f"{PM_GAMMA}/events", params={
            "tag_id": 65, "active": "true", "closed": "false", "limit": 100,
        })
        resp.raise_for_status()
        events = resp.json() if isinstance(resp.json(), list) else []
        logger.info("Polymarket: %d LoL events", len(events))

        all_tokens: list[str] = []
        token_to_market: dict[str, tuple[int, str, str]] = {}  # token -> (bundle_idx, field, side)

        for event in events:
            title = event.get("title", "")
            markets = event.get("markets") or []

            moneyline = None
            game_mkts: list[MarketPrices] = []

            for market in markets:
                q = market.get("question", "")
                q_lower = q.lower()

                # Parse outcomes/tokens
                outcomes = market.get("outcomes", "[]")
                if isinstance(outcomes, str):
                    try: outcomes = json.loads(outcomes)
                    except: outcomes = []
                tokens = market.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    try: tokens = json.loads(tokens)
                    except: tokens = []

                if len(outcomes) < 2 or len(tokens) < 2:
                    continue
                if outcomes[0] in ("Yes", "No", "Over", "Under"):
                    continue

                # Classify market type
                is_moneyline = ("(bo3)" in q_lower or "(bo5)" in q_lower) and \
                    not any(skip in q_lower for skip in [
                        "game 1", "game 2", "game 3", "game 4", "game 5",
                        "first blood", "over/under", "o/u ", "total kills",
                        "handicap", "most ", "will ",
                    ])

                is_game = "game" in q_lower and "winner" in q_lower and \
                    any(f"game {n}" in q_lower for n in range(1, 6))

                if not is_moneyline and not is_game:
                    continue

                mp = MarketPrices(
                    label="Moneyline" if is_moneyline else q,
                    outcome_a=str(outcomes[0]),
                    outcome_b=str(outcomes[1]),
                    token_a=tokens[0],
                    token_b=tokens[1],
                )

                if is_moneyline:
                    moneyline = mp
                elif is_game:
                    # Extract game number
                    gn_match = re.search(r"game (\d)", q_lower)
                    gn = int(gn_match.group(1)) if gn_match else 0
                    mp.label = f"Game {gn}"
                    game_mkts.append(mp)

                all_tokens.extend([tokens[0], tokens[1]])

            if moneyline and game_mkts:
                # Sort game markets by game number
                game_mkts.sort(key=lambda m: m.label)
                bundle = MatchBundle(
                    event_title=title,
                    event_id=str(event.get("id", "")),
                    moneyline=moneyline,
                    games=game_mkts,
                )
                bundles.append(bundle)

        # Batch fetch CLOB books
        if all_tokens:
            logger.info("Fetching CLOB books for %d tokens...", len(all_tokens))
            book_map: dict[str, dict] = {}
            batch_size = 50
            for i in range(0, len(all_tokens), batch_size):
                batch = all_tokens[i:i + batch_size]
                try:
                    resp = client.post(f"{PM_CLOB}/books",
                        json=[{"token_id": t} for t in batch],
                        headers={"Content-Type": "application/json"}, timeout=30)
                    resp.raise_for_status()
                    for b in (resp.json() if isinstance(resp.json(), list) else []):
                        aid = str(b.get("asset_id", ""))
                        if aid:
                            bids = b.get("bids") or []
                            asks = b.get("asks") or []
                            book_map[aid] = {
                                "bid": max((float(x["price"]) for x in bids), default=None) if bids else None,
                                "ask": min((float(x["price"]) for x in asks), default=None) if asks else None,
                            }
                except Exception as e:
                    logger.warning("CLOB batch %d failed: %s", i, e)

            # Apply prices
            for bundle in bundles:
                for mp in [bundle.moneyline] + bundle.games:
                    if mp is None:
                        continue
                    if mp.token_a in book_map:
                        mp.ask_a = book_map[mp.token_a].get("ask")
                        mp.bid_a = book_map[mp.token_a].get("bid")
                    if mp.token_b in book_map:
                        mp.ask_b = book_map[mp.token_b].get("ask")
                        mp.bid_b = book_map[mp.token_b].get("bid")

    except httpx.HTTPError as e:
        logger.error("Error: %s", e)
    finally:
        client.close()

    # Filter to bundles with actual prices
    priced = [b for b in bundles if b.moneyline and b.moneyline.ask_a is not None]
    logger.info("Found %d LoL match bundles with moneyline + game markets + prices", len(priced))
    return priced


def analyze_hedge(bundle: MatchBundle, fair_prob_a: float | None = None) -> None:
    """
    Analyze the full hedge for one match.

    Strategy: Buy moneyline A YES + buy B YES on each available game market.
    If A wins the series, moneyline pays $1. If A loses any individual game,
    the game hedge on B pays $1 for that game.
    """
    ml = bundle.moneyline
    if ml is None:
        return

    team_a = ml.outcome_a
    team_b = ml.outcome_b

    print(f"\n{'=' * 80}")
    print(f"  {bundle.event_title}")
    print(f"  Event ID: {bundle.event_id}")
    print(f"{'=' * 80}")

    # Show all market prices
    print(f"\n  {'Market':<20s}  {'':>12s}  {'Ask A':>8s}  {'Bid A':>8s}  {'Ask B':>8s}  {'Bid B':>8s}  {'Spread':>8s}")
    print(f"  {'':20s}  {'':>12s}  {'({})'.format(team_a[:8]):>8s}  {'':>8s}  {'({})'.format(team_b[:8]):>8s}")

    for mp in [ml] + bundle.games:
        if mp is None:
            continue
        spread = ""
        if mp.ask_a is not None and mp.bid_a is not None:
            spread = f"{mp.ask_a - mp.bid_a:.3f}"
        print(f"  {mp.label:<20s}  "
              f"{mp.outcome_a[:12]:>12s}  "
              f"{mp.ask_a or 0:8.3f}  {mp.bid_a or 0:8.3f}  "
              f"{mp.ask_b or 0:8.3f}  {mp.bid_b or 0:8.3f}  "
              f"{spread:>8s}")

    ml_ask_a = ml.ask_a
    ml_ask_b = ml.ask_b
    if ml_ask_a is None:
        print("\n  [SKIP] No moneyline ask price for A")
        return

    # ── BO3 path analysis ────────────────────────────────────────────────
    # Assume $100 on moneyline A YES
    stake_ml = 100.0
    cost_ml = stake_ml * ml_ask_a

    print(f"\n  === Hedge Analysis (${stake_ml:.0f} on {team_a} moneyline) ===")
    print(f"  Moneyline cost: ${cost_ml:.2f} (ask={ml_ask_a})")

    # Game markets for hedging: buy B YES on each game
    game_costs: list[tuple[str, float, float | None]] = []
    total_hedge_cost = 0.0

    for gm in bundle.games:
        if gm.ask_b is not None:
            # Hedge: buy B YES on this game
            # Size the hedge proportional to moneyline stake
            # Simple approach: equal allocation across games
            hedge_per_game = cost_ml * 0.35  # ~35% of main bet per game
            game_cost = hedge_per_game * gm.ask_b  # Cost to buy that many B shares

            # Actually, let's think about this differently.
            # We buy $X worth of B YES on each game.
            # If B wins that game, we get $X/ask_b shares * $1 = $X/ask_b payout
            # If B loses (A wins), we lose $X.
            game_costs.append((gm.label, gm.ask_b, gm.ask_a))
            total_hedge_cost += gm.ask_b  # Cost per $1 of hedge payout
        else:
            game_costs.append((gm.label, None, None))

    n_games = len(bundle.games)

    if not game_costs:
        print("  [SKIP] No game markets available for hedging")
        return

    print(f"\n  Game markets for hedge (buy {team_b} YES on each):")
    for label, ask_b, ask_a in game_costs:
        if ask_b is not None:
            print(f"    {label}: {team_b} ask={ask_b:.3f}  ({team_a} ask={ask_a:.3f})")
        else:
            print(f"    {label}: NO PRICE")

    # ── Enumerate all series paths ───────────────────────────────────────
    # For BO3: paths are WW, WLW, LWW, WLL, LWL, LLx (where W=A wins, L=B wins)
    # For BO5: more paths

    is_bo5 = "(bo5)" in bundle.event_title.lower()
    wins_needed = 3 if is_bo5 else 2
    max_games = 5 if is_bo5 else 3

    print(f"\n  Series format: {'BO5' if is_bo5 else 'BO3'} (first to {wins_needed})")

    # Get per-game ask prices for A and B
    game_ask_a: list[float | None] = []
    game_ask_b: list[float | None] = []
    for gm in bundle.games:
        game_ask_a.append(gm.ask_a)
        game_ask_b.append(gm.ask_b)

    # Pad if fewer game markets than max_games
    while len(game_ask_a) < max_games:
        game_ask_a.append(None)
        game_ask_b.append(None)

    # ── Concrete $100 example ────────────────────────────────────────────
    # Position: Buy $100 of A moneyline YES
    # Hedge: Buy $H_i of B YES on Game i
    #
    # If A wins series: moneyline pays $100/ask_a. Lose all game hedges where A won.
    #                   Game hedges where B won pay $H_i/ask_b_i per game.
    # If A loses series: lose $100 moneyline. Game hedges where B won pay out.

    # Simple hedge sizing: allocate a fraction of the moneyline cost to each game
    # Let's try: hedge_per_game = 35% of moneyline cost for BO3, 25% for BO5
    hedge_frac = 0.25 if is_bo5 else 0.35
    hedge_budget_per_game = cost_ml * hedge_frac

    print(f"\n  Hedge budget: {hedge_frac:.0%} of moneyline cost per game = "
          f"${hedge_budget_per_game:.2f}/game")

    total_hedge = 0.0
    hedge_shares: list[float] = []  # shares of B bought per game
    for i in range(min(n_games, max_games)):
        ask_b = game_ask_b[i]
        if ask_b and ask_b > 0:
            shares = hedge_budget_per_game / ask_b
            hedge_shares.append(shares)
            total_hedge += hedge_budget_per_game
            print(f"    Game {i+1}: Buy {shares:.1f} shares of {team_b} @ {ask_b:.3f} "
                  f"= ${hedge_budget_per_game:.2f}")
        else:
            hedge_shares.append(0)
            print(f"    Game {i+1}: NO HEDGE (no price)")

    total_outlay = cost_ml + total_hedge
    ml_shares = stake_ml  # $100 buys 100 shares at varying price... 
    # Actually: $cost_ml buys cost_ml/ask_a shares? No.
    # On Polymarket: you pay ask * shares. So $100 budget buys 100/ask shares? No.
    # Polymarket: buy N shares at ask price. Cost = N * ask. Payout = N * $1 if wins.
    # So with $100 budget: N = 100/ask. Payout = 100/ask * 1 = 100/ask.
    # 
    # Let me redo this more carefully:
    # Budget = $100 on moneyline. Shares = 100/ask_a. If A wins, payout = 100/ask_a.
    ml_shares_bought = cost_ml / ml_ask_a  # = stake_ml (trivially, since cost = stake * ask)
    # Wait, I defined cost_ml = stake_ml * ml_ask_a, so ml_shares = cost_ml / ml_ask_a = stake_ml
    # That means I'm buying `stake_ml` shares. Payout if A wins = stake_ml * $1 = $100.
    # Profit if A wins (from ML alone) = $100 - cost_ml = 100 - 100*ask_a = 100*(1-ask_a)

    print(f"\n  Total outlay: ${total_outlay:.2f} "
          f"(ML: ${cost_ml:.2f} + Hedge: ${total_hedge:.2f})")
    print(f"  ML shares: {stake_ml:.0f} → pays ${stake_ml:.0f} if {team_a} wins series")

    # ── Path enumeration ─────────────────────────────────────────────────
    def enumerate_paths(wins_needed: int, max_games: int):
        """Generate all possible series paths as tuples of 'W'/'L'."""
        paths = []
        def _recurse(path, a_wins, b_wins):
            if a_wins == wins_needed or b_wins == wins_needed:
                paths.append(tuple(path))
                return
            if len(path) >= max_games:
                return
            _recurse(path + ['W'], a_wins + 1, b_wins)
            _recurse(path + ['L'], a_wins, b_wins + 1)
        _recurse([], 0, 0)
        return paths

    paths = enumerate_paths(wins_needed, max_games)

    print(f"\n  {'Path':<12s}  {'A wins?':>8s}  {'ML P&L':>10s}  {'Hedge P&L':>10s}  "
          f"{'Net P&L':>10s}  {'ROI':>8s}")
    print(f"  {'─' * 70}")

    for path in paths:
        a_won_series = path.count('W') == wins_needed

        # ML P&L
        if a_won_series:
            ml_pnl = stake_ml - cost_ml  # Win: payout - cost
        else:
            ml_pnl = -cost_ml  # Lose: lose entire cost

        # Hedge P&L: for each game, if B won (L), hedge pays out
        hedge_pnl = 0.0
        for i, result in enumerate(path):
            if i < len(hedge_shares) and hedge_shares[i] > 0:
                if result == 'L':
                    # B won this game, hedge pays out
                    hedge_pnl += hedge_shares[i] * 1.0 - hedge_budget_per_game
                else:
                    # A won this game, hedge lost
                    hedge_pnl -= hedge_budget_per_game

        # But we also lose unplayed game hedges
        # Games not played: if series ended early, those hedges are...
        # On Polymarket, if Game 3 never happens, does the market resolve?
        # Typically YES - it resolves as void/50-50 or the market just stays open.
        # For now assume unplayed game markets resolve as VOID (get money back).
        games_played = len(path)
        for i in range(games_played, min(n_games, max_games)):
            pass  # Assume money back on unplayed games (void)

        net_pnl = ml_pnl + hedge_pnl
        roi = net_pnl / total_outlay * 100

        path_str = "-".join(path)
        series_result = "A WINS" if a_won_series else "B WINS"

        print(f"  {path_str:<12s}  {series_result:>8s}  ${ml_pnl:>9.2f}  ${hedge_pnl:>9.2f}  "
              f"${net_pnl:>9.2f}  {roi:>7.1f}%")

    # Note about unplayed games
    print(f"\n  NOTE: Assumes unplayed game markets resolve void (money returned).")
    print(f"  If they resolve as loss, worst-case increases by up to "
          f"${hedge_budget_per_game * (max_games - wins_needed):.2f}")


def main() -> None:
    print("=" * 80)
    print("COMBINATORIAL ARB — FULL HEDGE ANALYSIS")
    print("Moneyline + per-game hedge positions on Polymarket")
    print("=" * 80)

    bundles = fetch_lol_match_bundles()

    if not bundles:
        print("\nNo LoL matches with moneyline + game markets found.")
        sys.exit(1)

    print(f"\nFound {len(bundles)} matches with moneyline + game markets:")
    for b in bundles:
        gcount = len(b.games)
        ml = b.moneyline
        print(f"  {b.event_title}")
        if ml:
            print(f"    ML: {ml.outcome_a} ask={ml.ask_a}  {ml.outcome_b} ask={ml.ask_b}  "
                  f"| {gcount} game markets")

    # Analyze each match
    for bundle in bundles:
        analyze_hedge(bundle)


if __name__ == "__main__":
    main()
