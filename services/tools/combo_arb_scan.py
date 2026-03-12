"""
Combinatorial Arb Validation Scan
Compare sportsbook correct-score implied series probabilities vs Polymarket moneyline.

Usage (from repo root):
    python -m services.tools.combo_arb_scan

Uses cached GoalServe data (run gs_cache.py first if no cache).
"""

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent / "gs_odds_cache.json"
PM_GAMMA = "https://gamma-api.polymarket.com"
PM_CLOB = "https://clob.polymarket.com"
PM_GAME_BETS_TAG = 100639

# Bookmaker preference order (sharpest first)
BOOK_PREF = ["Pncl", "Pinnacle", "bet365", "Marathon", "WilliamHill", "1xBet"]


@dataclass
class CorrectScoreMatch:
    """A match with de-vigged correct score probabilities."""
    gs_id: str
    league: str
    home: str
    away: str
    date: str
    time: str
    bookmaker: str  # Which book the CS odds came from
    raw_scores: dict[str, float] = field(default_factory=dict)  # score -> decimal odds
    prob_scores: dict[str, float] = field(default_factory=dict)  # score -> de-vig prob
    prob_home: float = 0.0  # P(home wins series)
    prob_away: float = 0.0  # P(away wins series)


@dataclass
class PMMoneyline:
    """Polymarket moneyline market."""
    event_title: str
    market_question: str
    market_id: str
    outcome_a: str
    outcome_b: str
    token_a: str
    token_b: str
    ask_a: float | None = None
    bid_a: float | None = None
    ask_b: float | None = None
    bid_b: float | None = None


# ── GoalServe parsing ────────────────────────────────────────────────────────

def load_goalserve() -> list[CorrectScoreMatch]:
    """Load cached GoalServe odds and extract LoL correct score matches."""
    if not CACHE_FILE.exists():
        logger.error("No GoalServe cache. Run: python -m services.tools.gs_cache")
        sys.exit(1)

    data = json.loads(CACHE_FILE.read_text())
    matches = data.get("scores", {}).get("match", [])
    if isinstance(matches, dict):
        matches = [matches]

    target_leagues = ["lck", "lcs", "lec", "lpl", "lcp", "lfl"]
    results: list[CorrectScoreMatch] = []
    all_bookmakers: set[str] = set()

    for m in matches:
        game_type = (m.get("@type") or "").lower()
        if "league of legends" not in game_type:
            continue

        league = m.get("@league", "")
        home = m.get("localteam", {}).get("@name", "?")
        away = m.get("awayteam", {}).get("@name", "?")

        # Only target leagues
        if not any(t in league.lower() for t in target_leagues):
            continue

        # Find correct score market
        odds = m.get("odds", {})
        market_types = odds.get("type", [])
        if isinstance(market_types, dict):
            market_types = [market_types]

        cs_market = None
        for mt in market_types:
            name = mt.get("@value", mt.get("@name", ""))
            if "correct score" in name.lower():
                cs_market = mt
                break

        if cs_market is None:
            continue

        # Get bookmakers
        bookmakers = cs_market.get("bookmaker", [])
        if isinstance(bookmakers, dict):
            bookmakers = [bookmakers]

        # Track all available bookmakers
        for bk in bookmakers:
            all_bookmakers.add(bk.get("@name", "?"))

        # Pick the best available bookmaker
        chosen_bk = None
        chosen_name = None
        for pref in BOOK_PREF:
            for bk in bookmakers:
                bk_name = bk.get("@name", "")
                if pref.lower() in bk_name.lower():
                    chosen_bk = bk
                    chosen_name = bk_name
                    break
            if chosen_bk:
                break

        if chosen_bk is None and bookmakers:
            chosen_bk = bookmakers[0]
            chosen_name = chosen_bk.get("@name", "?")

        if chosen_bk is None:
            logger.debug("No bookmaker for CS in %s vs %s", home, away)
            continue

        # Extract odds — odd entries have @name (score) and @value (decimal odds)
        odds_list = chosen_bk.get("odd", [])
        if isinstance(odds_list, dict):
            odds_list = [odds_list]

        raw_scores: dict[str, float] = {}
        for odd in odds_list:
            score_name = odd.get("@name", "")
            odds_val = odd.get("@value", "")
            try:
                decimal_odds = float(odds_val)
                if decimal_odds > 1.0:
                    raw_scores[score_name] = decimal_odds
            except (ValueError, TypeError):
                pass

        if not raw_scores:
            logger.debug("No valid odds for %s vs %s from %s", home, away, chosen_name)
            continue

        # De-vig
        implied = {k: 1.0 / v for k, v in raw_scores.items() if v > 0}
        overround = sum(implied.values())
        if overround <= 0:
            continue
        devigged = {k: v / overround for k, v in implied.items()}

        # Sum paths
        prob_home = 0.0
        prob_away = 0.0
        for score_str, prob in devigged.items():
            match = re.match(r"(\d+)\s*[:\-]\s*(\d+)", score_str.strip())
            if not match:
                continue
            h, a = int(match.group(1)), int(match.group(2))
            if h > a:
                prob_home += prob
            elif a > h:
                prob_away += prob

        if prob_home <= 0 and prob_away <= 0:
            continue

        results.append(CorrectScoreMatch(
            gs_id=str(m.get("@id", "")),
            league=league,
            home=home,
            away=away,
            date=m.get("@date", ""),
            time=m.get("@time", ""),
            bookmaker=chosen_name or "?",
            raw_scores=raw_scores,
            prob_scores=devigged,
            prob_home=prob_home,
            prob_away=prob_away,
        ))

    logger.info("Available CS bookmakers across all matches: %s", sorted(all_bookmakers))
    logger.info("Extracted %d LoL matches with correct score data", len(results))
    return results


# ── Polymarket ───────────────────────────────────────────────────────────────

def fetch_polymarket_moneylines() -> list[PMMoneyline]:
    """Fetch Polymarket LoL moneyline markets with CLOB prices."""
    client = httpx.Client(timeout=30)
    results: list[PMMoneyline] = []

    try:
        # Use tag_id=65 (league-of-legends) for LoL-specific events
        logger.info("Fetching Polymarket LoL events (tag_id=65)...")
        resp = client.get(
            f"{PM_GAMMA}/events",
            params={
                "tag_id": 65,
                "active": "true",
                "closed": "false",
                "limit": 100,
            },
        )
        resp.raise_for_status()
        events = resp.json() if isinstance(resp.json(), list) else []
        logger.info("Polymarket: %d LoL events", len(events))

        # For each event, find the moneyline market (BO3/BO5 match winner)
        # Moneyline markets have the pattern: "LoL: X vs Y (BO3) - League"
        # and outcomes are team names, not Yes/No
        for event in events:
            title = event.get("title", "")
            markets = event.get("markets") or []

            for market in markets:
                question = (market.get("question") or "")
                q_lower = question.lower()

                # Skip non-moneyline markets
                if any(skip in q_lower for skip in [
                    "game 1", "game 2", "game 3", "game 4", "game 5",
                    "first blood", "over/under", "o/u ", "total kills",
                    "handicap", "most kills", "most towers", "most drakes",
                    "most nashors", "most inhibitors",
                    "will ", "win the lck", "win the lec", "win the lcs",
                    "win lcs", "win the lpl", "win the lcp",
                ]):
                    continue

                # Must contain "(BO3)" or "(BO5)" to be a series moneyline
                if "(bo3)" not in q_lower and "(bo5)" not in q_lower:
                    continue

                # Parse outcomes and tokens
                outcomes = market.get("outcomes", "[]")
                if isinstance(outcomes, str):
                    try:
                        outcomes = json.loads(outcomes)
                    except json.JSONDecodeError:
                        outcomes = []

                tokens = market.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    try:
                        tokens = json.loads(tokens)
                    except json.JSONDecodeError:
                        tokens = []

                if len(outcomes) < 2 or len(tokens) < 2:
                    continue

                # Skip if outcomes are Yes/No (tournament winner markets)
                if outcomes[0] in ("Yes", "No"):
                    continue

                results.append(PMMoneyline(
                    event_title=title,
                    market_question=question,
                    market_id=str(market.get("id", "")),
                    outcome_a=str(outcomes[0]),
                    outcome_b=str(outcomes[1]),
                    token_a=tokens[0],
                    token_b=tokens[1],
                ))

        # Fetch CLOB books in batches of 50 (avoid 400 errors)
        if results:
            all_tokens = []
            for m in results:
                all_tokens.extend([m.token_a, m.token_b])

            logger.info("Fetching CLOB books for %d tokens...", len(all_tokens))
            book_map: dict[str, dict] = {}
            batch_size = 50

            for i in range(0, len(all_tokens), batch_size):
                batch = all_tokens[i:i + batch_size]
                payload = [{"token_id": t} for t in batch]
                try:
                    resp = client.post(
                        f"{PM_CLOB}/books",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    books = resp.json()

                    if isinstance(books, list):
                        for b in books:
                            aid = str(b.get("asset_id", ""))
                            if aid:
                                bids = b.get("bids") or []
                                asks = b.get("asks") or []
                                best_bid = max(
                                    (float(x["price"]) for x in bids), default=None
                                ) if bids else None
                                best_ask = min(
                                    (float(x["price"]) for x in asks), default=None
                                ) if asks else None
                                book_map[aid] = {"bid": best_bid, "ask": best_ask}

                except Exception as e:
                    logger.warning("CLOB batch %d failed: %s", i, e)

            # Apply to results
            for m in results:
                if m.token_a in book_map:
                    m.ask_a = book_map[m.token_a]["ask"]
                    m.bid_a = book_map[m.token_a]["bid"]
                if m.token_b in book_map:
                    m.ask_b = book_map[m.token_b]["ask"]
                    m.bid_b = book_map[m.token_b]["bid"]

    except httpx.HTTPError as e:
        logger.error("Polymarket error: %s", e)
    finally:
        client.close()

    logger.info("Polymarket: %d moneyline markets with prices", len(results))
    return results


# ── Match + Compare ──────────────────────────────────────────────────────────

def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _name_match(gs_name: str, pm_name: str) -> bool:
    """Flexible name matching for team names across sources."""
    gs_n = normalize(gs_name)
    pm_n = normalize(pm_name)

    # Direct substring
    if gs_n in pm_n or pm_n in gs_n:
        return True

    # Known aliases (GoalServe name → Polymarket name)
    aliases = {
        "dnsoopers": "dnfreecs",
        "dnfreecs": "dnsoopers",
        "edwardgaming": "edg",
        "edg": "edwardgaming",
        "ohmygod": "omg",
        "omg": "ohmygod",
        "fukuokasoftbankhawksgaming": "fukuoka",
        "groundzerogaming": "groundzero",
        "thundertalkgaming": "thundertalk",
        "lngesports": "lng",
        "gamesports": "gam",
        "gamersports": "gam",
        "gamesports": "gam",
        "dpluskim": "dpluskia",
        "dpluskia": "dpluskim",
    }
    if aliases.get(gs_n) == pm_n or aliases.get(pm_n) == gs_n:
        return True

    # Token matching: split into words and check overlap
    gs_tokens = set(re.findall(r"[a-z0-9]+", gs_name.lower()))
    pm_tokens = set(re.findall(r"[a-z0-9]+", pm_name.lower()))
    # Remove common noise words
    noise = {"esports", "gaming", "team", "esport"}
    gs_tokens -= noise
    pm_tokens -= noise
    if gs_tokens and pm_tokens and gs_tokens & pm_tokens:
        return True

    return False


def compare(gs: list[CorrectScoreMatch], pm: list[PMMoneyline]) -> None:
    """Match and compare GoalServe correct score vs Polymarket moneyline."""

    print("\n" + "=" * 90)
    print("COMBINATORIAL ARB SCAN")
    print("Sportsbook Correct Score (de-vigged) vs Polymarket Moneyline")
    print("=" * 90)

    matched = 0
    all_gaps: list[tuple[str, str, float, float, float | None]] = []

    for g in gs:
        best_pm: PMMoneyline | None = None
        home_is_a = True

        for p in pm:
            if _name_match(g.home, p.outcome_a) and _name_match(g.away, p.outcome_b):
                best_pm = p
                home_is_a = True
                break
            elif _name_match(g.home, p.outcome_b) and _name_match(g.away, p.outcome_a):
                best_pm = p
                home_is_a = False
                break

        if best_pm is None:
            print(f"\n  [NO PM MATCH] {g.home} vs {g.away} ({g.league})")
            print(f"    CS implied: {g.home} {g.prob_home:.1%} / {g.away} {g.prob_away:.1%}")
            continue

        matched += 1

        # Align: "A" side = Polymarket outcome_a
        if home_is_a:
            cs_prob_a = g.prob_home
            cs_prob_b = g.prob_away
        else:
            cs_prob_a = g.prob_away
            cs_prob_b = g.prob_home

        pm_label_a = best_pm.outcome_a
        pm_label_b = best_pm.outcome_b

        # Compute gaps
        gap_a = (cs_prob_a - best_pm.ask_a) if best_pm.ask_a is not None else None
        gap_b = (cs_prob_b - best_pm.ask_b) if best_pm.ask_b is not None else None

        # Spread for context
        spread_a = None
        if best_pm.ask_a is not None and best_pm.bid_a is not None:
            spread_a = best_pm.ask_a - best_pm.bid_a

        spread_b = None
        if best_pm.ask_b is not None and best_pm.bid_b is not None:
            spread_b = best_pm.ask_b - best_pm.bid_b

        print(f"\n{'─' * 90}")
        print(f"  {g.home} vs {g.away}")
        print(f"  League: {g.league}  |  Date: {g.date} {g.time}  |  "
              f"Book: {g.bookmaker}")
        print()

        # Correct score detail
        print("  Correct Score (de-vigged):")
        overround = sum(1.0/v for v in g.raw_scores.values() if v > 0)
        for score, prob in sorted(g.prob_scores.items()):
            raw = g.raw_scores.get(score, 0)
            print(f"    {score:>5s}: {prob:6.1%}  (odds: {raw:.2f})")
        print(f"    Overround: {overround:.3f}")

        print()
        print(f"  {'':>20s}  {'CS Implied':>12s}  {'PM Ask':>8s}  {'PM Bid':>8s}  "
              f"{'Spread':>8s}  {'Gap':>8s}")
        print(f"  {pm_label_a:>20s}  {cs_prob_a:12.1%}  "
              f"{best_pm.ask_a or 0:8.3f}  {best_pm.bid_a or 0:8.3f}  "
              f"{spread_a or 0:8.3f}  "
              f"{gap_a:+8.1%}" if gap_a is not None else "     N/A")
        print(f"  {pm_label_b:>20s}  {cs_prob_b:12.1%}  "
              f"{best_pm.ask_b or 0:8.3f}  {best_pm.bid_b or 0:8.3f}  "
              f"{spread_b or 0:8.3f}  "
              f"{gap_b:+8.1%}" if gap_b is not None else "     N/A")

        if gap_a is not None:
            tag = " ← ACTIONABLE" if gap_a >= 0.03 else ""
            tag = " ← STRONG" if gap_a >= 0.05 else tag
            all_gaps.append((
                f"{g.home} vs {g.away}",
                pm_label_a,
                cs_prob_a,
                best_pm.ask_a or 0,
                gap_a,
            ))
            if tag:
                print(f"  >>> {pm_label_a}: gap = {gap_a:+.1%}{tag}")

        if gap_b is not None:
            tag = " ← ACTIONABLE" if gap_b >= 0.03 else ""
            tag = " ← STRONG" if gap_b >= 0.05 else tag
            all_gaps.append((
                f"{g.home} vs {g.away}",
                pm_label_b,
                cs_prob_b,
                best_pm.ask_b or 0,
                gap_b,
            ))
            if tag:
                print(f"  >>> {pm_label_b}: gap = {gap_b:+.1%}{tag}")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 90}")
    print(f"SUMMARY")
    print(f"  GoalServe LoL matches with CS data: {len(gs)}")
    print(f"  Matched to Polymarket moneyline: {matched}")
    print()

    if all_gaps:
        print(f"  All gaps sorted by absolute size:")
        print(f"  {'Match':>35s}  {'Side':>15s}  {'CS Imp':>8s}  {'PM Ask':>8s}  {'Gap':>8s}")
        for match, side, cs_p, pm_a, gap in sorted(all_gaps, key=lambda x: abs(x[4]), reverse=True):
            marker = ""
            if gap >= 0.05:
                marker = " *** STRONG"
            elif gap >= 0.03:
                marker = " ** ACTIONABLE"
            elif gap <= -0.05:
                marker = " *** SHORT?"
            elif gap <= -0.03:
                marker = " ** OVERPRICED"
            print(f"  {match:>35s}  {side:>15s}  {cs_p:8.1%}  {pm_a:8.3f}  {gap:+8.1%}{marker}")

        pos_gaps = [g for g in all_gaps if g[4] > 0]
        neg_gaps = [g for g in all_gaps if g[4] < 0]
        big_gaps = [g for g in all_gaps if abs(g[4]) >= 0.03]

        print(f"\n  PM underpriced (CS > PM ask): {len(pos_gaps)}")
        print(f"  PM overpriced  (CS < PM ask): {len(neg_gaps)}")
        print(f"  Actionable (|gap| ≥ 3%): {len(big_gaps)}")

        if pos_gaps:
            avg_pos = sum(g[4] for g in pos_gaps) / len(pos_gaps)
            print(f"  Avg positive gap: {avg_pos:+.1%}")
        if neg_gaps:
            avg_neg = sum(g[4] for g in neg_gaps) / len(neg_gaps)
            print(f"  Avg negative gap: {avg_neg:+.1%}")
    else:
        print("  No gaps computed (no matched markets or missing prices).")

    print(f"\n{'=' * 90}")


def main() -> None:
    print("=" * 90)
    print("COMBINATORIAL ARB VALIDATION SCAN")
    print("Comparing sportsbook correct-score implied probs vs Polymarket moneyline")
    print("=" * 90)

    gs_matches = load_goalserve()
    if not gs_matches:
        print("\nNo GoalServe LoL matches with correct score data.")
        sys.exit(1)

    print(f"\nGoalServe: {len(gs_matches)} matches")
    for m in gs_matches:
        print(f"  {m.home:>25s} vs {m.away:<25s}  ({m.league})")
        print(f"  {'':>25s}    CS: {m.prob_home:.1%} / {m.prob_away:.1%}  "
              f"[{m.bookmaker}]")

    pm_markets = fetch_polymarket_moneylines()
    if not pm_markets:
        print("\nNo Polymarket moneyline markets found.")
        sys.exit(1)

    print(f"\nPolymarket: {len(pm_markets)} moneyline markets")
    for m in pm_markets:
        mid = ""
        if m.ask_a is not None and m.bid_a is not None:
            mid = f"mid={(m.ask_a + m.bid_a)/2:.3f}"
        print(f"  {m.outcome_a:>25s} vs {m.outcome_b:<25s}  "
              f"ask={m.ask_a}  bid={m.bid_a}  {mid}")

    compare(gs_matches, pm_markets)


if __name__ == "__main__":
    main()
