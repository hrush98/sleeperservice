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

`P0.5.3` has now started with:
- a dedicated study package
- the first baseline runner and artifact contract
- saved calibration, maker/taker expectancy, longshot/favorite bias, and sizing-prior outputs

The next implementation slice inside `P0.5.3` should now:

1. review the first Becker-scale results and decide which outputs are durable enough to promote into later replay, ranking, and risk hooks
2. tighten promotion thresholds, warnings, and artifact fields where real-data instability shows up
3. start shaping the `P0.5.4` contract and loader surface around the outputs that survive review

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

### Why this slice matters

This is the point where the historical-research substrate stops being “data infrastructure” and starts becoming reusable platform knowledge.

`P0.5.1` and `P0.5.2` gave the repo:
- a real dataset landing path
- a profiler and quality surface
- normalized DuckDB views over Becker data

`P0.5.3` must convert that substrate into empirical priors that are strong enough to influence later:
- replay policy
- execution posture
- risk haircuts
- signal ranking
- analysis API outputs

The quality bar should be materially higher than:
- notebook screenshots
- one-off SQL queries
- social-post “edges”
- strategy claims with no denominator, no conditioning, and no reproducible artifact trail

### Research posture

Treat this phase as empirical market microstructure and calibration research, not as a loose backtesting sprint.

The intent is:
- descriptive first
- conditional rather than global
- venue-aware rather than blended
- reproducible rather than anecdotal
- interpretable enough that a later runtime can consume the result as a contract

The intent is not:
- to prove live alpha from one historical study bundle
- to fit a fragile predictive model and call it a strategy
- to collapse Polymarket and Kalshi into one behavioral surface
- to promote outputs that cannot survive basic robustness checks

### Research quality bar

The study package for this phase should inherit two standards:

1. Quant rigor
   - every study should disclose sample size, conditioning dimensions, exclusions, and uncertainty
   - predictive or policy-facing claims should be checked on time-sliced holdouts where appropriate
   - outputs should prefer stable effect estimates over clever but opaque modeling
   - robustness checks should look for regime sensitivity, sparse-bucket failure, and sign flips
2. Fintech-grade reliability
   - every run should produce an audit trail with dataset identity, code identity, parameters, and artifact paths
   - reruns should be deterministic for the same dataset snapshot and parameter set
   - artifacts should be versioned, machine-readable, and safe to consume without manual notebook reconstruction
   - promotion into later platform hooks should require explicit contracts, not informal analyst judgment alone

### Study design principles

- Condition before aggregating.
  - Default slices should include `venue`, `price_bucket`, and `time_to_resolution_bucket`.
  - Add `topic_class`, `size_bucket`, `contract_side`, and related conditioning only where coverage remains credible.
- Separate descriptive studies from policy studies.
  - Descriptive studies explain what historically happened.
  - Policy studies translate descriptive results into later execution or sizing guidance.
- Prefer simple surfaces over black-box models in the first bundle.
  - The first durable outputs should be tables and interpretable summaries, not model-heavy abstractions.
- Preserve venue mechanics.
  - Polymarket and Kalshi should remain separate until there is explicit evidence that a shared prior is justified.
- Carry uncertainty forward.
  - Sparse buckets, unstable periods, and wide empirical dispersion should survive into the output instead of being silently averaged away.

### Baseline study bundle

The first `P0.5.3` bundle should answer a narrow but durable set of questions.

#### 1. Calibration surfaces

Primary question:
- how does quoted price relate to realized resolution probability as a function of time to resolution?

Expected outputs:
- bucketed calibration tables by `venue`, `price_bucket`, and `time_to_resolution_bucket`
- miscalibration summaries showing realized minus implied probability
- coverage and sample-size surfaces so downstream users can distinguish “no effect” from “not enough data”
- short summaries calling out clear favorite or longshot distortions and near-expiry behavior

#### 2. Maker vs taker expectancy

Primary question:
- where did passive posting historically outperform taking, and where did urgency historically cost the most?

Expected outputs:
- expectancy tables by `venue`, `maker_taker`, `price_bucket`, and `time_to_resolution_bucket`
- optional conditioning by `topic_class` or `size_bucket` where sample depth supports it
- execution-policy summaries that identify where passive behavior structurally dominates and where taking appears least harmful

Important caveat:
- maker or taker inference is only as strong as the normalized source fields and venue-specific mapping, so every artifact must state the inference basis and data coverage clearly

#### 3. Longshot or favorite bias summaries

Primary question:
- which venues, topics, or market families exhibit repeatable probability distortion at the tails?

Expected outputs:
- venue-specific bias summaries
- topic-class or market-family bias tables where metadata quality is adequate
- stability checks across coarse time periods so one regime does not masquerade as a durable prior

#### 4. Edge-dispersion and sizing priors

Primary question:
- where is realized edge so noisy that later sizing logic should haircut model confidence even when mean edge looks positive?

Expected outputs:
- empirical dispersion summaries by core buckets
- simple sizing-haircut reference tables derived from observed dispersion rather than abstract Kelly-only logic
- clear flags for sparse or unstable buckets that should remain experimental

### Output contract for every study

Every saved study should produce the same minimum artifact set:

- `metadata.json`
  - study name and version
  - dataset manifest or dataset identifier
  - code commit or working-tree identifier
  - run timestamp
  - parameters and bucket definitions
  - source views consumed
  - row counts, exclusions, and warnings
- one or more machine-readable data artifacts such as parquet or JSON
  - tables should be normalized enough for later loaders and replay jobs to consume directly
- `summary.md`
  - plain-language interpretation
  - key findings
  - caveats and promotion recommendation

Outputs should live under a stable local structure rooted at `logs/historical_research/studies/` with venue separation explicit in either the directory layout, file naming, or both.

### Promotion criteria

An empirical result is only durable enough for `P0.5.4` if it satisfies all of the following:

- the effect is interpretable in plain language
- the bucket definitions and conditioning dimensions are explicit
- sample sizes are large enough to make the summary credible
- simple robustness checks do not reverse the sign or basic conclusion
- the artifact can be expressed as a stable contract for later consumers

Keep a study experimental if:
- the effect depends on one narrow time window
- the result disappears under minimal holdout or stability checks
- the underlying fields are too inferred or too sparse
- the summary cannot explain why a later runtime should trust the output

### Deliverables

- price by time-to-resolution calibration surfaces
- maker vs taker expectancy tables
- longshot or favorite bias summaries by venue and topic where possible
- empirical edge-dispersion outputs to support later sizing haircuts
- study metadata describing dataset version, code version, parameters, and artifact paths

### Implementation plan

1. Add a dedicated studies package and shared contracts.
   - Introduce `services/research/studies/` as the home for durable empirical work.
   - Split logic into explicit modules such as `calibration`, `execution`, `bias`, `sizing`, `contracts`, and `runner`.
   - Centralize study metadata, dataset fingerprinting, and artifact-writing helpers instead of duplicating them per study.
2. Define the baseline study schema before writing analysis code.
   - Standardize required metadata fields, artifact naming, and run layout.
   - Standardize how bucket definitions, exclusions, warnings, and sample counts are recorded.
   - Make the output schema stable enough that later phases can load it without scraping Markdown.
3. Build shared analytical primitives.
   - Add reusable helpers for bucket coverage, simple uncertainty summaries, stability checks, and venue-preserving aggregations.
   - Keep the first implementation table-driven and auditable; do not hide the core logic behind opaque modeling.
4. Implement studies in a strict order.
   - Start with calibration surfaces because they are the cleanest descriptive output and a natural quality check on the normalized layer.
   - Add maker-vs-taker expectancy next because it most directly informs later execution policy.
   - Add longshot-or-favorite bias summaries after the calibration path is stable.
   - Add edge-dispersion and simple sizing-prior outputs last because they depend on the credibility of the earlier surfaces.
5. Add the study runner.
   - Add `services/tools/run_historical_studies.py`.
   - Support running a single study, one venue, or the initial baseline bundle.
   - Make reruns safe and deterministic for the same dataset snapshot and parameter set.
6. Save outputs under a stable local layout.
   - Use a predictable directory structure under `logs/historical_research/studies/`.
   - Keep venue separation explicit in both metadata and artifact naming.
   - Preserve enough audit detail that a future consumer can trace any table back to dataset, code version, and study parameters.
7. Add focused verification around research logic, not just CLI plumbing.
   - Cover bucket construction, metadata writing, and core aggregation behavior with small fixtures.
   - Add at least one end-to-end study test over a synthetic DuckDB fixture so the output contract stays stable.
8. Document what is promotable versus experimental.
   - Each summary should state whether the result is descriptive only, a candidate execution prior, or too unstable for promotion.
   - Do not let an interesting artifact become a platform dependency unless its contract and interpretation are both clear.

### Verification target

- one documented command runs the baseline study bundle
- each study writes machine-readable outputs plus a short summary
- each study records dataset identity, code identity, parameters, and exclusions in metadata
- baseline outputs include sample-size and coverage context rather than headline metrics alone
- rerunning on the same dataset slice reproduces the same artifacts and metadata

### Exit criteria

- saved study outputs exist for calibration, maker or taker expectancy, longshot or favorite bias, and baseline sizing priors
- the first bundle produces venue-specific outputs with explicit conditioning dimensions and coverage context
- every promoted output has a machine-readable contract plus a plain-language interpretation
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
