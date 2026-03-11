# LoL + CS2 Lead–Lag Arbitrage Bot
Master project plan (v2 — simplified MVP)

## Vision
Detect and exploit short-lived **lead–lag** inefficiencies between Pinnacle odds (via OddsPapi) and Polymarket CLOB sports markets (currently LoL + CS2). Start with paper trading; gate live execution on measured edge.

In parallel, run a **standalone hold-to-resolution Gold-edge strategy** for LoL per-game markets using Goalserve live game-state data (gold/objectives) as the reference signal.

## Core insight
Pinnacle reprices faster than Polymarket. When Pinnacle moves, there's a brief window where Polymarket is stale. We detect that window, measure it, and (later) trade it.

## Design principles (MVP)
- **Two separate modes**: Discovery (on-demand CLI) vs Live Monitor (runs during matches)
- **No broad discovery**: Only ingest LoL + CS2 from selected target leagues
- **Minimal storage**: Only what's needed for mapping + live comparison
- **Clear observability**: Know exactly what's happening at each step

---

## System modes

### Mode 1: Discovery CLI (on-demand)
Run manually to collect upcoming matches for the next N days. Not always running.

**OddsPapi flow:**
1. `GET /v4/tournaments?sportId=18` (LoL) + `GET /v4/tournaments?sportId=17` (CS2) → target leagues
2. `GET /v4/participants?sportId=18/17` → cache team ID → name mappings
3. `GET /v4/fixtures?tournamentId=X&from=...&to=...&hasOdds=true` → get upcoming fixtures
4. Store: leagues, teams, fixtures

**Polymarket flow:**
1. `GET /sports` → find `sport="lol"` (series=10311) and `sport="cs2"` (series=10310)
2. `GET /teams?league=lol` and `GET /teams?league=counter-strike` → cache team mappings
3. `GET /events?series_id=...&tag_id=100639&closed=false` → get open (unresolved) LoL/CS2 events
4. Store: leagues, teams, events/markets
   - Store an **event fixture** (match-level parent) with league, teams, start time
     - Event start time is derived from the moneyline market `gameStartTime` when present
   - Only keep **match winner (moneyline)** and **game winner** markets (Game 1/2/3/5)
   - Persist market metadata needed to distinguish **match vs game**, **game number**, and **series type**
   - Link match + game markets to the **event** via `parent_fixture_id`

**Mapping:**
- Each OddsPapi fixture ↔ a Polymarket **event fixture** (parent match)
- Match by: league + team names + date (not exact time)
- Match/game markets are children of the event via `parent_fixture_id`
- Persist an orientation anchor in `mappings.match_details`:
  - `orientation_locked`, `team_a_is_home`, `home_team`, `away_team`
  - `orientation_anchor_source`, `orientation_anchor_confidence`, `orientation_anchor_ts`
  - `orientation_anchor_reason` for explicit unsafe states

**CLI interface:**
```
$ python -m cli discover --days 7
Collecting matches for next 7 days...
OddsPapi: Found 12 fixtures across 5 leagues
Polymarket: Found 8 events with markets
Mappings created: 6 (high confidence), 2 (review needed)
```

### Mode 2: Live Monitor (during matches)
Runs when a mapped match goes live. The operator picks the match up front; the monitor only watches that match.

The live process now supports two concurrent strategy managers over the same focused match:
- `lead_lag` (reference-prob driven)
- `complement_arb` (binary complement: `ask(A)+ask(B)<1`, depth-aware, FOK)

**For the selected mapped match:**
1. Poll OddsPapi `/v4/odds?fixtureId=X` for Pinnacle prices
2. Extract **moneyline** and **game winner** (Game 1/2/3) prices
3. Poll Polymarket CLOB orderbook for the corresponding market(s)
4. Compute gap per market: `pinnacle_implied_prob - polymarket_mid`
5. If gap exceeds threshold → record trade event and position (paper)
6. Log everything for later analysis

**Single-match trading policy:**
- Operator selects the match before the TUI starts.
- The live monitor polls, renders, and trades only that single match.
- Use `r` to re-select without restarting the process.
- Discovery remains separate; it only populates the DB for selection.
- Live balance reconciliation uses a fast first probe (~3s) and only applies `balance_reconciled` after consecutive zero-balance confirmations on the 30s cadence. No one-shot sync to zero or below a floor; sync-down requires ENTRY_CONFIRMED cooldown and multiple consecutive polls (see `docs/adr/balance-sync-safeguards.md`).
- Conservative default guards are enabled for live entry quality:
  - `max_spread` blocks wide books,
  - `pm_book_stale_seconds` blocks stale WS books,
  - `pm_endgame_threshold_high/low` blocks new entries in endgame certainty zones (exits continue to run).
- Orientation guard: entries require a locked, conflict-free orientation anchor. If unlocked/conflicting, the system records `ENTRY_SKIP` and does not trade.

**Output:**
- Console logs showing live comparison
- Trade events + positions stored in DB
- Simple `/ops/live` endpoint showing current state
- Rotating "black box" log file at `./logs/live-<paper|live>.log` for postmortems

### Mode 3: Gold-edge Monitor (standalone)
Runs independently from lead-lag and targets **per-game LoL winner** markets.

**Flow:**
1. Poll Goalserve `esports/home?json=1` at fixed cadence
2. Parse in-game stats (gold, kills, towers, dragons, barons, inhibitors)
3. Join against mapped/closest Polymarket game-winner fixtures
4. Log append-only live snapshots (Goalserve state + PM bid/ask)
5. Apply simple rule thresholds (`minute`, `gold_diff`, `max_ask`, optional baron condition)
6. In paper/live mode, buy YES and hold to resolution
7. Resolve P&L when game winner is known

---

## Target leagues
- **LCK** (South Korea) — strongest region
- **LPL** (China) — strongest region
- **LEC** (Europe)
- **LCS/LTA** (North America)
- **LCP** (Asia-Pacific)
- **CBLOL** (Brazil)
- **BLAST Premier Series** (CS2 Tier 1)
- **ESL Pro League** (CS2 Tier 1)
- **Intel Extreme Masters** (CS2 Tier 1)
- **PGL** (CS2 Tier 1)

OddsPapi sportId for LoL: **18**  
OddsPapi sportId for CS2: **17**

---

## Data model (minimal)

### leagues
- `id` (uuid pk)
- `source` (text; "oddspapi" | "polymarket")
- `source_id` (text; tournament_id or series_id)
- `name` (text)
- `slug` (text)
- `raw_json` (jsonb)
- `created_at`, `updated_at`

### teams
- `id` (uuid pk)
- `source` (text)
- `source_id` (text; participant_id or team_id)
- `league_id` (fk → leagues.id nullable)
- `name` (text)
- `abbreviation` (text nullable)
- `raw_json` (jsonb)
- `created_at`

### fixtures
- `id` (uuid pk)
- `source` (text; "oddspapi" | "polymarket")
- `source_id` (text; fixture_id or event_id/market_id)
- `league_id` (fk → leagues.id)
- `team_a_id` (fk → teams.id)
- `team_b_id` (fk → teams.id)
- `start_time` (timestamptz)
- `status` (text; "upcoming" | "live" | "finished")
- `has_odds` (boolean)
- `market_type` (text; "event" | "match_winner" | "game_winner")
- `game_number` (int nullable; 1/2/3 for game winner markets)
- `series_type` (text nullable; "bo1" | "bo3" | "bo5")
- `parent_fixture_id` (fk → fixtures.id nullable; parent event for match/game markets)
- `raw_json` (jsonb)
- `created_at`, `updated_at`

### mappings
- `id` (uuid pk)
- `oddspapi_fixture_id` (fk → fixtures.id)
- `polymarket_fixture_id` (fk → fixtures.id)
- `confidence` (double precision)
- `method` (text; "auto" | "manual")
- `match_details` (jsonb; mapping metadata, including orientation anchor and market-side map)
- `created_at`, `updated_at`

### odds_snapshots (append-only)
- `id` (uuid pk)
- `fixture_id` (fk → fixtures.id)
- `ts` (timestamptz)
- `source` (text; "oddspapi" | "polymarket_clob")
- `team_a_odds` (double precision nullable)
- `team_b_odds` (double precision nullable)
- `team_a_implied_prob` (double precision nullable)
- `team_b_implied_prob` (double precision nullable)
- `best_bid` (double precision nullable)
- `best_ask` (double precision nullable)
- `raw_json` (jsonb)

### positions (append-only + update on exit)
- `id` (uuid pk)
- `mapping_id` (fk → mappings.id)
- `pm_fixture_id` (fk → fixtures.id)
- `mode` (text; "paper" | "real")
- `strategy` (text; "lead_lag" | "complement_arb")
- `venue` (text nullable)
- `market_type` (text)
- `game_number` (int nullable)
- `side` (text; "A" | "B")
- `opened_at` (timestamptz)
- `entry_price` (double precision)
- `entry_p_ref` (double precision nullable)
- `entry_alpha` (double precision nullable)
- `entry_edge` (double precision nullable)
- `quantity` (double precision)
- `trigger_type` (text nullable)
- `status` (text; "submitted" | "confirmed" | "cancelled" | "closed")
- `external_order_id` (text nullable)
- `external_status` (text nullable)
- `closed_at` (timestamptz nullable)
- `exit_price` (double precision nullable)
- `exit_p_ref` (double precision nullable)
- `exit_reason` (text nullable)
- `pnl_absolute` (double precision nullable)
- `pnl_percent` (double precision nullable)
- `hold_seconds` (double precision nullable)
- `convergence_seconds` (double precision nullable)
- `edge_capture` (double precision nullable)
- `raw_json` (jsonb)

### trade_events (append-only)
- `id` (uuid pk)
- `ts` (timestamptz)
- `run_id` (uuid)
- `event_type` (text)
- `mode` (text)
- `strategy` (text; "lead_lag" | "complement_arb")
- `mapping_id` (fk → mappings.id)
- `pm_fixture_id` (fk → fixtures.id)
- `position_id` (fk → positions.id, nullable)
- `market_type` (text nullable)
- `game_number` (int nullable)
- `side` (text nullable)
- `reason` (text nullable)
- `details` (text nullable)
- `edge_threshold`, `spread_factor`, `alpha_min`, `alpha_spread_factor`, `exit_epsilon` (double precision nullable)
- `p_ref_a`, `p_ref_b`, `bid_a`, `ask_a`, `mid_a`, `bid_b`, `ask_b`, `mid_b` (double precision nullable)
- `best_edge` (double precision nullable)
- `best_side` (text nullable)
- `quantity`, `limit_price`, `avg_fill_price`, `size_available`, `net_edge` (double precision nullable)
- `exit_price`, `pnl_percent`, `convergence_seconds` (double precision nullable)
- `external_order_id`, `external_fill_id`, `external_status` (text nullable)
- `raw_json` (jsonb)

### order_attempts
- `id` (uuid pk)
- `position_id` (fk → positions.id)
- `run_id` (uuid)
- `mode` (text)
- `strategy` (text; "lead_lag" | "complement_arb")
- `phase`, `side`, `token_id`, `attempt_seq`
- `submitted_at`, `limit_price`, `requested_size`
- `external_order_id`, `external_status`, `matched_size`
- `finalized_at`, `final_state`, `final_reason`
- `raw_json` (jsonb)

### complement_arbs
- `id` (uuid pk)
- `run_id` (uuid)
- `mapping_id` (fk → mappings.id)
- `pm_fixture_id` (fk → fixtures.id)
- `market_type`, `game_number`, `mode`, `state`
- `leg_a_position_id`, `leg_b_position_id` (fk → positions.id)
- `vwap_a`, `vwap_b`, `target_size`, `locked_edge`
- `actual_cost_a`, `actual_cost_b`
- `created_at`, `resolved_at`, `resolution_pnl`
- `raw_json` (jsonb)

---

## Milestones

### M0 — Project skeleton **(complete)**
- Compose setup, shared settings, DB + migrations, service scaffolds.

### M1 — Discovery CLI **(complete)**
- Implement OddsPapi client: tournaments, participants, fixtures
- Implement Polymarket client: sports, teams, events
- Build mapping logic (league + teams + date)
- CLI: `discover --days N`
- Verification: See leagues, teams, fixtures, mappings in DB

### M2 — Live Monitor (event-driven, single match)
- Operator selects the match before the TUI starts
- Implement OddsPapi league polling via `/v4/odds-by-tournaments` (1s in-play / 5s pre)
- Implement hot fixture polling via `/v4/odds` (500ms, focus only)
- Implement Polymarket WS book state (top-of-book + depth in memory)
- Periodic Gamma refresh to keep market/token metadata fresh (focus only)
- Trigger on Δp_ref thresholds + lock/unlock
- Compare and compute edge using PM bid/ask
- Record trade events + paper positions / alerts
- CLI: `live` (watches one selected match)

### M3 — Observability
- `/ops/status` endpoint (counts, last update times)
- `/ops/live` endpoint (current live match + latest gap)
- Console output that makes sense

### M4 — Evaluation
- Review trade events + positions vs actual price movements
- Measure lag distributions
- Decide if edge is real

### M5+ — Execution (gated)
- Only after M4 shows consistent edge
- Conservative limits, circuit breakers
- Live mode supports taker-style FAK market orders (buy by USDC amount) with manual allowances and caps

---

## Tech stack
- Python 3.11+
- Typer for CLI
- FastAPI for API (minimal, observability only)
- Postgres + SQLAlchemy 2.x + Alembic
- Docker Compose for local dev
- No UI needed for MVP

## Repository layout
```
services/
  cli/           — Typer CLI (discover, monitor commands)
  api/           — FastAPI (ops endpoints only)
  shared/        — DB models, settings, clients
infra/
  docker-compose.yml
docs/
  project-plan.md
  architecture.md
  changelog.md
  oddspapi-guide.md
  polymarket-guide.md
migrations/
```

---

## Verification (how to know it works)

### Discovery
```bash
python -m cli discover --days 7
# Should output: leagues found, teams cached, fixtures found, mappings created
```

### Live Monitor
```bash
python -m cli live
# Should output: WS connected, league polling, hot fixtures when triggered
```

### Live Trading (optional)
```bash
python -m cli live --mode live
# Prompts for age passphrase, derives API creds, and only trades when signals fire
```

### API
```bash
curl http://localhost:8000/ops/status
# Should show: fixture count, mapping count, last discovery time, live match count
```

---

## What we're NOT doing (MVP scope control)
- No broad market discovery (only selected LoL + CS2 leagues)
- No always-running worker polling everything
- No complex settlement specs or quote snapshots for non-live matches
- No UI
- No actual order placement
