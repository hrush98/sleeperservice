# ADR 0002: Starting soon panel and polling state machine

## Status

Accepted

## Context

The live monitor currently treats fresh PRE odds as "live" for display. This blurs the line between in-play matches and pre-start matches, and can hide relevant matches when PRE odds are stale. We also fetch Gamma market metadata inside the 0.5s snapshot loop, which is higher-frequency than necessary.

## Decision

- Split Pinnacle classification into explicit phases:
  - `PIN_LIVE`: `statusId == 1` or fixture status is `live`.
  - `PIN_PRE_FRESH`: `statusId == 0` and `updatedAt` is recent.
  - `PIN_PRE_STALE`: `statusId == 0` and `updatedAt` is not recent.
  - `PIN_ENDED`: `statusId in {2,3}`.
- Render two tables in the TUI:
  - **Live**: strictly in-play matches.
  - **Starting soon**: matches starting within 15 minutes that have fresh PRE odds.
- Add a `PIN=...` label per row so the user can quickly see PRE vs LIVE.
- Introduce a two-tier league polling cadence:
  - 1s when any fixture is in-play.
  - 5s when only PRE fixtures are present.
- Move Gamma `/markets/{id}` refresh to its own periodic loop (10–15s) and keep snapshot loop merge-only.

## Rationale

- Distinguishing PRE vs LIVE prevents false “live” labeling and enables a dedicated “starting soon” view.
- The two-tier cadence reduces load without sacrificing in-play responsiveness.
- Decoupling Gamma refresh from the snapshot loop keeps the UI responsive and limits unnecessary API calls.

## Consequences

- The UI now has two tables instead of one; users should use the Starting soon panel for pre-start monitoring.
- PRE odds are only considered “fresh” for a bounded window, which is configurable.
- Gamma metadata freshness is controlled by a separate refresh interval, which is configurable.
