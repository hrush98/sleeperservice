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

## 2026-02-03
### Summary
- Added CBLOL to target league matching for discovery and Polymarket market filtering.
- Updated docs to reflect top-6 league scope.

### Files changed
- `services/cli/discover.py`
- `services/shared/config.py`
- `services/shared/polymarket_client.py`
- `README.md`
- `docs/project-plan.md`
- `docs/architecture.md`
- `docs/changelog.md`

### Behavior changes
- **Before**: discovery/mapping filtered to LCK/LPL/LEC/LCS/LTA/LCP only.
- **After**: CBLOL tournaments and markets are now included in discovery/mapping.

### Performance impact
- Slightly more fixtures and markets processed during discovery.

### Risk / edge cases
- CBLOL market naming may vary; matching relies on league text including "CBLOL".

### Config changes
- `target_leagues` default now includes `CBLOL`.

### Test plan
- Run `python -m cli discover --days 7` and confirm CBLOL tournaments/fixtures appear.

### Why
CBLOL has sufficient volume to justify mapping and monitoring.

### Impact
- Broader discovery scope for LoL without schema or migration changes.

## 2026-02-03
### Summary
- Unified paper positions into `positions` with paper/real mode and added append-only `trade_events` tape for decision + execution lifecycle tracking.
- Live monitor now writes positions + trade events atomically for entry/exit and emits structured signal events.

### Files changed
- `services/shared/models.py`
- `services/shared/__init__.py`
- `services/cli/monitor_core.py`
- `services/api/routers/ops.py`
- `services/cli/main.py`
- `migrations/versions/0007_trade_events_positions.py`
- `docs/architecture.md`
- `docs/project-plan.md`
- `docs/changelog.md`

### Behavior changes
- **Before**: paper trades were stored in `paper_positions`; no structured event tape for triggers/skips/entries/exits.
- **After**: trades persist in `positions` (`mode=paper|real`), and `trade_events` records trigger/entry/exit and execution lifecycle events.
- **Before**: entry/exit DB writes were separate from any event logging.
- **After**: entry/exit position writes and matching `trade_events` are written in the same DB transaction.

### Performance impact
- Additional DB writes per trigger/entry/exit to record `trade_events`; entry/exit remain low-frequency relative to polling.

### Risk / edge cases
- Increased write volume if triggers are frequent; monitor DB capacity/latency.
- Backfill of `pm_fixture_id` depends on `raw_json.market_id`; rows missing this key will remain null.

### Config changes
- None.

### Test plan
- Run `alembic upgrade head`.
- Run `python -m cli live` during a live match and confirm:
  - `positions` rows are created/updated with `mode=paper`.
  - `trade_events` rows are created for `TRIGGER`, `ENTRY`, `EXIT`.
- Run tests: `conda run -n poly env PYTHONPATH=. pytest -q`.

### Why
Add a first-class decision/execution tape and prepare the schema for real trading without duplicating position tables.

### Impact
- New migration required for `positions` + `trade_events`.
- Live monitor now persists a structured event trail for evaluation and execution workflows.

---

## 2026-02-02
### Summary
- Corrected Polymarket WS disconnect root cause; fixed keepalive + batch payload handling for stable live books.
- Fixed live monitor Polymarket token/outcome mapping by using fresh Gamma payload (reduces CLOB 404s and prevents A/B swaps after pauses/restarts).

### Files changed
- `services/shared/polymarket_ws.py`
- `services/cli/monitor_core.py`
- `docs/changelog.md`

### Behavior changes
- **Before**: WS would connect + subscribe, then drop with `CloseCode.ABNORMAL_CLOSURE` (1006) shortly after the first keepalive; occasional crashes from JSON list payloads (`'list' object has no attribute 'get'`).
- **After**: WS stays connected across many keepalive intervals; JSON list (batched) payloads are handled safely.
- **Before**: Live monitor relied on DB-stored `pm_fixture.raw_json` for `clobTokenIds/outcomes`; after match pause/restart this could go stale causing CLOB `/book` 404s and mis-assigning A/B when outcome labels differed from team names.
- **After**: Live monitor prefers freshly fetched Gamma market payload (`pm_latest`) for `tokens/clobTokenIds/outcomes`, and uses more forgiving outcome↔team matching, keeping Pinnacle p_ref and PM prices aligned.

### Performance impact
- Improved stability (removes reconnect churn); negligible overhead change (WS ping frames + pong wait).
- Slight increase in per-snapshot Gamma reads (but cached via TTL); reduced wasted CLOB HTTP retries/error noise from stale token IDs.

### Risk / edge cases
- If the server stops responding to ping frames, the client will reconnect (expected).
- If Gamma temporarily fails, the monitor falls back to DB-stored market payloads (may reintroduce stale-token behavior until Gamma recovers).

### Config changes
- None.

### Test plan
- Run `PYTHONPATH=./services python -m cli live` and confirm:
  - WS remains connected past the first 5s keepalive interval (no repeated “disconnected/connected” loop).
  - No `'list' object has no attribute 'get'` errors.
- Run tests: `conda run -n poly env PYTHONPATH=. pytest -q`.

### Why
The prior “threading mismatch” hypothesis didn’t match runtime evidence; disconnect timing correlated with the first keepalive message, and the server also sends batched (list) JSON payloads.

### Impact
- Live monitor WS connectivity is stable again.
- Live monitor is resilient to Polymarket market/token reshuffles during pauses/restarts.
- No migration required.

---

## 2026-02-02
### Summary
- Fixed Polymarket WebSocket "no close frame" disconnection bug caused by asyncio event loop threading mismatch.

### Files changed
- `services/shared/polymarket_ws.py`
- `services/shared/config.py`

### Bug description
WebSocket connections to Polymarket CLOB were immediately closing with `CloseCode.ABNORMAL_CLOSURE` (1006) and error message "no close frame received or sent". The connection would establish successfully, send the subscription, start the ping loop, but then drop within seconds. Standalone tests passed, but the live monitor always failed.

### Investigation timeline
1. **Initial hypothesis (wrong)**: Server requires subscription before PING. Fixed ordering, but issue persisted.
2. **Added detailed logging**: Confirmed subscribe completed successfully, ping loop started, but connection still closed.
3. **Key observation**: Standalone tests always worked. Live monitor always failed. Both used identical WebSocket code.
4. **Critical difference identified**: Live monitor runs the WebSocket in a **background thread** with its own event loop, while standalone tests run in the main thread.

### Root cause: asyncio primitives are event-loop bound

The `PolymarketWSManager` class created `asyncio.Lock()` and `asyncio.Event()` in its `__init__` method:

```python
def __init__(self, ...):
    ...
    self._lock = asyncio.Lock()      # Created in main thread's event loop
    self._state_lock = asyncio.Lock()
    self._stop = asyncio.Event()
```

The live monitor architecture:
1. **Main thread**: Creates `PolymarketWSManager()` → asyncio primitives bound to main thread's event loop (or no loop)
2. **Background thread**: `EventDrivenMonitor` creates a **new event loop** via `asyncio.new_event_loop()`
3. **Background thread**: Calls `ws_manager.run()` which tries to use `self._lock`, `self._stop`

**The problem**: `asyncio.Lock` and `asyncio.Event` are tied to the event loop that was active when they were created. Using them from a different event loop causes **undefined behavior**:
- Operations may silently fail
- Coroutines may not properly await
- The WebSocket recv() loop breaks, causing the connection to appear "closed"

This is a subtle bug because:
- No explicit error is raised
- The WebSocket handshake completes successfully
- The subscription sends successfully
- But internal state coordination fails silently

### Fix: Lazy initialization of asyncio primitives

Create asyncio primitives inside `run()` where the correct event loop is guaranteed to be active:

```python
def __init__(self, ...):
    ...
    # Don't create here - wrong event loop!
    self._lock: asyncio.Lock | None = None
    self._state_lock: asyncio.Lock | None = None
    self._stop: asyncio.Event | None = None

async def run(self) -> None:
    # Create in the running event loop (correct!)
    if self._lock is None:
        self._lock = asyncio.Lock()
    if self._state_lock is None:
        self._state_lock = asyncio.Lock()
    if self._stop is None:
        self._stop = asyncio.Event()
    ...
```

### Additional fixes applied
1. **Reordered connection flow**: Send subscription before starting ping loop (server requirement).
2. **Always send subscription**: Empty subscription `{"assets_ids": [], "type": "market"}` satisfies server protocol.
3. **Connection hardening**: Disabled compression, added Origin/User-Agent headers, explicit timeouts.
4. **Reduced ping interval**: 10s → 5s for more aggressive keepalive.
5. **Delayed first ping**: Wait one ping interval before first PING to let recv loop initialize.

### Behavior changes
- **Before**: WebSocket connected then disconnected within seconds with "no close frame" error.
- **After**: WebSocket connects and maintains stable connection indefinitely.

### Performance impact
- WebSocket connections now remain stable, enabling real-time orderbook updates.
- No performance regression.

### Lessons learned
1. **asyncio primitives are event-loop bound**: `Lock`, `Event`, `Queue`, `Condition` must be created in the same event loop where they'll be used.
2. **Thread + asyncio requires care**: When mixing threading with asyncio, primitives must be created inside the async context, not in `__init__`.
3. **Standalone tests can miss threading bugs**: The bug only manifested when the object was created in one thread and used in another.
4. **Silent failures are the hardest**: No exception was raised; the WebSocket just appeared to close randomly.

### Risk / edge cases
- Empty subscription returns no market data until `update_subscriptions()` is called with asset IDs.
- Server may still close connections for rate limiting; reconnect logic handles this.

### Config changes
- `ws_ping_interval_seconds`: Default changed from `10` to `5`.

### Test plan
- Run `python -m cli live` and confirm:
  - "Polymarket WS connected" appears once (not repeatedly).
  - "WS subscribed to N assets" appears.
  - Connection remains stable for extended periods (minutes/hours).
  - No "connection closed" or "no close frame" errors during normal operation.

### Why
WebSocket connectivity is critical for real-time orderbook data. The threading bug made the feature completely non-functional in the live monitor despite passing all standalone tests.

### Impact
- Live monitor WebSocket connections are now stable.
- No migration required.
- Pattern applies to any asyncio code that may run in a different thread than where objects are created.

---

## 2026-02-02
### Summary
- Added paper position lifecycle tracking with entry/exit persistence and cleaner live trade tape output with P&L.

### Files changed
- `services/shared/models.py`
- `services/cli/monitor_types.py`
- `services/cli/monitor_core.py`
- `migrations/versions/0006_paper_positions.py`
- `docs/architecture.md`
- `docs/changelog.md`

### Behavior changes
- **Before**: live paper trades were in-memory only and trade tape did not show P&L.
- **After**: entry/exit trades persist to `paper_positions`, and trade tape shows BUY/SELL with prices and P&L.

### Performance impact
- Slight additional DB writes on entry/exit only; no change to polling cadence.

### Risk / edge cases
- Exit records may have `null` P&L if bid is unavailable at exit.
- DB writes inside live loop assume DB connectivity; failure should be monitored.

### Config changes
- None.

### Test plan
- Run `alembic upgrade head`.
- Run `python -m cli live` during a live match and confirm:
  - Trade tape shows `TRIGGER`, `ENTRY`, `EXIT` with BUY/SELL and P&L%.
  - `paper_positions` rows are created on entry and updated on exit.

### Why
Persisting paper trades enables realistic evaluation and improves operator feedback during live matches.

### Impact
- New migration required for `paper_positions` table.
- Live monitor now writes paper trade lifecycle records to the database.

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
