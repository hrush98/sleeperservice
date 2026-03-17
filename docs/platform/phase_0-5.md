# Phase 0.5 Implementation Guide

## Purpose

This is the direct execution companion for `docs/platform/implementation-roadmap.md` while `Phase 0.5` is active.

Use it to turn the broader Phase 0.5 planning documents into concrete build slices.

Starting here, each active roadmap phase should get its own `phase_*.md` guide in `docs/platform/`.

## Phase objective

- create a reproducible historical research surface over external prediction-market trade data
- produce derived calibration and execution priors before deeper runtime expansion
- keep raw datasets outside git, outside the app database, and outside live runtime paths

## Working assumptions

- `Phase 0` is complete enough that isolated `P0.5.1` and `P0.5.2` work can begin now
- profile both Polymarket and Kalshi during landing, but treat Polymarket as the first platform-facing venue
- use DuckDB over parquet for raw and heavy analytical work
- use Postgres only later for selected derived outputs, not raw-trade ingestion
- keep generated reports and temporary artifacts under an ignored local path such as `logs/historical_research/`

## Phase output contract

By the end of `Phase 0.5`, the repo should have:

- a documented dataset path and research output-path contract
- a reproducible dataset manifest and schema-quality profiler
- normalized research views for markets, trades, resolutions, and trade features
- saved baseline empirical studies with machine-readable outputs
- versioned contracts for later replay, ranking, risk, and analysis API consumers

## Execution order

1. `P0.5.1 Dataset landing and audit`
2. `P0.5.2 Normalized research layer`
3. `P0.5.3 Baseline empirical studies`
4. `P0.5.4 Platform hooks`

## Immediate next slice

`P0.5.1` and `P0.5.2` now both have real Becker-dataset passes behind them.
The next implementation slice should move into `P0.5.3`:

1. add the historical study runner and output contracts
2. produce the first saved calibration and expectancy studies from the normalized DuckDB layer
3. decide which outputs are durable enough to promote into later replay, ranking, and risk hooks

## P0.5.1 Dataset landing and audit

### Goal

Create a reproducible local entrypoint that answers whether the external dataset is usable without hand inspection.

### Deliverables

- documented dataset-path convention
- canonical environment variables:
  - `HISTORICAL_DATASET_ROOT`
  - `HISTORICAL_RESEARCH_OUTPUT_ROOT`
- lightweight dataset manifest under `logs/historical_research/manifests/`
- schema and venue profiler command
- data-quality report covering missingness, timestamp sanity, resolution coverage, duplicate risk, and metadata quality

### Implementation plan

1. Add the config surface.
   - Document the expected external parquet root.
   - Reserve `HISTORICAL_RESEARCH_OUTPUT_ROOT` for generated manifests and reports.
   - Keep dataset paths out of tracked defaults and container config.
2. Add the initial research package skeleton.
   - Create `services/research/__init__.py`.
   - Add reusable modules for dataset discovery and profiling.
   - Keep the user-facing entrypoint thin in `services/tools/profile_historical_dataset.py`.
3. Build the profiler.
   - Enumerate available files or tables.
   - Capture schema, row counts, column null rates, min and max timestamps, venue counts, duplicate keys, and resolution coverage.
   - Emit both a machine-readable manifest and a human-readable summary.
4. Document the command path.
   - Add the canonical profiling command to the relevant docs once the tool exists.
   - If the first dataset run exposes blockers, update the roadmap before continuing downstream work.

### Verification target

- `conda run -n sleeperservice python -m services.tools.profile_historical_dataset --help`
- with `HISTORICAL_DATASET_ROOT` set, the profiler exits cleanly and writes a manifest plus summary under `logs/historical_research/`
- `git diff --check` remains clean after the docs and config updates

### Exit criteria

- dataset location and output location are documented
- one command produces a venue, schema, and quality report
- the repo can answer whether the dataset is usable without manual parquet inspection

## P0.5.2 Normalized research layer

### Goal

Turn raw trades into stable analytical views that later platform code can depend on.

### Deliverables

- a local DuckDB-backed normalized research schema
- named views or materialized tables for:
  - `historical_markets`
  - `historical_trades`
  - `historical_resolutions`
  - `historical_trade_features`
  - `historical_bucket_stats`
- feature derivations for time to resolution, price bucket, size bucket, maker or taker role, and topic class where available
- venue-specific study inputs, starting with Polymarket

### Implementation plan

1. Add reusable research modules.
   - Introduce modules for normalization, feature derivation, and local DuckDB materialization.
   - Keep venue-specific mapping code explicit instead of hiding it in one-off queries.
2. Build the normalized base layer.
   - Preserve source identifiers and timestamps.
   - Add an explicit `venue` field to every normalized table.
   - Normalize price, size, and resolution fields into stable analytical types.
3. Create downstream study inputs.
   - Publish shared base views with `venue` preserved.
   - Publish venue-specific views or materializations for Polymarket-first studies.
4. Add the materialization entrypoint.
   - Add `services/tools/materialize_historical_research.py` as the canonical local builder.
   - Make it safe to rerun without touching app Postgres or live runtime code.
5. Add narrow tests where behavior is derived.
   - Cover time-to-resolution, price-bucket, and maker-or-taker feature logic with small fixtures.

### Verification target

- `conda run -n sleeperservice python -m services.tools.materialize_historical_research --help`
- the normalized views materialize from the local dataset without manual SQL editing
- on Becker-scale datasets, `--skip-view-row-counts` is available so the local build can finish without waiting on full final-view counts
- feature-derivation tests pass in the `sleeperservice` environment

### Exit criteria

- baseline studies can run from named views instead of ad hoc queries
- Polymarket analysis does not depend on blended Kalshi assumptions
- rerunning the build reproduces the same normalized schema from the same dataset snapshot

## P0.5.3 Baseline empirical studies

### Goal

Produce the first durable studies that can shape platform decisions instead of notebook-only conclusions.

### Deliverables

- price by time-to-resolution calibration surfaces
- maker vs taker expectancy tables
- longshot or favorite bias summaries by venue and topic where possible
- empirical edge-dispersion outputs to support later sizing haircuts
- study metadata describing dataset version, code version, parameters, and artifact paths

### Implementation plan

1. Add a dedicated studies package.
   - Split study logic into separate modules for calibration, execution, bias, and sizing priors.
2. Standardize study outputs.
   - Write machine-readable outputs such as parquet or JSON.
   - Write a short human summary for fast inspection.
   - Include metadata needed to reproduce the run later.
3. Add the study runner.
   - Add `services/tools/run_historical_studies.py`.
   - Support running one study or the initial baseline bundle.
4. Save outputs under a stable local layout.
   - Use a predictable output structure under `logs/historical_research/studies/`.
   - Keep venue separation explicit in file names and metadata.
5. Only promote interpretable outputs.
   - If a study cannot be explained clearly, keep it experimental and do not treat it as a platform prior yet.

### Verification target

- one documented command runs the baseline study bundle
- each study writes machine-readable outputs plus a short summary
- rerunning on the same dataset slice reproduces the same artifacts and metadata

### Exit criteria

- saved study outputs exist for calibration, maker or taker expectancy, and baseline sizing priors
- outputs are reproducible from one documented command
- results are interpretable enough to inform execution and risk policy later

## P0.5.4 Platform hooks

### Goal

Make the research outputs usable by later phases without coupling the live runtime to raw datasets.

### Deliverables

- versioned contracts for calibration surfaces, execution priors, historical analog lookup inputs, and replay-facing research outputs
- loader or export helpers that read derived artifacts rather than raw parquet
- a documented consumer map for replay, ranking, risk, and analysis API work

### Implementation plan

1. Define the output contracts.
   - Add schema or contract definitions for each durable artifact class.
   - Version the contracts so later phases can evolve them without silent breakage.
2. Add artifact loaders.
   - Keep loaders in research-oriented modules.
   - Make them operate on saved derived outputs, not raw datasets.
3. Document downstream consumers.
   - For each later consumer, list the artifact it should read and the minimum fields it needs.
   - Keep this as a handoff point into `Phase 1`, `Phase 3`, and `Phase 4`.
4. Preserve the boundary.
   - Do not wire these contracts into `live.py`, `trader.py`, or direct execution flows during `Phase 0.5`.

### Verification target

- contract validation passes against saved study outputs
- downstream docs can point to one artifact contract per use case
- no live runtime module imports raw dataset tooling

### Exit criteria

- later phases can consume saved historical priors without access to the raw dataset
- the research layer exposes stable contracts instead of one-off study outputs
- replay, ranking, risk, and API work have a clear Phase 0.5 handoff surface

## Do not do this in Phase 0.5

- do not ingest the raw external trade dataset into app Postgres
- do not wire raw-dataset access into live trading or TUI paths
- do not start with full order-book reconstruction
- do not blend venue priors without explicit venue tags
- do not accept notebook screenshots as the only saved study output

## Phase completion standard

`Phase 0.5` is complete when:

- `P0.5.1` through `P0.5.4` all have working commands and documented outputs
- the repo can reproduce the baseline research artifacts from a documented local setup
- later platform phases can consume saved historical priors without querying raw parquet directly
