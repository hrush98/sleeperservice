# Coherence Service Changelog

Designated changelog for the **Polymarket Coherence Arbitrage Scanner** service. Changes to `docs/coherence/project-plan.md` or `docs/coherence/architecture.md` require an entry here.

## Entry format (required)
- What changed (1–5 bullets)
- Design decisions (explicit bullets + ADR links)
- Why (brief)
- Impact (behavior/migrations)
- How to verify (commands + expected output)

---

## 2026-02-12 — S2 implication lane + complement lane enabled

### What changed
- Implemented executable `BINARY_COMPLEMENT_PAIR` and `IMPLICATION_PAIR` lanes in coherence scanner routing.
- Added strict pair validity gates and typed findings for complement and implication checks in `services/coherence/checks.py` and `services/coherence/models.py`.
- Added CLI reporting blocks for complement/implication totals, validity splits, and hard-violation samples.
- Added new coherence config tunables for pair overlap thresholds and violation tolerances.
- Updated coherence docs (`family-strategies`, `project-plan`, `architecture`) to document active lane math and gating behavior.

### Design decisions
- **Conservative lexical gating first**: only score pairs when explicit directional/negation cues plus token overlap criteria are met.
- **Validity-first metrics**: hard violations only count for `VALID_PAIR`, mirroring existing partition validity semantics.
- **Minimal extension of current architecture**: integrate new lanes into existing scanner -> route -> checks -> CLI flow without introducing new services or storage.

### Why
Date-cascade opportunities can be sparse, so enabling implication and complement checks increases the actionable non-cascade surface while staying within pure internal coherence math.

### Impact
- `coherence scan` now emits executable readiness and finding summaries for complement and implication lanes.
- Family counts/routed strategy counts include `BINARY_COMPLEMENT_PAIR` and `IMPLICATION_PAIR` as active pathways.
- No DB migrations required.

### How to verify
- `PYTHONPATH=services conda run -n poly pytest services/coherence/test_scanner.py services/coherence/test_checks_ranker.py`
- `PYTHONPATH=services conda run -n poly python -m coherence scan --skip-m3 --show-samples 3`
- `PYTHONPATH=services conda run -n poly python -m coherence scan --m3-sizes 25,50,100 --show-samples 3`

---

## 2026-02-12 — Bootstrap: Dedicated Coherence Changelog

### What changed
- Created `docs/coherence/changelog.md` — dedicated changelog for the coherence service.
- Updated `.cursor/rules/core-guidance.mdc` — coherence doc changes now route to this changelog.

### Design decisions
- **Separate changelog per service**: Coherence is a designated service with its own docs; changelog entries stay scoped to coherence.
- **Same entry format** as root `docs/changelog.md`.

### Why
Coherence is a distinct strategy from lead-lag. Keeping its changelog in `docs/coherence/` avoids polluting the main changelog and keeps service-specific history in one place.

### Impact
- All future changes to `docs/coherence/project-plan.md` or `docs/coherence/architecture.md` must append to this file.
- Root `docs/changelog.md` continues to track lead-lag and cross-cutting changes.

---

## 2026-02-12 — Scope correction: keep all 3 coherence strategies in plan/architecture

### What changed
- Updated `docs/coherence/project-plan.md` to explicitly keep all three strategies in scope:
  - date-cascade monotonicity (first implementation slice)
  - logical implication bounds
  - Fréchet bounds
- Updated `docs/coherence/architecture.md` title/overview to reflect a 3-strategy roadmap with date-cascade implemented first.
- Removed wording that implied a date-only long-term strategy direction.

### Design decisions
- **Date-first, not date-only**: implementation sequencing starts with date cascades, but architecture and plan continue to represent all three strategy lanes.
- **Roadmap visibility in core docs**: implication and Fréchet remain first-class roadmap entries rather than being relegated to out-of-scope language.

### Why
Maintain intended product direction: the coherence service should remain a multi-strategy mathematical engine, with date-cascade as the first vertical slice only.

### Impact
- Coherence docs now match intended strategic scope.
- Current code remains unchanged; this is a documentation scope correction.

### How to verify
- Open `docs/coherence/project-plan.md` and confirm the explicit "Strategy stack (kept in scope)" section lists S1/S2/S3.
- Open `docs/coherence/architecture.md` and confirm the "Strategy lanes" section lists date-cascade, implication, and Fréchet.

---

## 2026-02-12 — Family-first discovery model added to coherence plan

### What changed
- Updated `docs/coherence/project-plan.md` to add a family-first pipeline: discovery -> family assignment -> strategy routing.
- Added explicit core family taxonomy to the plan:
  - `BY_CASCADE`
  - `ON_PARTITION`
  - `RANGE_PARTITION`
  - `BINARY_COMPLEMENT_PAIR`
  - `IMPLICATION_PAIR`
  - `JOINT_TRIPLE`
  - `MIXED_HYBRID`
- Added family-to-strategy routing guidance and in-memory data model additions (`FamilyAssignment`, `StrategyCandidate`).
- Updated milestones so M0/M1 start broad (classification + routing) while keeping date-cascade monotonicity as the first executable strategy lane.

### Design decisions
- **Family-first before strategy-specific checks**: classify structure first so each strategy runs only where mathematically valid.
- **In-memory first**: keep classification and routing lightweight for iteration speed before database schema expansion.
- **Preserve date-cascade-first execution**: broaden discovery now without delaying current S1 implementation sequence.

### Why
Avoid overfitting the coherence engine to date cascades and establish a reusable foundation for implication, partition, and Fréchet checks.

### Impact
- Coherence roadmap now reflects a broader scanner that can discover and route multiple market structures.
- Immediate implementation implication: scanner/detector work should emit family labels before running strategy checks.
- No migrations required (documentation-only change).

### How to verify
- Open `docs/coherence/project-plan.md` and confirm the new "Family-first discovery model (in-memory first)" section exists with all seven family labels.
- Confirm milestone M0 now references family assignment and family counts in verification output.
- Confirm milestone M2.5 defines non-cascade in-memory checks by family.
