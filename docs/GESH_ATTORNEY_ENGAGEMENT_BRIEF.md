# GESH Financial — Attorney Engagement Brief
**For:** a US investment-adviser / fintech regulatory attorney
**From:** Georgi Gaydarov, sole founder/operator, GESH Financial
**Date:** 2026-07-19
**Purpose:** Give you everything you need to quote a fixed fee and answer our core regulatory question efficiently. Please tell us which deliverables you can offer on a flat-fee basis.

> Prepared with the help of an AI assistant to save your billable time on fact-gathering. All product facts below are provided by the founder for your independent assessment; nothing here is a legal conclusion.

---

## 1. What GESH is (facts)
GESH is a **solo-operated, bootstrapped software product** that provides AI-assisted financial *research and analytics* for retail investors. Current scale: **1 paying subscriber at $49/month**; a handful of comped/test accounts. Payments run through **Lemon Squeezy (merchant of record)**. Founder is based in **Michigan, USA**.

**GESH does NOT:**
- Take custody of, or discretion over, any customer's money or brokerage account.
- Place trades for customers. (All execution in the system is *paper/simulated only*; live trading is disabled in code.)
- Manage assets or charge fees based on assets under management.

**GESH DOES:**
1. Show a **money-masked "model book"** — a scaled, impersonal view of the founder's own portfolio (same for every user).
2. Offer **general per-ticker research** to all users (technicals, news, an AI-generated research card) — impersonal, general circulation.
3. Publish a **public track-record page** of *hypothetical/simulated* paper-trading results (labeled as such).
4. **[The question feature]** Let a subscriber enter **their own holdings** ("My Book") and run the analytical engine suite on them — producing an **AI verdict (buy/hold/sell-style), a fair-value over/under label, a 0–100 risk score, and Kelly-criterion position-sizing suggestions** for *their specific holdings*. Outputs are labeled as model outputs / AI opinion, not calculations.

Data sources: Yahoo/`yfinance` (prices), the founder's Robinhood account via API, Finnhub, FRED, Reddit sentiment, SEC EDGAR, CoinDesk. AI: Anthropic Claude and Google Gemini (customer inputs are sent to these third-party LLMs).

---

## 2. The core question we need answered
**Does GESH qualify for the publisher's exclusion from the Investment Advisers Act (Lowe v. SEC), or does any part of it — specifically the per-customer "My Book" analysis in §1.4 — constitute regulated investment advice requiring state (Michigan) investment-adviser registration?**

We understand the general-circulation features (model book, ticker research, track record) are likely within the publisher's exclusion. Our concern is the **"My Book" feature**, which computes AI verdicts, over/under-valuation, risk scores, and Kelly position sizes **on a specific customer's own entered holdings**. We are aware the SEC has flagged "customization based on individual portfolios," "implicit buy/sell signals," and "proprietary/opaque algorithms + alternative data" as factors that can convert an information provider into an adviser.

Please advise which of the following we are in, and what it takes to stay in the least-burdensome one:
- **(A)** Publisher's exclusion applies as-is (with adequate disclaimers).
- **(B)** Publisher's exclusion applies *only if* we change framing/UX (please specify the changes — e.g., disclaimer language, removing "you should"-style outputs, presenting everything as impersonal "how the model scores this," an educational-use gate).
- **(C)** Registration is required. If so: state vs. SEC, exam requirements, and rough cost/timeline.

---

## 3. Requested deliverables (please quote each, flat fee preferred)
1. **Regulatory classification memo** answering §2 (publisher vs. adviser; if adviser, the path). *Primary deliverable.*
2. **Terms of Service review/redline.** We have a draft (not yet reviewed). It needs: not-investment-advice framing consistent with (A)/(B), limitation of liability, no-fiduciary-relationship, arbitration + class waiver, Michigan governing law, subscription/auto-renewal disclosure, refund policy.
3. **Privacy Policy review/redline.** We have a draft skeleton. Note: customer portfolio inputs are transmitted to third-party LLMs (Anthropic/Google) — please advise on required disclosures/consent.
4. **Data-licensing risk flag (optional/lightweight).** We are separately moving price data off `yfinance` to a commercially-licensed feed; a quick read on whether our derived-signal resale creates third-party-terms exposure would help.

---

## 4. Specific sub-questions
1. Do the AI **buy/hold/sell verdicts and fair-value over/under labels** on a customer's own holdings cross into "advice," or are they defensible as impersonal, educational model output with proper disclaimers?
2. Does **Kelly position-sizing on a customer's specific book** ("how much to allocate to X") materially increase adviser risk versus a pure score?
3. Does sending **customer portfolio data to third-party LLMs** create a privacy-consent or confidentiality obligation we must disclose?
4. Are there **hypothetical-performance advertising** constraints on our public simulated track record even though we are not (yet) a registered adviser?
5. Given we're **already charging one customer on an unreviewed ToS**, is there any remediation we should do now (e.g., re-consent, refund option, interim disclaimer)?
6. If we must register, would **simplifying "My Book" into purely educational/impersonal output** let us avoid registration while keeping the rest of the product?

---

## 5. What we've already done (so you don't bill to discover it)
- Live trading is **disabled in code** (no path to place real orders).
- The paper track record is **labeled hypothetical/simulated**; a data-integrity bug that had overstated simulated crypto returns has been fixed and the book restated.
- AI-generated figures (risk score, fair value) are **labeled as AI opinion, not calculations.**
- Auth, payments (Lemon Squeezy MoR), and per-customer data isolation are implemented and tested.
- We have `/terms` and `/disclaimer` pages live (draft language) and a privacy-policy draft ready for your review.

---

## 6. Logistics
- Budget-conscious solo founder; **fixed-fee** engagements strongly preferred over open hourly.
- Fastest path appreciated: we're happy to start with a **paid 1-hour consult** on §2 before committing to the full memo, if you offer that.
- We can provide read-only access to the product and the draft ToS/Privacy documents on request.
