# GESH Financial — Legal & Compliance Playbook
**Prepared by:** Claude (Cowork) for Georgi · **Date:** 2026-07-19
**Status:** Practical roadmap, NOT legal advice. I am not a lawyer. Every item marked **[COUNSEL]** requires sign-off from a qualified US securities/fintech attorney before you rely on it. Items marked **[DIY]** you can do yourself now to shrink the eventual legal bill.

---

## 0. Why this is the last blocker — and why it's a *judgment* problem, not a *paperwork* problem

Everything mechanical is done: payments work, auth is hardened, the crypto book is honest, disclaimers exist, live trading is disabled. What's left is a set of questions where the *facts of your architecture* meet *unsettled regulatory lines*, and getting them wrong has asymmetric downside (SEC/state enforcement, refunds, reputational). The good news: your system is already built conservatively, so most of this is about **framing, disclosure, and licensing** — not rebuilding.

The single most important legal question, from which most others flow:

> **Is GESH a "publisher" (exempt) or an "investment adviser" (regulated)?**

The research below shows GESH sits *right on that line*. That's the one thing only a lawyer can rule on — but you can hand them a tight brief so they spend their (billable) time on judgment, not on discovering how your product works.

---

## 1. The core question: Publisher's exclusion vs. Investment Adviser

### 1.1 The test (Lowe v. SEC, 472 U.S. 181, 1985)
A financial-content business is **excluded** from the Investment Advisers Act — no registration, no adviser rules — if its publications are:
1. **Impersonal** — generally available to all subscribers, not individualized to one person's situation;
2. **Bona fide** — accurate, genuine commentary, not a front for touting securities the publisher holds;
3. **Of general and regular circulation** — a real publication, not advice "issued from time to time in response to episodic market activity" for a specific client.

Filtering, alerts, and letting a user pick which of your general content to see does **not** break the exclusion (this is the 2024 Seeking Alpha analysis — email alerts and rating filters are fine).

### 1.2 Where GESH is SAFE (publisher side)
- The **masked $100K "model book" view** of the owner's portfolio — impersonal, same for everyone.
- **Generic per-ticker research** (`/api/analyze-ticker`, momentum, support/resistance, news, the AI research card) — general circulation, available to all.
- The **public `/track-record` proof page** — provided the numbers are honest and labeled hypothetical/simulated (they now are, after the phantom-POL fix).

### 1.3 Where GESH is ON / OVER the line **[COUNSEL]**
The SEC's own "information provider vs. adviser" factors flag exactly the features GESH has. Each of these is a named risk factor for *losing* the exclusion:

| GESH feature | SEC risk factor it maps to | Why it's dangerous |
|---|---|---|
| **`/api/mybook/analyze`** — runs the full engine suite (Kelly sizing, Monte Carlo, AI verdict) on the **customer's own entered positions** | "Customized based on individual portfolios" | This is the clearest tailoring-to-an-individual in the product. Impersonal content computed *on a specific person's holdings* is the exact fact pattern that converts a publisher into an adviser. |
| **AI verdicts + fair-value "over/undervalued" + 0–100 scores** | "Generates implicit buy/sell signals" | Labeling a specific holding a BUY/SELL or "overvalued" is advice-like. |
| **Kelly position sizing on the customer's book** | "Suggests the purchase, holding, or sale… in specific amounts" | Telling a specific user how much to allocate to a specific name is close to personalized advice. |
| **Proprietary opaque AI + Reddit/social sentiment + alternative data** | "Proprietary algorithms users cannot replicate" + "non-traditional data sources" | Opacity + alt-data both push toward "advice," per the SEC's own framing. |

**The verdict you need from counsel:** *Can the `/api/mybook/analyze` + per-customer Kelly/verdict surface be operated under the publisher's exclusion (with the right framing/disclaimers), or does it require registration?* There are three possible answers, each with a different path:

- **(A) It's fine as-is** with stronger disclaimers + framing as "educational analysis of hypothetical scenarios, not personalized advice." (Cheapest.)
- **(B) It's fine only if you change the framing/UX** — e.g., never say "you should," present outputs as "how the model scores this," keep it educational, add an explicit "not personalized to your circumstances" gate. (Moderate — mostly copy/UX work I can implement once counsel specifies it.)
- **(C) You must register** as an investment adviser (see §1.4). (Most expensive; likely avoidable for a solo educational tool if you accept (B).)

Most solo research/education platforms live in (A)/(B). The mybook-on-your-own-holdings feature is what makes (B) more likely than (A).

### 1.4 If registration is required (the fallback path)
- You are Michigan-based with tiny/no AUM and you **don't manage money** → this would be **state (Michigan) registration**, not SEC (SEC is generally for $100M+ AUM). Michigan registration runs through the IARD/FINRA system, requires a Form ADV, a Series 65 exam (or equivalent), a written compliance program, and ongoing filings.
- The **Internet Adviser Exemption** (SEC rule, amended 2024 / IA-6578) is narrower now and is for advisers who *do* advise but only through an interactive website — it's a path *into* SEC registration, not an escape hatch. Don't count on it as a shortcut.
- **Bottom line:** registration is a real business (Series 65 + ~annual compliance cost), so the goal of the counsel consult is to legitimately stay in (A)/(B) if at all possible.

---

## 2. Terms of Service — finalize the DRAFT **[COUNSEL]**
Your `TERMS_OF_SERVICE.md` literally says *"DRAFT — not yet legally reviewed. Have a qualified securities/fintech attorney review this before charging any user."* You are already charging. This is the top priority because it's live liability.

What the finalized ToS must nail (counsel drafts/reviews; I can prep the skeleton):
- Clear **"not investment advice / educational & informational only"** framing consistent with the publisher's-exclusion posture counsel chooses.
- **Limitation of liability + disclaimer of warranties** (no guarantee of accuracy, data may be delayed/wrong, simulated results are hypothetical).
- **No fiduciary relationship** disclaimer.
- **Arbitration + class-action waiver + governing law** (Michigan).
- **Subscription terms**: billing cadence, **auto-renewal disclosure** (US state auto-renew laws + the FTC negative-option rule require clear renewal terms and easy cancellation), refund policy, price-change notice.
- **Acceptable use, account termination, IP ownership.**

## 3. Privacy Policy — currently MISSING (highest DIY leverage) **[DIY → COUNSEL review]**
You have **no privacy policy** and you collect emails, positions, and usage analytics. Every US state privacy law and app-store/processor policy expects one. I've drafted a starting skeleton (`GESH_PRIVACY_POLICY_DRAFT.md`) — counsel reviews, but you save the drafting fee. It must cover:
- What you collect (email, entered positions/watchlist, usage events, payment handled by Lemon Squeezy).
- Why, legal basis, who you share with (Lemon Squeezy, Anthropic/Google for AI, Resend for email, data providers).
- **AI disclosure**: that user inputs may be sent to third-party LLMs (Claude/Gemini). This matters — you're sending customers' portfolio data to Anthropic/Google.
- Retention, security, cookies.
- **Data-subject rights**: access, export, deletion. You already have the *primitives* (`export_portfolio.py`, `clear_positions`, account deactivation) — they just need a user-facing workflow (I can build this) + a stated process.
- **CCPA/CPRA** (California) applies if you hit its thresholds; **GDPR** applies if you have any EU/UK users — decide whether to geo-block EU or comply.

## 4. Data licensing for commercial resale — the sleeper risk **[DIY + COUNSEL]**
You resell *derived* signals to paying customers using data you pull for free. Several of these licenses prohibit exactly that. See `GESH_DATA_LICENSING_MATRIX.md` for the per-provider breakdown. Headlines:
- **Yahoo/`yfinance`** — unofficial endpoints; Yahoo's API ToS restricts commercial use/redistribution. Building a **paying** product on it is real legal + reliability risk. **Action:** move price data to a commercially-licensed feed (Polygon.io, Tiingo, Twelve Data, Finnhub paid, or Alpha Vantage) before scaling customers.
- **Robinhood** (`robin_stocks` / MCP) — automating a *personal* brokerage account is governed by Robinhood's customer ToS; using it as the data spine of a commercial product is questionable. For the owner's own account it's a personal-use grey area; for customers it doesn't scale.
- **Finnhub, FRED, Reddit (PRAW), CoinDesk, alternative.me** — each has its own commercial-use terms; FRED is generally permissive, Reddit's API terms tightened, the others vary.

## 5. AI marketing claims — don't get "AI-washed" **[DIY]**
In March 2024 the SEC fined **Delphia ($225k)** and **Global Predictions ($175k)** for claiming AI capabilities they couldn't substantiate. The rule: **you must be able to prove every AI claim you make.**
- GESH genuinely uses Claude/Gemini/Ollama — good, that's substantiable.
- **Do**: describe AI accurately ("AI-generated research using Anthropic Claude"), keep the "AI opinion, not a calculation" labels I added on the risk score/fair value.
- **Don't**: say "AI that beats the market," "proven AI predictions," or imply the AI has an edge you can't document. The paper track record is *hypothetical/simulated* — keep it labeled that way everywhere (this also aligns with the Marketing Rule's hostility to hypothetical performance in general-circulation ads).

## 6. Everything else (lower risk, mostly DIY)
- **Refund/cancellation disclosure** — Lemon Squeezy (your merchant of record) handles sales tax/VAT and a lot of consumer-payment compliance, which is a real advantage; but you still must publish your own refund policy and honor auto-renew laws.
- **Cookie/analytics disclosure** — if you add any analytics/cookies, disclose them.
- **Account deletion & data export** — build the user-facing workflow (I can do this).
- **Record-keeping** — if counsel lands you in "adviser," record-keeping rules attach; if "publisher," keep basic business records anyway.

---

## 7. What you can do THIS WEEK without a lawyer (shrinks the bill) **[DIY]**
1. Keep **net-new paid signups paused** (or add a prominent "beta / educational" banner) until ToS + privacy are done. *(Business decision.)*
2. Publish the **privacy policy** (from the draft) and add **footer links** to /terms, /privacy, /disclaimer on every page (the dashboard currently has none).
3. **Swap the price-data feed** off yfinance onto a commercially-licensed provider (I can implement — `price_source.py` already has a provider-adapter shape).
4. Build the **data export + account deletion** user workflow (I can implement — primitives exist).
5. Tighten **AI + performance copy** to the substantiation standard (mostly done; I can sweep the rest).
6. Assemble the **attorney brief** (`GESH_ATTORNEY_ENGAGEMENT_BRIEF.md`) and start getting fixed-fee quotes.

## 8. How to actually engage counsel (cost + sourcing)
**What kind of lawyer:** a US **investment-adviser / securities regulatory** attorney (not a generic startup lawyer, not a litigator). Look for firms that do "RIA formation," "fintech regulatory," "investment adviser registration," or "no-action / regulatory opinion" work. Some names that appear repeatedly in this space: Jacko Law Group, RIA-focused boutiques, and fintech practices at firms like Greenberg Traurig / Wilson Sonsini (bigger, pricier).

**The efficient engagement for a solo founder:**
- Ask for a **fixed-fee "regulatory classification memo"** answering the §1.3 question (publisher vs. adviser, and if adviser, what path). This is the highest-value single deliverable and is often quotable as a flat fee.
- Separately, a **fixed-fee ToS + Privacy Policy review** (you provide drafts → they redline).
- Rough ranges (varies widely by firm/region — get quotes): a focused **regulatory classification memo** is often low-thousands to ~$5–10k; **ToS + privacy review** low-thousands; **full RIA registration** (only if required) is a multi-thousand + ongoing-compliance commitment. A **compliance consultant / "RIA-in-a-box"** service can be cheaper than a law firm for the registration mechanics *if* you end up needing to register.
- Cheaper first step: some fintech attorneys offer a **paid 1-hour consult** — bring the attorney brief, get a read on which of A/B/C you're in, then decide how much further legal work you actually need.

---

## 9. Sequencing (dependency-aware)
1. **Now (DIY, days):** privacy policy live + footer legal links; keep signups paused/bannered; assemble attorney brief.
2. **Now (DIY, parallel):** data-feed swap off yfinance; data export/deletion workflow; AI/performance copy sweep.
3. **Counsel step 1 (weeks):** fixed-fee regulatory classification memo (§1.3) → tells you if you're publisher (A/B) or must register (C).
4. **Counsel step 2 (parallel):** ToS + privacy redline.
5. **After counsel:** implement whatever framing/UX changes the memo requires (mostly my work); then, and only then, **resume/scale paid acquisition.**
6. **If (C) registration:** Series 65 + Michigan registration + compliance program — a bigger project; reassess whether the mybook feature is worth it or should be simplified into pure education to stay in (A/B).

---

**Reality check:** you are in a *good* position. The product is built conservatively (no live trading, honest simulated track record, real AI, masked customer data). The legal work here is mostly disclosure, licensing, and one classification question — not a rebuild. Do the DIY items now, get a fixed-fee classification memo, and you'll have a clear, defensible path to paid launch.

## Sources
- [Lowe v. SEC, 472 U.S. 181 (1985) — Justia](https://supreme.justia.com/cases/federal/us/472/181/)
- [Greenberg Traurig — publisher's exclusion / Seeking Alpha analysis (2024)](https://www.gtlaw.com/en/insights/2024/8/no-need-for-seeking-alpha-to-seek-registration)
- [Wilson Sonsini — Information…or Advice? SEC regulation of information providers / analytics](https://www.wsgr.com/en/insights/informationor-advice-sec-regulation-of-information-providers-may-expand-to-include-providers-of-innovative-investment-analytics.html)
- [InnReg — SEC Internet Adviser Exemption](https://www.innreg.com/blog/sec-internet-adviser-exemption-for-rias)
- [NASAA — State Investment Adviser Registration](https://www.nasaa.org/industry-resources/investment-advisers/state-investment-adviser-registration-information/)
- [Thomson Reuters — SEC AI-washing enforcement (Delphia, Global Predictions)](https://www.thomsonreuters.com/en-us/posts/investigation-fraud-and-risk/ai-washing-enforcement/)
- [SEC — Marketing Rule Compliance FAQs](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/marketing-compliance-frequently-asked-questions)
- [MarketXLS — Yahoo Finance API guide + licensed alternatives](https://marketxls.com/blog/yahoo-finance-api-ultimate-guide)
