# Architecture
LoL Lead–Lag Arbitrage Bot (v1)

## Overview
The system is split into:
- **Worker**: ingestion + event-driven analytics (lag/edge) + (later) execution
- **API**: read-only query layer
- **Postgres**: canonical storage for metadata + time series + raw payloads
- (Later) **UI**: Next.js consuming API

## Component diagram (logical)

+----------------------------+        +----------------------------+
| OddsPapi (Pinnacle odds)   |        | Polymarket CLOB + metadata |
| - odds / line moves        |        | - markets endpoint (HTTP)  |
| - fixture metadata         |        | - order books (WS)         |
+-------------+--------------+        +--------------+-------------+
              |                                      |
              | HTTP (polling)                       | WS + HTTP
              v                                      v
+------------------------------------------------------------------+
|                               WORKER                             |
| - adapter: oddspapi (pinnacle reference)                          |
| - adapter: polymarket gamma (market discovery + quote snapshots)  |
| - mapping resolver (external match → polymarket market/outcome)  |
| - lag profiler (reference move → polymarket repricing)           |
| - edge calculator (gap – spread – fees – slippage – delay pen.)  |
| - shadow execution recorder (paper orders; no live placement)     |
| - (later) execution module (marketable limit / IOC-like)         |
+---------------------------+--------------------------------------+
                            |
                            | SQL writes (append-only where relevant)
                            v
+------------------------------------------------------------------+
|                              POSTGRES                            |
| - polymarket: markets/outcomes/settlement_specs                   |
| - polymarket time series: quote_snapshots (now), orderbook_snapshots (later) |
| - reference: external_matches, external_odds_snapshots            |
| - mapping: external_polymarket_mappings                           |
| - derived: disagreement_events (gap/edge metrics)                 |
| - derived: shadow_orders (paper decisions; no live placement)      |
| - raw_json preserved for reprocessing                             |
+---------------------------+--------------------------------------+
                            ^
                            | SQL reads
+---------------------------+--------------------------------------+
|                                API                               |
| FastAPI (read-only):                                             |
| - list markets/matches/mappings                                  |
| - recent order books + odds snapshots                             |
| - disagreement events + lag metrics                               |
+---------------------------+--------------------------------------+
                            |
                            | HTTP JSON
                            v
+------------------------------------------------------------------+
|                      (Later) Next.js UI                          |
| - monitoring dashboards + mapping review                          |
| - charts for lag/edge + fillability metrics                        |
+------------------------------------------------------------------+

## Data flow
1) Worker ingests **reference odds** from OddsPapi (Pinnacle) (polling), writing:
   - external_matches (upsert by source + external_match_id)
   - external_odds_snapshots (append-only)
2) Worker ingests **Polymarket** market metadata + quotes from Gamma (HTTP polling), writing:
   - markets/outcomes/settlement_specs (idempotent upserts)
   - quote_snapshots (append-only)
3) Worker maintains **mapping** between external_matches and Polymarket markets/outcomes.
4) Worker emits **disagreement_events** when reference implied probability materially differs from Polymarket book.
5) Worker computes **lag profiles** and **fillability proxies** (derived analytics; stored as needed).
6) Worker records **shadow execution** decisions (paper orders) for measurement and replay.
7) API serves read-only queries for monitoring and evaluation.

## Interfaces and contracts

### Worker → DB
- Upsert polymarket entities by `(platform, platform_market_id)` and `(market_id, outcome_name/platform_outcome_id)`.
- Upsert external matches by `(source, external_match_id)`.
- Append snapshots (orderbook_snapshots, external_odds_snapshots, quote_snapshots); avoid exact duplicates with uniqueness rules on natural keys including `(…, ts)`.
- Treat mappings as high-integrity records: require provenance (method/confidence/raw_json) and be audit-friendly.

### API → DB
- Read-only queries; endpoints should be stable and pagination-first.

## Reliability & correctness expectations
- Worker must be restart-safe (idempotent upserts).
- Partial failures should not corrupt state:
  - if snapshot insertion fails, market upserts may still succeed.
- Store `raw_json` to allow reprocessing if schema changes.
- Mapping correctness is a first-class risk:
  - prefer conservative behavior over “best guess” auto-matches
  - automated decisions must only use mappings above a configured confidence threshold

## Extension points (future)
- Additional leagues/market types (beyond LCK/LPL; match winner + map winner).
- Additional reference sources (secondary books) to cross-check Pinnacle and improve robustness.
- Execution module inside worker (marketable limit / IOC-like), gated by measurement-first criteria:
  - empirical lag distributions
  - fillability metrics and conservative cost model
  - stable mapping accuracy
