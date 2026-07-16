# Decision Integrity Lab — Phase 0 kit

**Purpose:** run the cheapest experiment that can *kill or validate* the Decision
Integrity Lab before a single line of the MVP engine is built. This folder is the
operating kit for that experiment. It contains no product code by design.

**Source of truth for the design:**
[`../plans/2026-07-15-decision-integrity-lab-mvp-CLAUDE.md`](../plans/2026-07-15-decision-integrity-lab-mvp-CLAUDE.md)
— the CLAUDE revision (halo-free gates, build-order reorder). It revises an earlier Codex base
plan (`2026-07-15-decision-integrity-lab-mvp.md`, tracked separately / in a Codex worktree — not
required to run Phase 0). This kit operationalises **§A (gates)**, **§B (build order)**, and
**§D (honesty)** of the CLAUDE revision.

## Why Phase 0 exists (the reorder)

The MVP plan's own first kill gate — *"fewer than 5 of 25 interviews expose real decision
regret → stop"* — fires **before any of the ledger / crypto / Replay engineering is needed.**
Building 2–4 weeks of tamper-evident infrastructure for a hypothesis the interviews may kill
in week one is waste. Kromatic's canonical chain is: **concierge → pre-sale → automate the
highest-value manual step first → Wizard of Oz → PMF survey.** You cannot know *which* step is
highest-value until the concierge has run. So Phase 0 is: **falsify cheaply, learn what to build.**

## The two artifacts

| File | What it is | When |
|---|---|---|
| [`phase0-interview-guide.md`](phase0-interview-guide.md) | 25-interview discovery script + the recruit criteria + the **kill gate** | First — before any concierge loop |
| [`phase0-concierge-runbook.md`](phase0-concierge-runbook.md) | How to run the 5 paid concierge loops on a shared doc + signed content hash, with every gate (halo-free / contaminated / halo-isolation) and the exit question | After ≥5/25 interviews pass |

## Build order (do not skip)

1. **Phase 0a — 25 interviews.** Kill gate: **≥5 of 25** must expose *recent painful decision
   regret* **and** a *real upcoming decision*. Under 5 → stop, or narrow the segment and rerun.
2. **Phase 0b — 5 paid concierge loops.** Shared doc + signed content hash (no plan code).
   This teaches the same lesson as MVP Tasks 1–6 at ~1% of the cost and produces the
   **"which manual step mattered?"** answer that decides what to automate first.
3. **Phase 1 (MVP code) — only after two paid loops complete.** Then the ledger / encrypted
   storage / Replay work is justified, because you are no longer guessing at the shape of the
   thing you are making tamper-evident.

## Money framing (say it out loud)

20 seats × $99 = **$1,980**. This is a **learning purchase, not revenue.** Seat count is
**not** a continuation gate. No customer payment is accepted until counsel approves the exact
offer/workflow **and** ≥5/25 interviews pass.

## The one honesty rule that governs everything

Every concierge deliverable is produced by a **disclosed human**. The landing copy says
`concierge-assisted`, and the Replay panel says, verbatim:

> *"This Replay's observations were verified by a human reviewer. The automated version of this
> product would not have had that reviewer."*

A covert-human ("Wizard of Oz") test is **rejected** here: GESH's whole promise is tamper-evident
honesty and no fake certainty. Keep the disclosure; the gates below are engineered to see through
the "concierge halo" *without* hiding the human.
