# LoL Lead–Lag Arbitrage Bot
Master project plan (v2 — simplified MVP)

## Vision
Detect and exploit short-lived **lead–lag** inefficiencies between Pinnacle odds (via OddsPapi) and Polymarket LoL CLOB markets. Start with paper trading; gate live execution on measured edge.

## Core insight
Pinnacle reprices faster than Polymarket. When Pinnacle moves, there's a brief window where Polymarket is stale. We detect that window, measure it, and (later) trade it.

## Design principles (MVP)
- **Two separate modes**: Discovery (on-demand CLI) vs Live Monitor (runs during matches)
- **No broad discovery**: Only ingest LoL matches from top 5 leagues
- **Minimal storage**: Only what's needed for mapping + live comparison
- **Clear observability**: Know exactly what's happening at each step

---

## System modes

### Mode 1: Discovery CLI (on-demand)
Run manually to collect upcoming matches for the next N days. Not always running.

**OddsPapi flow:**
1. `GET /v4/tournaments?sportId=18` → get tournament IDs for LCK, LPL, LEC, LCS/LTA, LCP
2. `GET /v4/participants?sportId=18` → cache team ID → name mappings
3. `GET /v4/fixtures?tournamentId=X&from=...&to=...&hasOdds=true` → get upcoming fixtures
4. Store: leagues, teams, fixtures

**Polymarket flow:**
1. `GET /sports` → find `sport="lol"` entry (series=10311). Note: "lcs" = soccer Leagues Cup, "lpl" = cricket!
2. `GET /teams?league=lol` → cache team mappings
3. `GET /events?series_id=10311&tag_id=100639&closed=false` → get open (unresolved) LoL events
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

**CLI interface:**
```
$ python -m cli discover --days 7
Collecting matches for next 7 days...
OddsPapi: Found 12 fixtures across 5 leagues
Polymarket: Found 8 events with markets
Mappings created: 6 (high confidence), 2 (review needed)
```

### Mode 2: Live Monitor (during matches)
Runs when mapped matches go live. Compares odds in real-time.

**For each live mapped match:**
1. Poll OddsPapi `/v4/odds?fixtureId=X` for Pinnacle prices
2. Extract **moneyline** and **game winner** (Game 1/2/3) prices
3. Poll Polymarket CLOB orderbook for the corresponding market(s)
4. Compute gap per market: `pinnacle_implied_prob - polymarket_mid`
5. If gap exceeds threshold → record shadow order
6. Log everything for later analysis

**Output:**
- Console logs showing live comparison
- Shadow orders stored in DB
- Simple `/ops/live` endpoint showing current state

---

## Target leagues (top 5 LoL)
- **LCK** (South Korea) — strongest region
- **LPL** (China) — strongest region
- **LEC** (Europe)
- **LCS/LTA** (North America)
- **LCP** (Asia-Pacific)

OddsPapi sportId for LoL: **18**

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
- `match_details` (jsonb; what matched: league, teams, date)
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

### shadow_orders (append-only)
- `id` (uuid pk)
- `mapping_id` (fk → mappings.id)
- `ts` (timestamptz)
- `side` (text; "buy_a" | "buy_b")
- `pinnacle_prob` (double precision)
- `polymarket_price` (double precision)
- `gap` (double precision)
- `reason` (text)
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

### M2 — Live Monitor (basic)
- Implement OddsPapi odds polling
- Implement Polymarket CLOB polling
- Compare and compute gap
- Record shadow orders
- CLI: `monitor` (watches live matches)

### M3 — Observability
- `/ops/status` endpoint (counts, last update times)
- `/ops/live` endpoint (current live matches + latest gaps)
- Console output that makes sense

### M4 — Evaluation
- Review shadow orders vs actual price movements
- Measure lag distributions
- Decide if edge is real

### M5+ — Execution (gated)
- Only after M4 shows consistent edge
- Conservative limits, circuit breakers

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
python -m cli monitor
# Should output: watching N live matches, gap updates every X seconds
```

### API
```bash
curl http://localhost:8000/ops/status
# Should show: fixture count, mapping count, last discovery time, live match count
```

---

## What we're NOT doing (MVP scope control)
- No broad market discovery (only LoL top 5 leagues)
- No always-running worker polling everything
- No complex settlement specs or quote snapshots for non-live matches
- No UI
- No actual order placement
