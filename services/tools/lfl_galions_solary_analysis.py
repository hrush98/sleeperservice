"""
Combo arb analysis: Galions vs Solary (LFL BO3).
Reference: Pinnacle ML + Pinnacle Map 1 from GoalServe (no Pinnacle Correct Score for LFL).
"""
import json
from pathlib import Path

import httpx

PM_GAMMA = "https://gamma-api.polymarket.com"
PM_CLOB = "https://clob.polymarket.com"
CACHE = Path(__file__).parent / "gs_odds_cache.json"


def devig(odds_a: float, odds_b: float) -> tuple[float, float]:
    ia, ib = 1 / odds_a, 1 / odds_b
    t = ia + ib
    return ia / t, ib / t


def fair_bo3(p: float) -> float:
    """P(team wins BO3) = p^2 + 2*p^2*(1-p) = p^2*(3-2p)."""
    q = 1 - p
    return p * p * (3 - 2 * p)


def main() -> None:
    # ── GoalServe: Pinnacle for Galions vs Solary ─────────────────────────
    if not CACHE.exists():
        print("No GoalServe cache. Run: python -m tools.gs_cache")
        return

    data = json.loads(CACHE.read_text())
    matches = data.get("scores", {}).get("match", [])
    if isinstance(matches, dict):
        matches = [matches]

    gs_match = None
    for m in matches:
        home = m.get("localteam", {}).get("@name", "")
        away = m.get("awayteam", {}).get("@name", "")
        if home == "Galions" and away == "Solary":
            gs_match = m
            break

    if not gs_match:
        print("Galions vs Solary not found in GoalServe cache.")
        return

    league = gs_match.get("@league", "?")
    gs_date = gs_match.get("@date", "?")
    gs_time = gs_match.get("@time", "?")

    # Pinnacle odds: Home = Galions, Away = Solary
    pncl_ml_home, pncl_ml_away = None, None
    pncl_map1_home, pncl_map1_away = None, None

    odds_block = gs_match.get("odds", {})
    market_types = odds_block.get("type", []) or []
    if isinstance(market_types, dict):
        market_types = [market_types]
    for mt in market_types:
        if not mt or not isinstance(mt, dict):
            continue
        name = (mt.get("@value") or mt.get("@name") or "").lower()
        bookmakers = mt.get("bookmaker", []) or []
        if isinstance(bookmakers, dict):
            bookmakers = [bookmakers]
        for bk in bookmakers:
            if not isinstance(bk, dict) or "pncl" not in (bk.get("@name") or "").lower():
                continue
            oo = bk.get("odd", [])
            if isinstance(oo, dict):
                oo = [oo]
            home_odds, away_odds = None, None
            for o in oo:
                n = (o.get("@name") or "").strip()
                v = o.get("@value")
                if v and n:
                    try:
                        if n == "Home":
                            home_odds = float(v)
                        elif n == "Away":
                            away_odds = float(v)
                    except (TypeError, ValueError):
                        pass
            if home_odds is not None and away_odds is not None:
                if "map 1" in name:
                    pncl_map1_home, pncl_map1_away = home_odds, away_odds
                elif "home/away" in name and "map" not in name:
                    pncl_ml_home, pncl_ml_away = home_odds, away_odds

    print("=" * 90)
    print("Galions vs Solary (LFL) — Combo Arb Analysis")
    print("=" * 90)
    print(f"  GoalServe: {league} | {gs_date} {gs_time}")
    print(f"  Reference: Pinnacle (no Correct Score for LFL; we have ML + Map 1)")
    print()

    # Reference probabilities
    # Galions = Home, Solary = Away
    if pncl_ml_home and pncl_ml_away:
        p_galions_ml, p_solary_ml = devig(pncl_ml_home, pncl_ml_away)
        print(f"  Pinnacle ML:     Galions {pncl_ml_home}  Solary {pncl_ml_away}")
        print(f"    -> De-vigged:  Galions {p_galions_ml:.1%}  Solary {p_solary_ml:.1%}")
    else:
        p_galions_ml = p_solary_ml = None
        print("  Pinnacle ML: not found")

    if pncl_map1_home and pncl_map1_away:
        p_galions_game, p_solary_game = devig(pncl_map1_home, pncl_map1_away)
        fair_galions_bo3 = fair_bo3(p_galions_game)
        fair_solary_bo3 = 1 - fair_galions_bo3
        print(f"  Pinnacle Map 1:  Galions {pncl_map1_home}  Solary {pncl_map1_away}")
        print(f"    -> De-vigged per-game: Galions {p_galions_game:.1%}  Solary {p_solary_game:.1%}")
        print(f"    -> Fair BO3 series:   Galions {fair_galions_bo3:.1%}  Solary {fair_solary_bo3:.1%}")
    else:
        fair_galions_bo3 = fair_solary_bo3 = p_galions_game = None
        print("  Pinnacle Map 1: not found")

    print()

    # ── Polymarket ─────────────────────────────────────────────────────────
    client = httpx.Client(timeout=30)
    resp = client.get(
        f"{PM_GAMMA}/events",
        params={"tag_id": 65, "active": "true", "closed": "false", "limit": 100},
    )
    events = resp.json() if isinstance(resp.json(), list) else []

    target = None
    for e in events:
        t = e.get("title", "").lower()
        if "galions" in t and "solary" in t:
            target = e
            break

    if not target:
        print("  Polymarket: Galions vs Solary not found.")
        client.close()
        return

    print("  Polymarket event:", target.get("title"))
    print(f"  Event ID: {target.get('id')}")
    print()

    markets = target.get("markets", [])
    all_tokens = []
    minfo = []
    for m in markets:
        q = m.get("question", "")
        outcomes = m.get("outcomes", "[]")
        tokens = m.get("clobTokenIds", "[]")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if len(outcomes) >= 2 and len(tokens) >= 2:
            minfo.append({"q": q, "o": outcomes, "t": tokens})
            all_tokens.extend(tokens)

    # CLOB
    bmap = {}
    for i in range(0, len(all_tokens), 50):
        batch = all_tokens[i : i + 50]
        r = client.post(
            f"{PM_CLOB}/books",
            json=[{"token_id": t} for t in batch],
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 200:
            continue
        for b in (r.json() if isinstance(r.json(), list) else []):
            aid = str(b.get("asset_id", ""))
            bids = b.get("bids") or []
            asks = b.get("asks") or []
            bb = max((float(x["price"]) for x in bids), default=None) if bids else None
            ba = min((float(x["price"]) for x in asks), default=None) if asks else None
            bmap[aid] = {"bid": bb, "ask": ba}

    pm_ml = None
    pm_games = []

    for mi in minfo:
        q, o, t = mi["q"], mi["o"], mi["t"]
        a = bmap.get(t[0], {})
        b = bmap.get(t[1], {})
        ask_a, bid_a = a.get("ask"), a.get("bid")
        ask_b, bid_b = b.get("ask"), b.get("bid")

        is_game = any(f"game {i} winner" in q.lower() for i in range(1, 5))
        is_ml = ("bo3" in q.lower() or "bo5" in q.lower()) and not is_game
        if "game" in q.lower() and "winner" in q.lower() and "first" not in q.lower():
            is_game = True

        if is_ml:
            pm_ml = {"o": o, "ask_a": ask_a, "bid_a": bid_a, "ask_b": ask_b, "bid_b": bid_b}
        if is_game:
            gnum = next((i for i in range(1, 5) if f"game {i}" in q.lower()), None)
            pm_games.append({"gnum": gnum, "o": o, "ask_a": ask_a, "bid_a": bid_a, "ask_b": ask_b, "bid_b": bid_b})

    pm_games.sort(key=lambda x: x["gnum"] or 99)

    # Align: outcome_a = first team (could be Galions or Solary)
    o0 = (pm_ml or {}).get("o", ["", ""])[0].lower()
    galions_is_a = "galions" in o0
    if pm_ml:
        if galions_is_a:
            gal_ask, gal_bid = pm_ml["ask_a"], pm_ml["bid_a"]
            sol_ask, sol_bid = pm_ml["ask_b"], pm_ml["bid_b"]
        else:
            gal_ask, gal_bid = pm_ml["ask_b"], pm_ml["bid_b"]
            sol_ask, sol_bid = pm_ml["ask_a"], pm_ml["bid_a"]

        print("  PM Moneyline:")
        print(f"    Galions  ask={gal_ask}  bid={gal_bid}  spread={gal_ask - gal_bid if gal_ask and gal_bid else 'N/A'}")
        print(f"    Solary   ask={sol_ask}  bid={sol_bid}  spread={sol_ask - sol_bid if sol_ask and sol_bid else 'N/A'}")
        print()

        for gm in pm_games:
            if galions_is_a:
                ga, gb = gm["ask_a"], gm["ask_b"]
                gbid_a, gbid_b = gm["bid_a"], gm["bid_b"]
            else:
                ga, gb = gm["ask_b"], gm["ask_a"]
                gbid_a, gbid_b = gm["bid_b"], gm["bid_a"]
            print(f"  PM Game {gm['gnum']}: Galions ask={ga} bid={gbid_a}  Solary ask={gb} bid={gbid_b}")
        print()

    # ── Gap analysis ─────────────────────────────────────────────────────
    print("=" * 90)
    print("GAP: Pinnacle Reference vs Polymarket")
    print("=" * 90)

    if not pm_ml or gal_ask is None:
        print("  No PM moneyline prices.")
        client.close()
        return

    gal_mid = (gal_ask + gal_bid) / 2 if gal_ask and gal_bid else None
    sol_mid = (sol_ask + sol_bid) / 2 if sol_ask and sol_bid else None

    refs = []
    if p_galions_ml is not None:
        refs.append(("Pinnacle ML (series)", p_galions_ml))
    if fair_galions_bo3 is not None:
        refs.append(("Pinnacle Map 1 -> BO3 fair", fair_galions_bo3))

    # PM self-ref from game markets
    if pm_games:
        p_list = []
        for gm in pm_games:
            if galions_is_a:
                a, b = gm["ask_a"], gm["ask_b"]
            else:
                a, b = gm["ask_b"], gm["ask_a"]
            if a and b:
                p_list.append(a / (a + b))
        if p_list:
            avg_p = sum(p_list) / len(p_list)
            refs.append(("PM Games (self-ref BO3)", fair_bo3(avg_p)))

    print(f"\n  {'Reference':<35s}  {'Galions':>8s}  {'Solary':>8s}  {'vs PM Gal ask':>12s}  {'vs PM Gal mid':>12s}")
    print(f"  {'-'*35}  {'-'*8}  {'-'*8}  {'-'*12}  {'-'*12}")
    for name, p_gal in refs:
        p_sol = 1 - p_gal
        gap_ask = f"{(p_gal - gal_ask)*100:+.1f}%" if gal_ask is not None else "N/A"
        gap_mid = f"{(p_gal - gal_mid)*100:+.1f}%" if gal_mid else "N/A"
        marker = " << BUY Galions" if gal_ask is not None and p_gal - gal_ask >= 0.03 else ""
        marker_sol = " << BUY Solary" if sol_ask is not None and p_sol - sol_ask >= 0.03 else ""
        print(f"  {name:<35s}  {p_gal:>7.1%}  {p_sol:>7.1%}  {gap_ask:>12s}  {gap_mid:>12s}{marker}{marker_sol}")

    print(f"\n  PM: Galions ask={gal_ask} mid={gal_mid}  |  Solary ask={sol_ask} mid={sol_mid}")

    # Verdict
    print()
    print("=" * 90)
    print("VERDICT")
    print("=" * 90)
    if fair_galions_bo3 is not None and gal_ask is not None:
        gap = fair_galions_bo3 - gal_ask
        if gap >= 0.03:
            print(f"  Combo arb (BUY Galions): Pinnacle per-game implies Galions {fair_galions_bo3:.1%}, PM ask {gal_ask:.3f} -> gap {gap*100:+.1f}% (actionable)")
        elif gap >= 0.01:
            print(f"  Combo arb (Galions): gap {gap*100:+.1f}% — small edge, spread-dependent")
        else:
            print(f"  Combo arb: gap {gap*100:+.1f}% — no edge on Galions")
        if sol_ask is not None:
            gap_sol = (1 - fair_galions_bo3) - sol_ask
            if gap_sol >= 0.03:
                print(f"  Combo arb (BUY Solary): gap {gap_sol*100:+.1f}% (actionable)")
            elif gap_sol >= 0.01:
                print(f"  Combo arb (Solary): gap {gap_sol*100:+.1f}% — small")
            else:
                print(f"  Combo arb (Solary): gap {gap_sol*100:+.1f}% — no edge")
    else:
        print("  Cannot compute verdict (missing reference or PM prices).")

    # Hedge P&L summary if we have game markets
    if pm_games and len(pm_games) >= 2 and gal_ask is not None and all(g.get("ask_a") and g.get("ask_b") for g in pm_games):
        print()
        print("  Hedge structure (BO3): Buy Galions ML + hedge Solary on Game 1 & 2.")
        print("  Game spreads will determine if hedge is viable (tight = viable).")
        # Quick spread check
        spreads = []
        for gm in pm_games:
            if galions_is_a:
                sa = gm["ask_a"] - gm["bid_a"] if gm["ask_a"] and gm["bid_a"] else None
                sb = gm["ask_b"] - gm["bid_b"] if gm["ask_b"] and gm["bid_b"] else None
            else:
                sa = gm["ask_b"] - gm["bid_b"] if gm["ask_b"] and gm["bid_b"] else None
                sb = gm["ask_a"] - gm["bid_a"] if gm["ask_a"] and gm["bid_a"] else None
            if sa is not None:
                spreads.append(sa)
            if sb is not None:
                spreads.append(sb)
        if spreads:
            avg_spread = sum(spreads) / len(spreads)
            print(f"  Avg game market spread: {avg_spread:.3f} ({avg_spread*100:.1f}c)")
            if avg_spread > 0.10:
                print("  -> Wide spreads; hedge costly. Combo arb may not be worth it after hedge cost.")
            elif avg_spread <= 0.06:
                print("  -> Tight spreads; hedge structure viable if edge exists.")

    client.close()


if __name__ == "__main__":
    main()
