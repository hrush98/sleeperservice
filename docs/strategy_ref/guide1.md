# Polymarket Trading Blueprint: Full In-Depth Overview of the Thread by @LunarResearcher

**Documented Thread Summary & Mathematical Deep Dive**  
*Post ID: 2031454281572954438 (March 10, 2026)*  
*Author: Lunar (@LunarResearcher)*  

This Markdown document provides a complete, in-depth overview of the X thread. It extracts every core concept, formalizes all mathematics using precise notation (rendered in KaTeX), expands on the examples with derivations where the thread implies them, and structures the content exactly as the original post organizes it. The thread documents why ~87% of Polymarket traders lose money—not due to rigging or whales, but because they ignore the pure mathematical system powering the platform: the Logarithmic Market Scoring Rule (LMSR), Expected Value (EV) modeling, cognitive bias mitigation, and Kelly Criterion position sizing.  

The thread is framed as a "complete blueprint" reverse-engineered from top 1% wallets. It contrasts vibe-based gambling with rigorous probability trading. All formulas below are taken directly from the thread and presented in standard mathematical form for clarity.

---

## Part 1: How Polymarket Actually Prices Your Beliefs

### The Problem Traditional Order Books Cannot Solve
Conventional markets (e.g., stocks, crypto) rely on order books: buyers and sellers post limit orders and match at an equilibrium price. This fails for niche prediction markets ("Will Ecuador's president survive impeachment?") because liquidity is thin—no continuous bids/offers exist.  

Polymarket solves this with an **Automated Market Maker (AMM)** based on Robin Hanson's 2002 Logarithmic Market Scoring Rule (LMSR). The AMM always quotes two-sided prices, accepts any size trade, and guarantees liquidity.

### The Core LMSR Cost Function
Instead of matching counterparties, traders buy/sell *against a cost function* maintained by the market maker.  

The instantaneous cost to purchase a bundle of shares is:

$$
C(\mathbf{q}) = b \cdot \ln \left( \sum_{i=1}^{n} \exp\left(\frac{q_i}{b}\right) \right)
$$

where:  
- \(\mathbf{q} = (q_1, q_2, \dots, q_n)\): vector of outstanding shares purchased for each outcome \(i\)  
- \(b > 0\): liquidity parameter (controls market depth)  
- \(n\): number of mutually exclusive outcomes (usually 2 for binary markets)  

The **price** of outcome \(i\) (i.e., the marginal cost of one additional share) is the partial derivative \(\frac{\partial C}{\partial q_i}\):

$$
p_i = \frac{\exp\left(\frac{q_i}{b}\right)}{\sum_{j=1}^{n} \exp\left(\frac{q_j}{b}\right)}
$$

This is **identical to the softmax function** used in the final layer of neural networks for probability outputs. Polymarket prices are therefore mathematically equivalent to a neural network's belief distribution over outcomes.

### Numerical Example from the Thread ("Will it rain tomorrow?")
- Outcomes: Yes (Y), No (N)  
- Initial state: \(\mathbf{q} = [0, 0]\), \(b = 100\)  
- Initial prices: \(p_Y = p_N = 0.5\) (50¢)  

After buying 50 Yes shares:  
- New \(\mathbf{q} = [50, 0]\)  
- New prices (computed via softmax):  
  $$
  p_Y = \frac{\exp(50/100)}{\exp(50/100) + \exp(0/100)} \approx 0.622  
  $$
  $$
  p_N = 1 - p_Y \approx 0.378  
  $$

Buying Yes pushes its price up (and No down) automatically. The thread notes this creates a "price impact curve"—your own purchases degrade your average fill price.

### Role of the Liquidity Parameter \(b\)
- **Small \(b\)** (e.g., 50): Prices move violently. A $5K bet can swing a market 20%. High volatility, low depth.  
- **Large \(b\)** (e.g., 100,000): Prices move slowly. Requires massive capital to shift odds. Deep, stable markets.  

**Maximum theoretical loss to the market maker** (subsidy provided by Polymarket to ensure liquidity) for a binary market:

$$
\text{Max Loss} = b \cdot \ln(2) \approx 0.693147 \cdot b
$$

Example: \(b = 100{,}000\) → max loss ≈ $69{,}315. This is the "cost of truth discovery."

---

## Part 2: Expected Value – The One Formula That Runs Everything

Any trade's attractiveness is determined by **Expected Value (EV)**:

$$
\text{EV} = \sum_{i} \Pr(\text{outcome}_i) \cdot \text{payoff}_i
$$

If EV > 0, the trade has positive edge (in the long run you profit).

### Classic Coin-Flip Example (Loss Aversion Demonstration)
- Heads: +$150 (your friend pays you)  
- Tails: –$100 (you pay friend)  
- Assume fair coin: \(\Pr(H) = \Pr(T) = 0.5\)

$$
\text{EV} = (0.5 \times 150) + (0.5 \times (-100)) = 75 - 50 = +25
$$

Average profit: +$25 per flip. Over 100 flips: ~+$2,500. Yet most people reject it due to **loss aversion** (Kahneman & Tversky prospect theory: losses hurt ~2× more than equivalent gains).

### Applying EV to Polymarket (with Price Impact)
Market price: 40¢ (implied \(\Pr(\text{YES}) = 40\%\))  
Your researched true probability: 60%  

Naive edge per $1 risked: \(0.60 - 0.40 = 0.20\) (20¢).  

However, buying moves the price. The thread emphasizes modeling the **impact curve** (derived from the LMSR price function above). Pros compute exactly how many shares they can buy before EV drops to zero.

---

## Part 3: The 5 Mental Traps That Destroy Polymarket Traders

The thread lists five hardwired cognitive biases and supplies the mathematical antidote for each.

### 1. Base Rate Neglect
Classic medical example (thread verbatim):  
Disease prevalence = 1/1,000. Test accuracy = 99%. Positive test.  

Bayes' theorem:

$$
\Pr(\text{sick} \mid +) = \frac{\Pr(+ \mid \text{sick}) \cdot \Pr(\text{sick})}{\Pr(+)}
$$

- True positives: 1  
- False positives: ~10 (0.01 × 999)  
- \(\Pr(\text{sick} \mid +) = 1/11 \approx 9\%\) (not 99%).

**Polymarket application**: "Candidate won primary → guaranteed general win" ignores historical base rates. "85¢ contract = guaranteed" ignores that 85¢ markets still resolve No ~15% of the time.

### 2. Sunk Cost Fallacy
No formula, but rule: Ignore your entry price. Ask only: "If I had cash right now, would I buy at current price?" If no → sell immediately. The thread's example: bought at 70¢, now 40¢ with new evidence favoring No → sell regardless of the 30¢ loss.

### 3. Survivorship Bias
87% of Polymarket wallets lose money (thread statistic). Twitter shows only winners. Antidote: always demand the denominator ("How many people tried this? What fraction succeeded?").

### 4. Bad Bayesian Updating
Correct way to revise beliefs:

$$
\Pr(H \mid E) = \frac{\Pr(E \mid H) \cdot \Pr(H)}{\Pr(E)}
$$

Start with a prior \(\Pr(H)\), update proportionally to likelihood \(\Pr(E \mid H)\). Do **not** overreact to one poll or cling to an initial opinion. The thread notes Polymarket's 2024 election accuracy came from thousands of traders performing this update in aggregate.

### 5. Anti-Kelly Position Sizing
Kelly Criterion (optimal fraction \(f^*\) of bankroll):

$$
f^* = \frac{bp - q}{b}
$$

where:  
- \(p\): probability of winning  
- \(q = 1 - p\)  
- \(b\): net odds (payout multiple – 1; for even-money = 1)  

**Example** (thread): 60% win probability, even-money payout (\(b=1\)):

$$
f^* = \frac{1 \cdot 0.6 - 0.4}{1} = 0.20 \quad (20\% \text{ of bankroll})
$$

**Reality check**: Full Kelly has ruinous variance. Professionals use **quarter-Kelly to half-Kelly** (5–10% in the example). This survives drawdowns while still compounding.

---

## Part 4: The Edge Calculator (Practical Tool)
The thread references a "practical tool" combining LMSR price impact, your probability estimate, bankroll, and Kelly sizing. While no explicit code is shown, it implicitly solves:

1. Compute current market price \(p_{\text{market}}\) from LMSR.  
2. Estimate true \(p_{\text{true}}\).  
3. Simulate buying \(x\) shares → new price \(p_{\text{new}}(x)\).  
4. Compute marginal EV at each increment.  
5. Size at quarter-Kelly on the average EV across the fill.

(Top traders run this mentally or via scripts before every trade.)

---

## Part 5: The Complete System & The Uncomfortable Truth

The thread synthesizes everything into a single decision framework:

- **LMSR** → mechanical pricing & impact curve  
- **EV** → filter for positive-edge trades  
- **Base rates** → ground priors in reality  
- **Bayesian updating** → revise beliefs proportionally  
- **Survivorship & sunk-cost awareness** → ignore noise & past costs  
- **Kelly sizing** → survive long enough to compound  

**Key takeaway (thread verbatim)**:  
"The top Polymarket traders aren't smarter. They have better math."  

87% of users treat it like a casino. The 13% who treat it as a mathematical system quietly compound. The edge is public (Wikipedia formulas, Python implementations) but requires actually doing the calculations instead of trading on vibes.

---

**Closing Note from the Thread**  
"Bookmark it. The math doesn't change, but your understanding of it will deepen every time you re-read it after a real trade."

**Sources & Further Reading (as referenced)**  
- Robin Hanson (2002) – LMSR paper  
- Wikipedia: Logarithmic market scoring rule  
- Kahneman & Tversky – Prospect Theory  
- Kelly Criterion (1956)  

This document captures the entire thread with zero omissions and full mathematical rigor. Copy-paste into any `.md` file for offline reference.
