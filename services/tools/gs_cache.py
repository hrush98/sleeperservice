"""Fetch GoalServe odds once and cache to disk. Then parse offline."""
import gzip
import json
import sys
import time
from pathlib import Path

import httpx

CACHE_FILE = Path(__file__).parent / "gs_odds_cache.json"
GS_ODDS_URL = (
    "https://www.goalserve.com/getfeed/"
    "1a6c8e6dd6394e4e4f4308de6650ad6a/getodds/soccer"
)


def fetch_and_cache() -> dict:
    if CACHE_FILE.exists():
        age_min = (time.time() - CACHE_FILE.stat().st_mtime) / 60
        print(f"Cache exists ({age_min:.0f} min old). Loading from disk.")
        return json.loads(CACHE_FILE.read_text())

    print("No cache. Fetching from GoalServe...")
    for attempt in range(3):
        resp = httpx.get(
            GS_ODDS_URL,
            params={"cat": "esports_10", "json": "1"},
            timeout=30,
        )
        print(f"  HTTP {resp.status_code} (len={len(resp.content)})")

        if resp.status_code == 429 or resp.status_code == 500:
            wait = 15 * (attempt + 1)
            print(f"  Rate limit / error. Waiting {wait}s...")
            time.sleep(wait)
            continue

        raw = resp.content
        if raw[:2] == b'\x1f\x8b':
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8-sig")

        if not text.strip():
            print("  Empty response. Retrying...")
            time.sleep(5)
            continue

        data = json.loads(text)
        CACHE_FILE.write_text(json.dumps(data, indent=2))
        print(f"  Cached to {CACHE_FILE}")
        return data

    print("Failed after 3 attempts.")
    sys.exit(1)


def analyze(data: dict) -> None:
    scores = data.get("scores", {})
    matches = scores.get("match", [])
    if isinstance(matches, dict):
        matches = [matches]

    print(f"\nTotal matches: {len(matches)}")

    # Find LoL matches with target leagues
    target = ["lck", "lcs", "lec", "lpl", "lcp"]

    for m in matches:
        game_type = (m.get("@type") or "").lower()
        if "league of legends" not in game_type:
            continue

        league = m.get("@league", "")
        home = m.get("localteam", {}).get("@name", "?")
        away = m.get("awayteam", {}).get("@name", "?")

        # Only show target leagues
        if not any(t in league.lower() for t in target):
            continue

        print(f"\n{'='*60}")
        print(f"  {home} vs {away}")
        print(f"  League: {league} | Date: {m.get('@date')} {m.get('@time')}")

        odds = m.get("odds", {})
        market_types = odds.get("type", [])
        if isinstance(market_types, dict):
            market_types = [market_types]

        for mt in market_types:
            # Figure out the name key
            name = "?"
            for k in ["@name", "name", "@value", "@id"]:
                if k in mt:
                    name = mt[k]
                    break

            # Show bookmaker count
            bks = mt.get("bookmaker", [])
            if isinstance(bks, dict):
                bks = [bks]
            bk_names = [b.get("@name", b.get("@id", "?")) for b in bks]

            print(f"\n  Market: {name}  (bookmakers: {bk_names})")

            # If this is correct score, dump Pinnacle odds
            if "correct" in name.lower():
                for bk in bks:
                    bk_name = bk.get("@name", bk.get("@id", ""))
                    if "pncl" in bk_name.lower() or "pinnacle" in bk_name.lower():
                        print(f"    >>> PINNACLE correct score odds:")
                        odds_list = bk.get("odd", [])
                        if isinstance(odds_list, dict):
                            odds_list = [odds_list]
                        for odd in odds_list:
                            print(f"      {json.dumps(odd)}")


if __name__ == "__main__":
    data = fetch_and_cache()
    analyze(data)
