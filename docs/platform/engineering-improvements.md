# Engineering Improvements

## Purpose

This document turns the audit into a concrete engineering cleanup plan.

It is intentionally platform-focused rather than strategy-focused. The goal is to make strategy work faster, safer, and easier to validate.

## Current engineering problems

### Packaging and environment
- imports are inconsistent across `cli.*`, `shared.*`, and `services.*`
- tests depend on `sys.path` manipulation
- there is no single canonical project package
- runtime behavior differs too much by environment

### Runtime coupling
- the live TUI owns too much orchestration logic
- strategy logic is not registered through a shared interface
- discovery, analysis, execution, and presentation are too entangled

### Monolithic files
- `services/cli/discover.py`
- `services/cli/trader.py`
- `services/cli/gold_edge.py`

These files hold too many responsibilities each.

### Documentation drift
- README and MVP docs no longer match the codebase
- deprecated worker references still exist in infra
- strategy-specific planning is split across old and new eras of the repo

### Weak research surface
- no replay harness
- no signal ledger
- no experiment tracking
- no clean execution-cost simulation layer outside live runtime code

### Risk and ops gaps
- good trade-level controls exist
- portfolio-level and strategy-level controls do not
- secret handling and config partitioning need cleanup

## Recommended workstreams

## Workstream 1: Packaging and bootstrapping

### Goal
Make the repo runnable and testable from one documented entrypoint.

### Changes
- add a real Python package root under `services/app/` or equivalent
- standardize imports under one namespace
- add project metadata (`pyproject.toml`)
- define one canonical dev/test command
- remove path hacks from tests

### Success criteria
- tests import modules without mutating `sys.path`
- one command works for local dev and CI
- no mixed `services.*` vs `shared.*` import style

## Workstream 2: Runtime decomposition

### Goal
Separate domain services from CLI/UI code.

### Changes
- move discovery logic into dedicated service modules
- move strategy logic behind a shared interface
- reduce `live.py` to orchestration and presentation only
- move execution and reconciliation behind clearer boundaries

### Success criteria
- the same strategy can run in a batch scan, replay job, and live runtime
- the TUI is only one consumer of shared services

## Workstream 3: Research and replay

### Goal
Make strategy quality measurable instead of anecdotal.

### Changes
- create a `signals` table or equivalent persisted signal ledger
- create replay jobs that can re-run strategies over historical snapshots
- add experiment metadata and parameter versioning
- add fill simulation policies for FAK, FOK, and passive execution

### Success criteria
- every tradeable strategy has a replayable evaluation path
- strategy comparisons are made from saved experiments, not memory

## Workstream 4: Data model and market graph

### Goal
Unify sports mappings, coherence families, and future fair-value relationships under one graph model.

### Changes
- add canonical market entities
- add persisted relationship edges
- normalize outcome tokens and side mappings
- capture confidence and provenance for every relationship

### Success criteria
- complement, partition, implication, and parent-child logic no longer live in isolated modules only
- the API can query relationships directly

## Workstream 5: Risk system

### Goal
Move from trade hygiene to portfolio control.

### Changes
- add per-strategy budgets
- add concentration limits
- add unresolved semantic overlap limits
- add liquidity-tier based max sizing
- add runtime kill switches by strategy and by venue

### Success criteria
- multiple strategies can run at once without hidden overlap blowups
- risk policy is explicit and inspectable

## Workstream 6: API productization

### Goal
Build a real analysis API, not just an ops API.

### Changes
- add analysis endpoints for findings and fair values
- expose explanation payloads
- expose signal history and market-family views
- keep execution endpoints private or absent

### Success criteria
- the API is useful even if live trading is disabled
- analysis outputs are explainable and reproducible

## Workstream 7: Documentation reset

### Goal
Make docs match the repo and the next-stage plan.

### Changes
- keep old implementation docs as historical context
- move future planning into `docs/platform/`
- refresh README after the restructure starts landing
- remove or update dead infra references

### Success criteria
- a new contributor can tell what the repo is now
- platform docs and implementation docs are no longer in conflict

## Priority order

### P0 - immediate
- packaging cleanup
- environment reproducibility
- dead worker cleanup
- config and secret cleanup

### P1 - first structural pass
- strategy interface
- runtime decomposition
- signal ledger

### P2 - platform capability
- market graph
- replay engine
- analysis API

### P3 - sophistication and scale
- portfolio risk layer
- ranked opportunity service
- multi-strategy scheduling

## Concrete near-term tasks

### 1. Create canonical package layout
- choose one namespace
- move bootstrapping and settings there
- update imports and tests

### 2. Remove dead worker references
- clean `docker-compose`
- remove worker build references
- remove any stale docs that still imply worker-based runtime

### 3. Extract a strategy base interface
- `lead_lag`
- `complement_arb`
- `coherence`
- `gold_edge`

### 4. Introduce a signal ledger
- persist every signal
- include block reason when not executable

### 5. Build first replay slice
- start with complement or coherence
- add lead-lag second

### 6. Build first analysis endpoints
- `GET /analysis/opportunities`
- `GET /analysis/market-graph/{market_id}`
- `GET /analysis/signals`

## Anti-goals

Do not do these first:
- generic deep learning on sparse event histories
- cross-platform execution before semantic identity is robust
- new strategy lanes without replay support
- more feed integrations before the reference-normalization layer exists
