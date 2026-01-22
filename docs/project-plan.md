# LoL Lead–Lag Arbitrage Bot
Master project plan (v1)

## Vision
Build a system that detects and exploits short-lived **lead–lag** inefficiencies between a fast external reference line and slower-to-reprice **Polymarket LoL** markets—starting as an intelligence + measurement engine, with a gated path to automated execution.

The system’s advantage comes from:
- **reference speed**: Pinnacle odds via OddsPapi (polling; see OddsPapi docs)
- **target lag**: Polymarket CLOB order books that can remain stale for a brief window
- **measurement discipline**: empirical lag distributions + fillability metrics before any live execution

## User-facing concept
- A **disagreement monitor** for live LoL matches: “Pinnacle moved; Polymarket hasn’t (yet).”
- Analytics that explain *why* an edge is (or isn’t) actionable: lag, spread, fees, depth, slippage, delay constraints.
- Charts are allowed; the goal is measurement + execution-readiness, not manual TA.

## Initial scope
- Ingest **Polymarket LoL** market metadata + CLOB order book snapshots (or best-bid/ask + depth summaries).
- Ingest **Pinnacle odds** (via OddsPapi) as the external reference; store raw payloads.
- Maintain append-only snapshots for all time-series signals.
- Build derived “disagreement/edge” events and a **lag profiler**.
- Add **paper/shadow execution** records (no orders sent) to validate that lag opportunities exist in live conditions.
- Provide a read-only API for querying markets, mappings, snapshots, and disagreement events.

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

## Canonical data model (v1)
This is the baseline schema for lead–lag measurement and (later) execution. Evolve additively when possible.

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

### orderbook_snapshots (append-only; polymarket CLOB)
Stores either full depth or a depth summary; exact schema can evolve, but must remain append-only.
- `id` (uuid pk)
- `market_id` (fk)
- `outcome_id` (fk)
- `ts` (timestamptz)
- `best_bid` (double precision nullable)
- `best_ask` (double precision nullable)
- `mid` (double precision nullable)
- `bid_depth` (double precision nullable)  # e.g., within X cents; definition in raw_json
- `ask_depth` (double precision nullable)
- `raw_json` (jsonb)  # store full book when possible
Indexes:
- `(market_id, outcome_id, ts desc)`
- unique constraint candidate: `(market_id, outcome_id, ts)`

### external_matches (OddsPapi fixture / Pinnacle reference entity)
- `id` (uuid pk)
- `source` (text; e.g. "oddspapi_pinnacle")
- `external_match_id` (text; unique with source)
- `league` (text nullable)   # e.g., LCK/LPL
- `start_time` (timestamptz nullable)
- `team_a` (text)
- `team_b` (text)
- `raw_json` (jsonb)
- `created_at`

### external_odds_snapshots (append-only; reference time series)
- `id` (uuid pk)
- `external_match_id` (fk → external_matches.id)
- `ts` (timestamptz)
- `market_type` (text)       # match_winner, map_winner, etc.
- `selection` (text)         # team_a/team_b or normalized name
- `odds_decimal` (double precision nullable)
- `odds_american` (integer nullable)
- `implied_prob` (double precision nullable)     # post de-vig if applicable; definition in raw_json
- `raw_json` (jsonb)
Indexes:
- `(external_match_id, ts desc)`
- unique constraint candidate: `(external_match_id, market_type, selection, ts)`

### external_polymarket_mappings (critical correctness surface)
Mapping between reference events and Polymarket markets/outcomes.
- `id` (uuid pk)
- `source` (text; e.g. "oddspapi_pinnacle")
- `external_match_id` (fk → external_matches.id)
- `polymarket_market_id` (fk → markets.id)
- `polymarket_outcome_id` (fk → outcomes.id nullable)  # nullable for market-level mapping
- `mapping_confidence` (double precision nullable)
- `mapping_method` (text)  # manual, heuristic, hybrid
- `raw_json` (jsonb nullable)
- `created_at`, `updated_at`
Constraints:
- unique constraint candidate: `(source, external_match_id, polymarket_market_id, polymarket_outcome_id)`

### shadow_orders (paper/shadow execution; append-only)
Records “what we would have sent” without actually placing orders on Polymarket.
- `id` (uuid pk)
- `ts` (timestamptz)  # decision timestamp
- `external_match_id` (fk → external_matches.id)
- `polymarket_market_id` (fk → markets.id)
- `polymarket_outcome_id` (fk → outcomes.id)
- `side` (text)       # buy/sell
- `price` (double precision nullable)  # chosen limit price (if applicable)
- `size` (double precision nullable)   # chosen size (if applicable)
- `reason` (text)     # e.g. "lag_gap_threshold", "reprice_detected"
- `raw_json` (jsonb)  # full decision context: observed snapshots, thresholds, computed lag/edge, etc.
Indexes:
- `(polymarket_market_id, ts desc)`
- `(external_match_id, ts desc)`

### disagreement_events (derived; append-only)
Derived events where the reference implies a materially different probability than the Polymarket book.
- `id` (uuid pk)
- `ts` (timestamptz)
- `external_match_id` (fk → external_matches.id)
- `polymarket_market_id` (fk → markets.id)
- `polymarket_outcome_id` (fk → outcomes.id)
- `ref_implied_prob` (double precision)
- `poly_mid` (double precision nullable)
- `poly_best_bid` (double precision nullable)
- `poly_best_ask` (double precision nullable)
- `gap` (double precision)         # ref_implied_prob - poly_mid (or chosen comparator)
- `edge` (double precision)        # gap adjusted for fees/spread/slippage/delay penalty; definition in raw_json
- `raw_json` (jsonb)
Indexes:
- `(polymarket_market_id, ts desc)`
- `(external_match_id, ts desc)`

## Milestones (high level)
### M0 — Project skeleton **(complete)**
- Compose setup, shared settings, DB + migrations, service scaffolds.

### M1 — Polymarket LoL ingestion + read-only API **(complete)**
- Discover and track relevant LoL markets (LCK/LPL; match winner + map winner).
- Ingest market metadata + quote snapshots (polling Gamma `/markets`; order book snapshots are a later upgrade).
- Upsert markets/outcomes/settlement_specs; append quote_snapshots (append-only).
- Serve via API (market list/detail + recent book/snapshots).

### M2 — Reference ingestion (OddsPapi → Pinnacle) **(complete)**
- Add OddsPapi adapter and ingest external_matches + external_odds_snapshots (append-only).
- Store raw payloads and normalize odds/probabilities (including de-vig conventions when applicable).
- Goal: live reference feed for a real match with stable timestamps and dedupe.

### M3 — Mapping + disagreement monitor + lag profiling + shadow execution (measurement first) **(complete)**
- Build mapping between OddsPapi fixtures (Pinnacle) and Polymarket markets/outcomes.
- Run Polymarket + reference ingestion concurrently; compute disagreement metrics continuously.
- Emit disagreement_events with gap/edge metrics.
- Detect “reference move” events and measure time-to-reprice on Polymarket (lag distributions).
- Record shadow_orders (paper decisions) when lag/edge thresholds are met (no orders sent).
- Measure “fillability” proxies from book dynamics (spread, depth, churn, ghost liquidity signals).

### M4 — Evaluation + reliability (arbitrage-specific)
- Scheduled evaluation reports: ingestion health, mapping accuracy, lag stability, edge hit-rate under conservative assumptions.
- Backtest/simulate with explicit costs and delay constraints.

### M5 — Optional execution simulation layer (gated)
- Shadow decisions / paper execution records (no capital at risk).
- Execution constraints modeled explicitly (Polymarket sports delay, IOC/marketable limits).

### M6+ — Optional live execution (only after measurement gate)
- Conservative automation with strict limits, circuit breakers, and auditability.
- Expand leagues/market types only after mapping + lag/edge stability.

## Measurement-first gate (non-negotiable)
Live execution is gated on empirical evidence:
- **Lag distributions**: quantiles of repricing lag by league/market type/state; stable across samples.
- **Fillability metrics**: spread/depth/churn/queue position proxies; conservative slippage model validated vs observed prints/fills (when available).
- **Mapping correctness**: quantified error rate and audit trail; no “best guess” mappings in automated mode.
- **Cost model**: fees + spread + expected slippage + delay penalty; edge must remain positive under worst-case bands.

## Operational guidelines
- Prefer “one vertical slice working” over multiple unfinished modules.
- When a milestone is completed, mark it as **(complete)** next to the milestone heading in this document.
- Every milestone ends with:
  - verification commands
  - data sanity checks
  - a small ADR if a meaningful decision was made

## Open decisions (to revisit)
- OddsPapi integration details: polling cadence vs “spike polling” after a move; rate-limit strategy; fixture/market normalization.
- Canonical representation for order book depth (full depth vs summary) and storage cost controls.
- Mapping workflow: manual curation tool vs human-in-the-loop heuristics.
- When/if to add a UI (Next.js) and what endpoints it needs.
