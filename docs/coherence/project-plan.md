# Polymarket Coherence Arbitrage Scanner
Master project plan (v1 foundation — 3-strategy roadmap)

## Vision
Detect and exploit **price incoherencies** across related Polymarket markets using purely mathematical constraints. No external data sources, no news monitoring, no sentiment analysis — just structural mispricings within PM's own market catalog.

## Core insight
Polymarket contains multiple families of mathematically constrained market structures. We can exploit incoherencies in these structures without external priors:
- date-cascade monotonicity
- logical implication bounds
- Fréchet-type joint bounds

Date cascades remain the first implementation slice, but the scanner now starts one layer earlier: discover markets, assign family structure, then route to strategy checks.

## Design principles
- **Pure math, no opinions**: Edge comes from price inconsistency, not forecasting.
- **Hold to resolution**: No exit strategy needed — profit is locked at entry.
- **Zero external cost**: All data from Polymarket Gamma API (free, no auth).
- **Scan, don't stream**: Periodic batch scans, not continuous real-time monitoring.
- **Date cascades first, not date-only**: Build the first production slice on date cascades, while preserving implication and Fréchet as active roadmap strategies.

## Strategy stack (kept in scope)
- **S1: Date cascade monotonicity** (implement first)
- **S2: Logical implication bounds** (enabled with conservative lexical validity gates)
- **S_COMPLEMENT: Complement consistency bounds** (enabled with conservative lexical validity gates)
- **S3: Fréchet bounds** (joint-probability coherence checks)

---

## Family-first discovery model (in-memory first)

### Pipeline
1. **Discovery**: pull active events/markets from Gamma.
2. **Family assignment**: classify each event (or event subset) into one or more structural families.
3. **Strategy routing**: run only strategy checks that are valid for the assigned family.

This lets us scan broadly without forcing everything into date cascades.

### Core families
- **BY_CASCADE**: "Will X happen by T?" markets with strictly increasing dates and nested CDF-like structure.
- **ON_PARTITION**: "Will X happen on T?" bucketized dates plus optional "not by end date" bucket; mutually exclusive/exhaustive partition style.
- **RANGE_PARTITION**: buckets like "<A", "A-B", ">=B" over value ranges, usually same resolution timestamp.
- **BINARY_COMPLEMENT_PAIR**: separately framed complements (`A` vs `not A`), useful as consistency checks and glue constraints.
- **IMPLICATION_PAIR**: candidate logical links where `A => B` may hold across markets or series.
- **JOINT_TRIPLE** (Fréchet candidate): triplets needed for Fréchet bounds (`A`, `B`, `A∩B` or proxy).
- **MIXED_HYBRID**: events that contain multiple structures together and must be split/routed instead of directly scored.

### Family to strategy routing
- **BY_CASCADE** -> monotonicity checks (S1), later curve-shape checks.
- **ON_PARTITION** -> partition sum/exclusivity checks; optional conversion to cumulative form for cascade checks.
- **RANGE_PARTITION** -> partition coherence and adjacency consistency checks.
- **BINARY_COMPLEMENT_PAIR** -> complement consistency checks (`P(A) + P(not A) ~ 1`) with strict pair validity gating.
- **IMPLICATION_PAIR** -> implication bounds (`P(A) <= P(B)` when `A => B`) with strict directional validity gating.
- **JOINT_TRIPLE** -> Fréchet bounds on feasible intersection ranges.
- **MIXED_HYBRID** -> decompose into subfamilies, then apply the matching checks.

---

## Strategy: Date Cascade Monotonicity

### The Constraint
For any "X by date T" series: `P(X by T1) ≤ P(X by T2)` when `T1 < T2`.

A longer time window can only make an event *more* likely, never less. If "Iran strike by March" Yes = $0.16 but "Iran strike by June" Yes = $0.14, that's a mathematical violation.

### Trade Construction
- Buy "Yes" on the longer-dated (underpriced) market.
- Buy "No" on the shorter-dated (overpriced) market.
- Cost = `P_yes_long + P_no_short`. If cost < $1.00, guaranteed profit at resolution.

**Example:** Yes-June = $0.14, No-March = $0.84 → cost $0.98, guaranteed payout $1.00, profit $0.02/pair.

**Why it's guaranteed:** In all possible outcomes:
- Event by March → both resolve, Yes-June pays $1, No-March pays $0 → total $1.00
- Event in April–June → Yes-June pays $1, No-March pays $1 → total $2.00
- Event never → Yes-June pays $0, No-March pays $1 → total $1.00

Minimum payout is always $1.00. If entry cost < $1.00, guaranteed profit.

---

## How the Search Works

### The Wide Net: Pull All Active Events

The Gamma API `GET /events?active=true&closed=false` returns events with **nested markets**. One API call gives you the event metadata AND all its child markets. This is the natural grouping mechanism — Polymarket already organizes date cascades into single events.

**Pagination:** The endpoint returns up to 100 events per call. There are ~600+ active events. We paginate through all of them: ~7 API calls total.

**Per event, we get:**
- `id`, `slug`, `title` — event identity
- `markets[]` — nested array of all markets in the event, each with:
  - `question`, `description` — what the market asks + resolution criteria
  - `outcomePrices` — current Yes/No prices (e.g., `["0.14", "0.86"]`)
  - `endDate` — resolution deadline
  - `outcomes` — typically `["Yes", "No"]`
  - `active`, `closed` — market status
  - `volume`, `liquidity` — for filtering dead markets
  - `clobTokenIds` — needed later for orderbook depth

### Filtering: Date Cascades vs Other Multi-Market Events

Not every event with 3+ markets is a date cascade. There are two main types:

**Date cascades** (what we want):
- All markets have `outcomes: ["Yes", "No"]`
- Questions differ only by the date portion
- Example: "Starmer out by February 28?", "Starmer out by March 31?", "Starmer out by June 30?"

**Multi-outcome events** (not date cascades):
- Markets represent different candidates/options
- Example: "Democratic Presidential Nominee 2028" with 128 candidate markets
- These have their own coherence constraints (probabilities should sum to ~1) but are often handled by PM's neg-risk system already

**Detection heuristic:**
1. Event has 2+ active, unclosed markets with `outcomes = ["Yes", "No"]`.
2. Market questions share a common prefix when the date portion is stripped.
3. Each market has a parseable date in the question or `endDate`.

This is cheap and deterministic — no NLP, no similarity scoring. Just string parsing.

### What We Skip

- **Single-market events**: No series to compare. Skip.
- **Multi-outcome events**: Different strategy (neg-risk / probability sum). Out of scope for v1.
- **Resolved/closed markets**: Prices are fixed at 0 or 1. Skip.
- **Dead markets**: Volume < $1,000 or liquidity near zero. No point trading.

---

## How Trading Works

### Entry
For each detected monotonicity violation, construct a **paired position**:
- Leg A: Buy the underpriced side of the longer-dated market.
- Leg B: Buy the overpriced side of the shorter-dated market.
The pair is the atomic unit — never enter one leg without the other.

### Hold to Resolution
No exit needed. At resolution, the mathematical constraint guarantees minimum payout ≥ $1.00 per pair. Capital is locked until the shorter-dated market resolves (after which one leg pays out).

### Capital Efficiency
- Edge per pair is typically 2–5 cents.
- $1,000 deployed on a 3-cent violation = $30 guaranteed profit at resolution.
- Capital is locked until the shorter leg resolves (days to months).
- Annualized return depends on time-to-resolution: a 3% return in 30 days = ~36% annualized.

---

## Data Model (in-memory first, persist later)

### In M0–M3: In-Memory + JSON Cache

```python
@dataclass
class MarketInfo:
    market_id: str
    question: str
    description: str
    yes_price: float
    no_price: float
    end_date: str
    event_slug: str
    event_id: str
    volume: float
    liquidity: float
    clob_token_ids: str  # for orderbook queries later

@dataclass  
class DateCascade:
    event_slug: str
    event_title: str
    markets: list[MarketInfo]  # sorted by date ascending
    descriptions_consistent: bool  # all descriptions match

@dataclass
class FamilyAssignment:
    event_id: str
    event_slug: str
    family: str  # BY_CASCADE, ON_PARTITION, RANGE_PARTITION, ...
    market_ids: list[str]
    confidence: float
    notes: str

@dataclass
class StrategyCandidate:
    assignment: FamilyAssignment
    strategy: str   # S1_MONOTONICITY, S2_IMPLICATION, S3_FRECHET, ...
    ready: bool
    reason: str

@dataclass
class Violation:
    cascade_slug: str
    short_market: MarketInfo  # earlier date (overpriced)
    long_market: MarketInfo   # later date (underpriced)
    pair_cost: float          # yes_long + no_short
    edge_cents: float         # 1.00 - pair_cost (in cents)
    guaranteed: bool          # always True for monotonicity
```

### In M5+: Database Tables

Deferred until execution phase. Schema will include `coherence_families`, `coherence_violations`, and `coherence_positions` with paired-leg tracking and resolution monitoring.

---

## Milestones

### M0 — Market Discovery + Family Assignment
- Paginate `GET /events?active=true&closed=false` to pull all active events.
- Parse market metadata: question, prices, endDate, description, volume.
- Assign family labels in-memory (`BY_CASCADE`, `ON_PARTITION`, `RANGE_PARTITION`, etc.).
- Mark mixed events as `MIXED_HYBRID` and split into subgroups where possible.
- Cache to JSON file for offline analysis.
- **Verify:** Print event count, family counts, and sample assignments per family.

### M1 — Family Router + Date Cascade Detector
- Route assignments to compatible strategy checkers.
- For `BY_CASCADE` assignments:
  - Strip date from each market question to confirm common prefix.
  - Parse the date from each question (regex: month + day + year patterns).
  - Sort markets by parsed date.
  - Verify descriptions are consistent across the series (simple string equality or high similarity).
- Output: list of `DateCascade` objects, each with sorted markets.
- **Verify:** Print per-strategy routed counts and detected cascades with member markets and dates.

### M2 — Monotonicity Checker
- For each `DateCascade`, check all adjacent pairs:
  - If `yes_price[i] > yes_price[i+1]` (earlier date more expensive than later), flag violation.
  - Also check all non-adjacent pairs (i vs j where i < j) for completeness.
- Compute edge: `edge = 1.00 - (yes_long + no_short)`.
- Rank violations by edge size.
- **Verify:** Print violations with market pair, prices, and guaranteed edge.

### M2.5 — Non-Cascade Coherence Checks (in-memory)
- `BINARY_COMPLEMENT_PAIR`: check complement sum drift (`|1 - (P(A)+P(not A))|`) for `VALID_PAIR` entries.
- `IMPLICATION_PAIR`: check implication bounds (`P(A)-P(B)`) for `VALID_PAIR` entries.
- `ON_PARTITION` / `RANGE_PARTITION`: check partition sum and basic exclusivity coherence.
- `JOINT_TRIPLE`: collect Fréchet-ready triplets for S3 scoring.
- **Verify:** Print violation counts by family and top candidates per check.

### M3 — Liquidity + Orderbook Check
- For flagged violations, pull CLOB orderbook depth for both legs.
- Walk the ask ladder to compute realistic fill prices at $100, $500, $1000.
- Recompute edge after slippage.
- Filter out violations where post-slippage edge ≤ 0 or liquidity is too thin.
- **Verify:** Print actionable violations with fill prices and max deployable size.

### M4 — CLI + Continuous Scanning
- Typer CLI entry point:
  - `python -m coherence scan` — one-shot scan, print results.
  - `python -m coherence watch --interval 600` — periodic re-scan.
- Rich table output: cascade name, market pair, edge, liquidity, time-to-resolution.
- Configurable thresholds via `config.py`.
- **Verify:** Run `scan`, see violations. Run `watch`, see periodic updates.

### M5+ — Execution (gated on M4 showing real violations)
- Paired order submission via existing `ClobExecutor`.
- Position tracking in database (`coherence_positions`).
- Resolution monitoring and PnL tracking.

---

## Strategy roadmap after date-cascade foundation

### S2: Logical Implication Bounds
First pass enabled with conservative lexical gates (`or`/`either`/`any of` union cue plus token-subset overlap), then scored via `P(A) <= P(B)`. Future work is cross-event relation graphs and stronger semantic verification.

### S_COMPLEMENT: Complement Consistency Bounds
Enabled for explicitly negated pairs (`A` vs `not A`) using lexical overlap and opposite-negation gates. Hard violation condition is `|1 - (P(A)+P(not A))| > tolerance`.

### S3: Fréchet Bounds
Joint probability constraints across three markets. Rare on PM. Stretch goal.

### Curve Fitting (Soft Violations)
Fit hazard-rate models to date cascade term structures and trade deviations from the fitted curve. Positive EV but not guaranteed. Requires more statistical sophistication.

---

## Tech stack
- Python 3.11+ (conda env `poly`)
- Gamma API for market catalog (free, no auth)
- CLOB API for orderbook depth (free, no auth for reads)
- Existing `shared/polymarket_client.py` for API calls
- Existing `shared/clob_executor.py` for execution (M5+)
- Typer CLI (same as lead-lag)
- No database until M5 (in-memory / JSON cache)

## Repository layout
```
services/
  coherence/          — NEW: coherence scanner service
    __init__.py
    scanner.py        — market catalog pull + caching
    detector.py       — date cascade detection + date parsing
    checks.py         — monotonicity check math
    ranker.py         — opportunity scoring + liquidity check
    cli.py            — Typer CLI entry point
  shared/             — EXISTING: reused by both services
    polymarket_client.py
    clob_executor.py
    config.py
docs/
  coherence/
    project-plan.md   — this file
    architecture.md   — component diagram + data flow
  project-plan.md     — lead-lag bot (unchanged)
  architecture.md     — lead-lag bot (unchanged)
```

---

## What we're NOT doing (v1 scope control)
- No curve fitting / soft violations (v2+)
- No external data sources
- No database until execution phase
- No real-time streaming — periodic batch scans only
- No automated execution until violations are manually verified
