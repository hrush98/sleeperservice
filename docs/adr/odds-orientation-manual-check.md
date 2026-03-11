# Manual pull: OddsPapi vs Polymarket — why MATCH odds swap (not GAME 1)

**Date:** 2026-02-21  
**Context:** MATCH (ML) shows swapped odds (e.g. Fnatic 0.628/0.420, Vitality 0.372/0.590); GAME 1 is correct. No code changes yet — investigation only.

---

## 1. Root cause (from raw payloads)

### OddsPapi match moneyline: **no `playerId`**

- OddsPapi `/v4/odds` returns for the **match** market (`bookmakerMarketId`: `.../0/moneyline`) outcomes `home` and `away` with **no `playerId`** in the outcome `players.0` object.
- So we **cannot** tie Pinnacle’s “home”/“away” to `participant1Id`/`participant2Id` for the match.
- Orientation for **match_winner** therefore falls back to **name matching** (participant1Name/participant2Name vs our DB team_a/team_b). That can be wrong (order, spelling, or Polymarket vs OddsPapi team order).

### Game markets

- For **game winner** we use `extract_pinnacle_game_winner` and look for `gameN/home` and `gameN/away`. When OddsPapi returns **game-level** markets (e.g. `.../1/moneyline` or literal `game1/home`), those outcomes **can** include `playerId`, so we resolve orientation by ID and get it right.
- So: **MATCH** = name fallback (fragile). **GAME 1** = ID-based when game markets exist (stable).

### Polymarket

- Outcome order is explicit: `outcomes` is a JSON array `[first_team, second_team]`; `clobTokenIds` is `[token_a, token_b]`. We set team_a = first outcome, team_b = second. No ambiguity from the API.

---

## 2. Manual pull commands (re-run anytime)

Use these to fetch raw data and compare.

### OddsPapi (needs API key from `.env`)

```bash
# From repo root; conda env poly
cd /home/hmrush/Desktop/sleeperservice/services
PYTHONPATH=. conda run -n poly python -c "
from shared.config import settings
from shared.oddspapi_client import OddsPapiClient
from datetime import datetime, timedelta, timezone
import json
client = OddsPapiClient()
tournaments = client.get_tournaments()
tid = tournaments[0]['tournamentId']
now = datetime.now(tz=timezone.utc)
from_ = now - timedelta(hours=6)
to_ = now + timedelta(hours=24)
fixtures = client.get_fixtures(tid, from_, to_, has_odds=True)
f = fixtures[0]
fid = f.get('fixtureId')
print('Fixture:', f.get('participant1Name'), 'vs', f.get('participant2Name'), '| fixtureId:', fid)
payload = client.get_odds(str(fid), bookmakers='pinnacle', verbosity=3)
with open('/home/hmrush/Desktop/sleeperservice/tmp_oddspapi_odds.json', 'w') as out:
    json.dump(payload, out, indent=2)
print('Written tmp_oddspapi_odds.json')
client.close()
"
```

Then inspect:

```bash
# Top-level participants (OddsPapi order)
jq '{ participant1Name, participant2Name, participant1Id, participant2Id }' /home/hmrush/Desktop/sleeperservice/tmp_oddspapi_odds.json

# Match moneyline market: home/away and whether playerId exists
jq '.bookmakerOdds.pinnacle.markets | to_entries[] | select(.value.bookmakerMarketId | test("/0/moneyline")) | { marketId: .key, bookmakerMarketId: .value.bookmakerMarketId, outcomes: [.value.outcomes[] | .players["0"] | { bookmakerOutcomeId, price, playerId }] }' /home/hmrush/Desktop/sleeperservice/tmp_oddspapi_odds.json
```

**What to check:** In the match moneyline outcomes, `playerId` is missing (null). So orientation cannot be ID-based for match.

Example (2026-02-21 pull):

```json
{ "participant1Name": "Flyquest", "participant2Name": "Lyon Gaming", "participant1Id": 309222, "participant2Id": 280877 }
```

Match moneyline outcomes: both `playerId: null`:

```json
{ "marketId": "181", "bookmakerMarketId": "line/.../0/moneyline", "outcomes": [
  { "bookmakerOutcomeId": "away", "price": 2.28, "playerId": null },
  { "bookmakerOutcomeId": "home", "price": 1.636, "playerId": null }
]}
```

### Polymarket Gamma (no key)

```bash
# LoL events (series_id=10311, tag_id=100639)
curl -sS "https://gamma-api.polymarket.com/events?series_id=10311&tag_id=100639&active=true&limit=2" -o /home/hmrush/Desktop/sleeperservice/tmp_pm_events.json

# First event’s first market: outcome order and tokens
jq '.[0].markets[0] | { question, outcomes, clobTokenIds }' /home/hmrush/Desktop/sleeperservice/tmp_pm_events.json
```

**What to check:** `outcomes` is `[teamA, teamB]`. Our team_a = first, team_b = second.

---

## 3. Why it keeps happening

1. **Match moneyline** from OddsPapi has no `playerId` → we always use **name-based** orientation for match when IDs aren’t available.
2. **Discovery** sets Polymarket team_a/team_b from the **match market’s outcome order** (first = team_a). OddsPapi participant1/participant2 order is **independent** of Pinnacle’s home/away.
3. So “home” might be participant2 and “away” participant1. Name similarity can pick the wrong mapping (e.g. “Fnatic” vs “Team Vitality” vs “Vitality”), or the two sources list teams in different order.
4. **Game 1** (when present) often has `playerId` in game-level outcomes, so we use ID-based orientation and avoid the swap.

---

## 4. Manual check (for a manual fix / future code)

Before trusting MATCH (ML) odds:

1. From OddsPapi payload: note `participant1Name`, `participant2Name`, and match moneyline `home.price`, `away.price`.
2. From Polymarket: note outcome order `[outcome0, outcome1]` for the match market.
3. Decide: is Pinnacle “home” = outcome0 or outcome1? (Only name matching or external source like Goalserve can tell; OddsPapi doesn’t give it for match.)
4. If you have Goalserve or another source that says “home team” / “away team”, align: team_a should be the same side as the side we assign “home” to.

**Implemented (2026-02-21):** When orientation for **match_winner** is from name fallback (status `name_direct` / `name_swapped`) and not locked, show a **manual check** in the TUI (e.g. “MATCH orientation from names — confirm team order”) or require operator confirmation before treating match odds as aligned.

---

## 5. Files touched this session

- Fetched and inspected: `tmp_oddspapi_odds.json`, `tmp_pm_events.json` (can delete after review).
- This doc: `docs/adr/odds-orientation-manual-check.md`.
- Code: `services/cli/poller.py` (`_orientation_swap_from_game_markets`, match_winner use of game orientation); tests in `tests/test_orientation_safety.py`.
