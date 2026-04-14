# ADR 0004: Launch the first public analysis API as `v0 Public Beta` before full Phase 3 productization

## Status

Accepted — 2026-03-18

## Context

The repo now has a concrete `v0` analysis-API spec during active `Phase 0.5`.

At the same time:
- the historical-research workstream is still maturing its promoted artifact contracts
- the formal `Phase 3 - Analysis API` workstream has not started
- the fastest path to commercial validation is to start serving a narrow paid API before the full shared-runtime and productization work is complete

This creates one release-planning question:

Should the first public launch wait for full Phase 3 productization, or should the repo launch a narrow public beta earlier and continue the roadmap in parallel?

There is also a naming problem to avoid:
- `v0` is an API contract version
- `alpha` and `beta` are release-maturity labels

Those should not be conflated.

## Decision

- Keep `v0` as the API contract version.
- Keep internal alpha private and short-lived.
- Make the first public launch `v0 Public Beta`, not `v0 Alpha`.
- Launch the public beta during the current roadmap sequence, before full Phase 3 hardening is complete, as long as the narrow beta minimum bar is met.
- Use cheaper beta pricing and explicit feedback solicitation during the soft-launch period.
- Keep the public surface intentionally small:
  - `GET /analysis/opportunities`
  - `GET /markets/{market_id}/analysis`
- Treat `Phase 0.5` and especially `P0.5.4` as the contract-promotion work that feeds the first live beta.
- Treat later phases as quality and capability improvements on top of a live service:
  - `Phase 1`: service extraction and cleaner analysis composition
  - `Phase 2`: stronger relationship and market-graph quality
  - `Phase 3`: hardening, observability, billing maturity, and broader productization

## Consequences

- The first public API launch no longer waits for full Phase 3 completion.
- The roadmap can continue in parallel with a live, narrow public service.
- The external `v0` response contract must be kept stable even while internals are refactored.
- Public beta should stay honest about its maturity:
  - explicit beta wording
  - low or moderate rate limits
  - no SLA promise
  - clear warnings and provenance
- The repo should not market unsupported capabilities such as:
  - authoritative fair-value modeling without a real estimator
  - signal history before a signal ledger exists
  - “sharp money” or trader-identity inference

## Rationale

This repo needs real user feedback and paid demand validation sooner than the full target architecture will be complete.

Launching a narrow public beta preserves speed without pretending the platform is already at its final maturity level. It also keeps the product aligned with the repo's strongest current assets: historical priors, coherence checks, and explainable analysis.
