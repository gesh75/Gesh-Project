# GESH Financial — Terms of Service & Disclaimer

> **DRAFT — not yet legally reviewed.** Have a qualified securities/fintech attorney
> review this before charging any user or going public. This is template language
> adapted for a self-hosted quantitative research tool; it is **not** legal advice.
>
> Last updated: 2026-06-03 · Owner: Georgi Gaydarov (georgy75@gmail.com)

---

## 1. What GESH Financial Is

GESH Financial ("GESH", "the Service", "the Software") is a **self-hosted quantitative
research and educational tool**. It computes and displays research signals — consensus
scores, market-regime classifications, position-sizing math (Kelly), Monte-Carlo risk
ranges, and simulated ("paper") trade outcomes — from public market data.

GESH outputs are **research metrics and educational analysis**, in the same category as
an RSI reading, an ADX value, or a backtest result. They are **information**, not
instructions.

## 2. NOT FINANCIAL ADVICE

**GESH does not provide financial, investment, tax, or legal advice.** Nothing the
Service displays is a recommendation, solicitation, or offer to buy or sell any security
or other financial instrument, nor an endorsement that any transaction is suitable for
you.

- GESH is **not** a registered investment adviser (RIA) with the U.S. Securities and
  Exchange Commission or with any state securities regulator, and it does **not** purport
  to be one.
- GESH owes you **no fiduciary duty**. It does not know your financial situation,
  objectives, or risk tolerance, and does not tailor any output to you personally.
- You are **solely responsible** for your own investment decisions and for any orders you
  place in your own brokerage account. GESH never places, routes, or executes live orders
  in your account.

## 3. No Investment-Adviser Relationship

GESH is distributed as impersonal research of general and regular circulation. Use of the
Service does **not** create an advisory, brokerage, agency, or fiduciary relationship
between you and the operator of GESH. The operator does not exercise discretion over, and
has no access to, your funds or accounts.

## 4. Hypothetical & Simulated Performance

Any "track record", win-rate, Sharpe ratio, drawdown, equity curve, or other performance
figure shown by GESH is derived from **HYPOTHETICAL / SIMULATED paper trades**, not from
real-money trading, unless explicitly and prominently labeled otherwise.

**Hypothetical performance has inherent limitations.** It is prepared with the benefit of
hindsight, does not involve financial risk, and cannot fully account for the impact of
real-market factors (liquidity, slippage beyond modeled amounts, fills, fees, emotions, or
the effect of capital actually being at risk) on decision-making.

**PAST PERFORMANCE — INCLUDING SIMULATED PAST PERFORMANCE — DOES NOT GUARANTEE OR INDICATE
FUTURE RESULTS.** No representation is made that any account will or is likely to achieve
results similar to those shown.

## 5. Risk Disclosure

Trading and investing involve **substantial risk of loss** and are not suitable for every
person. You can lose some or all of your capital. Leverage, options, and crypto assets can
increase both gains and losses. Do not trade with money you cannot afford to lose. Consult
a licensed professional before making investment decisions.

## 6. Data & Accuracy

Market data, fundamentals, news, and signals are sourced from third parties (e.g.
yfinance, FRED, broker APIs) and may be delayed, incomplete, or inaccurate. GESH makes no
warranty as to the accuracy, completeness, timeliness, or fitness of any data or signal,
and is not liable for errors, omissions, or outages in third-party data.

## 7. AI-Generated Content

GESH uses large language models (including Anthropic Claude) to summarize and reason about
market data. AI output may be incomplete or wrong, can "hallucinate", and is provided for
research and educational purposes only. GESH is **not** an "AI financial advisor" and does
not make forecasts or guarantees of any kind.

## 8. Software "As Is" / Limitation of Liability

The Software is provided **"AS IS", without warranty of any kind**, express or implied,
including merchantability, fitness for a particular purpose, and non-infringement. To the
maximum extent permitted by law, the operator of GESH is not liable for any direct,
indirect, incidental, consequential, or special damages, or for any trading losses,
arising from your use of (or inability to use) the Service.

*Nothing in these Terms waives, limits, or disclaims any right or cause of action you may
have under the federal securities laws, the Investment Advisers Act of 1940, or any state
securities law to the extent such a waiver would be void under those laws.*

## 9. Acceptable Use

GESH is licensed for your own personal research use. You may not resell, sublicense, or
redistribute GESH outputs as personalized investment advice, nor represent GESH output as
the advice of a registered adviser.

## 10. Acceptance

By accessing GESH you acknowledge that you have read, understood, and agree to these Terms
and the Disclaimer above, and that you are using the Service at your own risk.

---

### Compliance notes (internal — remove before publishing)

- **Legal basis at alpha:** distribution as impersonal, bona-fide research of general and
  regular circulation (the *Lowe v. SEC* publisher's exclusion) — **not** the "2024
  Internet Adviser Exemption", which was *narrowed* by SEC Release IA-6578 (eff.
  2024-07-08, compliance 2025-03-31) and requires the adviser to already **be** SEC-registered.
- **Registration ladder if personalized recommendations are later offered:** Michigan
  state IA registration (Series 65 + Form ADV via IARD) → SEC only above the ~$100M
  state/SEC threshold.
- **Hard product boundary to preserve the exclusion:** never auto-route or execute live
  orders in a user's connected brokerage account. Per-user live auto-execution is a
  post-state-registration (Phase-2) feature only; keep alpha strictly paper / self-hosted.
- **AI-washing guardrail:** never market GESH as an "AI financial advisor" or imply
  "forecasts"/guaranteed returns (cf. SEC 2024 actions: Delphia $225K, Global Predictions
  $175K). Describe outputs as "quantitative research signals / educational analysis".
