# Changelog

## Changelog entry format (required)
- Summary (1–2 lines)
- Files changed (explicit list)
- Behavior changes (before/after)
- Performance impact (what improved, what regressed, how measured)
- Risk/edge cases
- Config changes (new keys, defaults)
- Test plan (how to validate)
- Why (brief)
- Impact (behavior/migrations)

## 2026-01-28
### Summary
- Migrated live HTTP to async clients, added bounded concurrency, and split live monitor core/TUI for more predictable latency and shutdown behavior.

### Files changed
- `services/shared/oddspapi_client.py`
- `services/shared/polymarket_client.py`
- `services/shared/polymarket_ws.py`
- `services/cli/monitor_core.py`
- `services/cli/monitor_types.py`
- `services/cli/live_tui.py`
- `services/cli/live.py`
- `services/cli/discover.py`
- `services/shared/config.py`
- `docs/performance.md`
- `docs/changelog.md`

### Behavior changes
- **Before**: live HTTP calls were sync via `asyncio.to_thread`, with serial hot polling across fixtures.
- **After**: live HTTP calls use `httpx.AsyncClient`, hot fixtures poll on independent schedules, and shutdown cancels tasks cleanly.

### Performance impact
- Reduced threadpool overhead and improved cancellation responsiveness under load.
- Hot fixture polling cadence no longer degrades linearly with number of hot fixtures.

### Risk / edge cases
- Async cooldown now serializes requests; verify expected throughput under high fixture count.
- WS book state snapshots are now locked; ensure no deadlocks in the snapshot loop.

### Config changes
- `oddspapi_max_concurrent_live` (default `4`)
- `polymarket_gamma_max_concurrent_live` (default `4`)
- `polymarket_clob_max_concurrent_live` (default `2`)

### Test plan
- Run `python -m cli live` during a live match and confirm:
  - Performance panel updates with stable loop timings.
  - Hot fixtures continue to update at ~500ms cadence.
  - Ctrl+C exits without hanging threads.

### Why
Async I/O and bounded concurrency reduce latency jitter while preserving rate limits.

### Impact
Live monitoring is more responsive and predictable under multi-match load; UI remains decoupled.

## 2026-01-26
### What changed
- Implemented event-driven live monitor flow: league polling + hot fixture polling + Polymarket WS state
- Added OddsPapi `/v4/odds-by-tournaments` client method and config cooldown
- Added Polymarket WS manager (`services/shared/polymarket_ws.py`) with in-memory book state
- Added fixture state manager (`services/shared/fixture_state.py`) for Δp_ref tracking, EWMA, hot TTLs
- Enhanced edge utilities with alpha/avg-fill/exit helpers in `services/shared/edge.py`
- Updated `services/cli/live.py` to use async loops, WS snapshots, and cached OddsPapi payloads
- Updated config with WS connection settings, trigger thresholds, and hot polling controls
- Added `websockets==12.0` dependency for WS client
- Updated docs: architecture, project plan, and OddsPapi guide with new endpoint + flow
  - Architecture flow update (per `docs/architecture.md`):
    - Old: per-mapping OddsPapi `/v4/odds` + CLOB HTTP batch each loop
    - New: league-wide `/v4/odds-by-tournaments` every 1s, hot per-fixture `/v4/odds` at 500ms
    - New: Polymarket WS drives top-of-book + depth in memory; CLOB HTTP only as fallback
    - New: Trigger-driven compute path: Δp_ref event → hot TTL → compare vs PM book
  - Data flow update:
    - OddsPapi batch odds now feed an in-memory cache keyed by fixtureId
    - WS book state now feeds edge calc directly (ask/bid + depth)
    - Gamma market status checks are cached and decoupled from price updates

### Why
This is a latency-sensitive strategy. League-level batch polling surfaces movement quickly, hot polling targets fast changes without blowing rate limits, and WS state removes redundant CLOB HTTP calls.

### Impact
- Live monitor now uses `odds-by-tournaments` as baseline, with 500ms hot polling per fixture
- Polymarket prices come from WS book state (top-of-book + depth in memory)
- Triggering is based on Δp_ref thresholds and hot TTL escalation
- New config options for WS reconnect and trigger tuning

### Files changed
- `services/cli/live.py`
- `services/shared/oddspapi_client.py`
- `services/shared/polymarket_ws.py`
- `services/shared/fixture_state.py`
- `services/shared/edge.py`
- `services/shared/config.py`
- `docs/architecture.md`
- `docs/project-plan.md`
- `docs/oddspapi-guide.md`

### Behavior changes
- **Before**: per-mapping `/v4/odds` and CLOB HTTP batch every loop.
- **After**: league-wide `/v4/odds-by-tournaments` baseline + per-fixture hot polling; CLOB HTTP only as fallback when WS is missing tokens.

### Performance impact
- Fewer CLOB HTTP calls due to WS caching.
- Faster movement detection via batch odds polling.

### Risk / edge cases
- WS disconnects fall back to HTTP; ensure fallback is visible in the perf panel.
- Hot polling escalation depends on trigger thresholds; false positives can increase load.

### Config changes
- `ws_ping_interval_seconds`, `ws_reconnect_base_seconds`, `ws_reconnect_max_seconds`
- `trigger_primary_threshold`, `trigger_burst_threshold`, `trigger_adaptive_multiplier`
- `hot_fixture_poll_ms`, `hot_fixture_ttl_seconds`

### Test plan
- Run `python -m cli live` during a live match and confirm:
  - WS connects and book state updates.
  - Hot polling triggers on Δp_ref movement.
  - Perf panel shows CLOB fallback counts when WS data is missing.

## 2026-01-26 — Map OddsPapi to Polymarket events

### What changed
- Discovery now stores a Polymarket **event fixture** (parent match) with league/teams/start time.
- Match + game markets are stored as **children** of the event via `parent_fixture_id`.
- Mapping now links OddsPapi fixtures to Polymarket **event fixtures** instead of match markets.
- Event start time is now derived from moneyline market `gameStartTime` when present.
- Event start time falls back to event `startDate`/`startDateIso` only if no market time is available.
- Live monitor now considers upcoming matches in the lookahead window and only displays them once OddsPapi reports `statusId=1` (live).
- Live monitor refreshes candidate mappings every 2 minutes (fixed), independent of odds polling cadence.
- Live monitor treats recent OddsPapi odds changes as live even if `statusId=0`, labeling rows as `LIVE*`.

### Why
Market-level timestamps can reflect listing/creation time, not match start. Event fixtures better represent the real-world match for reliable cross-source alignment.

### Impact
- Discovery output uses event start times for mapping and overview.
- Live monitor now resolves match/game markets via the mapped event fixture.

### Files changed
- `services/cli/discover.py`
- `services/cli/live.py`
- `services/shared/models.py`
- `migrations/0005_add_fixture_hierarchy.py`

### Behavior changes
- **Before**: mappings linked to match markets directly.
- **After**: mappings link to event fixtures; match/game markets follow via `parent_fixture_id`.

### Performance impact
- Lower mapping ambiguity reduces downstream comparison churn.

### Risk / edge cases
- Events with missing moneyline `gameStartTime` fall back to event start; confirm cross-source consistency.

### Config changes
- None.

### Test plan
- Run `python -m cli discover --days 7` and verify:
  - Event fixtures are created (market_type = event).
  - Child match/game markets reference the parent via `parent_fixture_id`.

---

## 2026-01-26 — Tighten discovery mapping gates

### What changed
- Mapping now filters OddsPapi and Polymarket fixtures to the requested date window before scoring.
- Added a hard UTC date gate: mappings require the same calendar date on both sources.
- League matching is enforced when detectable (OddsPapi tournament name vs Polymarket market text).
- Team normalization now strips common suffixes (`esports`, `gaming`, `team`) to reduce fuzzy false positives.

### Why
Mismatches were driven by broad candidate pools (old Polymarket events) and weak gating; stricter date + league + cleaner team strings reduces false links.

### Impact
- Fewer low-confidence mappings and far fewer cross‑league/time mismatches.
- Discovery results should align to the `--days` window and UTC dates.

### Files changed
- `services/cli/discover.py`

### Behavior changes
- **Before**: broad candidate pools could match across leagues or stale dates.
- **After**: strict UTC date gate + league match (when detectable) + team suffix normalization.

### Performance impact
- Reduced candidate comparisons per discovery run.

### Risk / edge cases
- If a start time lacks timezone, date gating can be too strict.

### Config changes
- None.

### Test plan
- Run `python -m cli discover --days 7` and confirm:
  - Mappings only occur within the date window.
  - Cross-league matches are rejected.

---

## 2026-01-26 — Decouple live UI and add perf timing

### What changed
- Live monitor now runs data polling in a background loop; UI renders from shared state
- Added per-loop CLOB batch timing and per-fixture OddsPapi timing logs
- Log buffers are now thread-safe to support concurrent data/UI loops
- Added a dedicated Performance panel in the live UI with per-loop timings

### Why
The UI render cadence should not gate data polling or future execution speed; timing logs help isolate the slowest calls.

### Impact
- UI refresh can run independently of data loop duration
- New perf logs in system panel: `perf clob_batch_ms=...` and `perf oddspapi_ms=...`
- Live UI now shows Total Loop, OddsPapi, CLOB, and Gamma timings each loop

### Files changed
- `services/cli/live.py`

### Behavior changes
- **Before**: UI render cadence gated data polling.
- **After**: data polling runs in a background loop; UI reads shared state.

### Performance impact
- Reduced UI-induced jitter in data polling.

### Risk / edge cases
- Shared state access must remain thread-safe under concurrent updates.

### Config changes
- None.

### Test plan
- Run `python -m cli live` and verify:
  - UI updates do not stall data polling.
  - Performance panel updates each loop.

---

## 2026-01-25 — Speed up live monitoring loop

### What changed
- Live monitor now batches CLOB orderbook fetches per loop instead of per mapping
- Gamma market status is cached for 5 seconds to reduce repeat calls
- Live monitor only loads **live** matches (no upcoming) to shrink candidate set
- Odds panel updates now show a timestamp on the Sharps side for parity

### Why
The UI was refreshing quickly but data updates lagged due to sequential per‑mapping API calls.

### Impact
- **Before:** per loop ≈ `N * (OddsPapi 1 + Gamma 1 + CLOB 1)` ⇒ `3N` calls
- **After:** per loop ≈ `N * OddsPapi 1 + Gamma cached (≤N per 5s) + CLOB 1 batch`
- In practice, CLOB calls drop from **N → 1 per loop**, and N is smaller (live‑only)

---

## 2026-01-25 — Add match/game hierarchy for Polymarket

### What changed
- Added `series_type` and `parent_fixture_id` to fixtures for Bo1/Bo3/Bo5 tracking
- Polymarket discovery now links game markets to the parent match market
- Mappings now target match markets; game markets follow parent linkage
- Live monitor loads game markets via match parent to track game end vs match ongoing

### Why
We need a durable match → game hierarchy so game markets can end while the match market remains live.

### Impact
- New migration required for `fixtures` table
- Discovery/live flows now rely on parent-child fixture links

---

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
