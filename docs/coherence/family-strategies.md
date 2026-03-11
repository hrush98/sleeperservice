# Coherence Family Groups and Strategy Routing

This document defines the current family groups in the coherence scanner and the strategy/check lane each family is routed into.

## Purpose

- Keep family classification and strategy application explicit.
- Separate what is implemented now from what is planned next.
- Make scan output interpretation easier (`Family counts`, `Routed strategy counts`).

## Current pipeline

1. Discover candidate events from Gamma (`active=true`, `closed=false`).
2. Decompose mixed events into subgroups.
3. Assign each subgroup a family label.
4. Route each family to a strategy lane.
5. Run only valid checks for that lane.

## Active strategy math (current implementation)

### `S1_MONOTONICITY` (`BY_CASCADE`)

For two markets in the same cascade:
- short date `t_s`
- long date `t_l`
- with `t_s < t_l`

Constraint:
- `P_yes(t_s) <= P_yes(t_l)`

Violation condition:
- `P_yes(t_s) > P_yes(t_l)`

Trade pair currently evaluated:
- buy `Yes` on long date
- buy `No` on short date

Pair cost:
- `pair_cost = yes_long + no_short`
- `no_short = 1 - yes_short`

Guaranteed edge (M2, before orderbook slippage):
- `edge = 1 - pair_cost`
- equivalent form: `edge = yes_short - yes_long`
- CLI prints `edge_cents = edge * 100`

M3 executable check:
- pull both books
- simulate fill prices at requested sizes
- recompute `edge_post_slippage_cents`
- keep opportunities only if `edge_post_slippage_cents >= min_edge_cents`

### `S_PARTITION_SUM` (`ON_PARTITION`, `RANGE_PARTITION`)

This strategy only runs for groups that pass the strict validity gate (`VALID_PARTITION`).

For a valid partition with buckets `i = 1..n`:
- `sum_yes = Σ p_i`
- `deviation = |1 - sum_yes|`

Violation condition:
- `deviation > tolerance`
- where `tolerance = coherence_partition_sum_tolerance` (default `0.08`)

Important:
- groups labeled `NON_APPLICABLE` or `INSUFFICIENT_EVIDENCE` are **not** counted as hard violations.
- threshold ladders like many `over X` / `under X` are usually non-applicable in this phase.

### `S_COMPLEMENT` (`BINARY_COMPLEMENT_PAIR`)

This strategy only runs for complement pairs that pass a strict validity gate (`VALID_PAIR`).

For a candidate pair `(A, not A)`:
- `sum_yes = P(A) + P(not A)`
- `deviation = |1 - sum_yes|`

Violation condition:
- `deviation > tolerance`
- where `tolerance = coherence_complement_sum_tolerance` (default `0.04`)

Validity gate (current implementation):
- exactly 2 markets in the pair
- opposite negation cues across questions (`not/no/without/...`)
- shared lexical subject above `coherence_complement_min_token_overlap` (default `0.70`)

### `S2_IMPLICATION` (`IMPLICATION_PAIR`)

This strategy only runs for implication pairs that pass strict validity (`VALID_PAIR`).

For directional pair `A => B`:
- implication bound: `P(A) <= P(B)`
- `gap = P(A) - P(B)`

Violation condition:
- `gap > tolerance`
- where `tolerance = coherence_implication_gap_tolerance` (default `0.02`)

Validity gate (current implementation):
- exactly 2 markets in the pair
- consequent includes explicit union cue (`or`, `either`, `any of`)
- antecedent lexical subset overlap above `coherence_implication_min_token_overlap` (default `0.70`)

## Family groups

### `BY_CASCADE`

- **Structure:** "Will X happen by T?" style markets with increasing time horizon.
- **Current route:** `S1_MONOTONICITY` (ready).
- **Current checks:**
  - date parsing and ordering
  - stem consistency
  - monotonicity (`P(by t1) <= P(by t2)` for `t1 < t2`)
  - M2 edge and M3 post-slippage edge using:
    - `pair_cost = yes_long + no_short`
    - `edge_cents = (1 - pair_cost) * 100`
- **Output:** M2 violations + optional M3 orderbook-aware opportunities.

### `ON_PARTITION`

- **Structure:** "Will X happen on T?" date buckets, usually with residual bucket semantics.
- **Current route:** `S_PARTITION_SUM` (ready, validity-gated).
- **Current checks:**
  - strict partition validity gate
  - only if valid: sum coherence near 1 (`sum_yes` with tolerance)
  - hard-violation rule: `abs(1 - sum_yes) > tolerance`
- **Important:** invalid or weak-evidence groups are marked non-applicable and skipped from hard violations.

### `RANGE_PARTITION`

- **Structure:** value-band buckets such as "between A and B", "<A", ">=B".
- **Current route:** `S_PARTITION_SUM` (ready, validity-gated).
- **Current checks:**
  - distinguish bucketized partitions vs threshold ladders
  - threshold ladders (`over X`, `under Y`) are non-applicable for hard partition scoring
  - only valid bucket partitions get sum checks
  - hard-violation rule: `abs(1 - sum_yes) > tolerance`

### `MIXED_HYBRID`

- **Structure:** event contains multiple structures or unclear grouping.
- **Current route:** `S_STUB` (not ready).
- **Current behavior:**
  - decomposition attempts to split into coherent subgroups first
  - unresolved groups stay hybrid and do not receive hard scoring

### `BINARY_COMPLEMENT_PAIR`

- **Structure:** separately framed complements (`A` and `not A`).
- **Current route:** `S_COMPLEMENT` (ready, validity-gated).
- **Current checks:**
  - strict pair validity gate
  - complement sum consistency: `sum_yes = P(A) + P(not A)`
  - hard-violation rule: `abs(1 - sum_yes) > tolerance`
- **Important:** weak lexical pairs are marked `INSUFFICIENT_EVIDENCE` and excluded from hard violations.

### `IMPLICATION_PAIR`

- **Structure:** candidate logical relation (`A => B`) across markets/series.
- **Current route:** `S2_IMPLICATION` (ready, validity-gated).
- **Current checks:**
  - strict pair validity gate
  - implication bound check (`P(A) <= P(B)`)
  - hard-violation rule: `P(A) - P(B) > tolerance`
- **Important:** only high-confidence directional pairs are scored; uncertain pairs are marked `INSUFFICIENT_EVIDENCE`.

### `JOINT_TRIPLE`

- **Structure:** triplets for joint probability bounds (`A`, `B`, `A∩B` or proxy).
- **Current route:** `S3_FRECHET` (stubbed, not ready).
- **Planned checks:**
  - Fréchet bounds over feasible intersection probability

## Strategy lane summary

- `S1_MONOTONICITY` -> active for `BY_CASCADE`
- `S_PARTITION_SUM` -> active for valid `ON_PARTITION` and valid `RANGE_PARTITION`
- `S_COMPLEMENT` -> active for valid `BINARY_COMPLEMENT_PAIR`
- `S2_IMPLICATION` -> active for valid `IMPLICATION_PAIR`
- `S3_FRECHET` -> defined, currently stubbed
- `S_STUB` -> placeholder lane for unresolved/not-yet-implemented families

## Validity gate semantics (ON/RANGE)

- `VALID_PARTITION`: scored and eligible for hard partition violations.
- `NON_APPLICABLE`: structurally not a strict partition for current math (skip hard scoring).
- `INSUFFICIENT_EVIDENCE`: not enough evidence to treat as strict partition (skip hard scoring).

Only `VALID_PARTITION` groups contribute to partition hard-violation counts.

## Validity gate semantics (COMPLEMENT/IMPLICATION)

- `VALID_PAIR`: scored and eligible for hard violations.
- `NON_APPLICABLE`: structure does not satisfy lane semantics (skip hard scoring).
- `INSUFFICIENT_EVIDENCE`: signal exists but lexical confidence is too weak (skip hard scoring).

Only `VALID_PAIR` findings contribute to complement/implication hard-violation counts.

## Recommended read of scan output

- `Family counts`: what structures were recognized after decomposition.
- `Routed strategy counts`: what is currently executable vs stubbed.
- `Partition valid/non-applicable`: quality of ON/RANGE structure detection.
- `Complement valid/non-applicable/insufficient`: quality of complement pair detection.
- `Implication valid/non-applicable/insufficient`: quality of implication pair detection.
- `M2 violations` / `M3 opportunities`: executable signal path for date cascades.

## CLI arguments (what they do)

Main command:

```bash
PYTHONPATH=services conda run -n poly python -m coherence scan
```

Common arguments:

- `--skip-m3`
  - skip orderbook slippage pass; faster; only M0/M1/M2 + partition checks.
- `--show-samples N`
  - how many example cascades/violations to print.
- `--min-edge-cents X`
  - minimum edge filter in cents.
  - applied to M2 listing and M3 listing.
  - `--min-edge-cents 1.0` means only show items with at least `1.0c` edge.
- `--m3-sizes "a,b,c"`
  - USD notionals used for M3 fill simulation.
  - example: `--m3-sizes 25,50,100` means evaluate fillability/edge at $25, $50, and $100.
  - larger sizes usually reduce edge because of slippage.
- `--cache-only`
  - run discovery/normalization and write cache JSON, then stop.
- `--limit`
  - Gamma page size (not max results). Usually leave default.

Practical presets:

```bash
# Fast structure/diagnostic run
PYTHONPATH=services conda run -n poly python -m coherence scan --skip-m3 --show-samples 5

# Execution realism check at small notionals
PYTHONPATH=services conda run -n poly python -m coherence scan --m3-sizes 25,50,100 --min-edge-cents 0.0 --show-samples 10

# Stricter edge display
PYTHONPATH=services conda run -n poly python -m coherence scan --min-edge-cents 2.0 --show-samples 10
```

## Verification command

```bash
PYTHONPATH=services conda run -n poly python -m coherence scan --skip-m3 --show-samples 3
```

