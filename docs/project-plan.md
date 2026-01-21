# Prediction Market Intelligence Engine
Master project plan (v0)

## Vision
Build a system that helps discover, understand, and analyze prediction markets—starting with **Polymarket**—and evolve into a robust intelligence + analytics engine, with an optional path to automated execution.

The system’s advantage comes from:
- **structured market understanding** (rules, settlement, timing)
- **high-quality data capture** (time series + metadata + raw payload preservation)
- **repeatable evaluation** (so iteration is grounded)
- later: **cross-market reasoning** and **segment-specific forecasting**

## User-facing concept
- A “radar” for markets: discover new markets, spot meaningful changes, compare related markets.
- Analytics that explain *why* something is interesting (liquidity, rule clarity, movement, disagreement).
- Charts are allowed; the goal is to avoid manual TA as the primary edge.

## Initial scope
- Ingest Polymarket public market data into a canonical store.
- Maintain append-only snapshots for price/probability-like fields and activity proxies.
- Provide a read-only API for querying markets and recent history.
- Add explainable ranking signals (quality, movement, novelty), then improve with evaluation.

## Non-functional requirements
- Local-first development via Docker Compose.
- Idempotent ingestion, robust to restarts and partial failures.
- Data model supports history (no silent overwrites of time series).
- “Raw payload” preservation to avoid losing platform-specific nuance.
- Documentation suitable for interviews (decisions, tradeoffs, evolution).

## Tech stack (default)
- Python 3.11+
- FastAPI for API
- Worker service for ingestion + periodic jobs
- Postgres for storage
- SQLAlchemy 2.x + Alembic migrations
- Pydantic settings (env-driven)
- Docker Compose
- Next.js UI later (optional early; backend-first priority)

## Repository layout (target)
- `services/`
  - `api/`        — FastAPI app (read-only)
  - `worker/`     — poller + schedulers + analytics jobs
  - `shared/`     — DB models, settings, utilities, canonical types
- `infra/`        — docker-compose, local scripts
- `docs/`
  - `adr/`
  - `evals/`
  - `build-log/`
- `migrations/`   — Alembic (or under services/shared)

## Canonical data model (v0)
This is the baseline schema; evolve additively when possible.

### markets
- `id` (uuid pk)
- `platform` (text; "polymarket")
- `platform_market_id` (text; unique with platform)
- `title` (text)
- `description` (text nullable)
- `url` (text nullable)
- `status` (text; best-effort)
- `open_time` (timestamptz nullable)
- `close_time` (timestamptz nullable)
- `raw_json` (jsonb)
- `created_at`, `updated_at`

### outcomes
- `id` (uuid pk)
- `market_id` (fk)
- `outcome_name` (text)
- `platform_outcome_id` (text nullable)
- `created_at`

### settlement_specs
Captures settlement semantics and protects against silent rule drift.
- `id` (uuid pk)
- `market_id` (fk)
- `source` (text nullable)
- `resolution_time` (timestamptz nullable)
- `criteria_text` (text nullable)
- `spec_version_hash` (text)  # derived from normalized fields + raw rule text
- `raw_json` (jsonb nullable)
- `created_at`

### quote_snapshots (append-only)
- `id` (uuid pk)
- `market_id` (fk)
- `outcome_id` (fk nullable)
- `ts` (timestamptz)
- `price` (double precision)
- `volume_24h` (double precision nullable)
- `liquidity` (double precision nullable)
- `raw_json` (jsonb)
Indexes:
- `(market_id, ts desc)`
- `(outcome_id, ts desc)`
- unique constraint candidate: `(market_id, outcome_id, ts)`

### derived_metrics (can be latest-per-market or append-only)
- `market_id` (pk/fk)
- `ts` (timestamptz)
- `quality_score` (double precision nullable)
- `move_24h` (double precision nullable)
- `interesting_score` (double precision nullable)
- `components` (jsonb nullable)

## Milestones (high level)
### M0 — Project skeleton
- Compose setup, shared settings, DB + migrations, service scaffolds.

### M1 — Polymarket ingestion + read-only API
- Poll markets endpoint.
- Upsert markets/outcomes/settlement spec (best-effort).
- Append quote snapshots.
- Serve via API (market list/detail + recent quotes).

### M2 — Basic analytics + ranking signals
- Compute explainable: quality/movement/interesting scores.
- Materialize derived_metrics for fast querying.

### M3 — Evaluation + reliability
- Scheduled evaluation reports: ranking quality, data completeness, ingestion health.
- Logging + error handling improvements.

### M4 — Enrichment and better market understanding
- Better settlement parsing, rule clarity heuristics.
- Optional: news/context linking.

### M5 — Cross-market matching and disagreement
- Match related markets within Polymarket and/or across platforms.
- Disagreement feed.

### M6 — Optional execution simulation layer
- Paper decisions / shadow book.
- Structured decision records (for later learning), without turning into a trade diary.

### M7+ — Segment specialization
- Pick a segment (macro/AI/geopolitics) and build exogenous forecasting or specialized intelligence, integrated into the engine.

## Operational guidelines
- Prefer “one vertical slice working” over multiple unfinished modules.
- Every milestone ends with:
  - verification commands
  - data sanity checks
  - a small ADR if a meaningful decision was made

## Open decisions (to revisit)
- When/if to add a UI (Next.js) and what endpoints it needs.
- Suggested cadence: snapshot interval, backfill policies.
- Whether to add a second platform after M3 vs deeper Polymarket data.
