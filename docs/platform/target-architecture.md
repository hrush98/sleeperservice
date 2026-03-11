# Target Architecture

## Purpose

This document defines the target architecture for turning the repo from a focused lead-lag bot into a broader Polymarket trading and analysis platform.

It is designed to support two end states at the same time:
- a private multi-strategy trading system
- a public or semi-public analysis API

The design assumes one important operating constraint:
- external live sports feeds are unreliable enough that they cannot be the core dependency of the platform

That constraint changes the center of gravity of the system:
- Polymarket-native structure, orderbook state, and market-family math should become first-class
- external feeds should be treated as optional reference adapters with explicit confidence and freshness scoring

## Design principles

### 1. Polymarket-first core

The platform should remain useful even if every external feed is disabled.

That means the core alpha and API value should come from:
- market graph structure
- orderbook state
- execution quality
- event and market family relationships
- fair-value math where external references are optional, not mandatory

### 2. Separate research, analysis, and execution

The current code mixes runtime concerns with strategy concerns. The target shape splits these cleanly:
- research and replay
- signal generation
- ranking and risk
- execution
- presentation layers

### 3. Strategies are plugins, not runtime branches

Adding a strategy should not require editing the live TUI runtime. A strategy should register itself against a common contract and declare:
- required inputs
- evaluation cadence
- risk limits
- outputs

### 4. Analysis API and trading runtime share the same domain services

The API should not be a second implementation of the logic. The private trader and the public analysis service should both run on the same:
- market graph
- fair-value services
- strategy evaluators
- ranking and explanation services

### 5. Every decision should be replayable

No strategy should be considered mature until it can be replayed on historical data with:
- book state
- market metadata
- reference inputs
- signals
- execution assumptions

## Target system shape

```text
services/
  app/
    __init__.py
    bootstrap/
      settings.py
      logging.py
      container.py
    db/
      engine.py
      session.py
      models/
      repositories/
      migrations/
    domain/
      markets/
        entities.py
        graph.py
        normalization.py
        relationships.py
      books/
        snapshots.py
        slippage.py
      references/
        models.py
        freshness.py
        confidence.py
      signals/
        models.py
        ledger.py
      execution/
        orders.py
        fills.py
        reconciliation.py
      risk/
        limits.py
        portfolio.py
        guards.py
      research/
        replay.py
        experiments.py
        metrics.py
    adapters/
      polymarket/
        gamma.py
        clob.py
        ws.py
        user_ws.py
      external/
        oddspapi.py
        goalserve.py
        options_chain.py
        news.py
    strategies/
      base.py
      lead_lag/
      complement/
      coherence/
      fair_value/
      sports_state/
    services/
      discovery/
      market_graph/
      signal_engine/
      ranking/
      api_views/
    runtimes/
      cli/
      live_tui/
      batch_scan/
      api/
      replay/
```

## Bounded contexts

### Market graph

This is the most important new domain layer.

It should hold normalized concepts such as:
- event
- market
- outcome token
- market family
- semantic identity
- structural relationship

Relationship types should include:
- complement
- partition bucket
- implication
- by-date cascade
- parent event to child market
- cross-venue semantic equivalent
- external-reference equivalent

This replaces the current pattern where relationship logic is spread across:
- discovery matching
- coherence scanner family detection
- runtime-side token mapping

### Reference layer

External information sources should no longer feed strategy code directly.

Every reference source should emit a normalized `ReferenceObservation` with:
- source name
- entity it refers to
- timestamp
- freshness score
- confidence score
- raw payload pointer
- derived probabilities or features

Examples:
- OddsPapi moneyline
- options-chain implied probability
- live sports-state feed
- human-curated manual anchor

This is where unreliable feeds are downgraded instead of silently becoming bad strategy input.

### Signal layer

Every strategy evaluation should emit explicit signal records, not just trades.

Signal objects should include:
- strategy id
- market ids and token ids touched
- evaluation timestamp
- signal type
- raw edge or score
- explanation payload
- execution eligibility
- risk-block reason

This ledger is required for:
- replay
- false-positive analysis
- API output
- ranking across strategies

### Execution layer

Execution should remain Polymarket-specific for now, but live behind a cleaner interface.

Responsibilities:
- order intent creation
- order submission
- fill tracking
- reconciliation
- venue-specific error handling
- maker/taker cost accounting

The existing execution work is good enough to preserve, but it should be moved behind a venue adapter boundary.

### Risk layer

The current repo has trade-level guards. The target platform needs portfolio-level risk.

Add:
- per-strategy capital budgets
- per-family exposure caps
- per-topic concentration limits
- liquidity bucket limits
- unresolved semantic-overlap caps
- max simultaneous live strategies per market

## Strategy contract

Every strategy should implement a common contract.

```python
class Strategy(Protocol):
    strategy_id: str
    required_inputs: set[str]

    def discover(self, context: DiscoveryContext) -> list[Candidate]:
        ...

    def evaluate(self, context: EvalContext) -> list[Signal]:
        ...

    def rank(self, signals: list[Signal], context: RankContext) -> list[RankedSignal]:
        ...

    def explain(self, signal: Signal) -> dict:
        ...

    def execution_plan(self, signal: Signal, context: ExecContext) -> ExecutionPlan | None:
        ...
```

This allows:
- lead-lag to stay a strategy
- complement arb to stay a strategy
- coherence to become a first-class strategy
- fair-value scanners to expose both API analysis and live trade plans

## Target runtimes

### 1. Analysis API runtime

This is the runtime for customers and internal analysis tools.

Primary endpoints should be:
- market graph query
- fair-value estimate query
- coherence findings
- ranked opportunities
- signal history
- strategy explanations

This runtime should be read-only.

### 2. Trading runtime

This is the private trading system.

Primary responsibilities:
- subscribe to market state
- run strategy evaluators
- apply portfolio risk
- generate execution plans
- reconcile fills and balances

The current TUI should become one view over this runtime, not the runtime itself.

### 3. Batch scan runtime

Used for:
- coherence sweeps
- fair-value batch ranking
- overnight discovery
- historical backfills

### 4. Replay runtime

This is a required maturity component, not a nice-to-have.

It should support:
- historical market snapshots
- historical orderbook or approximated top-of-book state
- strategy evaluation replay
- fill simulation policies
- experiment result persistence

## Data model evolution

The current tables should mostly be retained, but the platform needs several new categories.

### Keep and preserve

These already contain real value:
- `fixtures`
- `mappings`
- `positions`
- `trade_events`
- `order_attempts`
- `odds_snapshots`
- `complement_arbs`

### Add

#### `market_entities`
- canonical event and market identities
- market type, scope, resolution window, venue ids

#### `market_relationships`
- complement, partition, implication, by-date, parent-child, cross-venue
- confidence and validation status

#### `reference_observations`
- normalized external or derived references
- freshness, confidence, provenance

#### `strategy_runs`
- runtime id, strategy id, mode, parameters, start/end times

#### `signals`
- every evaluated signal, traded or not

#### `ranked_opportunities`
- ranked, execution-aware snapshots for API and dashboards

#### `replay_runs`
- experiment metadata, versioning, metrics, assumptions

## Migration map from current code

### Current to target mapping

- `services/shared/polymarket_client.py`
  becomes `adapters/polymarket/gamma.py` and `adapters/polymarket/clob.py`
- `services/shared/clob_executor.py`
  becomes `domain/execution/` plus Polymarket venue adapter pieces
- `services/cli/discover.py`
  gets split across `services/discovery/`, `domain/markets/`, and adapter modules
- `services/cli/trader.py`
  gets split across strategy logic, signal engine, execution layer, and risk layer
- `services/cli/live.py`
  becomes a thin runtime/view shell over shared services
- `services/coherence/*`
  stays mostly intact at first, then moves into `strategies/coherence/` plus shared market graph utilities

## Strategy priority under this architecture

Because external live feeds are unreliable, the target architecture should prioritize:

### Tier 1: Polymarket-native
- coherence and combinatorial mispricing
- complement and partition arbitrage
- execution-quality edge
- market family math
- semantic identity and cross-market structure

### Tier 2: Optional external fair value
- options-chain based threshold market valuation
- sports reference pricing only when source confidence is high

### Tier 3: External state-driven strategies
- live sports-state inference
- news ingestion
- social or alternative data

Tier 3 should never define the architecture. It should plug into the architecture.

## Migration phases

### Phase 0: Stabilize the repo
- standardize packaging and imports
- remove dead worker references
- make tests runnable in one environment
- isolate secrets and config

### Phase 1: Extract shared domain services
- carve discovery, book math, execution, and risk out of the CLI files
- introduce a strategy registry and signal ledger

### Phase 2: Build the market graph
- unify coherence relationships with sports/event mappings
- persist structural relationships explicitly

### Phase 3: Build the analysis API
- expose normalized read-only views for opportunities, signals, and explanations

### Phase 4: Add replay and experiment registry
- make every strategy measurable

### Phase 5: Expand strategy families
- coherence in main runtime
- fair-value lane
- calibrated sports-state lane

## Canonical replacement policy

For future platform work, this document should be treated as the canonical architecture target.

The older files:
- `docs/architecture.md`
- `docs/project-plan.md`

should be treated as implementation-history documents until the restructure is complete.
