# Changelog

## Changelog entry format (required)
- What changed (1–5 bullets)
- Design decisions (explicit bullets + ADR links)
- Why (brief)
- Impact (behavior/migrations)
- How to verify (commands + expected output)
- Use flowchart TD to show any new flow changes 

flowchart TD
  livePy[live.py_TUI] -->|reads_live_state| liveState[LiveState]
  livePy -->|user_enters_focus_choice| poller[SingleMatchPoller]
  livePy --> trader[TradeManager]
  poller -->|writes| liveState

  poller --> oddsByTournaments[OddsPapi_odds_by_tournaments]
  poller -->|focus_only| hotOdds[OddsPapi_odds_fixture]
  poller -->|focus_only| gamma[Polymarket_Gamma_market_by_id]
  poller -->|focus_only| ws[Polymarket_WS_subscriptions]
  trader -->|focus_only| trading[TradeSignals_and_CLOB_exec]

  Major Problem: 

The legacy monitor_core.py (removed) was a ~3000-line god-class doing everything in one loop: candidate loading, focus selection, match lifecycle tracking, odds polling, WS management, Gamma refresh, snapshot building, trade signal processing, order execution, position management, and UI state assembly. Every feature added more state, locks, and edge cases to one monolithic loop.
The fundamental problems:
Match lifecycle is inferred, not declared. You're trying to guess if a match is live/upcoming/ended from a combination of statusId, start_time, stale timers, PM resolution, PM certainty prices, and fresh-PRE heuristics. These signals conflict constantly, which is why matches show up in the wrong bucket.
The "candidate" abstraction is too broad. You load every mapped match within a time window, then try to figure out which ones matter. This creates the "which one is the focus?" problem that spawned all the buggy selection logic.
The UI is coupled to the polling logic. The snapshot loop builds UI state AND processes trade signals in the same iteration, so a UI display bug (wrong bucket) can interact with trading logic, and vice versa.
Rich Live + stdin is fragile. Rich's Live context redraws the entire screen every tick. Reading from stdin while that's happening is fundamentally awkward in a terminal.

## 2026-02-08 — Exit Retry Guardrails + Phantom Order Recovery (Live)

### What changed
- Normalize balance/allowance values from raw on-chain units using configurable token decimals.
- Clear phantom exit order ids after repeated REST `null` responses to trigger WS asset-side recovery.
- Reopen exit attempts on timeout when balance shows shares still held; add cooldown + max-attempt guards.
- Added unit tests for balance normalization.

### Design decisions
- Reuse existing WS asset recovery by clearing unusable order ids instead of duplicating logic.
- Keep all tunables in `config.py` (token decimals, retry thresholds, cooldown, max attempts).

### Why
Exit reconciliation was stuck by unit mismatch and untrackable order ids. This restores liveness while preventing exit storms.

### Impact
Live exits may retry after timeouts with bounded attempts and cooldowns; balance-based reconciliation now compares correct units. No schema changes.

### How to verify
- `conda run -n poly pytest tests/test_clob_executor.py` → 7 passed
- Live smoke: submit a small exit and confirm `EXIT_RETRY` appears only after timeout and respects cooldown.

```mermaid
flowchart TD
  submitExit[SubmitExit] --> restNull[RESTgetOrderReturnsNull]
  restNull --> notFoundCount[IncrementNotFoundCount]
  notFoundCount --> threshold{ThresholdReached?}
  threshold -->|No| waitRetry[WaitAndRetry]
  threshold -->|Yes| clearId[ClearOrderId]
  clearId --> wsRecover[WSAssetSideRecovery]
  wsRecover -->|NoOrder| balanceCheck[BalanceCheck]
  balanceCheck -->|BalanceGtEps| reopenExit[ReopenForRetryWithCooldown]
```

## 2026-02-08 — Exit Reconciliation via `size_matched` + Balance Fallback (Live)

### What changed
- **CLOB exec hardening**: `clob_executor.py` now catches `PolyApiException` / unexpected exceptions during `post_order` and returns a failed `OrderSubmission` instead of crashing the trade loop.
- **BUY sizing validity**: BUY orders are adjusted so maker notional \(price × size\) rounds down to **USDC cents** while shares remain step-quantized (default 4dp), preventing recurring `invalid amounts` 400 errors.
- **Exit reconciliation**: `trader.py` exit reconciliation now uses `size_matched` as the primary fill-progress signal even when `status` is missing/unexpected; partial exits update remaining share quantity.
- **Balance reconciliation fallback**: when exit order status is unavailable beyond timeout, reconcile using conditional-token balance (shares held) to close or partial-close positions.
- **WS fallback**: when REST status is missing, exit reconcile can sum user-WS trade sizes for the exit order id as an additional fill signal.
- **Tests**: added/extended unit coverage for BUY sizing and timeout helpers.

### Design decisions
- **Truth hierarchy**: balances (shares held) are ground truth; `size_matched` is order-level truth; WS is a low-latency hint with fallbacks.
- **Round down only**: all quantization/reconciliation rounds down and never increases risk (no over-selling / no over-buying).
- **No new migrations**: all changes are behavioral and operate within existing `positions` / `order_attempts` schemas.

### Why
Live mode was frequently getting stuck with:
- `reserved` positions blocking new entries after an exception during order placement, and
- exits stuck in `exit_submitted` due to missing/odd `get_order().status` even when shares had already moved.

### Impact
- Fewer crashes and fewer stranded `reserved` rows.
- Exits should complete automatically in more real-world failure modes (REST lag, WS gaps, status mismatches).
- No database migration required.

### How to verify
- Unit:
  - `conda run -n poly pytest -q tests/test_clob_executor.py tests/test_order_attempts.py`
- Live (small size):
  - Run `python -m cli live --mode live`
  - Confirm: no repeated `PolyApiException ... invalid amounts` errors; BUY/SELL attempts emit `ENTRY_*` and `EXIT_*` events; positions move to `closed_at` without manual exit.

```mermaid
flowchart TD
    exitSubmit[EXIT_SUBMIT] --> reconcile[reconcile_exit_attempt]
    reconcile -->|get_order ok| matched[size_matched drives fill/partial]
    reconcile -->|status missing| ws[User WS trades for order]
    reconcile -->|timeout| bal[conditional-token balance reconcile]
    matched --> closed[Position closed]
    ws --> closed
    bal --> closed
```

## 2026-02-08 — Market FAK Orders + Legacy Monitor Cleanup

### What changed
- **`clob_executor.py`**: FAK submissions now sign via `create_market_order(MarketOrderArgs)` with maker-amount semantics; BUY uses cents-quantized USDC amount, SELL uses shares.
- **`trader.py`**: Live entry computes `buy_usdc`, uses it for allowance checks and submission, and persists it in `raw_json`; FAK submissions pass per-market `tick_size`.
- **`poller.py` + `monitor_types.py`**: Snapshot includes `tick_size` extracted from Gamma; `compute_entry_edge` uses this tick size for price rounding.
- **Legacy removal**: Deleted `services/cli/monitor_core.py` and the ignored classifier test; docs updated to reflect current v3 architecture.
- **Docs**: Updated `docs/project-plan.md`, `docs/architecture.md`, `docs/performance.md`, `docs/adr/0003-single-match-monitor-decomposition.md`.

### Design decisions
- **Market-order signing for FAK**: Align with Polymarket’s maker/taker precision rails instead of forcing limit-order rounding.
- **Tick size from Gamma**: Use `orderPriceMinTickSize` (or fallback keys) so price rounding stays valid when markets change tick sizes.
- **Single live path**: Remove the legacy monitor to prevent diverging execution logic.

### Why
Limit-order signing with FAK produced invalid maker/taker precision even after local rounding. Switching to market-order signing fixes the root cause. Removing the legacy monitor reduces confusion and prevents accidental use of outdated logic.

### Impact
- Live BUY orders are now “spend up to $X at price ≤ P”; actual shares are confirmed via `size_matched`.
- Tick-size rounding follows per-market settings rather than a fixed 0.01.
- Legacy monitor/test removed.

### How to verify
- `conda run -n poly python -m pytest tests/ -q`
- Run `python -m cli live --mode live`, wait for a trigger, and confirm no `invalid amounts` 400s appear in the log.

## 2026-02-07 — Two-Phase Entry Confirmation for Live Orders

### What changed
- **`positions` schema**: Added `status`, `external_order_id`, `external_status` columns for two-phase entry tracking (migration `0009_two_phase_entry.py`).
- **`trader.py` entry flow**: Live entries now create a `submitted` position immediately and emit `ENTRY_SUBMIT` before confirmation.
- **`trader.py` reconciliation**: Submitted entries are confirmed via `get_order(size_matched)` or canceled on zero-fill/timeout; emits `ENTRY_CONFIRMED`/`ENTRY_CANCELLED`.
- **`monitor_types.py`**: Removed pending-only metadata from `PaperTrade` (no more in-memory-only pending state).
- **Docs**: Updated `docs/project-plan.md` + `docs/architecture.md` to reflect two-phase entry tracking.

### Design decisions
- **Durable submitted state**: Always persist a `submitted` position after `post_order` returns (even delayed), preventing zombie positions on restarts.
- **Fill truth = `size_matched`**: Confirmed quantity is taken from CLOB order status, not the requested size.
- **Exit only after confirm**: Exit logic ignores submitted entries until confirmed.

### Why
Delayed CLOB responses and occasional DB failures were creating zombie positions (filled on Polymarket, missing in DB). A durable submitted state plus reconciliation makes the entry lifecycle explicit and crash-safe.

### Impact
- New migration required.
- Live entries now move through `submitted → confirmed/cancelled` states before exit logic applies.
- Trade event tape gains `ENTRY_SUBMIT`, `ENTRY_CONFIRMED`, and `ENTRY_CANCELLED`.

### How to verify
- `conda run -n poly env PYTHONPATH=./services alembic upgrade head`
- `conda run -n poly python -m pytest tests/ -x -q --ignore=tests/test_live_display_classifier.py`
- Live: place a FAK order and confirm the trade tape shows `ENTRY_SUBMIT` followed by `ENTRY_CONFIRMED` or `ENTRY_CANCELLED` within a few seconds.

```mermaid
flowchart TD
    submit[REST_post_order] --> submitted[Position_status_submitted]
    submitted -->|orderId_found| confirmCheck[get_order_size_matched]
    confirmCheck -->|filled_gt_0| confirmed[Position_status_confirmed]
    confirmCheck -->|filled_eq_0| cancelled[Position_status_cancelled]
    submitted -->|timeout_no_orderId| cancelled
```

## 2026-02-07 — Clamp CLOB Order Amount Precision

### What changed
- **`clob_executor.py`**: Quantize FAK order `price` and `size` to 2 decimals before submission; block orders that round to zero.
- **`clob_executor.py`**: Accept `orderID` responses (fallback to `orderId`) to avoid losing delayed order IDs.
- **`clob_executor.py`**: Serialize `price`/`size` as fixed-decimal strings to prevent float precision drift.

### Design decisions
- **Round down to 2 decimals**: Avoids CLOB rejections for maker/taker amount precision while preserving safety (never over-orders).

### Why
Polymarket CLOB rejects orders with maker amount precision >2 decimals or taker amount precision >4 decimals. We were sending raw floats, causing repeated 400 errors and no entries.

### Impact
- Live orders now pass CLOB precision constraints and submit reliably.
- Orders that round to zero are blocked with a clear error reason.

### How to verify
- Run live mode and place a test order; confirm no `invalid amounts` 400 errors appear.
- Unit: `python - <<'PY'\nfrom shared.clob_executor import _quantize_amount\nprint(_quantize_amount(1.234, decimals=2))  # 1.23\nprint(_quantize_amount(0.004, decimals=2))  # 0.0\nPY`

## 2026-02-07 — Add `order_attempts` for Entry/Exit Reconciliation

### What changed
- **`models.py`**: New `order_attempts` table with per-attempt state, linked to `positions`.
- **`trader.py`**: Entry/exit submissions now write `order_attempts` rows; reconciliation processes attempts (entry + exit) and retries exits after 10s not-found.
- **`tests`**: Added a timeout helper test for phantom order IDs.

### Design decisions
- **Attempts are first-class**: Every entry/exit submission is recorded with an attempt sequence so retries are durable and queryable.
- **Exit not-found timeout (10s)**: Missing order IDs are treated as failed attempts; exits retry without manual intervention.
- **Events remain audit trail**: `trade_events` still records `ENTRY_*`/`EXIT_*`, while `order_attempts` is the operational state.

### Why
Exit orders can return delayed IDs that never appear in CLOB (`get_order` returns null), leaving positions stuck. Persisting attempts and adding a not-found timeout makes exit retries automatic and restart-safe.

### Impact
- New migration required.
- Reconciliation now handles both entry and exit attempts from DB (survives restarts).
- Exit failures due to phantom IDs are retried automatically.

### How to verify
- `conda run -n poly env PYTHONPATH=./services alembic upgrade head`
- `conda run -n poly python -m pytest tests/ -x -q --ignore=tests/test_live_display_classifier.py`
- Live: force a delayed exit ID, confirm after 10s you get `EXIT_ERROR(order_not_found)` and a new exit attempt is submitted.

## 2026-02-07 — Add User WS Order Tracking for Delayed CLOB Orders

### What changed
- **`clob_executor.py`**: Store derived API creds and expose `api_creds` for User WS auth.
- **`polymarket_user_ws.py`**: New authenticated User Channel WS manager for order/trade events, plus placement recovery queue.
- **`trader.py`**: `delayed` orders without orderId now create **in-memory pending entries**; reconcile loop upgrades them to real positions on PLACEMENT or drops them after timeout.
- **`monitor_types.py`**: `PaperTrade` now tracks pending metadata (`pending_since`, `pending_reason`).
- **`live.py`**: User WS is created, subscribed to condition IDs, and started/stopped alongside poller/trader.
- **`config.py`**: Added `user_ws_*` config settings (enable flag + timeouts).

### Design decisions
- **User Channel WS as source of truth**: REST `post_order` can return `delayed` with no order ID; User WS provides PLACEMENT/TRADE events with the missing ID.
- **Pending entries are memory-only**: No DB `positions` row is created until PLACEMENT is recovered.
- **Timeout drop**: Pending entries are dropped after `user_ws_untracked_timeout_seconds` if no PLACEMENT arrives.

### Why
`delayed` responses can still execute, but without an `orderId` the system cannot reconcile fills or close positions. The User Channel WS provides the missing lifecycle events.

### Impact
- Delayed orders can be recovered without creating premature DB positions.
- Prevents zombie positions caused by missing `orderId`.
- Adds a new dependency on authenticated WS in live mode.

### How to verify
- `conda run -n poly python -m pytest tests/ -x -q --ignore=tests/test_live_display_classifier.py` → all tests pass.
- Run live mode and place an order in a fast-moving market; verify a pending entry is logged, then upgraded to ENTRY when PLACEMENT arrives. If no PLACEMENT arrives, no DB position is created and the pending entry is dropped.

```mermaid
flowchart TD
    restPost[REST_post_order] -->|status=delayed_no_orderId| pendingEntry[Pending_entry_in_memory]
    pendingEntry -->|PLACEMENT| recoveredId[Recovered_orderId]
    pendingEntry -->|timeout| dropPending[Drop_pending_no_DB]
    recoveredId --> entryFlow[Normal_entry_flow]
```

## 2026-02-07 — Filter Closed-Book Game Markets from Trigger Pipeline

### What changed
- **`trader.py` → `_is_market_ended`**: Added price-certainty detection using `_pm_price_finished` (ported from `monitor_core.py`). Markets where one side is pinned at ≥0.995 and the other at ≤0.01 are now treated as ended, preventing trigger processing and unnecessary ENTRY_CHECK/no_depth events.
- **`trader.py` → trigger propagation**: When a match-level trigger fires, it no longer propagates to sub-markets (e.g. GAME 1, GAME 2) that have no reference price (`p_ref_a` and `p_ref_b` both None) or are already at price-certainty.

### Why
Game markets (GAME 1, GAME 2) share the same OddsPapi fixture ID as the match market. When a match-level p_ref change fires a trigger, it was being propagated to all sub-markets — including games with effectively closed books (e.g. TL at 1.000, opponent at 0.010). Since these game markets have no Pinnacle p_ref, entry candidates always come back empty, producing noisy `ENTRY_CHECK no_depth` log lines and unnecessary DB writes.

### Impact
- Eliminates spurious TRIGGER + ENTRY_CHECK(no_depth) events for decided game markets
- Reduces DB writes (fewer TradeEvent rows for dead markets)
- No behaviour change for actionable markets — match_winner and open game markets are unaffected

---

## 2026-02-07 — Add `edge_spike` Trigger for PM Mean-Reversion

### What changed
- **`fixture_state.py`**: New `check_edge_spike()` method — fires immediately (single poll, no confirmation) when `best_edge >= 4%`. Uses `urgency="high"` and burst-length hot TTL (90s).
- **`config.py`**: New setting `trigger_edge_spike_threshold` (default 0.04). Overridable via env var.
- **`trader.py`**: Edge-based trigger section now checks spike first, then persist. Spike takes priority — if it fires, the persist check is skipped for that poll. Both share the same stale-trigger clearing and entry pipeline wiring.

### Why — capturing PM-driven divergences
Observed that many edge events are caused by Polymarket prices deviating from fair value (not Pinnacle moving), with PM then converging back to book odds. This is a distinct alpha source from lead-lag:

- **Lead-lag**: Pinnacle moves → PM catches up (delta triggers)
- **PM mean-reversion**: PM overshoots/deviates → corrects back (edge triggers)

The `edge_persist` trigger (2 consecutive polls) misses single-poll spikes. A 12% edge lasting one poll would go unactioned. The `edge_spike` trigger catches these — if there's no book depth the entry pipeline skips anyway (`size_available = 0`), so no risk added.

### Design decisions
- **4% threshold**: High enough to avoid noisy small fluctuations, low enough to catch the actionable spikes observed in live monitoring. Configurable via env var.
- **Spike before persist**: In the edge check section, spike runs first. If a 5% edge appears, it fires as a spike immediately rather than waiting for the persist's 2-poll confirmation.
- **No counter/state**: Unlike persist, spike is stateless — just a threshold check. No new fields on `FixtureState`.

### Trigger coverage summary

| Trigger | Signal | Threshold | Confirmation | Catches |
|---|---|---|---|---|
| burst | p_ref delta | ≥ 5% | immediate | Fast Pinnacle moves |
| primary | p_ref delta | ≥ 2% | 2 consecutive polls | Moderate Pinnacle moves |
| adaptive | p_ref delta | ≥ 3×sigma | 2 consecutive polls | Moves above local volatility |
| edge_persist | absolute edge | ≥ 3% | 2 consecutive polls | Slow drift, pre-existing mispricings |
| **edge_spike** | **absolute edge** | **≥ 4%** | **immediate** | **PM overreaction, transient spikes** |

### Impact
- Single-poll edge spikes ≥ 4% now trigger entry evaluation.
- No change to entry criteria or risk profile.

### How to verify
```bash
cd services && python -c "
from shared.fixture_state import FixtureStateManager
mgr = FixtureStateManager()
print(mgr.check_edge_spike('f1', 0.039, 'buy_a'))  # None (below 4%)
print(mgr.check_edge_spike('f1', 0.04, 'buy_a'))   # TriggerEvent (edge_spike)
"
```

---

## 2026-02-07 — Add `edge_persist` Trigger + Fix `_format_seconds` Display Bug

### What changed
- **`fixture_state.py`**: New `check_edge_trigger()` method on `FixtureStateManager`. Tracks consecutive polls where absolute `best_edge >= 3%` on the same side; fires an `edge_persist` trigger after 2 consecutive polls. Counter resets after firing or when edge drops below threshold. `TriggerEvent` gains optional `best_edge` field; `FixtureState` gains `edge_above_count` / `edge_above_side` tracking.
- **`config.py`**: Two new settings — `trigger_edge_persist_threshold` (default 0.03) and `trigger_edge_persist_polls` (default 2). Overridable via env vars.
- **`trader.py`**: Trade loop now calls `check_edge_trigger` when no delta trigger fired and no entry window is active. Stale done triggers are cleared to allow re-triggering. TRIGGER log line shows `edge=+X.XX% (edge_persist)` for the new type. Also fixed `_format_seconds`: changed `if not delta` → `if delta is None` so `timedelta(0)` renders as `"0.0s"` instead of `"n/a"`.

### Why — the trigger funnel was top-heavy
The existing trigger system was purely delta-based: it only fired when p_ref *moved* between consecutive polls (burst ≥ 5%, primary ≥ 2% × 2 polls). This missed a significant class of actionable edges:

1. **Slow drift**: Pinnacle repricing incrementally (e.g. 1c per poll over many polls) — each individual delta was below the 2% primary threshold, but cumulative mispricings of 4–5% developed and sat unactioned.
2. **Pre-existing mispricings**: Edges that existed when monitoring started — the first reading has no prior state, so delta is None and no trigger fires.
3. **Poly-side divergence**: Polymarket book shifting while Pinnacle is stable — pure Poly-side movement isn't tracked by delta triggers at all.

Observed symptom: the live UI showed 4–5% screening edges with zero trade events, because the entry evaluation pipeline was never entered. The downstream gates (alpha entry, depth-aware fill, size constraints) are where risk management lives — the trigger should be a "something interesting, go look" signal, not the tightest filter.

### Design decisions
- Delta triggers retain priority — `check_edge_trigger` only runs when no delta trigger fired and no active entry window exists.
- Edge_persist fires feed into the exact same entry evaluation pipeline (alpha, depth, size). No change to risk controls.
- Counter resets after firing, providing natural cooldown (takes another N polls to re-trigger). Stale done triggers are cleared to allow re-triggering on persistent edges.
- Uses screening `best_edge` (from `compute_net_edges`) as the trigger gate, not the depth-aware edge — this is intentional: the screening edge is the "is it worth looking?" signal; depth-aware alpha check is the entry gate.

### Impact
- More TRIGGER and ENTRY_SKIP/ENTRY events for slow-drift and pre-existing mispricings.
- No change to entry criteria or risk profile — same alpha, depth, and size gates.
- CATCHUP events at `timedelta(0)` now display as `time=0.0s` instead of `time=n/a`.

### How to verify
```bash
# Config loads correctly
cd services && python -c "from shared.fixture_state import TriggerConfig; print(TriggerConfig.EDGE_PERSIST_THRESHOLD, TriggerConfig.EDGE_PERSIST_POLLS)"
# Expected: 0.03 2

# Smoke test: 2 consecutive polls above threshold fires trigger
cd services && python -c "
from shared.fixture_state import FixtureStateManager
mgr = FixtureStateManager()
print(mgr.check_edge_trigger('f1', 0.04, 'buy_a'))  # None (poll 1)
print(mgr.check_edge_trigger('f1', 0.04, 'buy_a'))  # TriggerEvent (poll 2)
print(mgr.check_edge_trigger('f1', 0.02, 'buy_a'))  # None (below threshold, resets)
"

# All tests pass
pytest tests/ -x -q --ignore=tests/test_live_display_classifier.py
```

---

## 2026-02-07 — Multi-Market Support (match_winner + game_winner) in V3 Pipeline

### What changed
- **`poller.py`**: `SingleMatchPoller` now accepts `pm_fixtures: list[Fixture]` instead of a single `pm_fixture`. OddsPapi polling (league + fixture) remains shared; Gamma refresh iterates over all PM markets; WS subscribes to all token IDs across all markets. New `build_all_snapshots()` builds one `FocusSnapshot` per PM fixture, sharing the same odds payload, WS state, and REST fallback books.
- **`live.py`**: Replaced `_resolve_match_market` (returns one fixture) with `_resolve_all_markets` (returns `[match_winner, game1, game2, ...]` by querying children of the event fixture). Selection prompt now reports how many game markets were loaded.
- **`trader.py`**: Trade loop iterates over all snapshots. Trigger detection uses match_winner p_ref; on trigger, a `TriggerRecord` is created per-market (keyed by `fixture_id:market_type:game_number`). Each market evaluates entry/exit independently.
- **`live_tui.py`**: Focus panel renders rows for every market (match_winner + each game), with blank-row separators between market groups.

### Design decisions
- Trigger fires from match_winner moneyline movement (the leading signal) and propagates to all markets. Each market has its own entry window and `entry_done` tracking, so a trigger can produce entries on different markets independently.
- OddsPapi data is shared (one fixture covers match + per-game lines). `_extract_p_refs_from_odds` already dispatched on `market_type`; no changes needed there.
- Gamma refresh polls each PM market sequentially (one `get_market_by_id` per fixture) then batches all token IDs into a single WS subscription update.
- REST fallback for stale WS batches all token IDs across all markets into one `get_orderbooks_batch` call.

### Why
For BO3/BO5 series, individual game markets often have different (and sometimes better) edge profiles than the match_winner. The V3 pipeline was only tracking match_winner, ignoring game_winner children that the discover flow had already ingested. This brought the V2 multi-game capability into the V3 architecture.

### Impact
- No schema changes. No migration required.
- `SingleMatchPoller.__init__` signature changed: `pm_fixture` → `pm_fixtures` (list). Callers must pass a list.
- `TradeManager.__init__` signature changed: `pm_fixture` → `pm_fixtures` (list).
- `render_layout` signature changed: `snapshot` → `snapshots` (list).
- TUI focus panel now shows 2 rows per market (e.g., 8 rows for a BO3 with match + 3 games).

### How to verify
```bash
# All existing tests pass
conda run -n poly python -m pytest tests/ -v --ignore=tests/test_live_display_classifier.py

# Import check
cd services && conda run -n poly python -c "
from cli.poller import SingleMatchPoller
from cli.trader import TradeManager
from cli.live_tui import render_layout
print('All imports OK')
"

# Run live mode on a BO3 match — TUI should show:
# MATCH (ML)  GAM   0.xxx   ...
# MATCH (ML)  DCG   0.xxx   ...
# GAME 1      GAM   0.xxx   ...
# GAME 1      DCG   0.xxx   ...
# ...
```

---

## 2026-02-06 — Partial Exit Fill Handling in Reconciliation Loop

### What changed
- Split `partially_filled` and `filled` handling in `_reconcile_open_orders_loop` — previously both were treated as `"closed"`.
- On `partially_filled`: parse `size_matched` from the CLOB `get_order` response, reduce `trade.quantity` by the filled amount, and set status back to `"open"` so `_check_for_exits` retries the remaining shares on the next tick with a freshly computed limit price.
- On `filled`: mark trade `"closed"` (unchanged behaviour).
- Added `_parse_filled_quantity()` helper that reads the `size_matched` string from the Polymarket order response.
- If `size_matched` is unparseable on a partial fill, the trade is reopened for retry as a safety fallback.

### Design decisions
- Re-use the existing exit retry path (`status="open"` → `_check_for_exits` re-evaluates `p_ref` / `bid` from live snapshot) rather than adding a separate retry queue. This keeps the architecture simple and ensures the retry always uses fresh market data.
- Log partial fills to both `logger` and the trade buffer so they're visible in the TUI.

### Why
- A FAK SELL that only partially fills left the unfilled shares invisible to the system — the trade was marked `"closed"` despite still holding on-chain shares. At small sizes ($2/50 shares) the risk was negligible, but this becomes a real problem at scale.

### Impact
- No schema changes, no migration required.
- Trades that partially fill on exit will now automatically retry until fully closed.
- New trade buffer messages: `PARTIAL_EXIT <key>: filled=X remaining=Y`.

### How to verify
- Unit test: mock `get_order` returning `{"status": "partially_filled", "size_matched": "30"}` for a 50-share trade; assert `trade.quantity == 20` and `trade.status == "open"`.
- Live: observe the trade buffer in the TUI for `PARTIAL_EXIT` lines during thin-book exits.

---

## 2026-02-06 — BUG FIX: Token ID Mismatch Causing Phantom `no_depth`

### Summary
The trader's `_resolve_books` used a different (narrower) token ID parser than the poller's WS subscription logic, causing the trader to fail to find books that were sitting in WS state under different keys.

### Bug details
- **Poller** (WS subscriptions) extracts token IDs via `_extract_outcome_token_pairs`, which reads the rich `tokens` array (`tokens[].token_id`) first, falling back to `clobTokenIds`.
- **Trader** (`_resolve_books`) went straight to `_parse_token_ids` which only reads `clobTokenIds`.
- If the fixture's `raw_json` had the `tokens` array but not `clobTokenIds` (or `clobTokenIds` was empty), the poller would subscribe correctly and receive books, but the trader would look up empty token IDs → `ws_state.get("")` → `None` → `no_depth` on every trigger, even with a healthy WS and populated book.

### What changed
- Added `_extract_outcome_token_pairs` to `trader.py` (same implementation as `poller.py`).
- Updated `_resolve_books` to try the rich `tokens` array first, falling back to `clobTokenIds` + `outcomes` — matching the poller's extraction order exactly.

### Files changed
- `services/cli/trader.py` — `_resolve_books` now uses `_extract_outcome_token_pairs` → fallback; added `_extract_outcome_token_pairs` function.

### Why
Phantom `no_depth` events on triggers where the book was clearly available. The two halves of the system (poller subscribing, trader resolving) disagreed on how to find token IDs from the same fixture data.

### Impact
- Eliminates false `no_depth` when `raw_json` uses `tokens` array instead of `clobTokenIds`.
- No schema changes. No migration required.

---

## 2026-02-06 — Entry Re-evaluation Window & Snapshot Latency Fix

### Summary
Two performance improvements to the live trading critical path: triggers now re-evaluate for 3 seconds instead of single-shot, and the trade loop builds snapshots directly instead of reading stale cached data.

### What changed
- **3-second entry re-evaluation window** — Previously, a trigger got exactly one chance to find actionable depth (`entry_logged = True` on first check). If the book was empty or the edge was below alpha at that instant, the trigger was consumed forever. Now `TriggerRecord` tracks `entry_checked_at` (timestamp) and `entry_done` (bool). The entry block re-evaluates every trade loop iteration (~500ms) for up to `ENTRY_REEVAL_SECONDS` (3s), giving ~6 attempts to find depth. Terminal conditions (entry success, already_open) immediately set `entry_done = True`.
- **Trade loop builds snapshot directly** — The trade loop previously read `poller.get_snapshot()` which was a cached value built by a separate `_snapshot_loop` on its own 500ms cadence. This added 0-500ms of unnecessary latency to every signal. The trade loop now calls `poller.build_snapshot()` directly, getting the freshest OddsPapi + WS book state on every iteration.
- **Logging de-duplication** — `ENTRY_CHECK: no_depth` and `ENTRY_SKIP: edge_below_threshold` only log/persist on the first check of a re-evaluation window, preventing log spam during the 3s retry period.

### Files changed
- `services/cli/monitor_types.py` — `TriggerRecord`: replaced `entry_logged: bool` with `entry_checked_at: datetime | None` and `entry_done: bool`
- `services/cli/trader.py` — Entry evaluation block rewritten for windowed re-evaluation; trade loop calls `build_snapshot()` directly; added `ENTRY_REEVAL_SECONDS = 3.0`
- `services/cli/poller.py` — Renamed `_build_snapshot` → `build_snapshot` (public). `_snapshot_loop` still runs for TUI rendering.

### Why
On thin-book LoL markets, the single-shot entry check was the biggest source of missed trades. Observed lead-lag gaps persist for 10-30+ seconds, but book depth can appear/disappear rapidly. A 3s window gives multiple chances to catch depth as it materialises. The snapshot loop latency was an unnecessary 0-500ms added to every signal evaluation.

### Impact
- Critical-path latency reduced by ~500ms (one fewer sleep hop).
- Triggers that previously failed on `no_depth` now get ~6 retry attempts over 3 seconds.
- No schema changes. No migration required.

### How to verify
```bash
# Unit tests pass
conda run -n poly python -m pytest tests/test_reference_ingest.py -v

# Run live mode and observe:
# - ENTRY_CHECK: no_depth should appear at most once per trigger
# - If depth appears within 3s of trigger, ENTRY should follow
# - Trade tape should NOT show repeated ENTRY_SKIP spam
```

---

## 2026-02-06 — Live Trading Bug Report & Fixes

### Summary
Full review of the live trading path ahead of first real execution. Two blocking bugs found and fixed, plus tick-size rounding added to the edge calculation.

### What changed
- **BUG FIX: Funder address not passed to `ClobExecutor`** — `TradeManager.__init__` constructed `ClobExecutor(private_key=...)` with no `funder`, `signature_type`, or `chain_id`. With `POLYMARKET_SIGNATURE_TYPE=2` (proxy/Gnosis Safe wallet), orders would fail to sign correctly because the CLOB client didn't know the proxy address. Fixed by passing `settings.polymarket_funder_address`, `settings.polymarket_signature_type`, and `settings.polymarket_chain_id` — matching the working pattern in `tests/test_clob_roundtrip.py` (lines 298-304).
- **BUG FIX: `_reconcile_open_orders_loop` crash** — `ClobExecutor.get_order()` returns a raw `dict`, but the reconciliation loop accessed `response.success` and `response.status` as attributes, which would raise `AttributeError` on the first exit-order reconciliation attempt. Fixed to use `isinstance(response, dict)` and `response.get("status")` — matching the test script's `_poll_order` pattern.
- **Tick-size rounding in `compute_entry_edge`** — `limit_price = p_ref - alpha` produced non-tick-aligned prices (e.g. `0.5327`) that Polymarket CLOB would reject with `INVALID_ORDER_MIN_TICK_SIZE`. Added `tick_size` parameter (default `0.01`) and round-down-to-tick logic using `math.floor`, with IEEE-754 float noise guards. The entire downstream pipeline (ask-ladder walk, size calculation, avg fill, edge check) now operates on the rounded price.

### Files changed
- `services/cli/trader.py` — `ClobExecutor` init (funder/sig_type/chain_id), reconciliation loop (dict access)
- `services/shared/edge.py` — `compute_entry_edge` (tick_size param, round-down logic, `import math`)

### Additional findings (not yet fixed)
- **No partial-fill tracking on FAK entry orders** — recorded `entry_price` uses the pre-computed avg fill from the ask ladder, not the actual exchange fill price. Low impact at current position sizes ($2 max, 50 shares max).
- **No alerting on prolonged WS outage in live mode** — REST fallback is correctly disabled, but there's no timeout/alert if the WebSocket stays down.
- **Kill switch / keyfile path typo** — `~/.sleeprservice/` (missing 'e') in config defaults. Works as long as the actual directory matches.

### Why
Preparing for first real live trade execution. The funder bug would have caused every order to fail; the reconciliation bug would have crashed exit-order tracking.

### Impact
- Live trading now functional for proxy wallet setups (signature type 2).
- Exit-order reconciliation loop no longer crashes.
- All limit prices sent to the CLOB are tick-aligned (0.01 default).
- No migration required. No schema changes.

### How to verify
```bash
# Unit tests pass
conda run -n poly python -m pytest tests/test_reference_ingest.py -v

# Manual: run live mode, confirm ClobExecutor init logs show funder address
# Manual: confirm order prices in trade events are multiples of 0.01
```

---

## 2026-02-06 — MAJOR UPDATE v3
### What changed
- Replaced the 3000-line `monitor_core.py` god-class with three focused modules: `poller.py`, `trader.py`, and a thin `live.py` TUI orchestrator.
- Live monitor now **requires an explicit match selection** at startup; the system only watches the chosen match.
- Removed candidate lists, focus heuristics, and LIVE/UPCOMING/RECENTLY_ENDED bucket logic from the live monitor.
- Simplified the TUI to a single-match layout: Status, Focus, Trade Tape, Logs, Command.
- Live CLI flags removed: `--min-confidence`, `--lookahead-minutes`, `--stale-minutes`.
- `monitor_types.py` simplified to `FocusSnapshot` + `MonitorState` and kept only the needed shared buffers/types.

### Design decisions
- **Operator declares the match**; the system never guesses which one matters. (ADR: `docs/adr/0003-single-match-monitor-decomposition.md`)
- Keep discovery fully separate; live reads from DB once at startup.
- Polling, trading, and rendering are separate modules with no shared mutable state beyond buffered snapshots.

### Why
- Focus-selection heuristics were the root cause of misclassification and wrong-bucket UI bugs.
- A single-match contract removes 90% of state, locks, and edge cases.
- Decoupling polling from UI prevents display bugs from affecting trading logic.

### Impact
- Live monitor watches exactly one match at a time; use `r` to re-select.
- No Upcoming/Recently Ended panels in live mode (use `discover`/`analyze` instead).
- Match lifecycle is no longer inferred; selection is explicit.

### How to verify
- Run `conda activate poly` then `python -m cli live`.
- Pick a match from the prompt; confirm the TUI locks to that match.
- Press `r` to re-select a match and confirm the poller/trader restart cleanly.

flowchart TD
  livePy[live.py_TUI] -->|match_selection| poller[SingleMatchPoller]
  livePy --> trader[TradeManager]
  poller -->|snapshot| livePy
  poller --> oddsByTournaments[OddsPapi_odds_by_tournaments]
  poller --> oddsFixture[OddsPapi_odds_fixture]
  poller --> gamma[Polymarket_Gamma_market_by_id]
  poller --> ws[Polymarket_WS_subscriptions]
  trader --> tradeEvents[TradeEvents_Positions]

## 2026-02-06
### What changed
 - Added a blocking startup focus prompt (next 5 mapped matches) before the TUI starts.
- Added a dedicated command input panel so typing is not overwritten by logs.
- Treat missing OddsPapi cache for stale fixtures as ended to avoid Upcoming misclassification.
- Added an in-run focus selection prompt when multiple live matches are detected.
- Restricted hot OddsPapi polling and Gamma refresh to the focus match only.
- Focus panel no longer auto-selects upcoming/ended matches during live ambiguity.
- Restricted trading and CLOB polling to a single live match at a time.
- Added auto-detection of team-side flips when Polymarket outcomes appear reversed.
- Kept Live/Upcoming/Recently ended UI buckets intact while trading only the focus match.

### Design decisions
- Avoid auto-focus when multiple live candidates exist; require explicit operator selection.
- Keep the three-bucket UI layout while making focus selection explicit.
- Keep focus selection interactive before live without new CLI arguments.
- Render a persistent input panel to prevent log overwrites.

### Why
- Reduce wrong-match focus and prevent trading/polling on non-target matches.
- Allow pre-selection before matches go live.
- Make TUI command entry reliable under live refresh.
- Prevent ended matches from lingering in Upcoming when odds data drops.

### Impact
- LIVE starts with a numbered focus list; use `focus <n>` to pre-select.
- When multiple live matches exist, trading waits for a manual focus pick.
- Hot polling and Gamma refresh now track only the selected match.
- Commands stay visible while the TUI refreshes.

flowchart TD
  startupList[StartupFocusList] -->|focus_n| manualFocus[ManualFocusSelected]
  manualFocus -->|match_live| focusMatch[FocusMatch]
  focusMatch -->|focus_only| hotOdds[OddsPapi_odds_fixture]
  focusMatch -->|focus_only| gamma[Polymarket_Gamma_market_by_id]
  focusMatch -->|focus_only| trading[TradeSignals_and_CLOB_exec]

### How to verify
- Run `conda activate poly` then `python -m cli live` from `services/`.
- Confirm the focus list appears at startup and `focus 1` locks the selection.
- Type commands while logs update and confirm the input line remains visible.

## 2026-02-05
### What changed
- Added a rotating "black box" log file for the live TUI at `./logs/live-<paper|live>.log`.
- Added config keys: `log_dir`, `live_log_backup_days`.
- Ignored `logs/` in git.
- Updated architecture + project plan to document the new runtime behavior.

### Design decisions
- Keep the on-screen TUI log stream unchanged; attach a file handler in parallel for postmortems.

### Why
- Preserve a full timeline of infra issues and unexpected exceptions during live runs without disrupting the Rich TUI.

### Impact
- Running `python -m cli live` now creates/rotates files under `./logs/` (best-effort; if unwritable, it falls back to TUI-only logging).

### How to verify
- Run `python -m cli live --mode paper` and confirm `./logs/live-paper.log` is created and appended to.
- Optionally run with `--mode live` and confirm `./logs/live-live.log` is created.

### What changed
- Fixed a Live-mode failure where odds/streams could appear to “freeze” after a trigger/trade event by hardening monitor task supervision and guarding the snapshot loop against uncaught exceptions.

### Design decisions
- Prefer failing loudly (log + stop) over continuing in a half-alive state where background polling continues but the UI state no longer updates.

### Why
- Triggers/entry handling can surface rare exceptions; if the snapshot task dies, the UI stops updating while other tasks (OddsPapi/Gamma/WS) may still run, making the issue intermittent and hard to diagnose.

### Impact
- Live runs should no longer silently freeze; if a task crashes, an explicit error is logged to the TUI log stream / black box log and the monitor is cancelled/stopped rather than hanging.

### How to verify
- Run `python -m cli live --mode live` and wait for a trigger.
- If an internal exception occurs, expect an `ERROR snapshot loop crashed:` (or `ERROR monitor task crashed:`) line in the log stream instead of a stuck UI.

## 2026-02-04
### What changed
- Split the live TUI into **Live**, **Upcoming**, and **Recently ended** tables and added `PIN=...` row labels.
- Introduced strict in-play vs fresh PRE Pinnacle classification.
- Implemented two-tier OddsPapi league polling cadence (1s in-play, 5s pre).
- Added periodic Gamma market refresh loop with cached metadata.
- Added a post-start grace window to keep delayed matches visible in Live with `PIN=PRE (post-start)`.
- When Pinnacle reports `statusId=1`, the match is force-included in candidates even if the DB fixture status is stale.
- Added config keys: `pin_pre_fresh_minutes`, `starting_soon_minutes`, `starting_soon_grace_minutes`, `oddspapi_league_poll_seconds_pre`, `oddspapi_league_poll_seconds_inplay`, `polymarket_gamma_refresh_seconds`.
- Updated architecture and project plan to reflect the new cadence and UI split.
- Reworked live display buckets into a single classifier and added **Upcoming** + **Recently ended** panels.
- Added a per-match rollup of successful positions in the **Recently ended** panel.
- Added `pm_done_threshold` (default `0.99`) for Polymarket finish detection.

### Design decisions
- ADR 0002: `docs/adr/0002-starting-soon-panel-and-polling-state-machine.md`

### Why
- Make pre-start visibility explicit without labeling PRE as live.
- Reduce request load while keeping live responsiveness.
- Prevent live/starting-soon flicker by using a single display bucket rule and always surfacing delayed matches in Upcoming.

### Impact
- Pre-start matches now appear under **Upcoming** only when PRE odds are fresh.
- Live matches can stay in **Live** during delays when PRE odds remain fresh (labelled `PIN=PRE (post-start)`).
- League polling is slower when nothing is in-play; Gamma metadata refresh is decoupled from the 0.5s snapshot loop.
- Completed matches now surface a compact rollup of winning positions; Polymarket finish uses the 0.99 threshold.

### How to verify
- `python -m cli live --min-confidence 0.7 --lookahead-minutes 30`
- Expect three match tables (Live / Upcoming / Recently ended), with pre-start matches showing `PIN=PRE (fresh)` in **Upcoming**, and in-play matches in **Live** (including `PIN=PRE (post-start)` during delays).
- Expect a winning-positions rollup when positions close with positive pnl.

## 2026-02-03
### Summary
- Added optional live trading mode using Polymarket CLOB client with strict safety rails.

### Files changed
- `services/cli/live.py`
- `services/cli/monitor_core.py`
- `services/cli/monitor_types.py`
- `services/shared/clob_executor.py`
- `services/shared/secret_utils.py`
- `services/shared/config.py`
- `requirements.txt`
- `docs/architecture.md`
- `docs/project-plan.md`
- `docs/changelog.md`

### Behavior changes
- **Before**: live monitor only simulated (paper) positions and trade events.
- **After**: live monitor can prompt for `paper` vs `live`; live mode decrypts the EOA key, derives API creds, and places FAK orders when signals fire.

### Performance impact
- Slight overhead from live-mode order reconciliation loop and allowance checks; no change in paper mode.

### Risk / edge cases
- Missing allowances or insufficient balance blocks live entries.
- Incorrect private key or passphrase prevents live startup.
- Kill switch file halts live order placement.

### Config changes
- Added `polymarket_keyfile_path`, `polymarket_chain_id`, `polymarket_signature_type`.
- Added live trading guards: `live_max_usd_per_order`, `live_max_shares_per_order`, `live_max_open_positions`, `live_min_seconds_between_orders`, `live_kill_switch_path`, `live_require_allowance_check`.
- Added dependency: `py-clob-client`.

### Test plan
- `python -m cli live` → choose `paper`, confirm normal TUI behavior.
- `python -m cli live --mode live` → ensure passphrase prompt appears and no orders are placed without signals.

### Why
- Begin controlled live execution with small caps for real-world behavior testing.

### Impact
- Optional live execution path; no DB migration required.

LIVE TRADING UPDATE 2026-02-3
Design
Trading mode selection
Extend the CLI flow used by python -m cli live to support a run mode:
Interactive prompt at start (default to paper if stdin is not a TTY).
Also add a non-interactive --mode {paper,live} so you can automate later.
Execution abstraction
Introduce a tiny execution interface used by EventDrivenMonitor:

PaperExecutor: current behavior (record positions/events, simulate fills)
ClobExecutor: real placement + reconciliation using py-clob-client
This keeps the monitor logic unchanged except at the “place entry” and “place exit” points.

Using py-clob-client
Add dependency py-clob-client.
Initialize as EOA (signature type 0):
client = ClobClient(host=settings.polymarket_clob_url, key=<decrypted_pk>, chain_id=137, signature_type=0)
creds = client.create_or_derive_api_creds() (nonce defaults to 0)
client.set_api_creds(creds)
Place orders:
Entry: OrderArgs(token_id=<token>, price=<limit_price>, size=<shares>, side=BUY) then post_order(..., OrderType.FAK)
Exit: same but side=SELL, OrderType.FAK
Mapping A/B to token_id
Today _resolve_books(...) uses WS BookState.asset_id which is already the token id.
Extend entry candidate building to also return the chosen token_id per side, so the executor doesn’t re-derive it.
State + persistence
Continue writing to existing tables:
positions: set mode="real", venue="polymarket_clob" for live.
trade_events: set mode="real", record external_order_id, external_status, and raw responses.
Add a periodic reconciliation loop in live mode:
Poll client.get_order(order_id) to update matched size and status.
Update Position.quantity to matched size once known (or store as `raw_json["matched_size"] and keep `quantity as intended size if you prefer).
Safety rails (must-have before first live run)
Configurable caps in services/shared/config.py (new settings):
live_max_usd_per_order and/or live_max_shares_per_order
live_max_open_positions
live_min_seconds_between_orders (throttle)
live_kill_switch_path (if file exists, never place orders)
live_require_allowance_check=true
Pre-trade checks (live only):
Ensure token id is known and book has depth.
Ensure you are not already in an open position for that market+side (DB unique constraint already supports this).
Check balances/allowances via client.get_balance_allowance(...); if insufficient, emit TradeEvent(event_type="BLOCKED") and skip.

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
- Added explicit `convergence_seconds` tracking on `trade_events` and `positions` for PM↔SB delay historicals.

### Files changed
- `services/shared/models.py`
- `services/cli/monitor_core.py`
- `migrations/versions/0008_convergence_seconds.py`
- `docs/project-plan.md`
- `docs/changelog.md`

### Behavior changes
- **Before**: convergence timing was implicit (derived from timestamps) and only recorded in `raw_json.catchup_seconds`.
- **After**: convergence timing is stored in `trade_events.convergence_seconds`, and on convergence exits also in `positions.convergence_seconds`.

### Performance impact
- Negligible extra write volume (one nullable float per event/position).

### Risk / edge cases
- If `trigger_ts` is missing, `convergence_seconds` remains null.

### Config changes
- None.

### Test plan
- Run `conda run -n poly env PYTHONPATH=./services alembic upgrade head`.
- Run `conda run -n poly env PYTHONPATH=.:./services python -m cli live` and confirm `EXIT`/`CATCHUP` rows have `convergence_seconds`.
- Run tests: `conda run -n poly env PYTHONPATH=.:./services pytest -q`.

### Why
PM↔SB convergence time is a core metric and should be explicitly queryable.

### Impact
- New migration required; no runtime config changes.

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
