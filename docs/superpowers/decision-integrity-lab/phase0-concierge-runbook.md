# Phase 0b — Concierge loop runbook (5 paid loops)

**Precondition:** ≥5/25 interviews passed the [kill gate](phase0-interview-guide.md#the-kill-gate-decide-before-you-start-honour-it-after).

**What a "loop" is:** one customer, one real upcoming decision, delivered end-to-end **by a
disclosed human** (you) on a **shared document + a signed content hash** — no Lab product code.
This buys the same lesson as MVP Tasks 1–6 at ~1% of the cost, and produces the single most
important output: **which manual step is worth automating first.**

**Disclosure is non-negotiable.** The customer knows a human runs this. See [README honesty
rule](README.md#the-one-honesty-rule-that-governs-everything). No covert automation, ever.

---

## The offer (hold until counsel approves)

- **Price:** $99 flat, one decision loop. Say plainly: *"You're paying for a concierge-assisted
  pilot. A person — me — does the work. There's no automated product behind this yet."*
- **No personalized-investment-advice monetization** before counsel signs off on the exact
  offer + workflow. If the decision is a securities trade, keep it to *process and evidence
  integrity*, not a recommendation.
- 20 seats × $99 = **$1,980 of learning**, not revenue. Do not treat seat count as traction.

---

## The loop, step by step

Run each on a **shared doc** (one per customer). Every artifact you produce gets a content hash
(below) so the "receipt" is tamper-evident even without the ledger code.

1. **Frame (ex ante draft).** Customer states the upcoming decision. You draft the decision
   record *before they act*: the options, what they'd need to believe for each, the evidence
   they have vs. lack, and what "no regret" looks like. **Timestamp the draft.**
2. **Lock.** Customer commits to a decision (or an explicit "not yet, need X"). You **lock**
   the record — freeze the text, stamp the lock time, compute + record the [content hash](#content-hash-the-poor-mans-ledger).
   Ex ante lock = locked **before** they act; POST_HOC = locked after (record which; never rewrite).
3. **Watch (the SLA).** Over the next 7 days you actively watch for anything that changes the
   picture, and deliver a **cited** review — every observation names its source, or is marked
   `UNKNOWN`. If nothing material changed, the review says exactly that: *"nothing important
   changed"* is a real, valid answer.
4. **Replay.** At the end, walk the customer through the locked record vs. what actually
   happened. The Replay panel carries the honesty line verbatim:
   > *"This Replay's observations were verified by a human reviewer. The automated version of
   > this product would not have had that reviewer."*
5. **Exit question (mandatory).** *"Which single manual step here mattered most to you?"*
   This answer decides what Phase 2 automates first. Record it every loop.

---

## Content hash (the poor-man's ledger)

You do **not** build the append-only crypto ledger in Phase 0. You approximate its *promise*
(tamper-evidence) with a hash the customer can independently verify:

```bash
# When you LOCK a decision record, freeze the exact text and hash it.
# The hash + UTC timestamp go in the shared doc; the customer can re-run this to verify
# the record was never altered after the lock.
shasum -a 256 decision_record_<customer>_<loop>.md
# e.g. -> 9f2c...e41  decision_record_ana_1.md
```

Record in the shared doc: `locked_at (UTC) · sha256 · ex_ante|post_hoc`. If the record changes
later, that is a **new** hashed entry with its own timestamp — you never overwrite a prior lock
(mirrors the ledger's `POST_HOC` reclassification that cannot rewrite history).

---

## Gates — three classes, read each honestly

Concierge delivery **inflates** perceived value (the "concierge halo": a human writing a cited
review is itself delightful, independent of the product idea). So the numbers are triaged:

### A. Halo-free gates — keep pass/fail teeth
These measure the **artifact**, not the relationship. A miss here is a real miss.

| Gate | Bar | Why it survives the halo |
|---|---|---|
| Median **draft → lock** time | **< 3 min** | Measures the receipt, not the human warmth |
| Locked contracts that are **ex ante** | **≥ 60%** | Measures whether people commit *before* acting |
| **SLA integrity** | **0** 7-day cited-review breaches on an active contract | The promise is that someone is watching; a silent slip breaks the core claim |

> **SLA breach → halt enrollment until the due queue is clear.** Gate on the total manual
> minutes / due-queue you already track per loop.

### B. Halo-contaminated signals — record, never gate
Observe and log, annotate **every one** as halo-contaminated. Enthusiasm that depends on a human
in the loop is **unproven until the automation is tested.**

- Paid seats · activation rate · 30-day second-contract rate.

### C. Halo-isolation gates — the only ones that predict the *automated* product
These are the point of the whole exercise.

| Gate | What it asks | Bar |
|---|---|---|
| **Quiet-week completion** | Do customers still complete loops in weeks the review says *"nothing important changed"*? Value that survives a no-touch week is value software can carry. | The single most informative number — track it explicitly |
| **Twin PMF survey (day 60)** | Sean-Ellis "how disappointed if this went away?" asked **twice**: once about the Lab, once about *"the Lab without the weekly human review."* | **≥ 40% "very disappointed" on the *second* question.** The **delta** between the two answers *is the concierge halo, quantified.* |
| **Automation pre-sale** | Would they pre-pay for the self-serve version — the one **without** the founder? | A yes here is worth more than all the seat counts combined |
| **"Which manual step mattered?"** | Exit question, every loop | Decides what Phase 2 automates first |

---

## Kill / continue decision

- **Missing a halo-free gate (A)** → written founder decision to stop, narrow, or rerun.
- **Failing the twin-PMF second question (C)** → same.
- **Seat count is NOT a continuation gate.** A full room that evaporates the moment the human
  steps back has taught you the product is the human, not the software.

Continue to **Phase 1 (MVP code)** only after **two paid loops complete** — then the ledger /
encrypted-storage / Replay engineering is aimed, not guessed. The highest-value manual step
(from the exit question) becomes **Task 1** of the next plan.

---

## What NOT to do in Phase 0

- ❌ Write any Lab product code (models, ledger, encryption, Replay engine). Deferred until aimed.
- ❌ Accept payment before counsel approves the exact offer/workflow.
- ❌ Hide the human, estimate taxes, or invent citations. Name only what you observed.
- ❌ Treat 20 paid seats as validation. Read [gate class C](#c-halo-isolation-gates--the-only-ones-that-predict-the-automated-product).
