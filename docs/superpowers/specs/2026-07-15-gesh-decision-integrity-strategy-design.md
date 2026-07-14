# GESH Decision Integrity Strategy

**Date:** 2026-07-15

**Scope:** Founder strategy, competitive stress test, product design, pricing, growth loops, 12-month roadmap, landing copy, and interview scripts

**Decision status:** Recommended as a **90-day paid wedge experiment**, not an immediate full rewrite of GESH

**Research basis:** 20 direct and adjacent competitors; official product and pricing sources preferred; live GESH product and proof state inspected locally

---

## 1. Executive decision

GESH should stop trying to win by having more analysis, more AI agents, or more signals. Those categories are crowded, and GESH's current engine evidence is not strong enough to sell alpha: as inspected on 2026-07-15, the public ledger contained 10 engine-closed trades, 0 wins, negative expectancy, and a failed autonomy gate. The honesty is an asset; the performance is not a customer promise.

The recommended category is:

> **Decision integrity for self-directed investors.**

The recommended promise is:

> **Know what would change your mind—before the market changes it for you.**

The product should become a system of record for why an investor acted. It captures the belief before action, preserves the evidence available at that moment, watches the investor's own falsification conditions, and later separates decision quality from market luck.

This is not four unrelated features. It is one accountability protocol:

```text
Capture belief → Lock contract → Watch exact claims → Record action/inaction
       → Resolve against precommitted alternative → Calibrate future judgment
```

The labels are not defensible. Competitors already offer AI co-pilots, thesis notes, behavioral coaching, trade replay, decision logs, confidence scores, and explicit NO_TRADE signals. The only credible differentiation is the **connected, tamper-evident lifecycle**:

1. Ex-ante capture before or at the decision.
2. Immutable history; changes create versions.
3. Claim-level monitoring against user-defined falsifiers.
4. A counterfactual selected before the outcome is known.
5. Separate scoring for user reasoning, AI assistance, execution, and outcome.
6. Longitudinal calibration by decision type, horizon, and market regime.

Do not immediately dismantle the existing product. Run this as a 90-day paid experiment with 10–20 design partners. Reposition the company only if real users repeatedly capture decisions before acting, return for resolution, and pay for the loop.

---

## 2. What the evidence says now

### Internal GESH facts

- Port 9000 already provides secure per-customer ticker research, portfolio analysis, reports, billing, quotas, and an honest public ledger. See [CUSTOMER_SURFACE.md](../../CUSTOMER_SURFACE.md) and [proof_page.py](../../../proof_page.py).
- The current pricing page sells one $49/month access plan centered on signal accuracy, daily analysis, the watch dashboard, and diversification radar. See [server.py](../../../server.py).
- The current public proof is not a saleable alpha story. At review time, the engine cohort showed 0/10 wins, negative expectancy, and failed statistical gates.
- The existing analytical engines are valuable as an evidence layer, but should not lead the customer promise until they earn a forward track record.

### External customer evidence

- FINRA's 2024 investor survey included 2,861 Americans with non-retirement investment accounts and found younger investors disproportionately use riskier behaviors such as options and margin. This supports a real need for decision guardrails, but does not itself prove willingness to pay. [FINRA Foundation](https://www.finra.org/media-center/newsreleases/2025/new-finra-foundation-research-examines-shifting-investor-behaviors)
- CFA Institute identifies loss aversion, overconfidence, self-control, status quo, endowment, and regret aversion as recurring investor biases. [CFA Institute](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/the-behavioral-biases-of-individuals)
- Schwab reported that only 27% of surveyed retirement participants felt very confident making investment decisions alone, versus 51% with professional help. The sample is retirement-plan participants, not GESH's exact beachhead, so this is directional evidence only. [Charles Schwab](https://pressroom.aboutschwab.com/press-releases/press-release/2025/Schwab-Study-Retirement-Confidence-Dips-Amid-Inflation-Concerns-Savers-Respond-by-Cutting-Personal-Spending-While-Maintaining-401k-Contributions/default.aspx)

### Strategic inference

Investors do not lack information. They lack a reliable memory of what they believed, a calm procedure when emotion rises, and honest feedback on whether their process improved. GESH's opportunity is not to produce another opinion. It is to preserve and test the investor's own opinion.

---

## 3. Three strategic positions considered

| Direction | Why it is attractive | Why it fails | Decision |
|---|---|---|---|
| **A. Superior signal engine** | Fits current code, consensus engine, paper execution, and proof page | Current forward evidence is negative; signals are commoditized; trust cannot be marketed ahead of proof | Keep internal and experimental; do not lead acquisition |
| **B. AI portfolio co-pilot** | Large apparent market, familiar value proposition, existing portfolio surface | Magnifi and PortfolioPilot already own this language and offer broader account aggregation, advice, tax, and scenarios at lower or comparable prices | Reject as positioning |
| **C. Decision-integrity protocol** | Uses GESH's evidence engines but solves a distinct human job; creates longitudinal proprietary data and switching cost | High capture friction; value is delayed; fragments are copyable; regulatory boundary needs care | **Recommend as a paid wedge experiment** |

The critical strategic choice is to sell **restored self-trust**, not predictions and not punishment.

> Bad framing: “GESH detects your investing mistakes.”
>
> Good framing: “GESH helps you make a decision you can still respect after the outcome is known.”

---

## 4. Competitive stress test: 20 products

Prices are public list prices observed during research and may change. Vendor traction numbers are company-reported unless stated otherwise. “Gap” means no capability was found in reviewed public product documentation; it is not proof of absence.

| Competitor | What it already owns | Price signal | Threat to GESH | Remaining gap GESH could own |
|---|---|---:|---:|---|
| [PortfolioPilot](https://portfoliopilot.com/) | Personalized advice, portfolio score, tax, scenarios, decision-log language, 50K users/$40B linked assets claimed | Gold $20/mo annual | 5/5 | Required user-authored immutable thesis, frozen evidence, falsifiers, and process-vs-luck replay |
| [Magnifi](https://magnifi.com/product) | “AI co-pilot,” account linking, risk, scenarios, forecasting, weekly health follow-up | $99/year Premium | 5/5 | Ex-ante commitment and claim-level falsifier monitoring |
| [Public Generated Assets](https://public.com/generated-asset/how-it-works) | Natural-language thesis to investable portfolio, backtest, edit, share, execute | 0.49%/year | 5/5 | Preserved belief state, invalidation clauses, and user calibration |
| [Composer by SoFi](https://www.composer.trade/) | Natural-language rules, no-code strategy, backtest, community, automated execution | Free build; $32/mo annual execution | 5/5 | Why-now thesis, real-world falsifiers, and human decision audit around the strategy |
| [Stock Rover](https://www.stockrover.com/portfolio-management/) | Institutional research, linked portfolios, notes, alerts, Monte Carlo, Trade Evaluator | $34–$199/mo | 4/5 | Reasoning quality rather than realized trade evaluation alone |
| [TipRanks](https://www.tipranks.com/smart-portfolio/analysis/overview) | Smart Score, expert accountability, portfolio warnings, proactive AI diagnostics | Approximately $360–$600/year; accessible pricing required secondary confirmation | 4/5 | Accountability for the user's reasoning, not only expert calls and profitable transactions |
| [Seeking Alpha](https://help.seekingalpha.com/what-is-seeking-alpha-premium) | Content, Quant ratings, alerts, portfolio health, notes, ratings history | $299/year Premium | 3/5 | Structured precommitment and user-specific claim monitoring |
| [Fiscal.ai](https://fiscal.ai/) | Auditable fundamentals, filings, transcripts, AI, structured thesis/risk/catalyst objects | $24–$199/mo | 4/5 | Connect sourced facts to a user's own decision and versioned belief |
| [Koyfin](https://www.koyfin.com/) | Institutional analytics, stress tests, linked accounts, advisor reports | $39–$299/mo | 3/5 | Investor-reasoning system of record |
| [TIKR](https://www.tikr.com/) | Saved valuation models with bull/base/bear assumptions | $24.95–$119.95/mo | 3/5 | Immutable models linked to actions, falsifiers, and behavioral resolution |
| [Wealthfront](https://www.wealthfront.com/) | Trusted passive automation, planning, tax-loss harvesting, rebalancing | 0.25%/year | 2/5 | Active-sleeve decision accountability; happy passive customers are not prospects |
| [Betterment](https://www.betterment.com/investing) | Goal-based automation, behavioral credibility, tax, human advice | 0.25%/year or $5/mo | 3/5 | Stock-level belief and predecision accountability |
| [Autopilot](https://www.joinautopilot.com/landing) | Copy/delegated portfolios, creators, brokerage execution, viral identity | Roughly $100–$700/year by portfolio | 3/5 | Why a user copied a strategy and when they should stop |
| [Tickeron](https://tickeron.com/trading-investing-101/ai-robots-instructions/) | AI signals/robots, backtests, live metrics, paper trading | Public pricing appears $80–$250/mo but is confusing/stale | 2/5 | Trust, clean provenance, and user reasoning; avoid its signal-volume model |
| [Gardio](https://gardiocoach.com/) | Behavioral-finance coach, memory, slowing down, thesis-change and regret questions | €19/mo | 4/5 | Structured tamper-evident contract, evidence monitor, and calibrated replay |
| [Horyzon](https://horyzonapp.com/investment-thesis-tracker/) | Why, conviction, horizon, risk, change-my-mind triggers, thesis reminders | $4.99/mo or $39.99/year | 4/5 | Automated event-to-clause monitoring, version history, and outcome scoring |
| [Oraklio](https://oraklio.com/) | BUY/SELL/NO_TRADE, confidence, verified misses, prediction tracking | $19.99/mo Pro | 3/5 | User reasoning, multiple horizons, full evidence/model snapshot, tamper evidence |
| [TradeZella](https://www.tradezella.com/) | Pre-session plan, rules, drift detection, AI review, trade replay | $35–$99/mo | 4/5 for traders | Long-horizon thesis evidence and explicitly rejected alternatives |
| [TradesViz](https://www.tradesviz.com/blog/plans-view/) | Pretrade plans, adherence analytics, deterministic coaching, what-if exit replay | $19.99–$29.99/mo | 4/5 | Locked original contract and predeclared buy/sell/hold/cash counterfactual |
| [Edgewonk](https://edgewonk.com/features) | Psychology, discipline, missed trades, rule breaks, alternative-strategy analysis | $197/16 months | 3/5 | Long-term claim monitoring and abstention as a first-class resolved decision |

### What the stress test killed

GESH cannot credibly claim uniqueness for any of these alone:

- “AI investment co-pilot”
- “Behavioral investing coach”
- “Remember why you bought”
- “Thesis tracker”
- “Trade replay”
- “No trade is sometimes best”
- “Transparent prediction record”
- “Track decisions, not returns”

### What survived

No reviewed major product publicly described the complete loop of:

> **immutable ex-ante belief + frozen source evidence + user-defined falsifiers + claim-level monitoring + precommitted counterfactual + process/outcome separation + longitudinal calibration.**

This is initially a product distinction, not a moat. The moat emerges only after GESH accumulates proprietary longitudinal data connecting beliefs, evidence, actions, overrides, outcomes, and recurring behavioral patterns.

---

## 5. Exact first customer

### Beachhead persona: the concentrated active-sleeve investor

| Attribute | Definition |
|---|---|
| Age/life stage | Typically 35–60; meaningful savings and enough experience to have felt regret |
| Portfolio | Approximately $75K–$750K liquid investments; 5–20 individual stocks; at least one position above 10% |
| Activity | 5–30 consequential decisions per year; not a day trader |
| Behavior | Reads Seeking Alpha/TIKR/Fiscal.ai, earnings, YouTube, Reddit, newsletters, or X; may keep loose notes |
| Identity | Wants to remain self-directed; does not want a robo-adviser to take control |
| Trigger | Recently sold a winner too early, held a loser while rewriting the thesis, added during FOMO, or ignored a warning |
| Willingness | Will pay to improve judgment, but distrusts hot picks and generic AI summaries |

### The emotional pain

> “I can find endless analysis. I cannot reliably remember what I believed when it mattered, and I do not know whether a bad result came from bad reasoning or bad luck.”

The underlying tension is autonomy versus self-trust:

- “I do not want someone else controlling my portfolio.”
- “I also do not fully trust myself when the position moves 20%.”
- “I want a calm second thought that remembers the version of me who made the decision.”

### Job to be done

> **When a concentrated holding moves sharply or new evidence arrives, help me determine whether my original reason still holds—without letting fear, FOMO, or hindsight rewrite the story.**

### Deliberate non-customers

- Passive index investors who intentionally avoid stock-level decisions.
- Day traders, who already have mature journaling/replay products and need different workflows.
- Users seeking stock tips, guaranteed returns, or automated execution.
- Professional advisers in year one; compliance, team workflows, and client records are a separate product.
- Investors unwilling to capture even a 60–90 second predecision contract.

---

## 6. Product design: one protocol, four surfaces

### 6.1 Decision Contract

The Decision Contract is the only required starting surface. It must be AI-drafted in under 60 seconds and accepted in under three minutes.

Minimum contract:

| Field | Requirement |
|---|---|
| Asset and action | Buy, add, hold, trim, sell, or deliberately pass |
| Thesis | One falsifiable sentence, not generic optimism |
| Confidence | Probability or bounded confidence, explicitly owned by user |
| Horizon | Exact resolution/review date: 30, 90, 180, or custom days |
| Supporting claims | Maximum three; each linked to cited evidence |
| Invalidators | Maximum three observable conditions that weaken or break the thesis |
| Precommitted alternative | Do nothing, cash, benchmark, another asset, or smaller/larger size |
| Risk boundary | User-defined maximum loss, size band, or condition for mandatory review |
| Decision context | Calm, anxious, FOMO, loss-averse, external influence; private by default |
| Evidence snapshot | URLs/data values, timestamps, source freshness, model version |

Rules:

- The original contract is append-only. Corrections and thesis changes create new versions.
- A contract entered after the broker action is labeled **post hoc** and excluded from ex-ante calibration.
- AI may draft and challenge; the user must accept the final belief and confidence.
- GESH must always allow “I do not know” and “do nothing.”
- Do not require a full portfolio import to reach first value.

### 6.2 Thesis Sentinel

The Sentinel monitors the contract, not the ticker.

Each claim becomes a small observable rule:

```text
Claim: Data-center revenue will remain above 35% YoY through the next two quarters.
Evidence: Earnings release / 10-Q segment revenue.
Review state: INTACT | WEAKENED | BROKEN | DISPUTED | UNKNOWN
Materiality: alert only when the state changes.
```

Requirements:

- Every alert must name the exact contract clause affected.
- Every alert must cite the new evidence and its timestamp.
- Data outage or stale evidence becomes **UNKNOWN**, never “intact.”
- Conflicting sources become **DISPUTED**.
- Price movement alone cannot break a fundamental thesis unless price was an explicit clause.
- “Nothing material changed” is a valuable weekly digest, not an absence of product activity.
- Notification budget: default maximum two non-critical messages per week.

### 6.3 Truth Ledger

The Truth Ledger is the underlying architecture, not a dashboard tab.

Append-only events:

```text
contract_created
evidence_snapshot_added
contract_version_proposed
contract_version_accepted
sentinel_state_changed
user_action_recorded
user_override_recorded
contract_resolved
replay_generated
```

Each event records actor, timestamp, prior-event hash, model/version, cited source, data freshness, and whether the event was user-entered or inferred. A simple hash chain is sufficient for tamper evidence; blockchain is unnecessary.

The ledger scores GESH and the user separately:

- Did GESH cite real and timely evidence?
- Did its extraction map evidence to the correct claim?
- Did the user follow, revise, or override the contract?
- Was confidence calibrated?
- Was the result good or bad?
- Was the reasoning process good or bad?

The user owns the data. Private by default; export and deletion must be supported. Any shareable receipt is opt-in and strips holdings size, account value, identity, and private notes.

### 6.4 Regret Replay

Regret Replay is resolution without hindsight theater.

It compares only alternatives selected when the contract was created:

| Lens | Question |
|---|---|
| Actual | What did the user actually do and what happened? |
| Precommitted alternative | What happened to the alternative selected before the outcome? |
| Benchmark | What did an agreed benchmark do over the same period? |
| Process | Were thesis, evidence, sizing boundary, and invalidators coherent? |
| Discipline | Did the user follow or deliberately revise the contract? |
| Calibration | Did stated confidence match outcomes over many comparable decisions? |

Rules:

- Never manufacture the best alternative after seeing the outcome.
- Show fees/slippage when available; label taxes as unavailable unless lots and jurisdiction are known.
- A good process can produce a loss; a bad process can produce a gain.
- Use Brier scoring only for genuinely resolvable binary forecasts. Do not attach fake mathematical precision to qualitative beliefs.
- Tone must be calm and specific, never shaming.

Example resolution:

> You expected a 70% chance that margins would expand by the Q3 report. Margins contracted, triggering Invalidator #1. Selling would have underperformed holding over 30 days, but the contract was correctly falsified. **Outcome: unlucky after a disciplined decision. Process: sound.**

### 6.5 What is deliberately deferred

- No generic AI chatbot as the product.
- No additional consensus nodes.
- No customer-facing automated execution.
- No social feed or public performance leaderboard.
- No broad multi-broker integration until pre-action capture is validated.
- No complex portfolio optimizer in the first six months.
- No “No-Trade Shield” as a fifth subsystem; abstention is an action inside the Decision Contract.

---

## 7. The killer wedge

The initial product is not all four surfaces. It is:

> **A 90-second Decision Receipt that locks the thesis and chosen alternative, followed by one automatic 90-day Replay.**

Why this wedge:

- It delivers immediate clarity before the delayed outcome.
- It creates the minimum proprietary data unit.
- It proves whether users will accept pre-action friction.
- It does not require replacing the existing dashboard.
- It is falsifiable within one quarter.

The first version can be concierge-assisted. A customer sends a ticker, voice note, screenshot, article, or URL. GESH drafts the contract, the customer corrects and locks it, and the founder manually verifies the first Sentinel rules. Automate only repeated behavior.

### Acquisition wedge: “Send this idea to GESH”

Out-of-the-box distribution should begin where investment conviction forms:

- Browser/share-sheet capture of an article, video, post, or analyst note.
- GESH extracts facts, forecasts, assumptions, and missing falsifiers.
- The user converts the idea into a private Decision Receipt.

This reframes GESH from another research destination into the accountability layer across every research source.

---

## 8. Onboarding and activation

### Do not begin with account linking

The first session should use one real holding or contemplated decision. Full portfolio entry creates work before value.

### Ten-minute onboarding

1. **Choose a real decision:** “What position are you thinking about right now?”
2. **Name the fear:** selling too early, holding too long, adding from FOMO, or missing an opportunity.
3. **Import context:** paste a link, speak a 30-second voice note, or type one sentence.
4. **AI drafts the contract:** thesis, confidence, horizon, evidence, invalidators, alternative.
5. **User corrects and locks it.**
6. **GESH shows the first calm state:** what will be watched, next review date, and what will *not* trigger noise.

### Activation definition

A user is activated only when they have:

- locked one ex-ante contract;
- named at least one observable invalidator;
- chosen one counterfactual alternative; and
- scheduled a resolution date.

Account creation, ticker search, and AI report generation are not activation.

### Activation target

- Median time to locked contract: under 3 minutes after choosing the ticker.
- At least 60% of pilot contracts captured before the related trade.
- At least 50% of activated users create a second contract within 30 days.

---

## 9. Pricing strategy

Monthly SaaS is poorly aligned with a product whose strongest value may arrive 90 days later. Start with a paid cohort, then introduce recurring plans only after replay value is proven.

### Stage 1: Founding Decision Lab

| Offer | Price | Includes | Purpose |
|---|---:|---|---|
| **90-day Founding Decision Lab** | **$99 one-time** | Up to 5 active contracts, founder-assisted first contract, weekly Sentinel, at least one Replay, private calibration summary | Test willingness to pay and complete the first loop |

Limit to 20 seats. Do not offer it free. If no contract reaches its scheduled resolution within 90 days, extend access until one does.

### Stage 2: after activation is proven

| Plan | Price | Includes |
|---|---:|---|
| **Free** | $0 | 1 active contract, basic evidence snapshot, one 30-day replay |
| **GESH Core** | $29/mo or $249/year | 10 active contracts, claim-level Sentinel, all Replays, confidence calibration, private exports |
| **GESH Pro** | $59/mo or $599/year | Only after demand: unlimited contracts, multiple accounts, advanced evidence rules, household/adviser export |

Do not launch Pro until at least 100 paying Core users reveal the need. Existing $49 research customers should be grandfathered; do not silently replace what they bought.

### Pricing experiment

During interviews, sell—not survey—three sequential cohorts:

1. First five seats at $49/90 days.
2. Next ten at $99/90 days.
3. Final five at $149/90 days with one founder review.

Measure conversion and completion, not stated willingness. The recommended public starting point remains $99 for the 90-day Lab.

---

## 10. Retention and growth loops

### Retention loops

| Loop | Trigger | User reward | Metric |
|---|---|---|---|
| **Clarity loop** | Contract creation | A decision that can be explained and tested | Contract completion and second-contract rate |
| **Calm loop** | No material evidence change | Permission not to react to price noise | Weekly digest opens; reduced notification muting |
| **Sentinel loop** | A clause changes state | Relevant evidence without generic news overload | Alert usefulness ≥70%; false positive <15% |
| **Replay loop** | Resolution date | Process versus luck made visible | Replay completion and next-decision change |
| **Calibration loop** | Multiple resolved contracts | Personal blind spots and strengths | Confidence calibration improvement |
| **Trust loop** | Every GESH claim | Citations, version, stale/unknown states, visible misses | Evidence coverage and correction rate |

### Growth loops

1. **Shareable redacted Decision Receipt:** “Here is my thesis, invalidator, and review date.” No account values or performance boasting.
2. **Public methodology ledger:** GESH publishes its extraction/monitoring errors and calibration, not stock-pick marketing.
3. **What changed, not what moved newsletter:** one example of a thesis clause changing; no hot-pick urgency.
4. **Research-source capture:** browser/share-sheet import makes GESH useful alongside Seeking Alpha, Fiscal.ai, TIKR, newsletters, and videos.
5. **Creator pilot later:** credible analysts publish timestamped thesis receipts and revisions. Legal review required before monetization or ranking.

### North-star metric

> **Completed Integrity Loops per retained customer per quarter.**

A completed loop requires an ex-ante contract, at least one evidence review, an actual action/inaction record, a resolution, and a Replay viewed by the user.

MAU, number of AI reports, and number of alerts are secondary metrics and can reward noise.

---

## 11. Twelve-month roadmap and kill gates

This roadmap intentionally validates behavior before building infrastructure.

| Timing | Build/operate | Learning target | Exit or kill gate |
|---|---|---|---|
| **Month 1** | 25 problem interviews; manually produce Decision Receipts; recruit 10 pilot users | Is the pain frequent and recent? Will users expose a real decision? | Stop if fewer than 5 of 25 have a recent painful decision and agree to use the process live |
| **Month 2** | Sell 20-seat 90-day Lab; concierge capture via form/voice/link | Will people pay and capture before acting? | Require ≥8 paid seats and ≥60% ex-ante capture; otherwise narrow or stop |
| **Month 3** | Decision Contract + append-only Truth Ledger MVP | Can activation occur in under 3 minutes? | ≥70% of paid users activate; ≥50% create a second contract |
| **Months 4–5** | Thesis Sentinel for earnings, filings, and user-selected objective claims only | Can alerts be materially better than generic news? | ≥70% alerts rated useful; <15% false positives; median ≤2 non-critical alerts/week |
| **Month 6** | First Regret Replays; actual versus precommitted alternative | Does delayed value change future behavior? | ≥30 resolved contracts; ≥30% of users cite a Replay in a later decision |
| **Months 7–8** | Confidence calibration and behavioral pattern summaries | Does history compound rather than shame? | ≥35% six-month retention among users with ≥2 Replays |
| **Month 9** | Redacted shareable receipts and referral loop | Can trust artifacts acquire users? | ≥15% of new qualified users arrive through receipts/referrals |
| **Month 10** | Core annual plan; cost and margin tuning | Will customers renew beyond the cohort? | ≥30% Lab-to-paid renewal; modeled gross margin ≥75% |
| **Month 11** | Broker transaction reconciliation or share-sheet capture—choose only the larger proven bottleneck | Is capture or reconciliation the real friction? | Ship one integration, not an integration platform |
| **Month 12** | Scale/kill decision; optional creator or adviser discovery pilot | Is this a repeatable business or a useful personal tool? | Scale only with ≥100 paying users, ≥40% six-month activated retention, and ≥50% ongoing contracts captured ex ante |

### One-year success definition

Success is not market-beating performance. It is evidence that customers change how they decide:

- 100+ paying users.
- 40% six-month retention among activated users.
- At least half of ongoing contracts captured before action.
- At least 30% of users with a Replay explicitly change a later decision process.
- Trust metrics published without hiding failures.
- No customer-facing reliance on unvalidated GESH signal claims.

If these gates fail, do not add features to hide the failure.

---

## 12. Landing-page messaging

### Recommended homepage

**Eyebrow**

Decision integrity for self-directed investors

**Headline**

**Know what would change your mind—before the market changes it for you.**

**Subheadline**

GESH turns every consequential investment decision into a timestamped contract, watches the evidence that matters, and later shows whether your process was sound—even when the outcome was not.

**Primary CTA**

Create your first Decision Receipt

**Secondary CTA**

See an honest Replay

**Trust strip**

No hot picks. No fake certainty. No rewritten history.

### Problem section

**The market remembers the price. It does not remember why you acted.**

When a position falls, the thesis changes in your head. When it rises, confidence becomes hindsight. Notes disappear, alerts create noise, and a profitable outcome can hide a poor decision.

GESH preserves the version of your thinking that existed before the result.

### How it works

1. **State the decision.** Speak, type, or share a link. GESH drafts the thesis, evidence, invalidators, confidence, horizon, and alternative.
2. **Lock the receipt.** You correct and accept it. Future changes create versions; the original remains visible.
3. **Watch the thesis.** GESH maps new filings, earnings, and evidence to the exact claims you made—not to generic market noise.
4. **Learn without hindsight.** At the review date, Replay compares what happened with the alternative you chose before you knew the outcome.

### Emotional section

**Sometimes the best alert is: “Nothing important changed.”**

GESH is designed to reduce unnecessary action, not manufacture engagement. It can tell you that price moved while your reason did not—or that your reason broke before the price admitted it.

### Proof section

**GESH keeps receipts on itself, too.**

Every AI draft records its model, sources, timestamps, and corrections. Unknown data stays unknown. Misses remain visible. GESH and the investor are scored separately.

### Founding offer

**Join the 90-day Founding Decision Lab — $99**

Bring up to five real investment decisions. Build your first contract with the founder, receive claim-level monitoring, and complete at least one private Replay.

### Alternative headline tests

- **Your portfolio has a memory. Your decisions should too.**
- **Make a decision you can respect—even if it loses money.**
- **Stop asking only “Was I right?” Start asking “Did I reason well?”**

Do not use “AI financial adviser,” “beat the market,” “high-confidence signals,” or “personalized buy/sell recommendations” without an intentional regulatory strategy.

---

## 13. Customer-interview system

### Recruiting message

> I am researching how self-directed investors make difficult hold/sell/add decisions. I am not selling stock picks and I will not ask for account access. I would like to reconstruct one real decision from the last six months—what you knew, what you feared, and what happened. Thirty minutes. If the problem is real, I may invite you into a small paid 90-day decision-quality pilot.

### Screening criteria

Recruit only people who:

- own at least five individual securities or have one concentrated active sleeve;
- made a meaningful buy/add/hold/trim/sell decision in the last six months;
- can describe a moment of uncertainty or regret;
- decide for themselves rather than fully delegating; and
- agree to use a real upcoming decision during the pilot.

### Thirty-minute interview script

#### 1. Context — 3 minutes

1. Walk me through how you currently manage the part of your portfolio you choose yourself.
2. Which tools or information sources did you use in the last week?

#### 2. Reconstruct the last consequential decision — 12 minutes

3. Tell me about the last time you seriously considered buying, adding, holding, trimming, or selling. Start before you decided.
4. What triggered the decision?
5. What did you believe would happen, and by when?
6. What evidence mattered most?
7. What would have proved you wrong at the time?
8. Where, if anywhere, did you write that down?
9. What alternative did you reject?
10. What did you actually do?
11. What happened afterward?
12. Looking back, what part feels like skill, discipline, luck, or error?

#### 3. Pain and workaround — 7 minutes

13. What was emotionally hardest about that moment?
14. What did your current tools fail to help with?
15. Have you ever rewritten the reason for holding after the price moved?
16. How often do decisions like this happen?
17. What have you tried to improve the process? Why did it fail or fade?

#### 4. Concept test — 5 minutes

Show a single example receipt, not the full roadmap.

18. What is immediately useful or uncomfortable here?
19. Which field would you refuse to complete?
20. What would make you distrust the Sentinel?
21. At the 90-day review, what would make the Replay worth returning for?

#### 5. Commitment test — 3 minutes

22. Do you have a real decision in the next 30 days that you would put through this process?
23. The Founding Lab is $99 for 90 days and five decisions. Would you enroll now?

Do not ask “Would you use this?” or “How much would you pay?” A real decision and real payment are the evidence.

### Interview scorecard

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| Recent pain | No example | Weak/old example | Specific costly or emotional example |
| Frequency | <1/year | 2–4/year | ≥5/year |
| Existing workaround | None/no interest | Informal notes | Pays for tools or maintains a process |
| Pre-action willingness | Refuses | Unsure | Commits a real upcoming decision |
| Accountability tolerance | Wants validation only | Accepts mild challenge | Wants falsifiers and honest scoring |
| Payment evidence | No | Verbal interest | Pays/deposits |

Qualified design partner: at least 8/12 and a real upcoming decision.

### Post-Replay interview

1. What did the Replay reveal that P&L alone did not?
2. Did it change how you framed your next decision? Show me.
3. Which conclusion felt unfair or wrong?
4. Did any alert cause unnecessary anxiety?
5. What would you miss if GESH disappeared tomorrow?
6. Will you pay $249 for another year? Why or why not?

---

## 14. Product evaluation and acceptance tests

The product must be evaluated as a decision system, not as a writing demo.

### Deterministic integrity tests

- An accepted contract cannot be edited or deleted in place; every change appends a version.
- The event chain detects missing, reordered, or modified records.
- A post-trade contract is always labeled post hoc and excluded from ex-ante calibration.
- Replay uses only the alternative, horizon, and benchmark stored in the original contract.
- Fees and slippage follow one documented calculation; missing tax data is never estimated as fact.

### Evidence and AI evaluations

- Build a frozen test bank of filings, earnings releases, and articles mapped to known contract clauses.
- Measure claim extraction precision, citation correctness, clause mapping, state-change precision, and abstention.
- A source outage must produce UNKNOWN; contradictory sources must produce DISPUTED.
- A model upgrade must run against the same frozen bank before release, with no regression in citation or state-change accuracy.
- Human review of the first 100 Sentinel alerts is mandatory; target ≥70% user-rated usefulness and <15% false positives.

### Behavioral product tests

- Compare a 60-second AI-drafted contract with a longer structured form; choose the shortest flow that preserves at least one real falsifier and one real alternative.
- Measure whether contracts are created before broker action, not merely whether they exist.
- Measure whether a viewed Replay changes the structure of the next contract; self-reported satisfaction alone is insufficient.
- Interview churned users specifically for shame, alert anxiety, delayed value, and capture burden.

### Safety tests

- Prohibit guaranteed-return, urgency, or personalized action language in customer-facing generated text during the experiment.
- Confirm that no private holding size, account value, emotional note, or identity appears in a shared receipt.
- Red-team prompt and source content for instructions that try to alter contract history, monitoring rules, or citations.
- Keep an append-only record of GESH corrections and user disputes; never silently regenerate a more favorable replay.

---

## 15. Trust, regulatory, and product risks

### Regulatory boundary

The SEC describes robo-advisers as registered investment advisers using algorithms to provide online investment advisory services. Compensation for personalized securities advice can trigger adviser obligations; a disclaimer alone is not a strategy. [SEC robo-adviser guidance](https://www.sec.gov/newsroom/press-releases/2017-52), [SEC broker-dealer guide](https://www.sec.gov/about/divisions-offices/division-trading-markets/division-trading-markets-compliance-guides/guide-broker-dealer-registration).

Before monetizing personalized action recommendations:

- obtain U.S. investment-adviser counsel;
- choose whether GESH remains recordkeeping/education, partners with an RIA, or registers;
- avoid customer-specific “buy/trim/sell now” language during the experiment;
- let users define decisions and invalidators; GESH may structure, cite, monitor, and explain;
- preserve disclosures and methods alongside every result.

This document is product strategy, not legal advice.

### Main product risks and mitigations

| Risk | Why it matters | Mitigation/test |
|---|---|---|
| Capture friction | The users most vulnerable to emotion may avoid structured reflection | AI draft from voice/link; under 3 minutes; kill if <60% ex-ante capture |
| Delayed value | Replay may take 90 days | Immediate clarity score and Sentinel; cohort pricing rather than month-to-month |
| Feeling judged | Users may churn to protect identity | Score process and outcome separately; calm language; private by default |
| Sentinel noise | Generic alerts recreate the problem | Claim-level alerts, strict materiality, notification budget, user ratings |
| Hindsight counterfactuals | Replay can become dishonest theater | Only precommitted alternatives; preserve exact original data |
| Competitor copying | Surface features are easy to reproduce | Longitudinal belief/evidence/action/outcome graph and trust brand |
| GESH's negative signal record | Undermines authority | Keep visible but clearly experimental; never use it as alpha proof |
| Legal exposure | Personalized monitoring can resemble advice | Narrow year-one boundary and engage specialist counsel |

---

## 16. Adversarial verdict and founder recommendation

The required Grok 4.5 project-agent challenge returned a **no-invest** verdict on a full pivot today. Its strongest objections were correct:

- the existing engine record undermines any claim to decision authority;
- pre-action capture is a high-friction behavior that GESH has not demonstrated;
- fragments are already offered by better-distributed competitors;
- the current stack is optimized for post-hoc analysis and signals, not capture and replay;
- regulation becomes harder as the product becomes more useful.

The right response is not to abandon the insight or rebuild the whole product. It is to make the thesis falsifiable with a small paid experiment.

### Final recommendation

**Invest 90 days, not 12 months.**

1. Recruit 25 qualified interviews.
2. Sell at least eight Founding Lab seats.
3. Manually complete the loop before automating it.
4. Require real pre-action capture, not retrospective journaling.
5. Reposition GESH only after customers create second contracts and return for Replay.

If that succeeds, GESH has the beginning of a defensible product: not an oracle, but an honest memory and calibration system for consequential investment decisions.

If it fails, preserve the existing research product and stop. Do not bury the result under another dashboard feature.

---

## 17. Research sources and methodology

Research was conducted on 2026-07-15 across three competitor clusters: portfolio/research platforms, robo-advice/automation products, and behavioral/trading-journal adjacencies. Official product, help, pricing, company, regulatory, and App Store sources were preferred. Vendor claims were not treated as independently audited traction.

Key sources not already linked above:

- [PortfolioPilot plan details](https://portfoliopilot.com/help/faqs/what-are-the-benefits-of-each-portfoliopilot-subscription-plan)
- [PortfolioPilot decision-quality article](https://portfoliopilot.com/ai-financial-advisor/resources/ai-financial-advisor-track-decisions-not-just-returns)
- [PortfolioPilot SEC record](https://adviserinfo.sec.gov/firm/summary/327520)
- [Composer pricing](https://www.composer.trade/pricing)
- [Composer/SoFi launch](https://investors.sofi.com/news/news-details/2026/Introducing-Composer-by-SoFi-AI-Powered-Investing-From-Idea-to-Execution/default.aspx)
- [Public Generated Assets fees](https://help.public.com/en/articles/12874251-what-are-the-fees-and-minimums-for-generated-assets)
- [Stock Rover plans](https://www.stockrover.com/plans/)
- [TipRanks AI Portfolio Analyzer](https://www.tipranks.com/news/labs/tipranks-introduces-ai-portfolio-analyzer)
- [Fiscal.ai pricing](https://fiscal.ai/pricing/)
- [Koyfin pricing](https://www.koyfin.com/pricing/)
- [TIKR valuation model](https://support.tikr.com/hc/en-us/articles/41118126176667-Forecasting-upside-with-TIKR-s-Valuation-Model-Builder)
- [TradeZella pricing](https://www.tradezella.com/pricing)
- [TradesViz plan analysis](https://www.tradesviz.com/blog/trading-plan-analysis/)
- [TradesViz what-if simulator](https://www.tradesviz.com/blog/stop-loss-profit-target-trade-simulator/)
- [Edgewonk psychology](https://edgewonk.com/trading-psychology)
- [Metaculus calibration reference](https://www.metaculus.com/questions/track-record/)

Confidence in the strategic direction is **medium**. Confidence that generic “AI co-pilot” positioning is crowded is **high**. Confidence in willingness to pay for the proposed accountability loop is **low until the paid 90-day experiment is completed**.
