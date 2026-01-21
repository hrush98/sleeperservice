# Architecture
Prediction Market Intelligence Engine (v0)

## Overview
The system is split into:
- **Worker**: ingestion + periodic analytics jobs
- **API**: read-only query layer
- **Postgres**: canonical storage for metadata + time series + raw payloads
- (Later) **UI**: Next.js consuming API

## Component diagram (logical)

+-------------------+           +-------------------+
|   Polymarket API   |           |  (Later) Other PM |
|  markets endpoint  |           |     connectors    |
+---------+---------+           +---------+---------+
          |                               |
          | HTTP (poll)                   |
          v                               v
+---------------------------------------------------+
|                     WORKER                        |
| - connector: polymarket                           |
| - ingestion loop (idempotent upserts)             |
| - snapshot writer (append-only)                   |
| - analytics jobs (derived_metrics)                |
+----------------------+----------------------------+
                       |
                       | SQL writes
                       v
+---------------------------------------------------+
|                    POSTGRES                       |
| markets, outcomes, settlement_specs               |
| quote_snapshots (append-only)                     |
| derived_metrics (latest or append-only)           |
| raw_json preserved for reprocessing               |
+----------------------+----------------------------+
                       ^
                       | SQL reads
+----------------------+----------------------------+
|                       API                         |
| FastAPI (read-only):                              |
| - list markets (filters/pagination)               |
| - market detail + outcomes + settlement spec      |
| - recent snapshots + derived metrics              |
+----------------------+----------------------------+
                       |
                       | HTTP JSON
                       v
+---------------------------------------------------+
|                 (Later) Next.js UI                |
| - tables + filters + memo views                    |
| - charts where helpful (not manual TA)            |
+---------------------------------------------------+

## Data flow
1) Worker polls Polymarket markets endpoint on interval.
2) Worker upserts:
   - markets
   - outcomes
   - settlement_specs (best-effort, version-hashed)
3) Worker appends quote_snapshots for market/outcomes (best-effort from available fields).
4) Worker periodically computes derived_metrics per market.
5) API serves read-only queries for UI or scripts.

## Interfaces and contracts

### Worker → DB
- Upsert markets/outcomes/settlement_specs by `(platform, platform_market_id)` and `(market_id, outcome_name/platform_outcome_id)`.
- Append quote_snapshots; avoid exact duplicates using a uniqueness rule on `(market_id, outcome_id, ts)`.

### API → DB
- Read-only queries; endpoints should be stable and pagination-first.

## Reliability & correctness expectations
- Worker must be restart-safe (idempotent upserts).
- Partial failures should not corrupt state:
  - if snapshot insertion fails, market upserts may still succeed.
- Store `raw_json` to allow reprocessing if schema changes.

## Extension points (future)
- Additional connectors (Kalshi, Manifold) implementing the same canonical mapping.
- Cross-market matching and disagreement computed in analytics jobs.
- Paper execution layer (shadow book), only after data quality + eval mature.
- Segment modules (macro/AI/geopolitics) plugged into analytics as additional signals.
