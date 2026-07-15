# Decision Integrity Lab MVP — CLAUDE revision

**Tag:** `CLAUDE`
**Revises:** `docs/superpowers/plans/2026-07-15-decision-integrity-lab-mvp.md` (Codex)
**Base:** live `main` @ `dd124ab`, 873-test baseline
**Status:** proposed — apply these sections into the Codex plan; everything not listed here stands unchanged.

The Codex plan is good: ~90% of its code dependencies verified accurate against the real tree, and its engineering (append-only ledger, per-user AES-GCM, deterministic Replay, external deletion journal) is sound. This revision changes **what the experiment measures**, **the order of the work**, and **four concrete defects**. It does not re-litigate the architecture.

Research basis for §A: [Kromatic — Concierge vs Wizard of Oz](https://kromatic.com/blog/concierge-vs-wizard-of-oz-test/), [Kromatic — Real Startup Book, Concierge Test](https://kromatic.com/real-startup-book/3-generative-product-research/concierge-test/), [LearningLoop](https://learningloop.io/blog/concierge-vs-wizard-of-oz), [Netguru](https://www.netguru.com/blog/concierge-testing-versus-wizard-of-oz-prototyping).

---

## §A — REPLACE "Execution and commercial gates"

**Why:** the plan runs a *disclosed concierge* test to *evaluate a pre-defined hypothesis* against *precise numeric gates*. All four sources above call that the wrong pairing: concierge is **generative**, and structurally biased toward **false positives**, because the human layer is itself a value proposition ("concierge halo"). GESH is the acute case — the automated Sentinel delivers **strictly less** than a founder writing a cited weekly review, so a green board cannot separate *"the product works"* from *"the weekly human review is delightful"*, and would authorize automating away what customers bought.

Wizard of Oz (hide the operator) is the textbook correction and is **rejected**: GESH's promise is tamper-evident honesty and "no fake certainty". A covert-human test on a truth-telling financial product is self-undermining, and worse in a regulated context. **Keep the disclosure. Fix the gates.**

> Replacement text:

### Execution and commercial gates

The Founding Lab is a **generative** experiment. Its job is to learn **what** the product should be and **which manual step is worth automating first** — not to prove a pre-defined solution with numeric precision. Concierge delivery inflates perceived value; every customer-behavior number below is read with that bias stated, never laundered into false precision.

Code may be completed behind the default-off flag, but no customer payment is accepted until counsel approves the exact offer/workflow **and** at least 5 of 25 qualified interviews expose recent painful decision regret plus a real upcoming decision.

**Gates that measure the artifact (halo-free — these keep pass/fail teeth):**

- **Median draft→lock under 3 minutes.** Measures the receipt, not the relationship.
- **≥60% of locked contracts are ex ante.** Measures whether people will commit *before* acting.
- **SLA integrity: zero 7-day cited-review breaches on an active contract.** The promise is that someone is actually watching; a silent slip breaks the core claim. Breach → halt enrollment until the queue is clear. Gate on the `manual_total_minutes` / due-queue the plan already computes.

**Signals contaminated by the concierge halo (observed and reported, NOT pass/fail):**

- Paid seats, activation rate, 30-day second-contract rate. Record them; annotate every one as halo-contaminated. Enthusiasm that depends on a human being in the loop is **unproven until the automation is tested**.

**Halo-isolation gates — the only ones that predict the automated product:**

- **Quiet-week completion.** Do customers still complete loops in weeks where the review says *"nothing important changed"*? Value that survives a no-touch week is value software can carry. This is the single most informative number in the pilot.
- **Twin PMF survey at day 60** (Sean Ellis): "How disappointed would you be if this went away?" asked **twice** — once about the Lab, once about *"the Lab without the weekly human review."* ≥40% "very disappointed" on the **second** question is the real bar. **The delta between the two answers is the concierge halo, quantified.**
- **Automation pre-sale.** Would they pre-pay for the self-serve version — the one without the founder? A yes here is worth more than all the seat counts combined.
- **"Which manual step mattered?"** Exit question on every loop. The answer decides what Task-1-of-the-next-plan automates. You cannot know this before the concierge runs, which is why §B reorders the build.

**Kill/continue:** missing a halo-free gate, or failing the twin-PMF second question, triggers a written founder decision to stop, narrow, or rerun. Seat count is **not** a continuation gate. 20 × $99 = **$1,980** — a learning purchase, not revenue, and the doc says so.

---

## §B — REPLACE "Scope boundary" sequencing

**Why:** the plan's own first kill gate ("fewer than 5 of 25 interviews → stop") fires **before a single line of Tasks 1–6 is needed**. Tasks 1–6 build a hash-chained ledger, per-user AES-GCM, an external HMAC deletion journal with restore guards, and Replay math — realistically 2–4 weeks — for a hypothesis the interviews may kill in week one. Kromatic's canonical chain is: concierge → pre-sale → **automate the highest-value manual step first** → Wizard of Oz → PMF survey. You cannot know which step is highest-value until the concierge has run.

> Replacement text:

### Build order

**Phase 0 — falsify cheaply (no code from this plan).** Run the 25 interviews. Run the **first five paid concierge loops on a shared document plus a signed content hash**. This teaches the identical lesson as Tasks 1–6 at ~1% of the cost, and produces the "which manual step mattered?" answer that decides what to build.

**Phase 1 — build only after two paid loops complete.** Then Tasks 1–3 (models, encrypted storage, ledger) become justified: you are no longer guessing at the shape of the thing you are making tamper-evident.

**Phase 2 — automate the highest-value manual step first**, as identified by Phase 0. Not the whole engine. The Codex plan's Tasks 4–10 are the menu, not the sequence; Phase 0 picks the order.

The engineering in Tasks 1–10 is not wasted — it is **deferred until it is aimed**.

---

## §C — Concrete defects (verified against live `main` @ `dd124ab`)

### C1 — Task 4, Step 6: the command hard-fails

`tests/test_ai_cost_budget.py` **does not exist**. An agentic worker following the plan stops here.

```diff
-Run: .../venv/bin/python -m pytest tests/test_decision_drafter.py tests/test_ai_cost_budget.py -q
+Run: .../venv/bin/python -m pytest tests/test_decision_drafter.py tests/test_ai_analyzer_caching.py -q
```

### C2 — Definition of done: the baseline gate is stale and toothless

Reads "the existing **780**-test baseline remains green." Actual baseline: **873**. As written you could regress 93 tests and pass.

```diff
-- Existing 780-test baseline remains green, every new test is offline, and the launch validator passes.
+- The FULL existing suite remains green (873 passing at time of writing — assert the whole suite, never a
+  hardcoded count, so this gate cannot rot again), every new test is offline, and the launch validator passes.
```

### C3 — Task 7, Step 5: the quota silently couples to an unrelated subscription

`_check_customer_quota` computes `limit = base * _TIER_QUOTA_MULT[get_tier(email)]` for **every** event (`server.py:2268`). Adding `"decision_draft": 10` therefore gives a **`desk` research subscriber 50 AI drafts/day vs an observer's 10 — for the same flat $99 Lab seat**, metered by a subscription that has nothing to do with the Lab. This directly contradicts the plan's own constraint that the Lab is isolated and never entangled with `users.tier`.

Fix — the Lab meters off Lab enrollment, not research tier:

```python
# server.py — decision_draft is a FLAT-priced Lab entitlement. It must NOT inherit the
# research tier multiplier: a $99 seat buys the same drafts whether the customer is an
# observer or a desk subscriber. Isolation runs both ways — the Lab must not rewrite
# users.tier, and users.tier must not read back into the Lab.
_TIER_MULT_EXEMPT = {"decision_draft"}

limit = base if event in _TIER_MULT_EXEMPT else base * _TIER_QUOTA_MULT.get(auth_users.get_tier(email), 1)
```

Add a guard test: a `desk`-tier enrollee and an `observer` enrollee get the **same** `decision_draft` cap.

### C4 — stale line references

Only `server.py:2141-2143` drifted (now `_latest_close_meta` @ 2189; `_QUOTA_DEFAULTS` @ 2248) from the GESH-1/#6 work merged today. Every other cited location verified accurate — including `server.py:222` `_HEAVY_PREFIXES`, `auth_users.py:49` `PUBLIC_PREFIXES`, and `tests/test_customer_leak_sweep.py:227` `EXCLUDED`.

---

## §D — one addition to the honesty surface

The Lab's landing copy already says `concierge-assisted`. Add one line to the **Replay** panel, because that is where the halo is invisible and the stakes are highest:

> *"This Replay's observations were verified by a human reviewer. The automated version of this product would not have had that reviewer."*

Rationale: the plan already refuses to fake data, estimate taxes, or invent citations. This is the same discipline applied to itself — the customer should know which part of what they bought is a person. Consistent with the `data_sources` work merged in #66: **name only what you observed.**

---

## Unchanged and endorsed

Ledger/crypto architecture, `UNKNOWN`-on-stale-evidence, `POST_HOC` reclassification that cannot be rewritten, "nothing important changed" as a real answer, taxes `UNAVAILABLE` rather than estimated, no personalized-advice monetization before counsel, north-star = completed integrity loops.
