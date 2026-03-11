# Polymarket Arbitrage Bots: Why Normies Lose + Full Blueprint Guide

**Documented Post Summary & Technical Deep Dive**  
*Post ID: 2024846407576801783 (February 20, 2026)*  
*Author: Oracle Boar (@bored2boar)*  
*Bio: 𝗢𝗻-𝗖𝗵𝗮𝗶𝗻 𝗜𝗻𝘃𝗲𝘀𝘁𝗶𝗴𝗮𝘁𝗶𝗼𝗻𝘀 / 𝗣𝗿𝗲𝗱𝗶𝗰𝘁𝗶𝗼𝗻 𝗺𝗮𝗿𝗸𝗲𝘁𝘀 / 𝗗𝗲𝗙𝗶 𝗺𝗼𝘃𝗲𝘀*  
*Engagement (at fetch): 776 likes, 88 reposts, 345K+ views*  

This Markdown document provides a **complete, in-depth overview** of the X post exactly as documented by the author. It extracts every phase, concept, chart reference, paper citation, and code hint; formalizes all implied mathematics using precise KaTeX notation; expands on arbitrage conditions, integer programming, and neural detection where the post implies them; and structures the content precisely following the original flow.  

The post is a **comprehensive educational blueprint** explaining why most retail “arb bots” lose money on Polymarket (despite bots capturing the biggest profits) and then delivers a full stack guide: theory → papers → 3 arbitrage types → tech stack → detection logic → execution → risk management. The author reveals his own bot has already generated **$7,800+** and shares the exact arXiv papers and APIs he used.  

It is framed as “one guide that actually helps” for serious builders (not beginners who copy-paste Claude output). Full disclaimer: educational only; real arbitrage requires fees/slippage simulation and is competitive.

---

## Opening Thesis: Bots Win, Normies Lose
- Trading bots extract the largest profits on Polymarket.  
- Every normie now tries to build one → universal result: **LOSS**.  
- Root cause: “Real bots are created by people who studied hard before building.”  
- One guide or LLM will never suffice for beginners.  
- The author’s solution: this complete guide (theory-first, not code-first).

---

## Polymarket Fundamentals (Prices = Collective Probability)
Polymarket lets users bet on real-world events.  
Contract prices reflect the market’s implied probability:  
- YES at $0.60 → market believes 60% chance.  

In a binary market:  
$$
p_{\text{YES}} + p_{\text{NO}} = 1
$$  
(ideal no-arbitrage condition).  

Deviations occur due to liquidity gaps, execution delays, or linked conditions → **risk-free profit opportunity** (in theory).  

**Classic example** (post verbatim):  
- YES = $0.50, NO = $0.49 → total = $0.99  
- Buy both for $0.99 → guaranteed $1 payout → **+1¢ instant profit** (before fees).  

**Warning**: “Not free money” — Polygon gas (0.5–1%), slippage, failed tx, and latency can erase the edge.

---

## Why Theory Matters First (Recommended Reading)
Before any code, study the academic foundation. The post cites four key papers with direct links:

1. **“Arbitrage in Prediction Markets”** — analyzes real Polymarket data; shows persistent arb in single- and multi-condition markets.  
2. **“Semantic Non-Fungibility and Violations of the Law of One Price in Prediction Markets”** — 2–4% cross-platform deviations due to fragmented liquidity.  
3. **“Arbitrage-Free Combinatorial Market Making”** — integer programming for consistent pricing across event trees (e.g., tournament brackets).  
4. **“Neural Networks for Static Arbitrage”** (full title in post: “NEURAL NETWORKS CAN DETECT MODEL-FREE STATIC ARBITRAGE STRATEGIES”) — ML detects arb without hardcoded rules.

**Volume reality check** (post charts referenced):  
Politics and sports dominate volume (example: Fed decision market at **$145M** volume). Higher volume = more frequent mispricings.

---

## Core Arbitrage Types on Polymarket (3 Categories)
Polymarket runs on Polygon with AMM liquidity (YES/NO tokens vs USDC).

### 1. Intra-Market Arbitrage (Single-Condition)
Simplest: YES + NO ≠ 1.  
Deviation formula (post threshold):  
$$
\text{Deviation} = |p_{\text{YES}} + p_{\text{NO}} - 1| > 0.02 + \text{fees}
$$  
- VWAP price charts show gaps often from execution lag.  
- Median profit > 2¢ in crypto-heavy markets.  
- High-frequency, small-edge game.

### 2. Multi-Condition / Combinatorial Arbitrage
Bundled outcomes (e.g., tournament brackets).  
Use **integer programming** to enforce:  
- If Team A wins semifinal → must be priced consistently in final.  
- Objective: maximize profit subject to no-loss constraints across the tree.

### 3. Semantic / Cross-Platform Arbitrage
Same real-world event priced differently (Polymarket vs Kalshi/Manifold).  
- 2–4% drifts common (paper-backed).  
- Detect with NLP / semantic similarity (HuggingFace transformers or OpenAI API).

---

## Machine-Learning Detection (Scalable Edge)
Hard-coded rules miss complex edges.  
Post references the neural-net paper: trains on price vectors → outputs “arb / no-arb”.  
**Model input**: price vectors + volume + spread.  
**Target**: ~90% recall.  
High-dimensional detection beats rules.

---

## Tech Stack & Environment Setup (Exact Commands)
**Core tools** (post verbatim):  
- Python 3.12+  
- `web3.py`, `requests`, `pandas`, `numpy`, `scipy`, `torch` (ML), `PuLP` (integer prog), `asyncio`  
- HuggingFace transformers (semantic)  

Install:  
```bash
pip install web3 requests pandas torch pulp transformers asyncio
