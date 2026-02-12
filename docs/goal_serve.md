
Pregame odds

https://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/getodds/soccer?cat=esports_10

Livescore

http://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/home
http://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/d-1 - yesterday


Fixtures for 7 days

http://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/d1 - tomorrow
http://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/d2
https://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/d3
https://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/d4
https://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/d5
https://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/d6
https://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/d7



Results matches by date

http://www.goalserve.com/getfeed/1a6c8e6dd6394e4e4f4308de6650ad6a/esports/home?date=dd.MM.yyyy

---

## API Integration Notes (learned 2026-02-09)

### Response format

- Default is **XML**. Append `?json=1` for JSON output.
- JSON responses may be **gzip-compressed** (check if first 2 bytes are `\x1f\x8b` and decompress with `gzip.decompress()`).
- JSON may also have a **UTF-8 BOM** — decode with `utf-8-sig` to strip it.
- Handling both: `raw = resp.content; if raw[:2] == b'\x1f\x8b': raw = gzip.decompress(raw); text = raw.decode("utf-8-sig")`

### Rate limits

- **Odds endpoint** (`/getodds/soccer?cat=esports_10`) is **aggressively rate-limited**. Returns 429 or 500 on rapid successive calls. Space calls by 15-30 seconds minimum. Cache responses to disk.
- **Livescore** (`/esports/home`) and **fixtures** (`/esports/d1`–`d7`) are NOT rate-limited and can be polled freely.

### JSON structure — Pregame odds

Top-level: `{"scores": {"@sport": "esports", "@ts": "<unix_ts>", "match": [...]}}`

Each match object:

```
{
  "@status": "Not Started",
  "@id": "385766",
  "@league_id": "...",
  "@league": "LCK Playoffs",       // ← league name
  "@round": "",                     // ← often empty for LoL
  "@type": "League Of Legends",     // ← game type (also: "CS GO", "DOTA 2")
  "@timer": "",
  "@date": "12.02.2026",           // ← dd.MM.yyyy format
  "@time": "08:00",                // ← HH:mm (appears to be UTC-ish)
  "localteam": {"@name": "BNK FEARX", "@id": "25446", "@score": ""},
  "awayteam": {"@name": "DN SOOPers", "@id": "22654", "@score": ""},
  "scoreboard": {...},
  "odds": {
    "@ts": "1770641550",
    "@rotation_home": "",
    "@rotation_away": "",
    "type": [...]                   // ← array of market types
  }
}
```

**Important:** All metadata keys use `@` prefix (XML attribute convention). Use `m.get("@type")`, NOT `m.get("type")`.

### Odds market types

`odds.type` is a **list of market type dicts**. Each has:

```
{
  "@value": "Correct Score",        // ← market name (NOT @name!)
  "@stop": "False",
  "@id": "81",
  "bookmaker": [...]                // ← array of bookmaker dicts
}
```

Known `@value` values for LoL matches:
- `"Home/Away"` — match winner (moneyline)
- `"Correct Score"` — series score (e.g., 2:0, 2:1, 0:2, 1:2 for BO3; 3:0, 3:1, 3:2, 0:3, 1:3, 2:3 for BO5)
- `"Home/Away (map 1)"` / `"Home/Away (map 2)"` / `"Home/Away (map 3)"` — per-game winner
- `"Total Maps"` — over/under on total games played (e.g., O/U 2.5)
- `"Maps Handicap"` — game spread

### Bookmaker structure

Each bookmaker entry:

```
{
  "@name": "bet365",               // ← bookmaker name
  "@stop": "False",
  "@ts": "1770667874",             // ← unix timestamp of last line update
  "@id": "16",
  "odd": [                         // ← array of individual odds
    {
      "@name": "3:0",              // ← outcome label (score, team name, etc.)
      "@value": "21.00",           // ← decimal odds
      "@us": "2000",               // ← US odds
      "@id": "9236126438116081608" // ← unique odd ID
    },
    ...
  ]
}
```

### Available bookmakers (as of Feb 2026)

Observed across LoL matches: `bet365`, `Marathon`, `WilliamHill`, `1xBet`, `Unibet`, `Betfair`, `188bet`

**Pinnacle (`Pncl`) is NOT currently present** in the esports odds feed. Not available for correct score, moneyline, or per-game markets. This means de-vigging must use bet365 or Marathon as the "sharpest" available reference. Overround on correct score markets is typically 1.12–1.18 (higher than Pinnacle would be).

### LoL coverage

- **Target leagues found:** LCK Playoffs, LPL Knights Rivals, LCS Playoffs, LEC Playoffs, LCP Playoffs, LFL Souper Group, plus smaller leagues (EBL, NLC, Hitpoint Masters, HLL, Road of Legends)
- **Game type filter:** `m.get("@type") == "League Of Legends"` (exact string, title case)
- **Correct score availability:** Most target-league BO3/BO5 matches have correct score odds from 2-4 bookmakers. Smaller leagues may only have 1xBet.
- **BO3 correct scores:** `2:0`, `2:1`, `0:2`, `1:2` (4 outcomes)
- **BO5 correct scores:** `3:0`, `3:1`, `3:2`, `0:3`, `1:3`, `2:3` (6 outcomes)
- **Series format detection:** The `@round` field is often empty for LoL. Infer from correct score outcomes: if scores go up to 3, it's BO5; if max is 2, it's BO3.

### Team name mapping (GoalServe → Polymarket)

Names differ between sources. Known mismatches:

| GoalServe | Polymarket |
|---|---|
| DN SOOPers | DN Freecs |
| EDward Gaming | EDward Gaming (sometimes "EDG") |
| Fukuoka SoftBank Hawks Gaming | Fukuoka SoftBank Hawks Gaming |
| Dplus KIA | Dplus KIA (sometimes "Dplus") |
| GAM Esports | GAM Esports |

Best matching strategy: tokenize both names, remove noise words (`esports`, `gaming`, `team`), check for token overlap.

### De-vigging correct score odds

To compute implied series win probability from correct score:

```python
# 1. Convert decimal odds to implied probabilities
implied = {score: 1.0 / odds for score, odds in raw_scores.items()}

# 2. De-vig by normalizing to sum=1
overround = sum(implied.values())
devigged = {score: p / overround for score, p in implied.items()}

# 3. Sum paths where home/away wins
prob_home = sum(p for score, p in devigged.items() if home_score > away_score)
prob_away = sum(p for score, p in devigged.items() if away_score > home_score)
```

### Combo arb scan results (Feb 9 2026 snapshot)

Compared bet365/Marathon correct-score implied probabilities vs Polymarket moneyline ask prices across 12 upcoming LoL matches:

- **Systematic bias:** Polymarket ask prices generally EXCEED sportsbook fair value (17/22 sides overpriced on PM)
- **Most large gaps are spread-driven:** Matches with >15% gaps (Sentinels/Disguised, Cloud9/FlyQuest) had 30-37c PM spreads — illiquid books, not real arb
- **Best signal:** Fnatic vs Natus Vincere (LEC): CS implied Fnatic 58.7%, PM ask 0.52 → +6.7% edge on tight 5c spread
- **Second best:** GAM vs Fukuoka (LCP): CS implied GAM 82.8%, PM ask 0.78 → +4.8% edge on 3c spread
- **Actionable opportunities per scan:** ~1-2 trades with 3-7% edge before execution costs
- **Caveat:** Without Pinnacle, de-vig carries more uncertainty. bet365/Marathon overround (12-18%) is higher than Pinnacle would be.
