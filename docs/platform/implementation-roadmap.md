# Implementation Roadmap

## Purpose

This is the primary execution document for the platform restructure.

Use this file when starting a new session.

This document is intentionally more flexible than:
- `docs/platform/target-architecture.md`
- `docs/platform/engineering-improvements.md`

Those two documents should remain mostly stable:
- `target-architecture.md` defines the desired end-state
- `engineering-improvements.md` defines the engineering standards and cleanup work

This roadmap is where sequencing, status, and tactical adjustments should live.

## How to use this document

When starting a new session, reference:
- this file first
- the active phase
- any open decisions listed here

If implementation needs to change:
- update this file first
- only update `target-architecture.md` if the end-state changes
- only update `engineering-improvements.md` if the engineering standards or workstreams change

When the active phase has a dedicated `phase_*.md` guide:
- use that guide as the direct execution companion for task slicing and handoff
- keep this roadmap as the source of status, sequencing, and open decisions

## Canonical workflow

1. Use this file to determine the active phase.
2. Use `target-architecture.md` to understand the intended system shape for that phase.
3. Use `engineering-improvements.md` to ensure the work is being done cleanly.
4. If the active phase has a `phase_*.md` guide, use it as the direct implementation plan.
5. Update this file at the end of each meaningful session.

## Current branch model

- feature work branches from `dev`
- feature branches open PRs into `dev`
- `dev` is promoted into `master`
- GitHub default branch is `dev`

## Roadmap status

### Overall status
- active branch: `phase_0`
- current focus: `Phase 0.5 - historical research foundation`
- previous completed focus: `Phase 0 - repo stabilization`
- next milestone: finish the `P0.5.4` public-beta plumbing slice so the first live `v0` routes have auth, rate limiting, deterministic errors, monitoring, maintenance controls, and launch docs

## Phase summary

### Phase 0 - Repo stabilization
- status: complete
- objective:
  - make the repo safe to work in repeatedly
  - remove known environmental and structural footguns
  - prepare for deeper refactors without compounding chaos
- primary references:
  - `target-architecture.md`
    - `Migration phases -> Phase 0`
    - `Migration map from current code`
  - `engineering-improvements.md`
    - `Workstream 1: Packaging and bootstrapping`
    - `Workstream 7: Documentation reset`
    - `P0 - immediate`

### Phase 0.5 - Historical research foundation
- status: active
- objective:
  - create the historical-trades foundation for replay, calibration, and execution analytics
  - move research and empirical strategy validation earlier in the platform sequence
  - keep raw external data outside the live runtime and app database
- primary references:
  - `api-v0-spec.md`
    - `Release maturity`
    - `Public beta minimum bar`
  - `phase_0-5.md`
    - `Immediate next slice`
    - `P0.5.1-P0.5.4 implementation plan`
  - `historical-research-workstream.md`
    - `Workstream phases`
    - `Recommended implementation boundaries`
    - `Open decisions`
  - `engineering-improvements.md`
    - `Workstream 3: Research and replay`
  - `future-strategy-avenues.md`
    - `Foundational capability - historical research and replay`
  - `docs/research/reference-library.md`
    - `Becker Prediction Markets Dataset`
    - `Guide 6 - Historical trades / calibration / maker-taker guide`

### Phase 1 - Shared domain extraction
- status: not started
- objective:
  - extract reusable domain services from CLI-heavy modules
  - introduce strategy interface and signal ledger
- primary references:
  - `target-architecture.md`
    - `Strategy contract`
    - `Bounded contexts`
    - `Migration phases -> Phase 1`
  - `engineering-improvements.md`
    - `Workstream 2: Runtime decomposition`
    - `Workstream 3: Research and replay`

### Phase 2 - Market graph foundation
- status: not started
- objective:
  - unify coherence logic, market relationships, and future fair-value relationships
- primary references:
  - `target-architecture.md`
    - `Market graph`
    - `Data model evolution`
    - `Migration phases -> Phase 2`
  - `engineering-improvements.md`
    - `Workstream 4: Data model and market graph`

### Phase 3 - Analysis API
- status: not started
- objective:
  - expose analysis services as a proper API surface
- primary references:
  - `api-v0-spec.md`
    - `Design rules`
    - `Endpoint 1`
    - `Endpoint 2`
  - `docs/adr/0004-v0-public-beta-before-phase-3-productization.md`
  - `target-architecture.md`
    - `Target runtimes -> Analysis API runtime`
    - `Migration phases -> Phase 3`
  - `engineering-improvements.md`
    - `Workstream 6: API productization`

## Parallel launch overlay

This is not a separate roadmap phase.

It is the release overlay for getting the first public API live while the broader platform roadmap continues.

### V0 Public Beta overlay
- status: planned
- objective:
  - soft-launch the first paid read-only analysis API during `Phase 0.5`
  - keep the public contract narrow and stable while the internal platform keeps evolving
- release policy:
  - internal alpha remains private
  - first public release is `v0 Public Beta`
  - beta pricing should stay cheaper and explicitly solicit feedback
- minimum bar:
  - stable JSON schemas for both endpoints
  - auth or payment works
  - rate limiting and abuse controls exist
  - provenance and warnings are returned
  - basic monitoring and a kill switch exist
  - short public docs exist
- phase relationship:
  - `Phase 0.5` promotes the historical and analysis artifacts that make the beta credible
  - `Phase 1` moves API assembly behind shared services
  - `Phase 2` improves market-relationship quality and ranking
  - `Phase 3` hardens the live runtime instead of being the first day the API exists

### Phase 4 - Replay and experiment system
- status: not started
- objective:
  - make strategy claims replayable and comparable
- primary references:
  - `target-architecture.md`
    - `Replay runtime`
    - `Migration phases -> Phase 4`
  - `engineering-improvements.md`
    - `Workstream 3: Research and replay`

### Phase 5 - Strategy expansion
- status: not started
- objective:
  - promote coherence into the shared runtime
  - add fair-value and later sports-state improvements
- primary references:
  - `target-architecture.md`
    - `Strategy priority under this architecture`
    - `Migration phases -> Phase 5`
  - `future-strategy-avenues.md`

## Detailed phase breakdown

## Phase 0 - Repo stabilization

### Goals
- standardize the project package/import model
- remove dead and misleading infrastructure
- make tests and local execution reproducible
- clean configuration boundaries
- reduce documentation drift enough to support the next phases

### Phase 0 task list

#### P0.1 Packaging and imports
- status: complete
- completed:
  - adopted `services.*` as the canonical package namespace for Phase 0
  - migrated code, tests, and Alembic imports away from bare `shared.*`, `cli.*`, `api.*`, and `coherence.*`
  - removed `sys.path` mutation from test conftests and test modules
- target:
  - choose one canonical package namespace
  - stop relying on `sys.path` mutation in tests
  - stop mixing `services.*`, `shared.*`, and `cli.*` imports

#### P0.2 Project metadata and commands
- status: complete
- completed:
  - added `pyproject.toml`
  - defined package discovery, console scripts, and pytest collection defaults
  - updated the API container to install the repo package and boot with `services.api.main:app`
  - documented the canonical install/run/test commands in `README.md`
- target:
  - add `pyproject.toml`
  - define canonical install/test commands
  - make local and CI execution predictable

#### P0.3 Dead worker and infra cleanup
- status: complete
- completed:
  - removed the stale `worker` compose service that referenced a missing Dockerfile
  - updated in-repo runtime/tool command references to the canonical `services.*` module paths
  - aligned top-level runtime documentation and command references with the current `services.*` execution model
- target:
  - remove stale worker references from docker/instructions
  - align compose and docs with the current runtime model

#### P0.4 Config and secret cleanup
- status: complete
- completed:
  - removed the hardcoded Goalserve feed key from tracked config defaults
  - added a tracked `.env.example` template and allowed it through `.gitignore`
  - fixed the default secret-path typo from `~/.sleeprservice/...` to `~/.sleeperservice/...`
  - tightened Goalserve client behavior so missing config fails explicitly
  - documented the env and secret surface for the canonical install/run path
- target:
  - remove hardcoded secrets from tracked config
  - separate config concerns more cleanly
  - document env requirements more clearly

#### P0.5 Documentation reset
- status: complete
- completed:
  - created `docs/platform/`
  - created `docs/research/reference-library.md`
  - rewrote `README.md` around the canonical Phase 0 package/install/run model
  - introduced the mutable roadmap and Codex repo guidance
  - added a changelog-backed planning trail for the restructure and Phase 0.5 transition

### Recommended execution order inside Phase 0

1. packaging/import cleanup
2. project metadata and canonical commands
3. dead worker and infra cleanup
4. config and secret cleanup
5. README/doc reset for the new execution model

## Phase 0.5 - Historical research foundation

### Goals
- create a reproducible historical research entrypoint
- build a normalized research layer over external trade data
- generate the first durable calibration and execution studies
- keep this work isolated from live runtime coupling at first

Execution companion:
- `phase_0-5.md`

### Phase 0.5 task list

#### P0.5.1 Dataset landing and audit
- status: completed
- completed:
  - defined the initial `HISTORICAL_DATASET_ROOT` and `HISTORICAL_RESEARCH_OUTPUT_ROOT` config surface
  - isolated historical-research path resolution in `services.research` so the profiler does not depend on live runtime config
  - added a read-only historical dataset profiler entrypoint under `services.tools`
  - added manifest and summary artifact output conventions under `logs/historical_research/`
  - added focused tests for dataset discovery, output contracts, and CLI help-path safety
  - landed the Becker prediction-markets dataset under an external local root and captured the first real audit output
  - switched parquet audit from per-file brute force to logical collection profiling so the real dataset completes in one command
  - added sampled deep-audit fallback for very large collections and bounded manifest file records for Becker-scale runs
- target:
  - define dataset-path conventions
  - add a schema and venue profiler
  - document raw-dataset boundaries and quality checks

#### P0.5.2 Normalized research layer
- status: completed
- completed:
  - added pure feature-derivation helpers for price, size, time-to-resolution, maker/taker role, and topic classification
  - added normalization contracts and alias resolution for market, trade, and resolution views
  - added `services.tools.materialize_historical_research` with a DuckDB-backed local materialization path
  - added focused synthetic-parquet tests for the materializer and normalized feature outputs
  - ran the materializer against the Becker dataset and captured the first real schema-mapping gaps
  - added Becker-specific Kalshi and Polymarket normalization for ticker-based Kalshi contracts, Polymarket token-id joins, block-timestamp joins, and legacy FPMM trades
  - added explicit `contract_side` support in normalized trade views so later studies can distinguish traded outcome from buy or sell action
  - added `--skip-view-row-counts` so Becker-scale materialization can finish without blocking on full final-view counts
  - switched the normalized `historical_*` layer from view-only definitions to persisted DuckDB tables so Becker-scale study runs stop rescanning raw parquet on every query
- target:
  - create stable analytical tables for historical markets, trades, resolutions, and trade features
  - separate venue-specific outputs from blended assumptions

#### P0.5.3 Baseline empirical studies
- status: in progress
- completed:
  - added a dedicated `services.research.studies` package for durable empirical-study logic and shared artifact contracts
  - added `services.tools.run_historical_studies` as the first baseline study runner over the normalized DuckDB layer
  - implemented the first saved calibration surface study with machine-readable parquet output, metadata, and short summary artifacts
  - implemented the first maker/taker expectancy study with both observed-role and inferred-counterparty role bases, plus explicit caveat metadata
  - implemented longshot/favorite bias summaries with venue and topic conditioning plus coarse year-level stability checks
  - implemented edge-dispersion and sizing-prior outputs with conservative promotion and haircut metadata
  - added focused synthetic end-to-end tests that verify study artifact contracts, venue filtering, and CLI help-path safety
- target:
  - run and review the first Becker-scale outputs to decide what is promotable into later platform hooks
  - tighten promotion thresholds and artifact fields where the first real dataset review exposes instability or ambiguity

#### P0.5.4 Platform hooks
- status: in progress
- completed:
  - defined the first `v0` paid analysis-API contract and public-beta launch policy
  - added promoted historical-artifact loaders under `services.research.artifacts` so downstream consumers can read saved study outputs without touching raw parquet
  - added the first `services.api.analysis_v0` service layer to combine live market snapshots with promoted calibration, maker/taker, and sizing contexts
  - added the first read-only `v0` API routes for ranked opportunities and single-market analysis
  - added focused tests that cover artifact lookup plus the first `v0` analysis responses with a fake Polymarket client
  - added shared API-scoped public-beta settings for enablement, maintenance mode, auth mode, rate limits, and request logging
  - added shared auth, rate-limiting, error-envelope, and request-context middleware modules under `services.api`
  - wired public-beta access control into the `v0` router with API-key auth first, single-instance in-memory throttling, request IDs, and deterministic error responses
  - expanded the API docs, env template, and focused tests for auth failures, maintenance mode, throttling, request-context logging, and router error mapping
- target:
  - run internal smoke checks against the live deployment shape before posting the beta broadly
  - keep the first launch posture honest:
    - API-key auth first is acceptable if it gets the beta live faster
    - `x402` can follow behind the same auth boundary without reshaping route handlers
    - single-instance rate limiting is acceptable only while the deployment stays single-instance
  - preserve the contract boundary so later phases can deepen ranking, coherence, and replay without rewiring public-beta plumbing

### Recommended execution order inside Phase 0.5

1. dataset landing and audit
2. normalized research layer
3. baseline empirical studies
4. platform-hook contracts

## Open decisions

These are decisions that may change implementation order or exact structure.

### D1 - Canonical package namespace
- status: decided
- options:
  - keep `services/` and introduce `services/app/...`
  - introduce a new top-level package name and migrate gradually
- decision:
  - use `services.*` as the canonical package namespace during Phase 0
  - reserve `services/app/...` as a later structural extraction, not a prerequisite for stabilization

### D2 - Treatment of legacy docs
- status: open
- question:
  - should `docs/architecture.md` and `docs/project-plan.md` remain as implementation-history docs
  - or should they be rewritten later as thin pointers to `docs/platform/`
- current bias:
  - keep them for now
  - convert them to pointers only after the restructure is well underway

### D3 - Scope of Phase 0
- status: decided
- question:
  - should Phase 0 stop at cleanup and reproducibility
  - or include the first extraction of shared modules if that proves necessary to stabilize imports
- decision:
  - Phase 0 remained focused on stabilization, packaging, environment reproducibility, config cleanup, and documentation reset
  - deeper domain extraction remains a later phase concern

### D4 - Start timing for Phase 0.5
- status: open
- question:
  - should H0/H1 start only after all remaining Phase 0 cleanup lands
  - or can isolated historical-research work begin once the current package and command model is stable enough
- current bias:
  - Phase 0 is now complete
  - begin isolated H0/H1 work with read-only boundaries before the rest of the deeper refactor sequence

### D5 - Commercial priority and first paid product surface
- status: decided
- question:
  - should the repo keep treating private trading as the main near-term product
  - or should the first commercial milestone be a minimal paid, read-only analysis API that can start serving requests before the full private multi-strategy runtime is mature
- decision:
  - prioritize the paid analysis API as the first product milestone
  - keep `Phase 0.5` active because its durable outputs are the cleanest inputs for early API endpoints
  - treat private trading as a second consumer of the same shared domain and research contracts rather than the first delivery gate
  - keep internal alpha private and launch the first public service as `v0 Public Beta`
  - use a soft-launch posture with cheaper pricing, explicit beta wording, and active feedback collection

## Change log for this roadmap

### 2026-03-19
- landed the shared public-beta plumbing layer in `services.api` with API settings, maintenance controls, auth, rate limiting, deterministic errors, request IDs, and request logging
- updated `P0.5.4` completed work to reflect the implemented public-beta plumbing slice and narrowed the remaining target to deployment smoke and launch readiness
- changed the next milestone from policy definition to the concrete `P0.5.4` public-beta plumbing slice
- expanded `P0.5.4` targets to include auth, rate limiting, deterministic errors, monitoring, maintenance controls, and launch docs
- clarified that API-key auth first and single-instance rate limiting are acceptable beta shortcuts if they stay behind stable boundaries

### 2026-03-18
- updated the next milestone to include defining the `v0` public-beta minimum bar
- added a public-beta launch overlay so the API can go live before formal Phase 3 hardening is complete
- converted the commercial-priority decision into an accepted policy: private alpha, public beta first

### 2026-03-17
- implemented the first real `P0.5.1` code slice under `services.research` and `services.tools.profile_historical_dataset`
- kept the research config surface separate from `services.shared.config` so dataset tooling stays outside live-runtime requirements
- added focused tests for path resolution, dataset discovery, output writing, and CLI help behavior
- documented the historical dataset env vars and command path in `README.md` and `.env.example`
- added the first real `P0.5.2` slice with normalized view contracts, DuckDB materialization, and synthetic-parquet verification

### 2026-03-12
- added `phase_0-5.md` as the direct execution companion for the active phase
- updated the roadmap workflow to allow active phases to carry dedicated `phase_*.md` implementation guides
- linked the Phase 0.5 summary, detailed phase breakdown, and session handoff template to the new guide
- started `P0.5.1` with the initial historical dataset config and profiler implementation slice

### 2026-03-11
- created roadmap as the primary mutable execution document
- defined phase structure and status tracking
- designated `target-architecture.md` as end-state reference
- designated `engineering-improvements.md` as engineering-quality reference
- set active focus to `Phase 0 - repo stabilization`
- established repo-root `AGENTS.md` as the Codex instruction entrypoint
- retired tracked Cursor rule files as a source of truth
- completed P0.1 by standardizing on `services.*` imports and removing test path hacks
- partially completed P0.2 with `pyproject.toml`, package entry points, and pytest defaults
- partially completed P0.3 by removing the stale compose worker service and package-path footguns
- completed P0.2 by documenting canonical install/run/test commands in `README.md`
- partially completed P0.4 by removing tracked secret defaults and adding `.env.example`
- advanced P0.5 by rewriting `README.md` for the restructure-era execution model
- introduced `Phase 0.5 - Historical research foundation` as an earlier research/replay workstream
- added `historical-research-workstream.md` as the dedicated planning document for guide6-driven work
- completed Phase 0 implementation and verification on the `sleeperservice` environment
- moved the active roadmap focus from Phase 0 to Phase 0.5

## Session handoff template

Use this in future sessions:

```text
Use docs/platform/implementation-roadmap.md as the primary reference.
We are in <phase>.
Please check the active phase section, follow the listed execution order,
and use docs/platform/target-architecture.md plus
docs/platform/engineering-improvements.md as supporting references.
If working on the historical-trades foundation, also use
docs/platform/historical-research-workstream.md and
docs/platform/phase_0-5.md.
Before making changes, update the roadmap status if needed.
```
