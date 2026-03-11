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

## Canonical workflow

1. Use this file to determine the active phase.
2. Use `target-architecture.md` to understand the intended system shape for that phase.
3. Use `engineering-improvements.md` to ensure the work is being done cleanly.
4. Update this file at the end of each meaningful session.

## Current branch model

- feature work branches from `dev`
- feature branches open PRs into `dev`
- `dev` is promoted into `master`
- GitHub default branch is `dev`

## Roadmap status

### Overall status
- active branch: `phase_0`
- current focus: `Phase 0 - repo stabilization`
- next milestone: packaging cleanup and dead worker cleanup

## Phase summary

### Phase 0 - Repo stabilization
- status: active
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
  - `target-architecture.md`
    - `Target runtimes -> Analysis API runtime`
    - `Migration phases -> Phase 3`
  - `engineering-improvements.md`
    - `Workstream 6: API productization`

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

## Active phase detail

## Phase 0 - Repo stabilization

### Goals
- standardize the project package/import model
- remove dead and misleading infrastructure
- make tests and local execution reproducible
- clean configuration boundaries
- reduce documentation drift enough to support the next phases

### Phase 0 task list

#### P0.1 Packaging and imports
- status: not started
- target:
  - choose one canonical package namespace
  - stop relying on `sys.path` mutation in tests
  - stop mixing `services.*`, `shared.*`, and `cli.*` imports

#### P0.2 Project metadata and commands
- status: not started
- target:
  - add `pyproject.toml`
  - define canonical install/test commands
  - make local and CI execution predictable

#### P0.3 Dead worker and infra cleanup
- status: not started
- target:
  - remove stale worker references from docker/instructions
  - align compose and docs with the current runtime model

#### P0.4 Config and secret cleanup
- status: not started
- target:
  - remove hardcoded secrets from tracked config
  - separate config concerns more cleanly
  - document env requirements more clearly

#### P0.5 Documentation reset
- status: partially complete
- completed:
  - created `docs/platform/`
  - created `docs/research/reference-library.md`
- remaining:
  - decide how and when to retire or relabel MVP-era top-level docs
  - update README after the first code refactor lands

### Recommended execution order inside Phase 0

1. packaging/import cleanup
2. project metadata and canonical commands
3. dead worker and infra cleanup
4. config and secret cleanup
5. README/doc reset for the new execution model

## Open decisions

These are decisions that may change implementation order or exact structure.

### D1 - Canonical package namespace
- status: open
- options:
  - keep `services/` and introduce `services/app/...`
  - introduce a new top-level package name and migrate gradually
- current bias:
  - keep `services/` as the repo-root execution area and introduce a canonical package under it

### D2 - Treatment of legacy docs
- status: open
- question:
  - should `docs/architecture.md` and `docs/project-plan.md` remain as implementation-history docs
  - or should they be rewritten later as thin pointers to `docs/platform/`
- current bias:
  - keep them for now
  - convert them to pointers only after the restructure is well underway

### D3 - Scope of Phase 0
- status: open
- question:
  - should Phase 0 stop at cleanup and reproducibility
  - or include the first extraction of shared modules if that proves necessary to stabilize imports
- current bias:
  - stay disciplined
  - keep Phase 0 focused on stabilization, not architectural ambition

## Change log for this roadmap

### 2026-03-11
- created roadmap as the primary mutable execution document
- defined phase structure and status tracking
- designated `target-architecture.md` as end-state reference
- designated `engineering-improvements.md` as engineering-quality reference
- set active focus to `Phase 0 - repo stabilization`

## Session handoff template

Use this in future sessions:

```text
Use docs/platform/implementation-roadmap.md as the primary reference.
We are in <phase>.
Please check the active phase section, follow the listed execution order,
and use docs/platform/target-architecture.md plus
docs/platform/engineering-improvements.md as supporting references.
Before making changes, update the roadmap status if needed.
```
