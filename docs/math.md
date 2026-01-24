# LoL Lead–Lag Arbitrage Engine
## Monitoring + Actionability Math Reference (Odds Ingestion → Comparison → Alerts)

This document defines the **analytics core** for the lead–lag strategy:
- Ingest **external “sharp” odds** (Pinnacle via OddsPapi).
- Ingest **Polymarket CLOB market state** (best bid/ask + depth).
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

**Polymarket CLOB state**
- best bid/ask for each outcome:
  - `bidA(t_pm)`, `askA(t_pm)`
  - `bidB(t_pm)`, `askB(t_pm)`
- depth ladder (for slippage modeling): top N levels on both sides

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

### 2.2 Mid price (for monitoring only)
- `midA = (bidA + askA)/2`
Use mid as a descriptive statistic; do **not** use mid to estimate fill cost.

### 2.3 Market quality primitives
These are inputs to gating and alert ranking:
- `spreadA = askA - bidA`
- `spreadB = askB - bidB`
- `spread = min(spreadA, spreadB)` or track per outcome
- Depth summaries:
  - `depth_to_price_A(x)` = available shares up to price `x` on asks (for buys)
  - `depth_to_price_A_bid(x)` similarly for bids (for sells)

---

## 3) Gross disagreement / “paper edge”

You’re comparing:
- `p_ref` (external fair probability)
- `p_pm_buy` / `p_pm_sell` (tradeable Polymarket prices)

### 3.1 Buying A
The gross “mispricing” if buying A right now:
- `edge_gross_buy_A = p_ref_A - p_pm_buy_A`

If `edge_gross_buy_A <= 0`, A is not cheap relative to reference.

### 3.2 Buying B (the complement)
Similarly:
- `edge_gross_buy_B = p_ref_B - p_pm_buy_B`

In a binary world, one side may look better depending on spreads and liquidity.

> Best practice: evaluate both sides and pick the **higher net actionable edge**, not “always buy the team that improved.”

---

## 4) Friction-aware edge: turn disagreement into “actionable”

This is the most important section. A disagreement is only actionable if it survives:
- spread
- depth/slippage
- fees (if known)
- (optionally) delay/latency penalty as a monitoring discount

### 4.1 Slippage model from depth (avg fill price)
For a candidate size `Q` shares, compute average buy price by walking asks:

Let ask ladder be `(p1, q1), (p2, q2), ...` where `p1` is best ask.

Fill `Q` shares from the ladder:
- `avg_buy_price_A(Q) = (Σ_i p_i * fill_i) / Q`
where `fill_i = min(q_i, remaining)`

Then:
- `slip_buy_A(Q) = avg_buy_price_A(Q) - askA`

Do this for whichever side you’re considering.

> Best practice: compute `avg_buy_price(Q)` for a small set of sizes (e.g., 25, 50, 100, 250, 500 shares) to build an “edge vs size” curve.

### 4.2 Spread penalty (for “close later” realism)
Even if you aren’t defining execution here, **actionability** should assume that eventually you must exit into the bid.

A conservative spread penalty:
- `spread_penalty_A = spreadA`
A moderate penalty (if you expect improved liquidity on exit):
- `spread_penalty_A = 0.5 * spreadA`

### 4.3 Fee penalty (if applicable / known)
Represent fees in dollar space when you later simulate fills. For monitoring, you can approximate a probability-space penalty:
- `fee_penalty ≈ fee_rate * p_pm_buy_A` (rough)
or store fee separately and subtract during execution simulation.

### 4.4 Latency/delay penalty as a monitoring discount (recommended)
Even without execution, you should discount edge by expected adverse movement during your reaction window.

Define empirical “price velocity after ref jump”:
- `v = median(|ΔmidA| / Δt)` within the first few seconds after similar ref moves
Then a delay discount:
- `delay_penalty ≈ v * τ`
where `τ` is your expected reaction+platform delay window (seconds).

If you don’t have this yet, start with a conservative fixed discount (e.g., 0.01–0.02) and replace with empirical estimates once you have data.

### 4.5 Net actionable edge (buy A, size Q)
A practical definition:
- `edge_net_buy_A(Q) = p_ref_A - avg_buy_price_A(Q) - spread_penalty_A - fee_penalty - delay_penalty`

Similarly for buying B:
- `edge_net_buy_B(Q) = p_ref_B - avg_buy_price_B(Q) - spread_penalty_B - fee_penalty - delay_penalty`

**Actionable condition** (monitoring):
- `max(edge_net_buy_A(Q*), edge_net_buy_B(Q*)) > edge_threshold`

Where `Q*` is your “standard probe size” (e.g., 100 shares) used to decide if the book is truly mispriced.

---

## 5) Triggers: when to compute / alert (avoid noise)

You don’t want to evaluate everything constantly; use trigger logic based on **reference movement** and **market conditions**.

### 5.1 Reference jump trigger
Compute `p_ref_A(t)` each poll. Define:
- `jump = |p_ref_A(t) - p_ref_A(t_prev)|`

Trigger evaluation if:
- `jump >= jump_threshold` (e.g., 0.02)

### 5.2 Reference “lock/suspend” trigger (high signal)
If external feed indicates suspension/lock then reopens with new odds, treat reopen as an immediate trigger. Locks often coincide with discrete events.

### 5.3 Polymarket “staleness” trigger
If `p_ref` moved but PM mid did not:
- `stale = |p_ref_A(t) - midA(t)| >= stale_threshold`
AND PM updates are not keeping pace (few trades / unchanged top-of-book)

This helps identify moments where PM truly lags.

### 5.4 Quality gating (must pass before alert)
A gap isn’t actionable if the market is untradeable.

Example gates:
- `spreadA <= spread_max` (e.g., 0.04)
- `depth_at_or_better_A(askA + depth_band) >= min_depth` (e.g., ≥ 200 shares within +2c)
- Data freshness: `now - t_ref <= ref_staleness_max` and `now - t_pm <= pm_staleness_max`

---

## 6) Actionability outputs: what you store and alert on

### 6.1 Disagreement event record
When a trigger fires and gates pass, create an event with:

**Identity**
- match id (external)
- polymarket market id
- outcome side (A or B)
- market type (match winner / map winner)

**Timestamps**
- `t_ref`, `t_pm`, `t_event_created`

**Probabilities / prices**
- `p_ref_A`, `p_ref_B`
- `bidA`, `askA`, `midA` (and B)
- `spreadA`, `spreadB`

**Edge**
- `edge_gross` (for chosen side)
- `edge_net(Q_probe)` for a small set of Q sizes (or at least one)
- supporting components (spread_penalty, slippage_est, delay_penalty)

**Liquidity**
- depth summaries (e.g., depth within +1c, +2c, +5c)

**Confidence / mapping**
- mapping confidence score + method (auto/manual)
- league/tournament identifiers

### 6.2 Alert score (rank ordering)
Rank alerts by something like:
- `alert_score = edge_net(Q_probe) * liquidity_factor * freshness_factor * mapping_confidence`

Where:
- `liquidity_factor` increases with depth and decreases with spread
- `freshness_factor` decreases with staleness of ref/pm snapshots

Goal: prioritize **realizable** edges, not theoretical ones.

---

## 7) Measurement-first: lag profiling and calibration (best practice)

Even before execution, you should measure whether your assumed edge window exists.

### 7.1 Define “ref move” events
A ref move event occurs when:
- `jump >= jump_threshold`

Record:
- `t0 = t_ref`
- magnitude `Δp_ref`

### 7.2 Measure PM catch-up time
Define PM “caught up” when:
- `|midA(t) - p_ref_A(t0)| <= ε` (e.g., ε = 0.01)
or when PM moves by a large fraction of the ref shift:
- `|midA(t) - midA(t_before)| >= κ * |Δp_ref|` (e.g., κ = 0.6)

Then compute:
- `lag = t_catchup - t0`

Store lag distributions by:
- league (LCK/LPL)
- market type (match/map)
- liquidity bucket (depth/spread)
- magnitude bucket (size of ref move)

### 7.3 “Would-have-been-actionable” rate
Compute:
- fraction of ref move events where `edge_net(Q_probe) > threshold` for at least one side
- average time that condition remained true (edge half-life)

This tells you if polling cadence and reaction windows are even viable.

### 7.4 Guardrails for soundness
To align with best practices in lead–lag systems:
- Treat external odds as **reference**, not oracle: track overround and data glitches.
- Require **freshness** and **mapping confidence** for any alert.
- Use **book depth** to estimate slippage; never alert solely on mid price.
- Keep thresholds conservative until you have empirical lag + fillability evidence.

---

## Recommended default parameters (starting points)
These are intentionally conservative; tune with measurement.

- `poll_interval_ref`: 2–5 seconds (start with 5s if rate-limited)
- `jump_threshold`: 0.02
- `edge_threshold` (net): 0.02–0.05 (2–5 cents)
- `spread_max`: 0.04
- `Q_probe`: 100 shares (and optionally 250)
- `ε` catch-up tolerance: 0.01

---

## Checklist: “Is this signal actionable?”
A disagreement becomes an alert only if:

1) **Fresh data**: ref and PM snapshots are recent.
2) **Correct mapping**: external match ↔ PM market/outcome is high confidence.
3) **Trigger**: ref moved meaningfully (jump/lock).
4) **Quality gate**: spread and depth are acceptable.
5) **Net edge**: `edge_net(Q_probe) > edge_threshold` after spread/slippage/discounts.
6) **Logged**: store an event record with full components for offline analysis.

---

## Appendix: Why odds-first is the right v0 reference
Using external live odds avoids building a LoL win-probability model immediately:
- the “event → probability” mapping is already embedded in the sharp book’s model
- you focus on lead–lag mechanics: latency, book state, slippage, and alerting

Later, you can add event feeds as features or corroboration—but 
::contentReference[oaicite:0]{index=0}
