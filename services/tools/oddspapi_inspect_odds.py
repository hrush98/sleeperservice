#!/usr/bin/env python3
"""
One-off: fetch OddsPapi odds for a LoL fixture and dump Pinnacle market structure.
Usage: from repo root with conda env poly:
  cd services && python -m tools.oddspapi_inspect_odds [fixture_id]
  cd services && python -m tools.oddspapi_inspect_odds   # find GiantX vs Heretics live
"""
import json
import sys
from datetime import datetime, timedelta, timezone

# Run from services/
sys.path.insert(0, ".")
from shared.config import settings
from shared.oddspapi_client import OddsPapiClient


def _find_giantx_heretics(client: OddsPapiClient) -> str | None:
    """Find fixture ID for a fixture with GiantX and Heretics (LEC)."""
    tournaments = client.get_tournaments()
    now = datetime.now(tz=timezone.utc)
    from_ = now - timedelta(hours=4)
    to_ = now + timedelta(hours=6)
    for t in tournaments:
        name = (t.get("tournamentName") or "").lower()
        # LEC / EMEA
        if "lec" not in name and "lol" not in name and "league" not in name and "emea" not in name:
            continue
        fixtures = client.get_fixtures(t["tournamentId"], from_, to_, has_odds=True)
        for f in fixtures:
            p1 = (f.get("participant1Name") or "").lower()
            p2 = (f.get("participant2Name") or "").lower()
            # Match "Giant X" / "GIANTX" / "GiantX" and "Heretics" / "Team Heretics"
            if ("giant" in p1 or "giant" in p2) and ("heretic" in p1 or "heretic" in p2):
                return str(f.get("fixtureId"))
    return None


def _list_live_lol(client: OddsPapiClient) -> list[dict]:
    """Get live LoL fixtures via sportId + from/to + statusId=1."""
    import httpx
    now = datetime.now(tz=timezone.utc)
    from_ = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_ = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "sportId": settings.oddspapi_lol_sport_id,
        "from": from_,
        "to": to_,
        "statusId": 1,
        "hasOdds": "true",
        "language": "en",
    }
    try:
        data = client._request("/v4/fixtures", params, settings.cooldown_fixtures_ms)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return []
        raise
    if isinstance(data, list):
        return data
    return []


def _dump_pinnacle_markets(payload: dict) -> None:
    """Print market IDs, bookmakerMarketId, and outcome bookmakerOutcomeIds."""
    bo = payload.get("bookmakerOdds") or {}
    pinnacle = bo.get("pinnacle") or {}
    markets = pinnacle.get("markets") or {}
    print("participant1Name:", payload.get("participant1Name"))
    print("participant2Name:", payload.get("participant2Name"))
    print("statusId:", payload.get("statusId"))
    print("markets count:", len(markets))
    for mid, m in markets.items():
        if not isinstance(m, dict):
            continue
        bmid = m.get("bookmakerMarketId")
        outcomes = m.get("outcomes") or {}
        outcome_ids = []
        for oid, out in outcomes.items():
            if not isinstance(out, dict):
                continue
            players = out.get("players") or {}
            p0 = players.get("0") or {}
            if isinstance(p0, dict):
                sel = p0.get("bookmakerOutcomeId")
                price = p0.get("price")
                if sel is not None:
                    outcome_ids.append((str(sel), price))
        print(f"  market_id={mid!r} bookmakerMarketId={bmid!r} -> outcomes: {outcome_ids}")
    print()


def main() -> None:
    client = OddsPapiClient()
    fixture_id = None
    if len(sys.argv) > 1:
        fixture_id = sys.argv[1].strip()
    if not fixture_id:
        print("Looking for live LoL fixtures...")
        live = _list_live_lol(client)
        if live:
            for f in live:
                fid = f.get("fixtureId")
                p1 = f.get("participant1Name") or ""
                p2 = f.get("participant2Name") or ""
                print(f"  Live: {fid}  {p1} vs {p2}")
                if ("giant" in p1.lower() or "giant" in p2.lower()) and (
                    "heretic" in p1.lower() or "heretic" in p2.lower()
                ):
                    fixture_id = str(fid)
                    print(f"Using fixtureId: {fixture_id}")
                    break
            if not fixture_id and live:
                fixture_id = str(live[0]["fixtureId"])
                print(f"Using first live fixture: {fixture_id}")
        if not fixture_id:
            print("Looking for GiantX vs Heretics in tournaments...")
            fixture_id = _find_giantx_heretics(client)
        if not fixture_id:
            print("No fixture found. Try passing fixtureId as arg.")
            sys.exit(1)
    payload = client.get_odds(fixture_id, bookmakers="pinnacle", verbosity=3)
    _dump_pinnacle_markets(payload)
    # Also run extract logic to see candidates
    from shared.oddspapi_client import OddsPapiClient as O
    bo = (payload.get("bookmakerOdds") or {}).get("pinnacle") or {}
    markets = bo.get("markets") or {}
    candidates = []
    for market_id, market in markets.items():
        if not isinstance(market, dict):
            continue
        is_game = O._market_is_game(market, market_id)
        parsed = O._parse_home_away_market(market)
        if parsed:
            candidates.append((market_id, market.get("bookmakerMarketId"), is_game, "home/away"))
        elif is_game:
            candidates.append((market_id, market.get("bookmakerMarketId"), is_game, "game (skipped)"))
    print("Candidate breakdown (extract_pinnacle_moneyline):")
    for c in candidates:
        print(" ", c)
    client.close()


if __name__ == "__main__":
    main()
