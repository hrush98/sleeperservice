# Research Reference Library

## Purpose

This file is the working reference shelf for the platform buildout.

It lists:
- official Polymarket docs
- academic papers
- related finance references
- how each source is useful for this repo
- where to be careful

Use this document as the canonical reference index when building new strategy or architecture work.

## Official platform docs

### 1. Polymarket Trading Overview
- Link: https://docs.polymarket.com/trading/overview
- Type: official platform documentation
- Main content:
  - describes Polymarket as a CLOB
  - offchain order matching with onchain settlement
  - non-custodial trading model
  - EIP-712 signed orders and venue auth model
- Why it matters here:
  - this is the correct venue model for the current repo
  - it is the reference point when judging whether LMSR-style guide claims actually apply
- How to use it:
  - execution design
  - venue assumptions
  - API-product explanations

### 2. Polymarket Orders Overview
- Link: https://docs.polymarket.com/trading/orders/overview
- Type: official platform documentation
- Main content:
  - all orders are expressed as limit orders
  - FOK, FAK, GTC, GTD semantics
  - tick sizes
  - allowance model
  - trade lifecycle states
- Why it matters here:
  - directly relevant to `clob_executor.py`
  - supports the repo's execution and reconciliation model
- How to use it:
  - fill simulation
  - execution plans
  - order-state normalization

### 3. Polymarket Order Book Primer
- Link: https://docs.polymarket.com/polymarket-learn/trading/using-the-orderbook
- Type: official educational documentation
- Main content:
  - order book structure
  - bids, asks, spreads, and limit orders
- Why it matters here:
  - useful for API explanations and user-facing analytics
  - reinforces that execution quality and slippage are first-class

## Prediction-market and market-structure papers

### 4. Robin Hanson - Market Scoring Rules
- Link: https://hanson.gmu.edu/mktscore.pdf
- Type: foundational market-design paper
- Main content:
  - proper scoring rules
  - market scoring rules
  - automated market maker framing
  - logarithmic scoring-rule foundations
- Why it matters here:
  - good conceptual background for AMM-style prediction markets
  - helpful when evaluating guide material about LMSR
- Caution:
  - this is not a description of the current Polymarket CLOB microstructure
- Recommended use:
  - conceptual background
  - guide assessment
  - market-design intuition

### 5. Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets
- Link: https://arxiv.org/abs/2508.03474
- Type: recent prediction-market arbitrage paper
- Main content:
  - empirical analysis of arbitrage on Polymarket
  - distinguishes market rebalancing arbitrage and combinatorial arbitrage
  - uses historical order book data
  - reports material realized profit extraction
- Why it matters here:
  - strongest external validation for the coherence and structural-arb direction
  - directly supports a Polymarket-native strategy thesis
- Recommended use:
  - coherence roadmap
  - market graph design
  - opportunity ranking assumptions

### 6. Semantic Non-Fungibility and Violations of the Law of One Price in Prediction Markets
- Link: https://arxiv.org/abs/2601.01706
- Type: recent cross-platform prediction-market paper
- Main content:
  - event identity fragmentation across venues
  - semantic alignment framework
  - human-validated cross-platform dataset
  - persistent 2-4% execution-aware mispricings for equivalent events
- Why it matters here:
  - validates semantic identity as a real source of edge
  - supports building a market-relationship graph, not just text matching
- Recommended use:
  - semantic matching system
  - cross-venue future roadmap
  - API explanations around equivalent markets

### 7. Toward Black Scholes for Prediction Markets: A Unified Kernel and Market Maker's Handbook
- Link: https://arxiv.org/abs/2510.15205
- Type: recent prediction-market modeling preprint
- Main content:
  - proposes a logit jump-diffusion kernel for traded probabilities
  - introduces a belief-volatility style framework
  - discusses calibration and derivative-style layers for prediction markets
- Why it matters here:
  - useful as a more sophisticated theoretical lane for fair-value and volatility-style thinking
  - supports a future probability-surface approach
- Caution:
  - this is a preprint and not a direct recipe for immediate live edge
- Recommended use:
  - medium-term fair-value and risk modeling
  - theoretical background for probability-surface analytics

### 8. Arbitrage-Free Combinatorial Market Making via Integer Programming
- Link: https://www.columbia.edu/~ck2945/papers/milp_market.pdf
- Type: combinatorial prediction-market paper
- Main content:
  - arbitrage-free combinatorial market making
  - integer-program based pricing consistency
  - real-world combinatorial market experiments
- Why it matters here:
  - supports the idea that market-family consistency should be encoded structurally
  - useful if the platform expands deeper into partition and event-tree logic
- Recommended use:
  - coherence roadmap
  - future partition and event-tree checks

## Finance and arbitrage references

### 9. Neural Networks Can Detect Model-Free Static Arbitrage Strategies
- Link: https://arxiv.org/abs/2306.16422
- Type: finance/arbitrage paper
- Main content:
  - neural networks for static arbitrage detection
  - model-free arbitrage detection in high-dimensional settings
  - convex semi-infinite program approximation
- Why it matters here:
  - useful as a reference for future ranking or detection models in structured markets
- Caution:
  - this is about financial static arbitrage more generally, not prediction markets specifically
  - should not be treated as immediate justification for a generic Polymarket LSTM
- Recommended use:
  - later-stage detector design
  - not an early roadmap item

### 10. Damodaran note on risk-neutral probability and N(d2)
- Link: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/eqemailspr20.html
- Type: finance teaching note
- Main content:
  - practical interpretation of option pricing terms
  - risk-neutral probability intuition
  - why `N(d2)` appears in digital or threshold-style pricing discussions
- Why it matters here:
  - useful bridge for threshold-market fair-value work
  - good explanatory reference for an analysis API
- Caution:
  - risk-neutral probability is not automatically the same thing as true real-world probability
- Recommended use:
  - fair-value API explanations
  - stock or crypto threshold market modeling

## Guide-derived references and judgments

### Guide 1 - LMSR / EV / Kelly thread
- Repo file: `docs/strategy_ref/guide1.md`
- Keep:
  - EV framing
  - Bayes
  - Kelly and fractional Kelly discipline
- Do not copy directly:
  - LMSR as a description of current Polymarket trading
- Use as:
  - trader-discipline reference

### Guide 2 - LSTM directional bot thread
- Repo file: `docs/strategy_ref/guide2.md`
- Keep:
  - general reminder that confidence estimation matters
- Main caution:
  - weak structural grounding for this repo
  - high leakage and overfitting risk
- Use as:
  - low-priority inspiration only

### Guide 3 - Domer / EV / Kelly / Bayes thread
- Repo file: `docs/strategy_ref/guide3.md`
- Keep:
  - process discipline
  - sizing discipline
  - Bayesian thinking
- Use as:
  - operator mindset reference

### Guide 4 - Black-Scholes on threshold markets
- Repo file: `docs/strategy_ref/guide4.md`
- Keep:
  - threshold markets as digital-option-like products
  - fair-value benchmark idea
- Main caution:
  - model risk and calibration still matter
- Use as:
  - one of the best future expansion lanes

### Guide 5 - Polymarket arbitrage guide
- Repo file: `docs/strategy_ref/guide5.md`
- Keep:
  - combinatorial and structural arb focus
  - execution-aware caution
- Use as:
  - strongest expansion direction alongside coherence

## How these references map to the platform

### For architecture
- Polymarket docs
- semantic non-fungibility paper
- arbitrage in prediction markets paper

### For strategy expansion
- arbitrage in prediction markets paper
- semantic non-fungibility paper
- Black-Scholes style prediction-market paper
- combinatorial market-making paper

### For trader process and risk discipline
- Hanson scoring-rule background
- EV / Bayes / Kelly guide material
- Damodaran note for threshold-probability explanations

## Recommended reading order

1. Polymarket trading overview
2. Polymarket orders overview
3. Unravelling the Probabilistic Forest
4. Semantic Non-Fungibility
5. Guide 5
6. Guide 4
7. Hanson market scoring rules
8. Toward Black Scholes for Prediction Markets

## Notes

- Some papers above are recent preprints. Treat them as research inputs, not settled truth.
- Official Polymarket docs should win whenever there is a conflict about venue mechanics.
- Social threads should be treated as hypothesis generators, not evidence by themselves.
