# Architecture
LoL + CS2 Lead–Lag Arbitrage Bot (v2 — simplified MVP)

## Overview
Two-mode system for detecting lead–lag inefficiencies between Pinnacle (via OddsPapi) and Polymarket sports markets (currently LoL + CS2).

An additional standalone mode supports **Goalserve-driven gold-edge trading** for LoL per-game markets (hold-to-resolution).

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

**Purpose:** Collect upcoming LoL + CS2 matches and build mappings between OddsPapi and Polymarket.

**When to run:** On-demand, before matches start. E.g., once per day or week.

### OddsPapi flow
```
/v4/tournaments?sportId=18 (LoL), /v4/tournaments?sportId=17 (CS2)
    → Filter to configured LoL + CS2 target leagues
    → Store in: leagues table

/v4/participants?sportId=18/17
    → Store in: teams table (participantId → name)

/v4/fixtures?tournamentId=X&from=...&to=...&hasOdds=true
    → Store in: fixtures table
```

### Polymarket flow
```
/sports
    → Filter to LoL + CS2 sports (series_id)
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
Additionally, discovery persists an orientation anchor in `mappings.match_details`:
- `orientation_locked` + `team_a_is_home` (contract for assigning OddsPapi home/away to side A/B)
- `orientation_anchor_source` / `orientation_anchor_confidence` / `orientation_anchor_reason`
- `home_team` / `away_team` for operator visibility and audits

---

## Mode 2: Live Monitor (CLI)

**Purpose:** Compare live odds for a **single, operator-selected match**, detect discrepancies, and optionally execute trades.

**When to run:** When a mapped match is live (or shortly before it starts).

### Flow (event-driven, single match, all markets)
```
1. Operator selects a mapped match before the TUI starts.
   → Loads match_winner + all game_winner children from DB.

2. League poll (OddsPapi):
   GET /v4/odds-by-tournaments?tournamentIds=... @ 1s (in-play) / 5s (pre)
   → Update p_ref for the focus fixture (shared across all markets)

3. Hot fixture poll (OddsPapi):
   GET /v4/odds?fixtureId=X @ 500ms (focus fixture only)
   → Refresh p_ref with finer cadence (shared)

4. Polymarket Gamma refresh (periodic):
   GET /markets/{id} @ 10–15s for EACH PM market (match + games)
   → Refresh clobTokenIds/status, collect all token IDs for WS

5. Polymarket WebSocket:
   wss://ws-subscriptions-clob.polymarket.com/ws/market
   → Maintain top-of-book + depth for ALL markets' tokens

6. Compare (per market):
   p_ref vs PM ask/bid (match moneyline or per-game line)
   If direct game-winner odds are missing, derive game p_ref from match moneyline
   using series math (bo3/bo5 inversion) and tag snapshot source as derived.
   For totals lines, use direct Pinnacle totals (over/under) when line matches PM.
   → Build snapshot per market, compute edge independently

7. Trade signals:
   Trigger fires from match_winner p_ref movement → propagates to all markets.
   Entry/exit evaluated independently per market.
   New entries require `orientation_locked=true` and no active orientation conflict.

8. (Optional, live mode)
   Place FAK market orders via CLOB client (buy by USDC amount, sell by shares)
   → Record submitted positions, then confirm fills via reconciliation
```

### Exit reconciliation (live mode)
- Normalize conditional token balances from raw on-chain units using configured token decimals.
- Run the first balance probe after a short warmup (`balance_first_poll_delay_seconds`, default 3s), then continue on the regular cadence (`balance_poll_interval_seconds`, default 30s).
- Require multiple consecutive zero-balance probes (`balance_reconcile_zero_polls_required`, default 3 total polls: initial + 2 follow-ups) before auto-closing with `balance_reconciled`.
- If REST order status is unavailable, fall back to user WS recovery by asset+side after phantom order-id threshold.
- On repeated timeouts with remaining balance, reopen the trade for retry with cooldown and max-attempt guardrails.
- Repeated phantom order-id failures are finalized and reopened as fresh retries to avoid non-finalized reconciliation loops.
- Stale-position sweep treats a market as resolved when either fixture status is terminal or PM resolution/price-certainty indicates end of market.

### Entry safety gates (live mode)
- Hard max spread gate (`max_spread`, default `0.08`) blocks entries when books are too wide.
- Derived game p_ref gate uses stricter entry thresholds (`derived_game_*`) when
  game p_ref is inferred from series moneyline.
- Totals markets use strict totals thresholds (`totals_*`) for spread/depth/alpha gates.
- WS book staleness gate (`pm_book_stale_seconds`, default `5s`) blocks entries on outdated book snapshots.
- Endgame gate (`pm_endgame_threshold_high` / `pm_endgame_threshold_low`, default `0.97 / 0.03`) blocks new entries while still allowing exits.

**Single-match trading (multi-market):**
- Operator picks the match explicitly before the TUI starts.
- The live monitor loads the match_winner AND all game_winner children for the selected mapping.
- The live monitor also loads totals children (all matched lines) for the selected mapping.
- OddsPapi polling is shared (one fixture); Gamma + WS cover all PM markets.
- The TUI displays all markets (match_winner + game markets + totals lines) in the focus panel.
- Triggers fire from match_winner p_ref movement and propagate to all markets.
- Each market is evaluated independently for entry/exit signals.
- To switch matches, use the `r` command to re-select without restarting the process.

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

### Observability
- The live TUI redirects stdout logging into an on-screen log buffer.
- A rotating "black box" log file is also written to `./logs/live-<paper|live>.log` for postmortems (timeline, infra issues, unexpected exceptions).

---

## Mode 3: Gold-edge Monitor (CLI)

**Purpose:** Capture live in-game LoL state from Goalserve, measure EV against Polymarket game-winner asks, and optionally execute hold-to-resolution entries.

### Flow
```
Goalserve /esports/home?json=1 (poll)
  -> parse game stats (gold/kills/towers/dragons/barons)
  -> map to PM game_winner fixture + token IDs
  -> fetch PM CLOB orderbook (bid/ask)
  -> append snapshot row
  -> evaluate simple rule thresholds
  -> optional BUY YES (paper/live), hold to resolution
  -> resolve P&L from final winner
```

### Storage
- `game_snapshots` (append-only): Goalserve + PM book state at each poll
- `game_results` (upsert): final per-game outcomes and final stats
- `gold_edge_trades` (append + resolve update): paper/live entries held to resolution

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
Operator picks match
  │
  v
OddsPapi /odds-by-tournaments ──► p_ref updates (in-memory)
OddsPapi /odds (focus only) ─────► p_ref updates (in-memory)
Polymarket WS book state ───────► top-of-book + depth (in-memory)
                                   │
                                   v (if edge > threshold)
                          positions + trade_events / alerts
                                   │
                                   v (live mode only)
                             CLOB order placement
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
  - `/v4/tournaments?sportId=18|17` — LoL + CS2 leagues
  - `/v4/participants?sportId=18|17` — team names
  - `/v4/fixtures?tournamentId=X&...` — upcoming matches
  - `/v4/odds?fixtureId=X&bookmakers=pinnacle` — live odds
- Cooldowns: 500ms–2000ms per endpoint

### Polymarket (target)
- Base: `https://gamma-api.polymarket.com`
- Endpoints used:
  - `/sports` — LoL + CS2 leagues (series_id)
  - `/teams?league=lol|counter-strike` — team names
  - `/events?series_id=X&tag_id=100639` — upcoming matches
  - CLOB orderbook (separate endpoint) — live prices

---

## What's NOT in this architecture
- No broad market discovery
- No always-running background worker
- No complex derived tables (disagreement_events, etc.)
- No settlement specs or quote snapshots (non-live)
- No UI
- No multi-wallet or market-maker inventory management

These can be added later after the MVP proves the edge exists.
