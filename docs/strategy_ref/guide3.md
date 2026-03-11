# Domer's $3M Polymarket Blueprint: Full In-Depth Overview of the Post by @DenisKursakov

**Documented Post Summary & Mathematical Deep Dive**  
*Post ID: 2031008640862163147 (March 9, 2026)*  
*Author: Denis Kursakov (@DenisKursakov)*  
*Bio: Prediction arc | Research: @Polymarket | Member: @ZscDao*  
*Engagement (at fetch): 611 likes, 576K+ views*  

This Markdown document provides a complete, in-depth overview of the X post exactly as documented by the author. It extracts every core concept, formalizes all mathematics using precise notation (rendered in KaTeX), expands on the poker-to-Prediction-Market parallels and examples with derivations where the post implies them, and structures the content precisely following the original sections.  

The post profiles “Domer” (Polymarket handle **ImJustKen**, wallet `0x9d84ce0306f8551e02efef1680475fc0f1dc1344`), a former professional poker player who turned probability, research, and discipline into **~$3 million profit** on Polymarket (publicly verifiable on the leaderboard). He wagered **>$400 million** across **10,000+ positions**. Featured on CBS *60 Minutes* with Anderson Cooper (Nov 2025).  

The post frames Domer’s edge as a **mental framework** transferred from poker: EV-positive bets + Kelly sizing + Bayesian updating + iron discipline. It ends with a practical “copy trading” recommendation using the author’s affiliate tool (Predictr).  

All formulas are taken directly from the post and presented in standard mathematical form. The post explicitly states these are **foundational tools used by pros for decades**, not Domer inventions.

---

## Introduction & Domer’s Profile
- **Morning routine** (direct quote to Anderson Cooper): “I look at my phone within five seconds of waking up. ‘What have I missed?’ My phone is my coffee. The news doesn’t sleep.”
- **Self-description**: “I don’t think of myself as a gambler. I’m taking very, very well-researched views on things. I feel it’s much more akin to investing.”
- **Career path**: Pro poker (mid-2000s golden era) → movie box-office betting → Intrade → Polymarket (joined early 2021).
- **2024 performance**: Nearly **$3 million profit**.
- **Signature quote** (Oct 2024 On Chain Times interview):  
  > “I think I might be losing slightly more bets than I am winning, but winning more money than I am losing. I’ll let you puzzle that one out.”  
  (This is the **EV red pill** — win rate < 50% is fine if edge is large.)

---

## Why Poker and Polymarket Are the Same Game
Every decision must be evaluated on **probability-weighted expected value**, sized appropriately, and executed without emotion.  
The post states: “Prediction markets are basically slow-motion poker hands where you can out-research your opponents.”

---

## Three Formulas Behind Every Good Prediction Market Trade

### 1. Expected Value (EV) – The Only Number That Matters
On Polymarket, a YES contract pays **$1** if the event happens, **$0** otherwise. You pay the current market price `price` to buy it.

$$
\text{EV per \$1 risked} = p - \text{price}
$$

where:  
- \( p \): your researched true probability (0 to 1)  
- `price`: current YES contract price (market’s implied probability)

**Vance VP trade example** (real Domer bet, placed 5 months early):  
- Domer’s estimate: \( p \approx 0.25 \) (25 %)  
- Market price: \( 0.02 \) (2 %)  
- EV = \( 0.25 - 0.02 = +0.23 \) → **+23¢ per dollar** risked (massive edge).

**Note from post**: This simplified formula is **exact** for Polymarket binary contracts because the NO side also pays $1 if it wins.

### 2. Kelly Criterion – How Much to Bet
The Kelly formula (1956) gives the optimal fraction \( f^* \) of your bankroll to wager:

$$
f^* = \frac{b p - q}{b}
$$

where:  
- \( p \): your estimated win probability  
- \( q = 1 - p \): loss probability  
- \( b \): net odds = \(\frac{1 - \text{price}}{\text{price}}\) (profit per $1 risked if you win)

**Vance example** (post numbers):  
- \( p = 0.25 \), price = 0.02 → \( b = \frac{0.98}{0.02} = 49 \)  
- Full Kelly: \( f^* = \frac{49 \times 0.25 - 0.75}{49} = 0.236 \) (23.6 % of bankroll)  

**Professional adjustment** (explicitly stated):  
Pros use **¼ Kelly to ½ Kelly** (5–12 % of bankroll) because probability estimates are never perfect.  
Example: ½ Kelly on Vance → ~11.8 % of bankroll. A $4,000 bet implies ~$34K total bankroll at the time.

### 3. Bayesian Updating – How to Think About New Information
Poker players update hand ranges with every card. Domer does the same with news.

Standard Bayes’ theorem:

$$
P(A \mid B) = \frac{P(B \mid A) \cdot P(A)}{P(B)}
$$

where:  
- \( P(A) \): prior probability  
- \( P(B \mid A) \): likelihood of new signal if A is true  
- \( P(B) \): total probability of the signal  

**Vance illustrative walkthrough** (post):  
- Prior \( P(\text{Vance}) = 0.05 \)  
- New signal: Trump praises Vance at rally  
- \( P(\text{signal} \mid \text{Vance wins}) = 0.70 \)  
- \( P(\text{signal} \mid \text{Vance loses}) = 0.30 \)  
- \( P(\text{signal}) \approx 0.32 \)  
- Posterior \( P(\text{Vance} \mid \text{signal}) \) is then recomputed and used to re-evaluate EV.

---

## Crowd vs. Evidence: The Profit Gap
Domer’s biggest wins came from markets where the crowd was anchored to media narrative rather than base rates.  

**The Two-Column Rule** (post’s mental model):  
| Crowd Narrative                  | Evidence / Base Rates             |  
|----------------------------------|-----------------------------------|  
| “Media says X is impossible”     | Historical precedents + research  |  
| High price from hype             | Your researched probability       |

---

## Discipline: The Domer Principles (Direct Quotes & Sources)
The post lists these as publicly stated by Domer (CBS interview, On Chain Times Oct 2024, his X posts):

1. **Bet only in accordance with your edge**  
   “If you don’t find an edge, don’t bet. If you find a big edge, bet a lot.”

2. **Out-research, don’t out-react**  
   Biggest wins (Vance, Pope) were placed months early after deep research.

3. **High liquidity can trap you**  
   “High liquidity can be a bad thing if you wind up betting more than you should just because you can.”

4. **Subject-matter experts can be wrong**  
   Experts overweight their own field. Domer deliberately plays unfamiliar topics where everyone starts equal.

5. **Sound out your bets**  
   “I’m smart, but not all the time… talk to smart people and get a reality check.”

6. **Process > Outcome**  
   Lost CZ prison bet: “It was a phenomenal bet, even though it lost.”

---

## Practical Copy-Trading Section (Author’s Recommendation)
“No need to reinvent the wheel. You can simply copy what already exists.”

- Select traders with **good PNL** and **100–300 predictions/month** (avoid 1,000+/month bots).  
- Tool recommended: **Predictr** (Telegram bot)  
  Link: [https://t.me/predictr_trade_bot?start=ref_deniskursakov](https://t.me/predictr_trade_bot?start=ref_deniskursakov)  
- How to use:  
  1. Open app → “Create Copy Trade”  
  2. Paste wallet address (examples given):  
     - Domer: `0x9d84ce0306f8551e02efef1680475fc0f1dc1344`  
     - Huhaoli (non-bot): `0xf19d7d88cf362110027dcd64750fdd209a04276f`  
  3. Set percentage (e.g., 10 % → $350 trader bet = your $35 bet)  
  4. Test on small balance first.

Author offers to post more top traders in his profile.

---

## Closing “Domer Principle” (Post Verbatim)
> Prediction markets are not casinos. There is no house edge. Your counterparty is other participants.  
> “If you research more carefully, estimate probabilities more accurately, and size positions more rationally — you will extract value from those who don’t. Not on every bet. But over a large sample: consistently.”

---

**Key Takeaway from the Post**  
Domer’s edge is **not secret alpha** — it is publicly documented probability math + poker discipline executed at scale. The 87 % of losing traders (referenced in related Polymarket threads) ignore these exact formulas. The post’s message: copy the math, copy the process, or copy the man himself via Predictr.

**Sources Referenced in Post**  
- CBS *60 Minutes* interview (Nov 2025)  
- On Chain Times interview (Oct 2024)  
- Domer’s public X activity and Polymarket leaderboard  

This document captures the **entire post** with zero omissions and full mathematical rigor (including all examples and the copy-trading instructions). Copy-paste into any `.md` file for offline reference or further annotation.
