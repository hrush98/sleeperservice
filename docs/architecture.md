# Architecture
LoL Lead–Lag Arbitrage Bot (v2 — simplified MVP)

## Overview
Two-mode system for detecting lead–lag inefficiencies between Pinnacle (via OddsPapi) and Polymarket LoL markets.

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER (CLI)                              │
│  $ cli discover --days 7    │    $ cli monitor                  │
└──────────────┬──────────────┴──────────────┬────────────────────┘
               │                             │
               v                             v
┌──────────────────────────┐   ┌──────────────────────────────────┐
│     DISCOVERY MODE       │   │         LIVE MONITOR MODE        │
│  (on-demand, not live)   │   │    (runs during live matches)    │
├──────────────────────────┤   ├──────────────────────────────────┤
│ 1. Fetch leagues/teams   │   │ 1. Poll OddsPapi /odds           │
│ 2. Fetch fixtures        │   │ 2. Poll Polymarket CLOB          │
│ 3. Build mappings        │   │ 3. Compare → compute gap         │
│                          │   │ 4. Record positions + events     │
└────────────┬─────────────┘   └──────────────┬───────────────────┘
             │                                │
             v                                v
┌─────────────────────────────────────────────────────────────────┐
│                          POSTGRES                               │
│  leagues │ teams │ fixtures │ mappings │ odds_snapshots │ trade_events │ positions │
└─────────────────────────────────────────────────────────────────┘
             ^
             │ SQL reads (ops only)
┌────────────┴────────────────────────────────────────────────────┐
│                       API (minimal)                             │
│  GET /ops/status    — counts, last update times                 │
│  GET /ops/live      — current live matches + gaps               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Mode 1: Discovery (CLI)

**Purpose:** Collect upcoming LoL matches and build mappings between OddsPapi and Polymarket.

**When to run:** On-demand, before matches start. E.g., once per day or week.

### OddsPapi flow
```
/v4/tournaments?sportId=18
    → Filter to: LCK, LPL, LEC, LCS/LTA, LCP, CBLOL
    → Store in: leagues table

/v4/participants?sportId=18
    → Store in: teams table (participantId → name)

/v4/fixtures?tournamentId=X&from=...&to=...&hasOdds=true
    → Store in: fixtures table
```

### Polymarket flow
```
/sports
    → Filter to LoL leagues (series_id)
    → Store in: leagues table

/teams?league=...
    → Store in: teams table (teamId → name)

/events?series_id=X&tag_id=100639&active=true
    → Store in: fixtures table (event + market data)
    → Store an **event fixture** as the parent match object
       - Event start time is derived from the moneyline market `gameStartTime` when present
    → Keep only match winner (moneyline) and game winner markets (Game 1/2/3/5)
    → Link match + game markets to the parent **event** via parent_fixture_id
```

### Mapping logic
For each OddsPapi fixture, find a matching Polymarket **event** by:
1. **League match:** normalized league name
2. **Team match:** both team names appear (fuzzy)
3. **Date match:** same calendar date (ignore exact time)

Store mapping with confidence score for the event; markets join via parent_fixture_id.

---

## Mode 2: Live Monitor (CLI)

**Purpose:** Compare live odds and detect discrepancies.

**When to run:** When mapped matches are live.

### Flow (event-driven)
```
1. League poll (OddsPapi):
   GET /v4/odds-by-tournaments?tournamentIds=... @ 1s
   → Update p_ref per fixture
   → Detect Δp_ref triggers

2. Hot fixture poll (OddsPapi):
   GET /v4/odds?fixtureId=X @ 500ms (only for hot fixtures)
   → Refresh p_ref with finer cadence

3. Polymarket WebSocket:
   wss://ws-subscriptions-clob.polymarket.com/ws/market
   → Maintain top-of-book + depth in memory

4. Compare:
   p_ref vs PM ask/bid
   → Compute edge, alert when actionable
```

### Console output (example)
```
[14:32:05] T1 vs Gen.G (LCK)
           Pinnacle: T1 @ 1.45 (68.9%) | Gen.G @ 2.85 (35.1%)
           Polymarket: T1 bid=0.65 ask=0.68 mid=0.665
           Gap: +2.4% on T1 (below threshold)

[14:32:10] T1 vs Gen.G (LCK)
           Pinnacle: T1 @ 1.38 (72.5%) ← MOVED
           Polymarket: T1 bid=0.65 ask=0.68 mid=0.665
           Gap: +6.0% on T1 ⚠️ SHADOW BUY recorded
```

---

## Data flow summary

### Discovery
```
OddsPapi ──► leagues, teams, fixtures (source="oddspapi")
Polymarket ──► leagues, teams, fixtures (source="polymarket")
                         │
                         v
                    mappings (oddspapi_fixture ↔ polymarket_fixture)
```

### Live Monitor
```
OddsPapi /odds-by-tournaments ──► p_ref updates (in-memory)
OddsPapi /odds (hot only) ──────► p_ref updates (in-memory)
Polymarket WS book state ───────► top-of-book + depth (in-memory)
                                   │
                                   v (if edge > threshold)
                          positions + trade_events / alerts
```

---

## Database tables (8 total)

| Table | Purpose | Write mode |
|-------|---------|------------|
| leagues | League/tournament metadata | Upsert |
| teams | Team/participant cache | Upsert |
| fixtures | Upcoming/live matches + event/market type/number + parent | Upsert |
| mappings | OddsPapi ↔ Polymarket links | Upsert |
| odds_snapshots | Live price time series | Append-only |
| shadow_orders | Paper trade decisions (legacy) | Append-only |
| positions | Trade lifecycle (paper/real) | Append-only + update on exit |
| trade_events | Trade decision/execution tape | Append-only |

---

## API (minimal, ops only)

### GET /ops/status
```json
{
  "leagues": {"oddspapi": 5, "polymarket": 4},
  "teams": {"oddspapi": 120, "polymarket": 85},
  "fixtures": {"oddspapi": 15, "polymarket": 12},
  "mappings": {"total": 10, "high_confidence": 8},
  "last_discovery": "2026-01-24T10:00:00Z",
  "live_matches": 2,
  "odds_snapshots": 1200,
  "shadow_orders": 0,
  "positions": 5,
  "trade_events": 42
}
```

### GET /ops/live
```json
{
  "matches": [
    {
      "mapping_id": "...",
      "teams": "T1 vs Gen.G",
      "league": "LCK",
      "pinnacle_prob": 0.725,
      "polymarket_mid": 0.665,
      "gap": 0.06,
      "last_update": "2026-01-24T14:32:10Z"
    }
  ]
}
```

---

## External API reference

### OddsPapi (Pinnacle reference)
- Base: `https://api.oddspapi.io`
- Auth: `?apiKey=...`
- Endpoints used:
  - `/v4/tournaments?sportId=18` — LoL leagues
  - `/v4/participants?sportId=18` — team names
  - `/v4/fixtures?tournamentId=X&...` — upcoming matches
  - `/v4/odds?fixtureId=X&bookmakers=pinnacle` — live odds
- Cooldowns: 500ms–2000ms per endpoint

### Polymarket (target)
- Base: `https://gamma-api.polymarket.com`
- Endpoints used:
  - `/sports` — LoL leagues (series_id)
  - `/teams?league=...` — team names
  - `/events?series_id=X&tag_id=100639` — upcoming matches
  - CLOB orderbook (separate endpoint) — live prices

---

## What's NOT in this architecture
- No broad market discovery
- No always-running background worker
- No complex derived tables (disagreement_events, etc.)
- No settlement specs or quote snapshots (non-live)
- No UI
- No actual order execution

These can be added later after the MVP proves the edge exists.
