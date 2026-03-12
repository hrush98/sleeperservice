# Future Strategy Avenues

## Core observation

The repo started in sports because lead-lag looked plausible, but both OddsPapi and Goalserve proved unreliable enough to limit strategy quality.

That implies a better strategy roadmap:
- prioritize edges that come from Polymarket itself
- treat external feeds as optional amplifiers, not architectural dependencies
- only add feed-driven strategies when the source can be normalized and scored for confidence

## Foundational capability - Historical research and replay

This is not a direct strategy lane.

It is a foundational capability that should move earlier in the roadmap because it supports:
- replay
- calibration
- execution analytics
- empirical sizing
- historical analog lookup

Why it matters:
- gives the platform a Polymarket-native path to sophistication
- reduces dependence on unreliable live event-state feeds
- improves both private trading and analysis API product quality

What to add:
- external historical dataset profiling
- normalized research views
- calibration-surface generation
- maker/taker expectancy analysis
- derived priors for replay, ranking, and risk

Priority:
- highest foundational workstream

## Strategy classes

## Class A - Polymarket-native strategies

These should be the highest priority because they do not depend on fragile external state.

### 1. Coherence and combinatorial arbitrage

Examples:
- complements
- partitions
- implication chains
- by-date cascades
- cross-market semantic duplicates

Why it matters:
- structural
- explainable
- API-friendly
- low dependence on outside data

What to add:
- persist findings in shared tables
- execution-aware ranking
- market graph relationships
- signal and replay support

Priority:
- highest

### 2. Execution edge and microstructure edge

Examples:
- spread capture quality
- passive vs aggressive fill modeling
- stale or thin-book exploitation
- maker/taker-aware sizing and sequencing

Why it matters:
- this repo already has real execution code
- better execution can create edge even when signal edge is thin

What to add:
- venue-aware execution simulator
- passive order placement policies
- queue and partial-fill analytics

Priority:
- highest

### 3. Polymarket family and semantic identity edge

Examples:
- same event framed twice
- nearly identical markets with different liquidity pools
- resolution semantics causing temporary misalignment

Why it matters:
- directly aligned with the semantic non-fungibility literature
- useful for both private trading and analysis API

What to add:
- semantic clustering
- resolution-rule parsing
- identity confidence scoring

Priority:
- high

## Class B - Optional external fair-value strategies

These are attractive because they can be rigorous and explainable, but they require cleaner reference adapters.

### 4. Digital-option style fair value for threshold markets

Examples:
- stock above/below strike by date
- BTC or ETH threshold markets
- macro threshold markets where an external options or forecast surface exists

Why it matters:
- benchmark fair probability is legible
- strong fit for analysis API products
- lower semantic ambiguity than live sports-state feeds

What to add:
- options-chain adapter
- risk-neutral probability calculators
- calibration and liquidity filters

Priority:
- high

### 5. Reference-ensemble sports pricing

This is the more mature version of lead-lag.

Instead of:
- one sharp feed

Use:
- multiple references
- line-move velocity
- source confidence and lag weighting

Why it matters:
- if sports remains viable, this is the correct evolution path

Risk:
- still depends on outside sources
- operationally heavier than native Polymarket strategies

Priority:
- medium

## Class C - External state-driven strategies

These are the least reliable and should be built only after the platform can degrade gracefully.

### 6. Live sports-state strategies

Examples:
- gold diff
- towers, dragons, barons
- win-probability calibration from in-game state

Reality:
- this is intellectually attractive
- it is only as good as the feed quality

Recommendation:
- keep the lane alive, but do not make it the main platform thesis
- require a better source before major expansion

Priority:
- medium to low

### 7. News or event ingestion

Examples:
- politics
- macro releases
- scheduled event monitors

Why it matters:
- could eventually produce differentiated research signals

Risk:
- high complexity
- quality and latency are hard problems

Priority:
- low for now

## What should be deferred

### Generic ML direction prediction

This includes:
- generic LSTM price-up-or-down models
- broad technical-indicator based direction models across unrelated contracts

Why defer:
- weak structural grounding
- high leakage risk
- sparse and regime-sensitive data
- not the strongest fit with the repo's current advantages

## Recommended expansion order

### Stage 1
- historical research foundation
- coherence into the shared platform
- signal ledger
- replay support
- execution analytics

### Stage 2
- digital-option fair-value lane
- semantic identity and cross-market analysis
- ranked opportunity API

### Stage 3
- improved sports reference ensemble
- better live-state ingestion if a reliable feed exists

### Stage 4
- only then evaluate whether ML adds incremental value

## API product angle

The best customer-facing outputs are likely:
- historical calibration surfaces
- execution analytics and maker/taker tendency summaries
- historical analog lookup
- coherence findings
- fair-value deltas
- semantic duplicate detection
- ranked opportunities with explanations

These are better API products than:
- raw live trading endpoints
- opaque black-box predictions

## Private trading angle

The strongest private-trading priorities are:
- empirical execution priors
- uncertainty-aware sizing from historical distributions
- polymarket-native structural edge
- execution quality
- confidence-aware ranking
- portfolio overlap control

This is the sophistication path that relies least on fragile third-party live feeds.
