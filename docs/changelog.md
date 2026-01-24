# Changelog

## 2026-01-24 — Clarify Bo3 market scope (moneyline + games)

### What changed
- Documented the required markets for Bo3 matches:
  - Match winner (moneyline)
  - Game winner (Game 1/2/3)
- Updated discovery/mapping flow to track market_type and game_number
- Updated live monitor flow to compare moneyline and game markets separately
- Noted fixtures should carry market_type + game_number for Polymarket markets
- Added migration to add market_type + game_number fields on fixtures

### Why
Polymarket offers separate markets for match winner and each game. We must avoid mixing or guessing odds across market types.

### Impact
- Discovery and live monitor logic must select the correct market type
- Schema additions are required (market_type, game_number) when implemented

---

## 2026-01-24 — Fixed Polymarket discovery flow

### What changed
- **Fixed Polymarket discovery** to follow the documented API flow:
  - `/sports` → find LoL entry (`sport="lol"`, `series=10311`)
  - `/teams?league=lol` → cache teams (100 teams)
  - `/events?series_id=10311&tag_id=100639&closed=false` → get only open LoL events
- **Key fix**: Use `closed=false` filter instead of `active=true` to get unresolved events
- **Key fix**: Match `sport="lol"` specifically (not by title, since "lcs" = soccer Leagues Cup!)
- **Added retry logic for OddsPapi 429 errors** with exponential backoff
- **Added global cooldown** (1s between ANY OddsPapi requests) to avoid rate limits
- **Fixed 404 handling** for tournaments with no fixtures

### Why
The original implementation was downloading ~10,000+ markets from `/markets?tag_id=100639` (ALL game bets across ALL sports), then filtering locally. This was slow and wasteful. Now we fetch only LoL-specific data.

### Impact
- Discovery now completes in ~30 seconds (vs several minutes)
- Found 76 open events → 530 fixtures on Polymarket
- **7 mappings created** between OddsPapi and Polymarket fixtures
- Far fewer API calls to Polymarket (3 calls vs 100+)

---

## 2026-01-24 — v2 IMPLEMENTATION (code complete)

### What changed
- **Deleted old worker service** — removed `services/worker/` files (main.py, poller.py, league_filter.py, mapping_resolver.py, reference_ingest.py, shadow_engine.py, spec_hash.py, Dockerfile)
- **New simplified models** — `services/shared/models.py` with 6 tables (leagues, teams, fixtures, mappings, odds_snapshots, shadow_orders)
- **New Alembic migration** — `0003_v2_simplified_schema.py` drops old tables and creates new schema
- **Refactored OddsPapi client** — `services/shared/oddspapi_client.py` with proper cooldown handling and v4 API support
- **New Polymarket client** — `services/shared/polymarket_client.py` with Gamma API + CLOB orderbook support
- **New CLI module** — `services/cli/` with Typer commands:
  - `discover --days N` — collect upcoming matches and build mappings
  - `monitor` — watch live matches and compare odds
  - `status` — show system state
- **Simplified API** — only `/ops/status` and `/ops/live` endpoints remain
- **Updated README** — complete rewrite with v2 usage instructions

### Why
Implementation of the v2 simplified MVP design documented in the earlier changelog entry.

### Impact
- Run migrations: `alembic upgrade head` (will drop old tables!)
- Install typer: `pip install -r requirements.txt`
- New CLI usage: `python -m cli discover --days 7` and `python -m cli monitor`
- Old API endpoints removed: `/markets`, `/external/*`, `/mappings`, `/disagreements`, `/shadow-orders`

---

## 2026-01-24 — MAJOR REDESIGN (v2 simplified MVP)

### What changed
- **Completely rewrote `docs/project-plan.md`** — new v2 design focused on minimal MVP
- **Completely rewrote `docs/architecture.md`** — two-mode system (Discovery CLI + Live Monitor)
- **Removed broad discovery approach** — no longer polling all Polymarket markets
- **New data model** — 6 tables instead of 10+ (leagues, teams, fixtures, mappings, odds_snapshots, shadow_orders)
- **CLI-first design** — `discover --days N` and `monitor` commands instead of always-running worker

### Why
The previous design was over-engineered for an MVP:
- Polling all Polymarket markets (ETH price action, etc.) when we only care about LoL
- Too many tables with unclear purpose (quote_snapshots, settlement_specs, derived_metrics, disagreement_events)
- No clear way to validate the system was working
- "Worker runs forever" model with no visibility into what it was doing
- No CLOB orderbook integration (the actual execution surface)

The new design is:
- **Targeted**: Only LoL top 5 leagues (LCK, LPL, LEC, LCS, LCP)
- **On-demand discovery**: Run CLI to collect upcoming matches, not continuous polling
- **Live-focused**: Monitor mode only runs during live matches
- **Observable**: Clear console output + simple `/ops/status` endpoint
- **Minimal**: 6 tables that directly support the use case

### Impact
- **Codebase cleanup required**: Most existing worker code should be replaced
- **Schema migration needed**: New tables, old tables can be dropped
- **CLI entry point needed**: `services/cli/` with Typer commands
- **Clients refactored**: OddsPapi + Polymarket clients aligned to new API flows

### New API endpoints (OddsPapi)
- `/v4/tournaments?sportId=18` — get LoL leagues
- `/v4/participants?sportId=18` — get team names
- `/v4/fixtures?tournamentId=X&from=...&to=...` — get upcoming matches
- `/v4/odds?fixtureId=X&bookmakers=pinnacle` — get live odds

### New API endpoints (Polymarket)
- `/sports` — get LoL leagues (series_id)
- `/teams?league=...` — get team names
- `/events?series_id=X&tag_id=100639` — get upcoming matches
- CLOB orderbook — get live prices (not Gamma `/markets`)

---

## 2026-01-22
- Updated OddsPapi auth assumption: API key is passed via `apiKey` query parameter; aligned settings/env vars with `ODDS_API_KEY` + `POLY_API_KEY`.
- Marked M0–M3 as **(complete)** in `docs/project-plan.md` and adjusted milestone descriptions to match implemented ingestion (Gamma `/markets` polling + quote snapshots).
- Updated `docs/architecture.md` diagram/data flow to match current implementation (Gamma `/markets` polling + quote snapshots; orderbook snapshots are later).
- Updated roadmap to prioritize **paper/shadow execution** (record decisions, no live orders) alongside lag measurement.
- Swapped the planned reference source from BetInAsia/PS3838 to **OddsPapi (Pinnacle)**.
- Updated `docs/architecture.md` + `docs/project-plan.md` to reflect the new reference adapter and data flow.
- Why: validate that actionable lag windows exist in live conditions before investing in execution complexity.
- Impact: documentation-only change; code and schema updates will follow the updated milestones.

## 2026-01-21
- Updated project-plan process guidance:
  - When a milestone is finished, annotate it with **(complete)** in `docs/project-plan.md`.
- Why: keeps the master plan current and reduces ambiguity about progress.
- Impact: no runtime changes; documentation workflow change only.

## 2026-01-21
- Pivoted project direction from "Prediction Market Momentum" to **LoL Lead–Lag Arbitrage Bot**:
  - Reframed scope around PS3838 (BetInAsia) as fast reference vs Polymarket LoL CLOB as lagging target
  - Added measurement-first gate (lag distributions + fillability metrics) before any live execution
  - Defined new canonical entities: external matches/odds snapshots, mapping table, disagreement/edge events, and order book snapshots
- Why: the core edge is a short timing window where Polymarket prices lag a sharp external line; measurement must validate the window and fillability.
- Impact: governing docs now target lead–lag arbitrage; future milestones, schema additions, and ingestion sources should align with this plan.

## 2026-01-16
- Initialized repo documentation seed:
  - Added core-guidance.md, project-plan.md, architecture.md
  - Established canonical schema v0 and component boundaries
- Rationale: create stable, always-on project guidance for agentic development in Cursor.
- Impact: provides governing docs; subsequent changes must be reflected here.
