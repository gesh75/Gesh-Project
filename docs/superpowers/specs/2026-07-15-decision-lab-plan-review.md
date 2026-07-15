# Review — Decision Integrity Lab MVP plan

**Reviewed:** `docs/superpowers/plans/2026-07-15-decision-integrity-lab-mvp.md` (Codex worktree `decision-lab-mvp`)
**Against:** live `main` @ `dd124ab`, 873-test baseline
**Date:** 2026-07-15
**Verdict:** Strategy sound. Plan unusually well-researched (~90% of its code dependencies verified accurate). Ship it **after** one experiment-design change and four concrete fixes.

The plan doc itself was open in two live `vim` processes during this review, so nothing in it was edited. Apply the fixes below by hand.

---

## 1. The experiment-design problem (the one that matters)

The plan runs a **disclosed concierge test** ("a human founder reviews the cited evidence weekly") to **evaluate a pre-defined solution hypothesis** (Decision Receipt → Sentinel → Replay) against **precise numeric gates** (8 paid seats, 60% ex-ante, 70% activation, sub-3-minute median lock, 50% second-contract).

Four independent sources say that is the wrong pairing:

> "If we attempt to use a Concierge test to validate our solution hypothesis, we're liable to get a bias towards a **false positive**… Concierge testing is very bad when we have a clear idea of the solution."
> — [Kromatic, *Wizard of Oz vs. Concierge*](https://kromatic.com/blog/concierge-vs-wizard-of-oz-test/)

> "**Concierge halo (false-positive bias).** The manual delivery adds value of its own: trust, responsiveness, and a personal relationship. Founders can mistake positive feedback on the hand-delivered service for validation of the product concept. **When you automate, that human layer disappears and customers may reject the result.** … treat enthusiasm that depends on you being in the loop as **unproven until automation is tested**."
> — [Kromatic, *The Real Startup Book — Concierge Test*](https://kromatic.com/real-startup-book/3-generative-product-research/concierge-test/)

> "a concierge experiment is **not ideal for validating a very specific feature or solution hypothesis**… Use concierge testing when you need to learn **what** the solution might look like… **not to rigorously prove a pre-defined solution works**. … trying to validate a precise metric with a concierge test could mislead you."
> — [LearningLoop](https://learningloop.io/blog/concierge-vs-wizard-of-oz)

> "the results of any Concierge MVP test can be skewed by the **likability of the concierge**, and therefore not accurately reflect the usefulness of the underlying product idea."
> — [Netguru](https://www.netguru.com/blog/concierge-testing-versus-wizard-of-oz-prototyping)

**Why this bites GESH specifically.** The eventual automated Sentinel delivers **strictly less** than a founder personally reading filings and writing a cited weekly review. Kromatic: *"In a Concierge test, the value proposition is actually significantly higher than the eventual product."* So if the pilot **passes** all five gates, you still cannot distinguish:

- "the Decision Integrity product works", from
- "Georgi personally reviewing my investment thesis every week is delightful."

A green day-90 board would then authorize building automation that removes the thing customers actually bought. That is a false positive with a 6-month bill attached.

**Do not fix this by hiding the human.** The textbook answer is a Wizard of Oz (hide the operator, measure clean). GESH must not: the entire promise is tamper-evident honesty, "no fake certainty", and the plan's own constraint that the UI says `concierge-assisted`. Running a covert-human test on a truth-telling financial product is self-undermining, and worse in a regulated context. **Keep the disclosure; fix the gates instead.**

### Recommended change

Reclassify the 90-day Lab as **generative** (its literature-correct use), and make the gates isolate the halo rather than absorb it:

1. **Keep** the operational gates that don't depend on the founder relationship: sub-3-minute median lock, 60% ex-ante capture. These measure the *artifact*, not the concierge.
2. **Demote** activation/second-contract from pass/fail to *observed*, and annotate both as halo-contaminated.
3. **Add the halo-isolation gate** — the only one that actually predicts the automated product:
   - **Quiet-week completion:** do customers still complete loops in weeks where the review says *"nothing important changed"*? Value that survives a no-touch week is value the software can carry.
   - **Sean Ellis PMF survey** at day 60: ≥40% "very disappointed" if it went away — and ask it **twice**, once about the Lab, once about *"the Lab without the weekly human review."* The delta between those two numbers **is** the concierge halo, quantified.
   - **Pre-sales test for the automated version** (Kromatic's prescribed next step): would they pre-pay for the self-serve product? Enthusiasm contingent on you is unproven until they buy the version without you.
4. **Ask the plan's own question of the data:** *"Which manual step was highest-value?"* — Kromatic: *"Identify which manual steps are highest-value and should be automated first."* You cannot know that before the concierge runs, which is exactly why building the whole engine first is backwards (§2).

### The canonical sequence (Kromatic), vs. this plan

| Step | Canonical | This plan |
|---|---|---|
| 1 | Concierge test (generative) | ✅ but mis-cast as evaluative |
| 2 | Pre-sales test for willingness to pay | ⚠️ merged into step 1 |
| 3 | Single-feature MVP automating the **highest-value manual step first** | ❌ builds the whole engine up front |
| 4 | Wizard of Oz once a repeatable hypothesis exists | ❌ n/a (and see honesty note) |
| 5 | PMF survey ("disappointed without") | ❌ absent |

---

## 2. Sequence inversion

The plan's **first kill gate** is "fewer than 5 of 25 interviews expose real pain → stop." That fires **before a single line of Tasks 1–6 is needed**. Yet Tasks 1–6 build a hash-chained ledger, AES-GCM per-user key wrapping, an external HMAC deletion journal with restore guards, and deterministic Replay math — realistically 2–4 weeks — for a hypothesis the interviews may kill in week one.

The first five paid concierge loops can run on a shared doc plus a signed hash and teach the identical lesson. **Build the ledger once someone has paid twice.** Cheapest falsifier first.

---

## 3. Founder capacity — the unpriced risk

The plan budgets **12 founder-hours/week** of SLA-bound review (100 active contracts × 6 min + 2h exceptions), with a **7-day** review promise to paying customers.

`CLAUDE.md`: **Sr Manager, Network Operations at One Call starts Aug 10, 2026.** A 90-day experiment starting now runs straight through that date. This is a second part-time job with a customer-facing SLA landing on top of a new demanding role, and it appears **nowhere** in the strategy, the plan, or the kill gates — all of which measure *customer* behavior, never founder throughput.

- Add a **founder-capacity kill gate**: if weekly review SLA breach exceeds X% in any 2-week window, halt enrollment. The plan already computes `manual_total_minutes` — gate on it.
- Price the honesty: 20 × $99 = **$1,980 for ~156 hours ≈ $12.70/hr**. That's fine as a *learning purchase*; it is not revenue, and the doc should say so plainly so nobody mistakes seat count for traction.
- Consider 10 seats, not 20. Half the halo-contaminated data, half the SLA exposure, same lesson.

---

## 4. Concrete fixes (verified against live `main` @ `dd124ab`)

| # | Fix | Evidence |
|---|---|---|
| 1 | **Task 4 Step 6 hard-fails.** It runs `tests/test_ai_cost_budget.py` — that file does not exist. The real one is **`tests/test_ai_analyzer_caching.py`**. An agentic worker following the plan hits a stop. | `ls tests/` |
| 2 | **Stale baseline gate.** Definition of Done says "the existing **780**-test baseline remains green." Actual: **873**. As written you could regress 93 tests and pass. Change to 873 — or better, `--maxfail=1` on the full suite so the number can't rot again. | `pytest -q` |
| 3 | **Quota coupling contradicts the plan's own isolation rule.** `_check_customer_quota` computes `limit = base * _TIER_QUOTA_MULT[get_tier(email)]` for **every** event. Adding `"decision_draft": 10` therefore gives a **`desk` research subscriber 50 AI drafts/day vs an observer's 10 — for the same flat $99 Lab seat**, metered by an unrelated subscription. The plan insists "Lab enrollment never rewrites `users.tier`" but misses that the tier reads *back* into it. **Fix:** exempt `decision_draft` from `_TIER_QUOTA_MULT`, or key the Lab quota off `decision_lab_enrollments`, not `users.tier`. | `server.py:2268` |
| 4 | **Strategy commit was unpushed and drifting.** `7cef121` was local-only; Codex reported "ahead 4, behind 1" — by review time it was **ahead 4, behind 13**. Now cherry-picked onto this branch. **Three other local-main commits remain stranded** (`51c8159`, `7885447`, `b0c68d6` — chain/preflight fixes) and should be PR'd too. | `git rev-list --left-right main...origin/main` |

Line-number references in the plan are otherwise accurate; only `server.py:2141-2143` drifted (now `_latest_close_meta` @ 2189, `_QUOTA_DEFAULTS` @ 2248) because of the GESH-1/#6 work merged today.

---

## 5. What's right (keep it)

- Refusing the full pivot and converting to a **falsifiable 90-day experiment with kill gates** is the correct call. Incorporating Grok's no-invest verdict rather than arguing with it is good epistemics.
- **North-star = completed integrity loops**, not AI reports or alerts — resists the vanity-metric trap the whole category falls into.
- The honesty discipline is consistent with everything shipped today: `UNKNOWN` on stale evidence, `POST_HOC` reclassification that cannot be rewritten, *"nothing important changed"* as a real answer, taxes `UNAVAILABLE` rather than estimated. Same principle as the `data_sources` work merged in #66 — **name only what you observed**.
- ~90% of the plan's code dependencies verified correct against live `main`. Everything it flags as missing (`get_user_id`, `call_structured_tool`, `tests/__init__.py`, `static/`) is correctly something it creates.

---

## Recommended next step

1. Apply fixes §4.1–§4.3 to the plan doc (it's open in vim — do it by hand).
2. Rewrite the day-90 gates per §1: keep lock-time + ex-ante, demote activation/second-contract to observed, add quiet-week completion + the twin PMF survey + the automation pre-sale.
3. Add the founder-capacity gate from §3 and decide 10 vs 20 seats **before** counsel review.
4. Run the 25 interviews. Hold Tasks 1–10 until the 5-of-25 gate passes.
