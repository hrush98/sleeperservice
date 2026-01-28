# LoL Lead–Lag Arbitrage Engine
## Monitoring + Actionability Math Reference (v2 — Event-Driven Architecture)

This document defines the **analytics core** for the lead–lag strategy:
- Ingest **external "sharp" odds** (Pinnacle via OddsPapi).
- Ingest **Polymarket CLOB market state** (best bid/ask + depth via WebSocket).
- Convert both into comparable probabilities.
- Compute **actionable disagreement** (edge after realistic frictions).
- Produce alerts + lag/quality metrics.

> Scope: **Monitoring and Action side only.** No order execution design here.

---

## 0) Canonical quantities and invariants

### Notation
- External reference: `ref` (Pinnacle)
- Polymarket: `pm`
- Teams/outcomes: `A` and `B` (two-outcome markets)
- Time: `t` (UTC timestamps; store as `t_ref`, `t_pm` from each source)
- Prices/probabilities always in **[0, 1]**, where `1.0` means certainty.

### Required inputs per matched event
You must be able to map:
- External match identifier → Polymarket market + outcome token IDs

**Reference feed (OddsPapi / Pinnacle mirror)**
- `oddsA(t_ref)` and `oddsB(t_ref)` (decimal odds preferred)
- optional: `suspended/locked` state, `last_update`

**Polymarket CLOB state (via WebSocket)**
- best bid/ask for each outcome:
  - `bidA(t_pm)`, `askA(t_pm)`
  - `bidB(t_pm)`, `askB(t_pm)`
- depth ladder (for slippage modeling): top N levels on both sides
- Maintained in-memory via WebSocket `book` and `price_change` messages

### Invariants / sanity checks
- `askA >= bidA`, `askB >= bidB`
- `0 <= bid/ask <= 1`
- External odds must be > 1.0 (decimal) or handled per odds format conversion
- Market definition must match exactly (match winner vs map winner, map number, league, etc.)

---

## 1) Reference odds → implied probability (de-vig)

External books quote odds that embed margin (vig). You should **de-vig** before comparison.

### 1.1 Convert decimal odds → raw implied probability
For team A:
- `qA = 1 / oddsA`
For team B:
- `qB = 1 / oddsB`

### 1.2 De-vig / normalize to fair probabilities
- `p_ref_A = qA / (qA + qB)`
- `p_ref_B = qB / (qA + qB)`

This yields a coherent probability pair: `p_ref_A + p_ref_B = 1`.

> Best practice: store both the raw `qA,qB` and de-vigged `p_ref_A,p_ref_B` plus the implied overround `(qA+qB - 1)` to monitor feed health.

### 1.3 Handling other odds formats
If OddsPapi delivers American odds or fractional odds, convert to decimal first, then apply the above.

---

## 2) Polymarket order book → probability-like prices

Polymarket outcome token prices are already probability-like. Use **order book** values, not last trade.

### 2.1 Definitions
For outcome A:
- **Buy price now** (taker): `p_pm_buy_A = askA`
- **Sell price now** (taker exit): `p_pm_sell_A = bidA`

Same for B.

### 2.2 Mid price (for monitoring/reference only)
- `midA = (bidA + askA)/2`

Use mid as a descriptive statistic and as the **reference point for comparison**. Do **not** use mid to estimate fill cost—use the ask ladder for buys.

### 2.3 Market quality primitives
These are inputs to gating and alert ranking:
- `spreadA = askA - bidA`
- `spreadB = askB - bidB`
- `spread = min(spreadA, spreadB)` or track per outcome
- Depth summaries:
  - `depth_to_price_A(x)` = available shares up to price `x` on asks (for buys)
  - `depth_to_price_A_bid(x)` similarly for bids (for sells)

---

## 3) Dynamic alpha: spread-aware edge threshold

Rather than a fixed edge threshold, we use a **dynamic alpha** that adapts to market conditions.

### 3.1 Alpha formula (entry threshold)

```
alpha_entry = max(min_alpha, spread_factor * spread + buffer)
```

**Default parameters:**
- `min_alpha = 0.03` (3 cents minimum edge required)
- `spread_factor = 1.5`
- `buffer = 0.01` (1 cent)

**Examples:**
| Spread | Alpha |
|--------|-------|
| 0.01   | max(0.03, 1.5×0.01 + 0.01) = 0.03 |
| 0.02   | max(0.03, 1.5×0.02 + 0.01) = 0.04 |
| 0.03   | max(0.03, 1.5×0.03 + 0.01) = 0.055 |
| 0.04   | max(0.03, 1.5×0.04 + 0.01) = 0.07 |

**Rationale:** Wider spreads imply worse books—require bigger edges to compensate for execution friction.

### 3.2 Implementation

```python
def compute_alpha_entry(spread: float, min_alpha: float = 0.03, spread_factor: float = 1.5) -> float:
    return max(min_alpha, (spread_factor * spread) + 0.01)
```

---

## 4) Entry edge: depth-aware average fill

### 4.1 Average fill price from ask ladder

For a candidate size `Q` shares, compute expected average buy price by walking asks:

Let ask ladder be `(p1, q1), (p2, q2), ...` where `p1` is best ask.

```python
def compute_avg_fill_price(asks: list[tuple[float, float]], quantity: float) -> float | None:
    if quantity <= 0 or not asks:
        return None
    remaining = quantity
    total_cost = 0.0
    filled = 0.0
    for price, size in asks:
        if remaining <= 0:
            break
        take = min(size, remaining)
        total_cost += price * take
        filled += take
        remaining -= take
    if filled <= 0:
        return None
    return total_cost / filled
```

### 4.2 Entry edge calculation

The entry signal uses **p_ref vs expected average fill** (not best ask):

```
limit_price = p_ref - alpha
size_available = sum(size for price, size in asks if price <= limit_price)
avg_fill = compute_avg_fill_price(asks, min(Q_probe, size_available))
net_edge = p_ref - avg_fill
actionable = (avg_fill is not None) and (net_edge >= alpha)
```

### 4.3 Entry edge implementation

```python
def compute_entry_edge(
    p_ref: float | None,
    asks: list[tuple[float, float]],
    quantity: float,
    alpha: float,
) -> dict:
    if p_ref is None:
        return {"actionable": False, "limit_price": None, "size_available": 0.0, "avg_fill": None, "net_edge": None}
    
    limit_price = p_ref - alpha
    size_available = sum(size for price, size in asks if price <= limit_price)
    avg_fill = compute_avg_fill_price(asks, min(quantity, size_available)) if size_available else None
    net_edge = (p_ref - avg_fill) if avg_fill is not None else None
    actionable = avg_fill is not None and net_edge is not None and net_edge >= alpha
    
    return {
        "actionable": actionable,
        "limit_price": limit_price,
        "size_available": size_available,
        "avg_fill": avg_fill,
        "net_edge": net_edge,
    }
```

### 4.4 Example

```
p_ref_A = 0.72
asks = [(0.68, 100), (0.69, 150), (0.70, 200)]
spread = 0.68 - 0.65 = 0.03
alpha = max(0.03, 1.5*0.03 + 0.01) = 0.055
limit_price = 0.72 - 0.055 = 0.665

Size available at ≤ 0.665: 0 (best ask is 0.68)
→ Not actionable (no shares below limit price)

If asks = [(0.65, 100), (0.66, 150), (0.67, 200)]:
limit_price = 0.665
size_available = 100 (only first level qualifies)
avg_fill for 100 shares = 0.65
net_edge = 0.72 - 0.65 = 0.07 >= 0.055
→ Actionable! BUY up to 100 shares at limit 0.665
```

---

## 5) Exit signal: convergence detection

### 5.1 Exit condition

Exit (close position) when the market has converged with the sharp reference:

```
exit_signal = (p_ref - bid) <= epsilon
```

**Default:** `epsilon = 0.01` (1 cent)

**Interpretation:** When the best bid price is within 1 cent of the sharp fair probability, the edge has evaporated—time to exit.

### 5.2 Implementation

```python
def compute_exit_signal(p_ref: float | None, bid: float | None, epsilon: float = 0.01) -> bool:
    if p_ref is None or bid is None:
        return False
    return (p_ref - bid) <= epsilon
```

### 5.3 Example

```
Entry: p_ref_A = 0.72, bought at avg_fill = 0.66
...market moves...
Now: p_ref_A = 0.70, bidA = 0.69
exit_signal = (0.70 - 0.69) = 0.01 <= 0.01 → True, EXIT
```

---

## 6) Triggers: event-driven evaluation (two-tier polling)

### 6.1 Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    OddsPapi Two-Tier Polling                    │
├─────────────────────────────────────────────────────────────────┤
│  League Poll                    │  Hot Fixture Poll             │
│  /v4/odds-by-tournaments        │  /v4/odds                     │
│  @ 1000ms (all fixtures)        │  @ 500ms (hot fixtures only)  │
│  Broad radar for Δp_ref         │  High-frequency when moving   │
└─────────────────────────────────┴───────────────────────────────┘
                  │                             │
                  ▼                             ▼
         ┌───────────────────────────────────────────┐
         │           Fixture State Manager           │
         │  - p_ref history per fixture              │
         │  - EWMA(σ) volatility tracking            │
         │  - hot_until timestamps                   │
         │  - trigger detection                      │
         └───────────────────────────────────────────┘
                           │
                           ▼
         ┌───────────────────────────────────────────┐
         │         Polymarket WebSocket              │
         │  - Subscribed to tracked asset_ids        │
         │  - In-memory book state (bid/ask/depth)   │
         │  - No HTTP polling for book data          │
         └───────────────────────────────────────────┘
```

### 6.2 Trigger thresholds

| Trigger Type | Condition | Action |
|--------------|-----------|--------|
| **Primary** | `\|Δp_ref\| >= 0.02` | Evaluate edge, log event |
| **Burst** | `\|Δp_ref\| >= 0.05` | Immediate hot mode (90s TTL), high urgency |
| **Lock/Unlock** | Status toggle | Trigger regardless of Δp_ref |
| **Adaptive** | `\|Δp_ref\| >= max(0.02, 3σ)` | Dynamic threshold based on volatility |

### 6.3 EWMA volatility tracking

Track rolling volatility of p_ref changes per fixture:

```
σ_new = α * |Δp_ref| + (1 - α) * σ_old
```

Where `α = 0.1` (smoothing factor).

Adaptive threshold:
```
threshold_adaptive = max(0.02, 3 * σ)
```

This keeps triggers quiet during calm periods and sensitive during chaotic ones.

### 6.4 Hot fixture escalation

When a trigger fires:
1. Set `hot_until = now + TTL`
   - Primary trigger: `TTL = 60s`
   - Burst trigger: `TTL = 90s`
2. Poll this fixture at 500ms (vs 1s league poll) until `hot_until` expires
3. Renew TTL on continued movement

### 6.5 Anti-flicker guard

Don't trigger purely on single-tick moves. Options:
- Require move to persist for 2 polls (anti-flicker)
- Or accept single-tick triggering only for large jumps (`>= 0.05`)

---

## 7) Quality gating (must pass before alert)

A gap isn't actionable if the market is untradeable.

### 7.1 Spread gate
```
spreadA <= spread_max  (e.g., 0.05)
```

### 7.2 Depth gate
```
size_available >= min_depth  (e.g., 100 shares at limit_price)
```

### 7.3 Freshness gate
```
now - t_ref <= ref_staleness_max  (e.g., 10s)
now - t_pm <= pm_staleness_max    (e.g., 5s, from WS timestamp)
```

### 7.4 Mapping confidence gate
```
mapping_confidence >= min_confidence  (e.g., 0.6)
```

---

## 8) Actionability outputs

### 8.1 Entry alert record

When entry conditions are met:

```python
{
    # Identity
    "fixture_id": "...",
    "market_id": "...",
    "outcome_side": "A",
    "market_type": "match_winner",
    
    # Timestamps
    "t_ref": "...",
    "t_pm": "...",
    "t_alert": "...",
    
    # Reference
    "p_ref_A": 0.72,
    "p_ref_B": 0.28,
    
    # Polymarket state
    "bid_A": 0.65,
    "ask_A": 0.68,
    "mid_A": 0.665,
    "spread_A": 0.03,
    
    # Edge calculation
    "alpha": 0.055,
    "limit_price": 0.665,
    "size_available": 150,
    "avg_fill": 0.66,
    "net_edge": 0.06,
    "actionable": True,
    
    # Trigger info
    "trigger_type": "burst",
    "delta_p_ref": 0.07,
    "urgency": "high",
}
```

### 8.2 Exit alert record

When exit conditions are met:

```python
{
    "fixture_id": "...",
    "outcome_side": "A",
    "p_ref_A": 0.70,
    "bid_A": 0.69,
    "gap": 0.01,
    "exit_signal": True,
    "reason": "convergence",
}
```

---

## 9) Summary: BUY vs SELL logic

| Action | Price Used | Condition | Calculation |
|--------|------------|-----------|-------------|
| **BUY (entry)** | ASK ladder | `p_ref - avg_fill >= alpha` | Walk asks to compute avg fill |
| **SELL (exit)** | BID | `p_ref - bid <= epsilon` | Direct comparison |

### 9.1 Why ASK for entry, BID for exit?

- **Entry:** You're buying into asks. The ask ladder determines your actual fill price.
- **Exit:** You're selling into bids. The best bid determines your exit price.

### 9.2 Why dynamic alpha?

Fixed thresholds don't account for market quality:
- Tight spread (0.01) → small edge can still be actionable
- Wide spread (0.04) → need bigger edge to cover friction

Dynamic alpha automatically adjusts expectations based on current book quality.

---

## 10) Recommended parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| `min_alpha` | 0.03 | Minimum edge threshold |
| `spread_factor` | 1.5 | Alpha multiplier for spread |
| `buffer` | 0.01 | Fixed buffer added to alpha |
| `epsilon` | 0.01 | Exit convergence tolerance |
| `Q_probe` | 100 | Default probe size for depth |
| `primary_threshold` | 0.02 | Δp_ref to trigger evaluation |
| `burst_threshold` | 0.05 | Δp_ref to trigger hot mode |
| `hot_ttl_primary` | 60s | Hot mode duration (primary) |
| `hot_ttl_burst` | 90s | Hot mode duration (burst) |
| `league_poll_ms` | 1000 | OddsPapi batch poll interval |
| `hot_poll_ms` | 500 | OddsPapi hot fixture interval |
| `ewma_alpha` | 0.1 | Volatility smoothing factor |

---

## 11) Checklist: "Is this signal actionable?"

A disagreement becomes a BUY alert only if:

1. **Fresh data**: ref and PM snapshots are recent (WS connected, league poll active)
2. **Correct mapping**: external match ↔ PM market/outcome is high confidence
3. **Trigger fired**: Δp_ref >= threshold (primary/burst/adaptive) or lock toggle
4. **Quality gate**: spread acceptable, depth sufficient
5. **Alpha computed**: `alpha = max(0.03, 1.5 * spread + 0.01)`
6. **Entry edge**: `p_ref - avg_fill >= alpha` after walking ask ladder
7. **Logged**: store full event record for offline analysis

A position becomes a SELL/EXIT signal when:

1. **Exit condition**: `p_ref - bid <= epsilon` (market converged)
2. **Or**: market closed/resolved

---

## Appendix: Implementation reference

See `services/shared/edge.py` for:
- `compute_alpha_entry(spread, min_alpha, spread_factor)`
- `compute_avg_fill_price(asks, quantity)`
- `compute_entry_edge(p_ref, asks, quantity, alpha)`
- `compute_exit_signal(p_ref, bid, epsilon)`
- `devig_two_way_decimal(odds_a, odds_b)`

See `services/shared/fixture_state.py` for:
- `FixtureStateManager` — p_ref history, EWMA σ, hot tracking
- `TriggerEvent` — trigger type, delta, urgency

See `services/shared/polymarket_ws.py` for:
- `PolymarketWSManager` — WebSocket connection, book state
- `BookState` — bid/ask/depth per token
