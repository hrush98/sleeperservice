# Ask Gina AI Agent Playbook: 18 Months of Real-Money Lessons: Full In-Depth Overview of the Post by @sidshekhar24

**Documented Post Summary & Technical Deep Dive**  
*Post ID: 2038651145841684811 (March 30, 2026)*  
*Author: Sid Shekhar (@sidshekhar24)*  
*Bio: Founder @askginadotai (your AI wallet) | built @thetokenanalyst (acq by @coinbase)*  
*Engagement (at fetch): 26 likes, 5 reposts, 5 quotes, 2 replies, 32 bookmarks, 4.1K+ views*  

This Markdown document provides a **complete, in-depth overview** of the X post exactly as documented by the author. It extracts every lesson, section, example, contrast (prompts vs. code), architecture insight, and practical pattern; formalizes any implied mathematical or engineering concepts using precise KaTeX notation where the post references them (e.g., position sizing, deterministic logic); and structures the content precisely following the original narrative flow (intro → prompts vs code → code-based execution → plan before code → atomic prompts → memory as files → system as edge → closing).  

The post is a **hard-won playbook** distilled from 18 months of building and running Ask Gina — one of the earliest AI agents executing real-capital transactions. It reports **$5M+ volume** and **100K+ transactions** across Polymarket, Hyperliquid perps, and 12+ blockchains. The core thesis: frontier models are table stakes; the **system** (harness, atomic steps, planning, memory, guardrails) is the real edge. It contrasts natural-language prompts (great for judgment) with code (required for exact math) and promotes their `/create` harness while giving away the patterns for anyone to apply. One minor reply exists but adds no substantive content.  

No explicit equations appear in the post, but the author repeatedly references **exact mathematical logic** (position sizing to basis points, multi-condition filters, deterministic sequencing) — formalized below where implied.

---

## Opening Thesis & Context
- Started building Ask Gina in early 2024 as one of the first AI agents that could execute transactions with **real capital**.  
- Results: $5M+ volume, 100K+ transactions across Polymarket, Hyperliquid perps, and 12+ blockchains.  
- Exposure: Hundreds of strategies, edge cases, and prompt variations — all with real money on the line.  
- Outcome: Completely changed how the team thinks about AI in finance (prompt structuring, memory, complex strategy logic in a robust harness).  
- Promise: “Here’s what 18 months of building agents that handle real money taught us.”

---

## Financial Automations: Prompts vs. Code
**Mid-2025 launch: “Recipes”**  
- Natural language prompt + schedule/trigger (daily 9am, every 5 min, BTC drops 10%, etc.).  
- Enabled everything from simple reports (“morning portfolio summary”) to advanced multi-step trading strategies.  

**When prompts were “enough”**  
- Strategy could be broken into steps a “sharp 15-year-old could read and execute reliably.”  
- Each step: clear input → clear output → zero ambiguity.  
- Multi-prompt workflows worked well for a surprisingly long time.

**Where prompts cracked**  
- Strategies requiring **exact math**:  
  - Position sizing to basis points  
  - Multi-condition filters with deterministic sequencing  
- Core limitation (verbatim): “A language model predicts the next token — that’s fundamentally at its core just a really good guess.”  
- High-stakes example: Scaling into a Hyperliquid perp with 5× leverage — you cannot afford “really good guesses” on exact math.

**Implied math contrast** (formalized for clarity):  
Position sizing must be **deterministic**, e.g.:  
$$
\text{size} = f(\text{account balance}, \text{risk per trade}, \text{volatility}, \text{edge})
$$  
A prompt might approximate; code executes the exact function every time.

---

## Code-Based Execution for Complex Mathematical Logic
**Early-2026 shift**: Coding agents became dramatically more reliable.  

**New paradigm**:  
- Best use of a frontier model in finance = **writing code that does the math**.  
- The model generates the code; the code runs as the automation logic.  

**Ask Gina’s `/create` harness** (opinionated framework):  
- Each automation = sequence of **discrete, executable steps**.  
- Example steps in one automation:  
  1. **Scanner**: find markets/assets/conditions matching criteria  
  2. **Filter**: apply logic, rank by signal, remove noise  
  3. **Executor**: take action and execute transactions/trades  
  4. **Monitor**: track outcome, log results, adjust  

**Key benefits**:  
- Each step isolated → tested independently before touching live capital.  
- **Deterministic**: does exactly what you told it, every time (Polymarket, Hyperliquid, Base spot swap, etc.).  
- No more token-prediction guesses on math-critical paths.

---

## Plan Before Code (Always)
**Critical guardrail**: Nobody reads code anymore (delegated to AI).  

**Process**:  
- Before writing a single line, Gina generates a **plain-English plan**:  
  - Goal  
  - Scope  
  - Steps  
  - Risks  
  - Open questions  
- User reads + approves **before** any code is built.  

**Real example** (verbatim):  
- User prompt: “buy if funding rate is negative.”  
- Plan flagged open question: “How negative?” (–0.001% vs. –2% treated identically).  
- Result: Caught bad logic in seconds; prevented countless bad entries and wasted credits.  

**Why it works**: Bad logic is obvious in bullet-point English. Much harder to spot in pure code.

---

## If You Use Prompts, Make Each One Atomic Then Combine
**Prompts are not going away** — perfect for:  
- Genuine interpretation  
- Reading news events  
- Assessing sentiment  
- Deciding if a situation is anomalous  

**The mistake**: Packing too much into one prompt.  

**Atomic principle** (good software engineering):  
- One prompt = **one job**  
- Runnable in isolation  
- Independently testable  
- Understandable by the smallest LLM  

**Workflow**: Build atomic steps separately → chain them into one overarching “strategy.”  
If one step fails, you know exactly why (no mysterious reasoning hops).

---

## Memory Is Just Files. Stop Overcomplicating It
**Problem**: Agents lack persistent memory by default (every session starts fresh).  
**Common overkill**: Vector DBs, RAG pipelines, elaborate context injection.  

**What actually works**: Treat the agent like a computer user.  
- Agents can **read and write files natively**.  
- That **is** your memory layer — simple, durable, no context-window dependency.  

**Proven workflow** (researching unfamiliar markets or evaluating strategies):  
1. Search for relevant market (e.g., HYPE, BTC 15m, NBA games).  
2. Ask agent to record key details to a file (liquidity, volatility, historical price action, notable events).  
3. Come back days later.  
4. Ask agent to open the file and analyze.  

→ Lightweight backtest without data pipelines or a quant team.  

**Gina implementation**: Every user gets a personal agent filesystem with **250 MB free storage**.  
Strategies that improve over time are the ones that **record key details in a file and keep learning from them**.

---

## The Model Is Table Stakes. The System Is the Edge.
- Every team has access to the same frontier models and APIs.  
- **Edge = infrastructure** around the model:  
  - Data access  
  - Execution structure  
  - Memory accumulation  
  - Guardrails (prevent expensive mistakes when conditions shift)  

**Hard-won lessons** (all learned with real money on the line — Gina moving several hundred thousand dollars a day):  
- Use **code for math**  
- Use **prompts for judgment**  
- Keep each unit **atomic**  
- **Plan before you build**  
- Give your agent a **filesystem** and let memory compound  

“None of this requires our platform. These are patterns that work across different sectors of agent-led work regardless of what you’re building.”

---

## Closing & Call to Action
- The playbook is simple but hard-won.  
- `/create` on askgina.ai/create encodes all of it into a harness that “just works out of the box without the scar tissue.”  

---

**Key Takeaway from the Post**  
After $5M+ real-capital volume and 100K+ transactions on Polymarket and beyond, the author’s core insight is that **frontier models are commoditized** — the durable edge lives in the **system**: atomic prompts for judgment + code for exact math + human-readable plans + filesystem memory + isolated testable steps. Prompts suffice for simple, interpretable tasks; anything involving deterministic math (position sizing to basis points, multi-condition filters) must be code. The `/create` harness and the five patterns (plan-first, atomic units, files-as-memory, etc.) turn fragile token-prediction agents into reliable financial automations. This is not theory — it was battle-tested moving hundreds of thousands of dollars daily. The post generously gives the entire playbook away for free while inviting users to try the encoded version.

**Note on Scope**  
Educational / architectural only. The post is a transparent share of production lessons from a live AI-agent product (Ask Gina) rather than a sales pitch; it explicitly states the patterns work independently of their platform. No financial advice; real-money execution carries risk. One reply in the thread is a simple emoji reaction with no added insight.

This document captures the **entire post** (including every lesson, example, contrast, and practical pattern) with zero omissions and full technical rigor. Copy-paste into any `.md` file for offline reference or further annotation.
