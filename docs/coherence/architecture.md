# Architecture
Polymarket Coherence Arbitrage Scanner (v1 foundation — date-cascade first, 3-strategy roadmap)

## Overview
Batch scanner that detects coherence violations in Polymarket market families using purely internal constraints.  
Current implementation includes date-cascade monotonicity, partition-sum checks, complement checks, and first-pass implication checks, with Fréchet intentionally kept in roadmap scope.

## Strategy lanes
- **S1 (implemented first): Date cascade monotonicity**
  - Nested "X by date" probabilities must be non-decreasing over time.
- **S_PARTITION_SUM (implemented): ON/RANGE partition checks**
  - Valid-partition sum constraints near 1.
- **S_COMPLEMENT (implemented): Complement consistency**
  - Pair constraint `P(A) + P(not A) ~ 1` for validity-gated complement pairs.
- **S2 (implemented, conservative): Logical implication bounds**
  - Cross-market constraints such as `P(A) <= P(B)` for `A => B`.
- **S3 (planned): Fréchet bounds**
  - Joint-probability bounds across related events/markets.

```
┌──────────────────────────────────────────────────────────────────┐
│                      USER (CLI)                                   │
│  $ coherence scan          │    $ coherence watch --interval 600  │
└──────────────┬─────────────┴──────────────┬──────────────────────┘
               │                            │
               v                            v (periodic loop)
┌──────────────────────────────────────────────────────────────────┐
│                    MARKET SCANNER (scanner.py)                     │
│  Paginate: GET /events?active=true&closed=false&limit=100         │
│  ~7 API calls → ~600+ events with nested markets                  │
│  Filter: events with 2+ active Yes/No markets                     │
│  Cache: write to coherence_cache.json                             │
└──────────────────────────┬───────────────────────────────────────┘
                           │ list[Event with markets]
                           v
┌──────────────────────────────────────────────────────────────────┐
│                 DATE CASCADE DETECTOR (detector.py)                │
│  For each candidate event:                                        │
│  1. Strip date from each market question → common prefix          │
│  2. Parse date from question (regex)                              │
│  3. Sort markets by parsed date                                   │
│  4. Verify descriptions are consistent across series              │
│  Output: list[DateCascade] with sorted markets                    │
└──────────────────────────┬───────────────────────────────────────┘
                           │ list[DateCascade]
                           v
┌──────────────────────────────────────────────────────────────────┐
│               MONOTONICITY CHECKER (checks.py)                    │
│  For each cascade, check all pairs (i < j):                      │
│    if yes_price[i] > yes_price[j]:  → VIOLATION                  │
│    edge = 1.00 - (yes_price[j] + no_price[i])                   │
│  Output: list[Violation] ranked by edge                           │
└──────────────────────────┬───────────────────────────────────────┘
                           │ list[Violation]
                           v
┌──────────────────────────────────────────────────────────────────┐
│                OPPORTUNITY RANKER (ranker.py)                     │
│  For each violation:                                              │
│  1. Pull CLOB orderbook depth for both legs                      │
│  2. Walk ask ladder → realistic fill price at target size         │
│  3. Recompute edge after slippage                                │
│  4. Filter: edge_after_slippage > min_edge_cents                 │
│  5. Score: edge × min(liquidity_a, liquidity_b)                  │
└──────────────────────────┬───────────────────────────────────────┘
                           │ list[RankedOpportunity]
                           v
┌──────────────────────────────────────────────────────────────────┐
│                      CLI OUTPUT (cli.py)                          │
│  Rich table: Cascade | Short Leg | Long Leg | Edge | Liq | Days  │
│  Sorted by edge × liquidity                                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## How the Search Works

### Step 1: Wide Net — Pull Everything

The Gamma API `GET /events` returns events with nested `markets[]`. One event = one logical grouping. We paginate through all active, unclosed events.

```
GET /events?active=true&closed=false&limit=100&offset=0   → 100 events
GET /events?active=true&closed=false&limit=100&offset=100  → 100 events
...
GET /events?active=true&closed=false&limit=100&offset=600  → remaining
```

~7 API calls. Each event includes full market data: questions, prices, descriptions, endDates. No need for separate `/markets` calls.

**What comes back per event:**
```json
{
  "id": "114242",
  "slug": "us-strikes-iran-by",
  "title": "US strikes Iran by ___?",
  "markets": [
    {
      "question": "US strikes Iran by February 13, 2026?",
      "outcomePrices": "[\"0.0115\", \"0.9885\"]",
      "description": "This market will resolve to Yes if...",
      "endDate": "2026-01-31T00:00:00Z",
      "outcomes": "[\"Yes\", \"No\"]",
      "clobTokenIds": "...",
      "volume": "550612",
      "liquidity": "12345",
      "active": true,
      "closed": false
    },
    ...more markets with different dates...
  ]
}
```

### Step 2: Filter to Date Cascade Candidates

From ~600+ events, filter to those that look like date cascades:

1. **Has 2+ markets** (need at least a pair to compare).
2. **All markets are Yes/No** (`outcomes = ["Yes", "No"]`). Multi-candidate events (128 presidential candidates) are filtered out.
3. **Questions share a common stem** when dates are stripped. "US strikes Iran by February 13, 2026?" and "US strikes Iran by March 31, 2026?" share "US strikes Iran by".
4. **Markets are not all resolved** (skip if all prices are 0/1).

This yields ~50-100 testable date cascade series from the current catalog.

### Step 3: Parse Dates and Sort

For each cascade, extract the date from each market's question using regex:

```python
# Patterns like "by February 13, 2026" or "by March 31" or "in 2025"
DATE_PATTERNS = [
    r"by (\w+ \d{1,2},? \d{4})",     # "by February 13, 2026"
    r"by (\w+ \d{1,2})\?",            # "by March 31?"
    r"in (\d{4})\?",                   # "in 2025?"
    r"by (\w+ \d{4})\?",              # "by December 2026?"
]
```

Parse each date, sort the series chronologically. This gives us the term structure.

### Step 4: Check Monotonicity

For each sorted cascade `[m0, m1, m2, ...]` where dates are `t0 < t1 < t2 < ...`:

```python
for i in range(len(markets)):
    for j in range(i + 1, len(markets)):
        yes_short = markets[i].yes_price  # earlier date
        yes_long = markets[j].yes_price   # later date
        if yes_short > yes_long:          # VIOLATION
            edge = 1.00 - (yes_long + (1.0 - yes_short))
            # = yes_short - yes_long
            violations.append(Violation(...))
```

The edge for a hard monotonicity violation is simply `yes_short - yes_long` (the amount by which the shorter date exceeds the longer date in "Yes" price).

---

## Module Responsibilities

### scanner.py — Market Catalog
- Paginate through Gamma `/events` endpoint.
- Parse `outcomePrices` (string → float list).
- Filter to date cascade candidates (2+ Yes/No markets).
- Skip dead markets (volume < threshold, prices at 0/1).
- Cache full catalog to JSON for offline analysis.

### detector.py — Date Cascade Detection
- For each candidate event, extract dates from market questions via regex.
- Verify all questions share a common stem (strip date portion, compare).
- Verify descriptions are consistent (string equality or >95% similarity).
- Sort markets by parsed date → `DateCascade` objects.
- Flag any series where descriptions diverge for manual review.

### checks.py — Monotonicity Math
- Pure functions, no side effects.
- Check all pairs within a cascade for `yes_price[i] > yes_price[j]` where `date[i] < date[j]`.
- Compute edge, pair cost, and guaranteed minimum payout.
- Validate and score ON/RANGE partition sum findings.
- Validate and score complement pair findings.
- Validate and score implication pair findings.
- Return ranked findings by lane.

### ranker.py — Opportunity Scoring
- For each violation, pull CLOB orderbook via `GET /book?token_id=X`.
- Walk ask ladder to compute fill price at target sizes ($100, $500, $1K).
- Recompute edge after slippage.
- Filter by minimum edge and liquidity thresholds.
- Score: `edge_cents × min(depth_a, depth_b)`.

### cli.py — User Interface
- `scan`: one-shot scan → Rich table of violations.
- `watch --interval N`: periodic re-scan with diff (new/stale/gone).
- Configurable via `config.py` settings.

---

## Configuration (additions to shared/config.py)

```python
# Coherence scanner
coherence_scan_interval_seconds: int = 600       # 10 min for watch mode
coherence_min_edge_cents: float = 1.0             # minimum edge to report
coherence_min_volume: float = 1000.0              # skip dead markets
coherence_min_liquidity_usd: float = 50.0         # minimum orderbook depth per leg
coherence_cache_path: str = "services/coherence/cache.json"
coherence_description_match_threshold: float = 0.95  # flag if descriptions diverge
```

---

## API Usage

### Gamma API (free, no auth)
- `GET /events?active=true&closed=false&limit=100&offset=N` — paginated active events with nested markets
- ~7 calls per full scan
- Rate limit: ~60 req/min (generous; a full scan uses <10 requests)

### CLOB API (free, no auth for reads)
- `GET /book?token_id=X` — orderbook depth for a specific token
- Only called for flagged violations (M3+), not during scanning
- ~2 calls per violation (one per leg)

---

## What's NOT in this architecture (current implementation scope)
- No curve fitting / soft violations (hazard rate models — v2+)
- No database (in-memory + JSON cache until execution phase)
- No real-time WebSocket streaming
- No automated execution (manual review first)
- No external data sources
