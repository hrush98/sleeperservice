# Changelog

## 2026-01-22
- Updated OddsPapi auth assumption: API key is passed via `apiKey` query parameter; aligned settings/env vars with `ODDS_API_KEY` + `POLY_API_KEY`.
- Marked M0–M3 as **(complete)** in `docs/project-plan.md` and adjusted milestone descriptions to match implemented ingestion (Gamma `/markets` polling + quote snapshots).
- Updated `docs/architecture.md` diagram/data flow to match current implementation (Gamma `/markets` polling + quote snapshots; orderbook snapshots are later).
- Updated roadmap to prioritize **paper/shadow execution** (record decisions, no live orders) alongside lag measurement.
- Swapped the planned reference source from BetInAsia/PS3838 to **OddsPapi (Pinnacle)**.
- Updated `docs/architecture.md` + `docs/project-plan.md` to reflect the new reference adapter and data flow.
- Why: validate that actionable lag windows exist in live conditions before investing in execution complexity.
- Impact: documentation-only change; code and schema updates will follow the updated milestones.

## 2026-01-21
- Updated project-plan process guidance:
  - When a milestone is finished, annotate it with **(complete)** in `docs/project-plan.md`.
- Why: keeps the master plan current and reduces ambiguity about progress.
- Impact: no runtime changes; documentation workflow change only.

## 2026-01-21
- Pivoted project direction from “Prediction Market Momentum” to **LoL Lead–Lag Arbitrage Bot**:
  - Reframed scope around PS3838 (BetInAsia) as fast reference vs Polymarket LoL CLOB as lagging target
  - Added measurement-first gate (lag distributions + fillability metrics) before any live execution
  - Defined new canonical entities: external matches/odds snapshots, mapping table, disagreement/edge events, and order book snapshots
- Why: the core edge is a short timing window where Polymarket prices lag a sharp external line; measurement must validate the window and fillability.
- Impact: governing docs now target lead–lag arbitrage; future milestones, schema additions, and ingestion sources should align with this plan.

## 2026-01-16
- Initialized repo documentation seed:
  - Added core-guidance.md, project-plan.md, architecture.md
  - Established canonical schema v0 and component boundaries
- Rationale: create stable, always-on project guidance for agentic development in Cursor.
- Impact: provides governing docs; subsequent changes must be reflected here.
