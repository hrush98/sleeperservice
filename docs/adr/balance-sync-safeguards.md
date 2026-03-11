# ADR: Balance sync safeguards (post exit_size_zero failure)

**Date:** 2026-02-21  
**Status:** Implemented  
**Context:** A live position could not be exited past hard stop because balance reconciliation overwrote a just-confirmed entry quantity (e.g. 78.33 → 0 or near-zero) on a single poll, then exit path saw `sell_shares <= 0` and emitted `exit_size_zero`, blocking the sell.

---

## Root cause (from code)

1. **`_reconcile_open_trade_balances`** (trader.py):
   - Uses `_next_balance_zero_poll_count(balance, eps, prior_count)`. **Only when `balance <= eps`** does `zero_count > 0`; then we use the zero-poll path (require `balance_reconcile_zero_polls_required` before closing) and **never** overwrite `trade.quantity`.
   - When `balance > eps`, `zero_count` is 0; we then hit `if abs(balance - trade.quantity) > eps` and **one-shot** assign `trade.quantity = balance`, `position.quantity = balance`.

2. **Failure mode:** Balance returned as a **small positive** (e.g. 0.001, or rounding artifact) so `balance > eps` → zero path skipped → **one-shot qty_sync 78.33 → 0.001** → `_quantize_market_shares(trade.quantity)` → 0 → **exit_size_zero** and exit blocked.

3. **Alternative failure mode:** Balance returned as **0.0** but very soon after fill (e.g. 2s). Then zero_count=1; we need 3 polls to close. We do **not** overwrite quantity in the zero path. So the only way quantity gets nuked is the **sync branch with balance above eps but small**. If instead the API had returned 0.0 and we had a bug that synced anyway, that would be a different bug; from the current code, the dangerous path is strictly “balance in (eps, small]” taking the sync branch.

Conclusion: the direct fix is to **never one-shot sync a confirmed full size down to zero or below a floor**, and to **trust recent ENTRY_CONFIRMED** so a single low/zero balance right after fill cannot overwrite quantity.

---

## Evaluation of proposed safeguards

### 1. Don’t sync down to 0 (or below a floor) on a single poll

**Idea:** If `balance <= eps` **or** `balance < min_sane_quantity`, treat as “zero” and use the **existing zero-poll path** (increment zero_count, require `required_zero_polls` before closing). Never one-shot `qty_sync 78.33 → 0.00` (or 78.33 → 0.001).

**Adequacy:** **Necessary and sufficient** for the “single poll nuked my size” class of bug.

- Today, only `balance <= eps` goes through the zero path. Adding “or `balance < min_sane_quantity`” (e.g. 1.0 shares or 0.01) ensures any balance below the floor never hits the sync branch; it only goes through zero_count and eventual close after repeated confirmations.
- **Implementation:** Extend “effective zero” in the reconcile loop: e.g. `effective_zero = (balance <= eps) or (balance < getattr(settings, "balance_reconcile_min_sane_quantity", 0.01))`. Use `effective_zero` when computing zero_count (e.g. pass it into a small helper or make `_next_balance_zero_poll_count(balance, eps, min_sane_quantity, current_count)` treat “below floor” as zero). Do **not** run the sync block when we’re in the “effective zero” path (you already don’t, because you `continue` when `zero_count > 0`).
- **Edge case:** Very small real position (e.g. 0.5 shares). With a floor of 1.0, we’d never “sync” to 0.5; we’d treat it as zero and require 3 zero polls to close. That’s acceptable (we never one-shot overwrite 78 → 0.5).

**Verdict:** Adequate; implement with a configurable `balance_reconcile_min_sane_quantity`.

---

### 2. Trust recent ENTRY_CONFIRMED

**Idea:** For a position with a **recent** ENTRY_CONFIRMED (e.g. within 60–120s), **do not overwrite** `trade.quantity` / `position.quantity` with a **lower** balance. Only allow sync when balance **≥** last confirmed quantity (e.g. partial fill then top-up) or after a cooldown.

**Adequacy:** **Necessary and recommended.** Handles “balance lag right after fill” and transient API zeros.

- Even with safeguard 1, a single poll could return a **wrong** balance (e.g. 30) that’s above the floor but below the true 78.33. Without this rule we’d one-shot sync 78.33 → 30 and then try to sell 30 (or less after quantization). If the 30 was a lag/glitch, we’ve already reduced size incorrectly. So “trust recent confirmation” is a second layer.
- **Implementation:** Before applying the sync block (when `balance != trade.quantity`), query the **latest** `TradeEvent` for this `position_id` with `event_type == "ENTRY_CONFIRMED"` (index `ix_trade_events_position_ts`). If found and `(now - event.ts).total_seconds() < entry_confirmed_sync_cooldown_seconds` (e.g. 60–120) and **balance < trade.quantity**, **skip** the sync (do not overwrite). Optionally: only skip when we’re syncing **down** (balance < trade.quantity); syncing up (balance > trade.quantity) can be allowed immediately.
- **Edge case:** User really sold part of position (e.g. 78.33 → 28.33 on UI). After cooldown (e.g. 120s), allow sync so we eventually see 28.33 and sync. So cooldown should be long enough to avoid post-fill lag, but not so long that real partial sells are delayed forever; 60–120s is a good range.

**Verdict:** Adequate; implement with `entry_confirmed_sync_cooldown_seconds` and a query for latest ENTRY_CONFIRMED per position.

---

### 3. “Sync down” only after multiple polls (optional)

**Idea:** When syncing **down** (balance < current quantity), only apply the new quantity when the **same** lower balance has been seen for **several consecutive polls** (similar to `balance_reconcile_zero_polls_required`).

**Adequacy:** **Optional but recommended** for robustness.

- Reduces impact of single-poll API glitches (e.g. one read of 40 when true balance is 78.33). With safeguard 1, we already avoid syncing below a floor; with safeguard 2 we don’t overwrite soon after ENTRY_CONFIRMED. Safeguard 3 adds: when we **do** allow a sync-down (above floor, after cooldown), require e.g. 2–3 consecutive polls with the same lower balance before writing `trade.quantity = balance`.
- **Implementation:** Track per position: `(last_sync_down_balance, consecutive_polls)`. When `balance < trade.quantity` and balance above floor: if `balance == last_sync_down_balance` then increment count; else set `last_sync_down_balance = balance`, count = 1. Only when `consecutive_polls >= balance_sync_down_polls_required` do we set `trade.quantity = balance` and `position.quantity = balance`. When `balance >= trade.quantity`, sync up immediately (or after one poll) and clear the sync-down state.

**Verdict:** Adequate as an optional hardening; combine with 1 and 2 for defense in depth.

---

## Summary

| Safeguard | Adequate? | Role |
|-----------|-----------|------|
| 1. Don’t sync down to 0/floor on one poll | **Yes** | Prevents one-shot 78.33 → 0 (or tiny); use zero-poll path below floor. |
| 2. Trust recent ENTRY_CONFIRMED | **Yes** | Prevents overwriting confirmed size with a lower balance within cooldown. |
| 3. Sync down only after multiple polls | **Yes (optional)** | Requires repeated same lower balance before applying sync-down. |

Together they prevent:
- One-shot quantity wipe from a single low/zero balance (1),
- Post-fill balance lag from overwriting confirmed size (2),
- Single-poll noise when syncing down above floor (3).

---

## Implementation order

1. **Safeguard 1** — Add `balance_reconcile_min_sane_quantity`, treat balance below it as “zero” for zero-count path; no sync below floor.
2. **Safeguard 2** — Add `entry_confirmed_sync_cooldown_seconds`, query latest ENTRY_CONFIRMED per position; skip sync-down when within cooldown and balance < trade.quantity.
3. **Safeguard 3** (optional) — Add `balance_sync_down_polls_required` and per-position state; only apply sync-down after N consecutive same lower balance.

---

## Config suggestions (config.py)

- `balance_reconcile_min_sane_quantity: float = 0.01` (or 1.0 if you prefer “at least 1 share”).
- `entry_confirmed_sync_cooldown_seconds: float = 90.0` (between 60–120).
- `balance_sync_down_polls_required: int = 2` (optional; 2–3 consecutive same balance before syncing down).

---

## Implementation (2026-02-21)

- **Config:** `services/shared/config.py` — `balance_reconcile_min_sane_quantity`, `entry_confirmed_sync_cooldown_seconds`, `balance_sync_down_polls_required`.
- **Trader:** `services/cli/trader.py` — `_next_balance_zero_poll_count` takes `min_sane_quantity`; `_reconcile_open_trade_balances` queries latest ENTRY_CONFIRMED per position, applies cooldown skip and sync-down multi-poll state (`_balance_sync_down_state`); stale cleanup for both zero-poll and sync-down state.
- **Tests:** `tests/test_order_attempts.py` — `test_next_balance_zero_poll_count_below_min_sane_quantity_increments`, `test_next_balance_zero_poll_count_at_or_above_min_sane_quantity_resets`.

---

## Possible gap (out of scope for this ADR)

- **Exit reconciliation** also updates `trade.quantity` / `position.quantity` on partial fills. If a balance poll runs immediately after and overwrites that with a stale balance, we could still get wrong size. Mitigation: safeguard 2’s cooldown could be defined from “last quantity increase” (entry or exit partial) rather than only ENTRY_CONFIRMED; that’s a possible follow-up.
