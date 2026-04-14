# API V0 Spec

## Purpose

This document defines the earliest viable paid API surface for the platform.

The product goal for `v0` is narrow:
- sell explainable, read-only Polymarket analysis
- serve agent builders that already have raw market data but want better analysis primitives
- ship on top of durable historical and structural work before the private trading runtime is mature

This is not an execution API.

## Product thesis

The first paid product should act as the missing analyst layer in an agent stack:
- scanner finds candidate markets
- this API returns ranked, explainable analysis
- the caller decides whether to size, route, or ignore

The strongest `v0` value comes from:
- historical calibration context
- maker or taker expectancy context
- sizing-haircut context
- coherence and structural findings
- live market-state context

The weakest current value comes from:
- unsupported per-market fair-value claims
- opaque “smart money” stories
- any chain-of-thought-like reasoning dump

## Design rules

### 1. Read-only

`v0` exposes analysis only.

It does not:
- place orders
- simulate private portfolio state
- expose private execution controls

### 2. Evidence-backed

Every returned analysis field should map to one of:
- current Polymarket market data
- saved historical-study artifacts
- deterministic structural checks

If the repo cannot support a field with a documented source, the field should not exist in `v0`.

### 3. Explainable without chain-of-thought

The API should expose an optional `evidence_trace`, not raw reasoning text.

The trace should contain:
- short summaries
- source identifiers
- typed metrics
- warnings or caveats

It should not expose hidden chain-of-thought or speculative internal narrative.

### 4. Market-state context is descriptive, not identity inference

`v0` should include a `market_state` block so agents can tell whether a market is calm, thin, or moving quickly.

This block may describe:
- price velocity
- spread
- depth
- imbalance
- short-window volume abnormality

It should not claim:
- “sharps are buying”
- “whales know something”
- “smart money entered”

unless a real, documented classifier exists later.

### 5. Keep the surface tiny

`v0` should ship with two endpoints only:
- ranked opportunities
- single-market analysis

Batch or specialty endpoints can follow later if the first surface gets real demand.

## Base URL

`https://api.yourplatform.com/v0`

Headers:
- `Accept: application/json`
- optional `X-Evidence-Trace: true`

Query parameters may also request trace output. If both header and query param are present, query params win.

## Authentication and payment

Primary:
- `x402` micropayment flow

Fallback:
- bearer API key for non-crypto clients

Suggested rate limits:
- default: `60` requests per minute per key
- higher limits tied to payment or subscription tier

Implementation note for the first public beta:
- start with bearer API keys behind a clean auth boundary
- keep `x402` as the next auth or payment adapter rather than blocking launch on it
- single-instance in-memory rate limiting is acceptable only while the deployment stays single-instance

## Release maturity

The `v0` path is the API contract version.

It is not the same thing as release maturity.

### Internal alpha

Internal alpha is private and short-lived.

Use it for:
- smoke testing
- payload verification
- auth and billing checks
- field naming cleanup
- deployment checks before public traffic

Internal alpha may tolerate:
- changing implementation details
- temporary response rough edges
- restricted access for only the operator and a few trusted testers

It should not be treated as the public launch milestone.

### Public beta

The first public launch target for this API should be `v0 Public Beta`.

Public beta means:
- the API is publicly shareable
- the external response contract is intentionally stable
- the surface remains narrow
- pricing can be cheaper to encourage trial and feedback
- docs should explicitly solicit feedback
- the product should carry clear beta wording and no SLA promise

Public beta is the right launch posture for this repo because:
- the product can start serving real requests now
- the contract can stay honest and narrow
- later phases can improve quality without delaying customer validation

### Later hardened release

After public beta, later work can harden the same `v0` or a later version with:
- stronger caching and latency control
- better observability and incident response
- wider endpoint surface
- more mature ranking and provenance
- later replay, signal-history, and fair-value features where justified

## Public beta minimum bar

Before posting the service broadly, `v0 Public Beta` should have:
- stable JSON schemas for both endpoints
- working auth or payment flow
- rate limiting and abuse controls
- basic monitoring and request logging
- deterministic error responses
- provenance and warnings in the response body
- a manual kill switch or maintenance mode
- short public docs with example requests
- explicit wording that the product is read-only analysis, not execution

These are the minimum public-launch requirements.

They are stricter than internal alpha, but much lighter than a fully hardened production service.

## Public beta error envelope

Public beta should return deterministic JSON errors with:
- `error.code`
- `error.message`
- `error.retryable`
- optional `error.details`
- `request_id`

Example:

```json
{
  "error": {
    "code": "maintenance_mode",
    "message": "SleeperService public beta is temporarily unavailable while maintenance is in progress.",
    "retryable": true,
    "details": {
      "retry_after_seconds": 30
    }
  },
  "request_id": "3d5d7d30780d4fa5a1ef4b1f3c4f6ce0"
}
```

Public beta should also return:
- `X-Request-ID` on every response
- `Retry-After` on `429` and maintenance-driven `503` responses where retry guidance exists

## Shared response objects

### `price_snapshot`

```json
{
  "bid": 0.67,
  "ask": 0.69,
  "midpoint": 0.68,
  "spread_cents": 2.0,
  "as_of": "2026-03-18T08:57:00Z"
}
```

### `market_state`

This is the one intentionally “extra” context block in `v0`.

Its job is to answer:
- is this market quiet or moving
- is there enough depth to care
- is the edge potentially actionable right now

Proposed shape:

```json
{
  "price_velocity_15m_points": 4.2,
  "price_velocity_1h_points": 7.9,
  "volume_1h_usd": 18234.11,
  "volume_zscore_1h": 2.8,
  "depth_near_mid_usd": 1240.0,
  "book_imbalance_ratio": 2.4,
  "volatility_regime": "elevated",
  "state_summary": "Fast-moving and relatively thin: midpoint +4.2c in 15m, 2.8x normal volume, yes-side book imbalance 2.4:1.",
  "as_of": "2026-03-18T08:57:00Z"
}
```

Field notes:
- `price_velocity_*_points` are absolute probability-point changes, not percentages
- `book_imbalance_ratio` should describe visible resting liquidity, not inferred trader identity
- `volatility_regime` should come from a documented threshold policy such as `calm`, `normal`, or `elevated`

### `evidence_trace`

Optional structured trace:

```json
[
  {
    "kind": "coherence_violation",
    "summary": "Complement pair sums to 1.08, above 1.00 by 8.0c.",
    "source": "coherence.complement.v1",
    "metrics": {
      "sum_yes": 1.08,
      "deviation": 0.08,
      "tolerance": 0.04
    }
  },
  {
    "kind": "historical_calibration",
    "summary": "Current bucket historically resolves 3.9 points below implied probability.",
    "source": "historical_study.calibration.v1",
    "metrics": {
      "price_bucket": "60c_to_75c",
      "time_to_resolution_bucket": "1d_to_7d",
      "avg_miscalibration": -0.039,
      "trade_count": 842
    }
  }
]
```

### `calibration_context`

```json
{
  "price_bucket": "60c_to_75c",
  "time_to_resolution_bucket": "1d_to_7d",
  "historical_trade_count": 842,
  "historical_market_count": 117,
  "avg_implied_probability": 0.683,
  "realized_win_rate": 0.644,
  "avg_miscalibration": -0.039,
  "mean_squared_error": 0.087
}
```

### `maker_taker_context`

```json
{
  "role_basis": "counterparty_inferred",
  "maker_avg_pnl_per_contract": 0.021,
  "taker_avg_pnl_per_contract": -0.018,
  "maker_trade_count": 913,
  "taker_trade_count": 913,
  "summary": "Historically, passive participation outperformed taking in this price and time bucket, with inferred takers paying the larger expectancy cost."
}
```

### `sizing_context`

```json
{
  "recommended_haircut_multiplier": 0.5,
  "promotion_status": "candidate",
  "edge_to_noise_ratio": 0.41,
  "trade_count": 147,
  "summary": "Positive historical edge exists, but dispersion is large enough to warrant a 50% haircut."
}
```

## Endpoint 1

### `GET /analysis/opportunities`

Primary use:
- scanner to analyst handoff
- “give me the top N explainable opportunities right now”

#### Query params

- `limit`
  - default: `10`
  - max: `50`
- `categories`
  - comma-separated topic classes
  - default: all supported classes
- `opportunity_types`
  - allowed values: `coherence`, `calibration_watch`, `execution_watch`
  - default: all
- `min_confidence`
  - default: `0.50`
- `min_edge_value`
  - numeric threshold interpreted according to `edge.metric`
- `time_horizon`
  - optional market-time filter
  - interpreted as current time to resolution, not model holding period
  - allowed values for `v0`: `24h`, `7d`, `30d`
- `include_trace`
  - default: `false`

#### Response

```json
{
  "opportunities": [
    {
      "market_id": "0xabc123",
      "slug": "will-x-happen",
      "title": "Will X happen?",
      "category": "politics",
      "opportunity_type": "coherence",
      "price_snapshot": {
        "bid": 0.67,
        "ask": 0.69,
        "midpoint": 0.68,
        "spread_cents": 2.0,
        "as_of": "2026-03-18T08:57:00Z"
      },
      "edge": {
        "metric": "edge_cents",
        "value": 8.4,
        "basis": "coherence_violation"
      },
      "confidence": 0.84,
      "explanation_summary": "Complement and implication checks disagree with current prices, while the current bucket also carries a mild historical overpricing tendency.",
      "market_state": {
        "price_velocity_15m_points": 4.2,
        "price_velocity_1h_points": 7.9,
        "volume_1h_usd": 18234.11,
        "volume_zscore_1h": 2.8,
        "depth_near_mid_usd": 1240.0,
        "book_imbalance_ratio": 2.4,
        "volatility_regime": "elevated",
        "state_summary": "Fast-moving and relatively thin: midpoint +4.2c in 15m, 2.8x normal volume, yes-side book imbalance 2.4:1.",
        "as_of": "2026-03-18T08:57:00Z"
      },
      "calibration_context": {
        "price_bucket": "60c_to_75c",
        "time_to_resolution_bucket": "1d_to_7d",
        "historical_trade_count": 842,
        "historical_market_count": 117,
        "avg_implied_probability": 0.683,
        "realized_win_rate": 0.644,
        "avg_miscalibration": -0.039,
        "mean_squared_error": 0.087
      },
      "maker_taker_context": {
        "role_basis": "counterparty_inferred",
        "maker_avg_pnl_per_contract": 0.021,
        "taker_avg_pnl_per_contract": -0.018,
        "maker_trade_count": 913,
        "taker_trade_count": 913,
        "summary": "Passive participation historically outperformed taking in this bucket."
      },
      "warnings": [
        "Maker context is inferred from counterparty rows rather than direct passive-fill observation."
      ],
      "evidence_trace": []
    }
  ],
  "metadata": {
    "generated_at": "2026-03-18T08:57:00Z",
    "total_scanned": 1843,
    "trace_included": false
  }
}
```

#### Notes

- `edge.metric` prevents ambiguous percentages
- `opportunity_type` tells the caller how to interpret the edge
- `calibration_context` and `maker_taker_context` may be omitted or `null` if no credible bucket match exists

## Endpoint 2

### `GET /markets/{market_id}/analysis`

Primary use:
- deep-dive analysis for a market the caller already selected

#### Query params

- `include_trace`
  - default: `false`

#### Response

```json
{
  "market_id": "0xabc123",
  "slug": "will-x-happen",
  "title": "Will X happen?",
  "category": "politics",
  "price_snapshot": {
    "bid": 0.67,
    "ask": 0.69,
    "midpoint": 0.68,
    "spread_cents": 2.0,
    "as_of": "2026-03-18T08:57:00Z"
  },
  "market_state": {
    "price_velocity_15m_points": 4.2,
    "price_velocity_1h_points": 7.9,
    "volume_1h_usd": 18234.11,
    "volume_zscore_1h": 2.8,
    "depth_near_mid_usd": 1240.0,
    "book_imbalance_ratio": 2.4,
    "volatility_regime": "elevated",
    "state_summary": "Fast-moving and relatively thin: midpoint +4.2c in 15m, 2.8x normal volume, yes-side book imbalance 2.4:1.",
    "as_of": "2026-03-18T08:57:00Z"
  },
  "coherence_findings": [
    {
      "kind": "complement",
      "related_market_id": "0xdef456",
      "deviation": 0.08,
      "tolerance": 0.04,
      "summary": "Complement pair sums to 1.08, above the allowed threshold."
    }
  ],
  "calibration_context": {
    "price_bucket": "60c_to_75c",
    "time_to_resolution_bucket": "1d_to_7d",
    "historical_trade_count": 842,
    "historical_market_count": 117,
    "avg_implied_probability": 0.683,
    "realized_win_rate": 0.644,
    "avg_miscalibration": -0.039,
    "mean_squared_error": 0.087
  },
  "maker_taker_context": {
    "role_basis": "counterparty_inferred",
    "maker_avg_pnl_per_contract": 0.021,
    "taker_avg_pnl_per_contract": -0.018,
    "maker_trade_count": 913,
    "taker_trade_count": 913,
    "summary": "Passive participation historically outperformed taking in this bucket."
  },
  "sizing_context": {
    "recommended_haircut_multiplier": 0.5,
    "promotion_status": "candidate",
    "edge_to_noise_ratio": 0.41,
    "trade_count": 147,
    "summary": "Positive historical edge exists, but dispersion is large enough to warrant a 50% haircut."
  },
  "warnings": [
    "Historical contexts are bucket-level priors rather than a direct fair-value model for this market."
  ],
  "evidence_trace": []
}
```

## Ranking policy for `v0`

`v0` should rank opportunities with a conservative composite score that rewards:
- structural clarity
- historical coverage
- confidence
- actionability from current market state

and penalizes:
- sparse historical buckets
- wide dispersion
- ambiguous inference basis
- stale market snapshots

The exact weighting can change without breaking the external contract as long as:
- inputs remain documented
- returned `edge` and `confidence` semantics stay stable
- `evidence_trace` continues to identify the basis clearly

## Explicit non-goals for `v0`

Do not include these in the first paid release:
- raw execution endpoints
- raw chain-of-thought
- “sharp money” or trader-identity inference
- Bayesian posterior or Monte Carlo claims unless a real documented model exists
- per-market `fair_value` fields that look authoritative without a mature estimator
- signal-history claims before a real signal ledger exists
- historical analog claims before a real analog service exists

## Launch positioning

Recommended launch language:
- `v0 Public Beta`
- read-only
- cheaper early pricing
- feedback requested
- no execution
- no SLA yet

Avoid launch language that implies:
- mature production reliability guarantees
- privileged trader-identity inference
- authoritative per-market fair-value modeling that does not yet exist

## Deferred extensions

Reasonable next additions after `v0`:
- `GET /analysis/calibration-surfaces`
- `GET /analysis/coherence/{market_id}`
- signal-history endpoint after the shared signal ledger exists
- fair-value endpoint after a documented estimator exists
- historical analog endpoint after analog contracts exist

## Mapping to current repo capabilities

This `v0` is intentionally shaped around work the repo either already has or is actively formalizing:
- calibration context from `services.research.studies.calibration`
- maker or taker context from `services.research.studies.execution`
- sizing context from `services.research.studies.sizing`
- topic classification from `services.research.features`
- coherence findings and ranked structural opportunities from `services.coherence`

This `v0` is intentionally not shaped around claims the repo does not yet support as first-class contracts.

## Implementation notes

Suggested build order:

1. Add loader helpers for promoted historical-study artifacts.
2. Add an analysis service layer that combines:
   - live price snapshot
   - market-state calculations
   - historical-study priors
   - coherence lookups
3. Add the two read-only API routes.
4. Add `x402` or API-key auth middleware.
5. Add response-contract tests before widening the surface.

## Verification target for the spec

- The endpoint names match the intended read-only analysis product surface.
- The release-maturity policy distinguishes internal alpha from the first public beta launch.
- The required fields can be traced back to either current market data, saved historical artifacts, or deterministic structural checks.
- The spec does not promise unsupported fair-value or trader-identity inference claims.
