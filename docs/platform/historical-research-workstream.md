# Historical Research Workstream

## Purpose

This document defines the historical-trades foundation that should sit between repo stabilization and broader platform expansion.

It exists to answer one question:

How do we make the platform more sophisticated without depending on fragile live external feeds?

The answer is to treat large historical prediction-market trade data as a core research substrate for:
- replay
- calibration
- execution analytics
- empirical sizing
- historical analog lookup

This is a foundational capability, not a standalone strategy.

## Position in the platform

This workstream should be treated as `Phase 0.5` in the mutable roadmap:
- after enough Phase 0 cleanup to make the repo runnable and repeatable
- before deeper shared-domain and live-strategy expansion

It does not materially change the target architecture.

It mostly changes sequencing:
- replay and research move earlier
- strategy expansion becomes more evidence-driven
- execution policy becomes empirical instead of anecdotal

## Why this is worth doing

The repo's original sports lead-lag path was limited by unreliable external event-state data.

Historical prediction-market trade data offers a different path:
- market-native instead of feed-native
- execution-focused instead of prediction-only
- useful for both private trading and analysis APIs

This aligns with three current platform needs:
- a replay surface
- a signal calibration surface
- a way to reason about maker/taker behavior and time-varying bias

## Workstream goals

- create a reproducible historical research environment
- keep raw external data separate from the app runtime and app database
- generate derived empirical outputs the platform can consume later
- produce first-class calibration and execution priors
- support future replay and experiment tracking

## Non-goals

Do not make this workstream attempt all of the following immediately:
- full order-book reconstruction
- direct live-trading integration
- generic machine learning over raw trade histories
- a new monolithic warehouse inside the app Postgres database
- blended Polymarket and Kalshi modeling without venue separation

## Core principles

### Raw data is external and read-only

The raw historical dataset should live outside git and outside the app database.

Use an environment variable or local config path for dataset location.

### Venue separation first

Do not treat Polymarket and Kalshi as one homogeneous market.

Keep venue-specific profiling, calibration, and expectancy outputs separate unless there is a strong reason to combine them.

### Derived outputs over raw ingestion

The platform does not need to own all raw trades in Postgres.

The platform needs a stable way to consume derived artifacts such as:
- calibration surfaces
- maker/taker expectancy tables
- historical analog summaries
- strategy priors

### Conditioning beats global averages

Do not build global “Kelly” or “maker good / taker bad” rules.

Condition by:
- venue
- market family
- price bucket
- time to resolution
- liquidity bucket
- topic class
- execution posture

### Research before execution

This workstream should first produce explainable research outputs.

Those outputs can later shape:
- signal ranking
- sizing haircuts
- passive versus aggressive execution policy
- API endpoints

## Proposed data architecture

### Raw layer

Recommended storage:
- parquet files from the external dataset
- dataset path provided by environment variable
- raw files never committed into git

Recommended engine:
- DuckDB for local analytical queries over parquet

### Normalized research layer

Create normalized views or materialized tables for:
- `historical_markets`
- `historical_trades`
- `historical_resolutions`
- `historical_trade_features`
- `historical_bucket_stats`

These are research-facing, not app-runtime-facing.

### Derived platform layer

Persist only durable outputs that the platform can use later, such as:
- calibration surfaces
- maker/taker expectancy tables
- empirical sizing priors
- market-family historical analog summaries
- experiment metadata

These can later be exposed to:
- signal ranking
- risk policy
- replay jobs
- analysis API endpoints

## Workstream phases

## H0 - Dataset landing and audit

### Goal

Create a reproducible, local, read-only research entrypoint for the external dataset.

### Deliverables

- documented dataset path convention
- lightweight dataset manifest
- schema profiler
- venue split summary
- data quality notes:
  - missingness
  - timestamp sanity
  - resolution coverage
  - market metadata quality

### Success criteria

- one documented command profiles the dataset
- one documented command produces a basic venue and schema report
- the repo can answer whether the dataset is usable without hand inspection

## H1 - Normalized research layer

### Goal

Turn raw trades into stable analytical views that later platform code can rely on.

### Deliverables

- normalized research schema
- feature derivations for:
  - time to resolution
  - price bucket
  - topic class if available
  - maker/taker role
  - simple trade-size buckets
- venue-specific derived views

### Success criteria

- calibration and expectancy studies can run from named views instead of ad hoc one-off queries
- Polymarket can be analyzed without depending on Kalshi behavior

## H2 - Baseline empirical studies

### Goal

Produce the first durable studies that actually inform platform decisions.

### Initial study set

- price x time-to-resolution calibration surface
- maker vs taker expectancy by bucket
- longshot/favorite bias by venue and topic
- simple execution-policy priors:
  - when passive posting historically dominates taking
  - where taker urgency is most expensive
- empirical edge-dispersion summaries for sizing haircuts

### Success criteria

- each study produces saved outputs, not just notebook screenshots
- results are reproducible from one documented command
- outputs are interpretable enough to drive execution and risk decisions

## H3 - Platform hooks

### Goal

Make the research outputs usable by the rest of the system without pulling the raw dataset into the live runtime.

### Deliverables

- interfaces for historical analog lookup
- strategy priors or reference tables
- execution-policy reference tables
- replay-compatible data contracts
- analysis-API-ready outputs

### Success criteria

- later strategy work can consume historical priors directly
- replay and ranking work can start from saved outputs instead of re-querying raw parquet every time

## H4 - Extension lanes

This is where the workstream can expand after the foundation is stable.

Possible extensions:
- topic-specific calibration
- event-family-specific analog services
- hybrid trade-history plus market-graph analysis
- venue-drift monitoring over time
- richer fill or slippage proxies

## First platform questions this workstream should answer

- At what price and time-to-resolution buckets are contracts systematically miscalibrated?
- Where is maker behavior structurally better than taker behavior?
- Which market families exhibit the strongest repeatable bias patterns?
- Where should sizing be haircutted because empirical edge dispersion is too wide?
- Which research outputs are strong enough to expose through an analysis API?

## Recommended implementation boundaries

### Keep this outside the live runtime at first

Do not start by wiring this into `live.py`, `trader.py`, or execution submission paths.

Keep the first slice read-only and research-oriented.

### Prefer a dedicated research area

Probable code homes:
- `services/research/` for reusable research logic
- `services/tools/` for temporary or narrow profiling entrypoints

The exact placement can be decided after the current Phase 0 changes settle.

### Keep app DB usage narrow

Do not ingest the full external dataset into Postgres.

If persistence is needed, persist only derived and versioned outputs.

## Open decisions

### D-H1 - Dataset scope

- start with Polymarket only
- or support Polymarket and Kalshi from the first profiler pass

Current bias:
- profile both
- build platform-facing priors from Polymarket first

### D-H2 - Research storage contract

- DuckDB-only derived outputs
- or DuckDB for raw research plus Postgres for selected derived tables

Current bias:
- DuckDB for raw and heavy analytical work
- Postgres only for later platform-consumed summaries

### D-H3 - Start timing

- finish all of Phase 0 first
- or start H0/H1 as soon as the repo-stability work is good enough

Current bias:
- allow H0 planning and isolated H0/H1 work as soon as the current Phase 0 package and command changes are stable enough to run repeatably

## Recommended next move

The first practical implementation slice should be:

1. document dataset-path conventions
2. add a schema and venue profiler
3. define a normalized research schema
4. materialize the first calibration and maker/taker studies

That is enough to prove whether this workstream deserves deeper integration before the rest of the platform is rearranged around it.
