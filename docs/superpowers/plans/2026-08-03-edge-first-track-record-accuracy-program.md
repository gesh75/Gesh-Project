# Edge-First Track-Record Accuracy Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Keep the public track record truthful and reproducible, determine cheaply whether either strategy has usable forward edge, and build portfolio-grade trust infrastructure only after the evidence earns the cost.

**Architecture:** GESH has two non-comparable books. The sibling tracker database owns the consensus/market_scan cohort and autonomy gate. The product database owns an experimental, owner-specific daily-analysis recommendation journal. Each book gets its own strategy versions, cohort predicate, metrics, limitations, and artifact. The public product may render both disclosures, but no interface is allowed to aggregate their trades, P&L, win rate, or return. Work proceeds through hard GO/KILL/INCONCLUSIVE gates: public truth repair, a reproducible entry/exit falsification, observation-integrity repair, paired shadow validation, a thin NAV account, and only then the deferred signed-artifact program.

**Tech Stack:** Python 3.12, FastAPI, SQLite opened read-only for research, dataclasses, Decimal/fixed-point accounting, SHA-256, canonical JSON, pytest, Ruff, yfinance for private exploratory research only, and the existing historical_settle, stop_policy_grid, CPCV, and shadow infrastructure in gesh-project-trading.

## Global Constraints

- Read AGENTS.md and CLAUDE.md in every touched repository before work. Use Grok 4.5 through an approved host coding harness; do not invoke a shell AI client from the sandbox.
- Port 9000 is the customer product. No production database write, LaunchAgent change, service restart, tunnel operation, broker action, billing action, or deployment is authorized by this plan alone.
- Keep the current autonomy-gate cohort and thresholds unchanged. Research and shadow results never feed paper_outcomes or the gate.
- Never merge the tracker and daily-analysis books. They have different signals, sizing, execution, exits, capital assumptions, and purposes. A combined 43/44-trade count or combined P&L is methodologically invalid.
- Call the daily figure a nominal simulated row-level P&L sum. It is not cash lost, portfolio P&L, a return, or subscriber performance.
- Treat all rows in paper_trades as legacy/unversioned until a durable strategy version and causal risk-decision link exist. Do not rewrite or delete them.
- TRIM and SELL recommendations are reductions of an owner holding unless a real, cash-backed short account explicitly proves otherwise. They must not be modeled as standalone shorts.
- Include open BUY observations in entry-edge analysis. Never condition the cohort on a recorded exit or outcome.
- The historical results already inspected in this review are exploratory. They cannot be relabeled as untouched out-of-sample evidence. Every inspected policy counts as a trial.
- Do not tune the official tracker book. A behavior change starts a new named strategy version and a new forward cohort.
- Keep owner symbols, holdings, account data, and raw research artifacts private. Public output is aggregate-only.
- yfinance is acceptable for a zero-cost private falsification, not as the sole source for a customer performance claim. Record provider, request time, adjustment mode, missing bars, and corporate-action checks.
- Every implementation task is test-first: write the failing test, observe the expected failure, implement the smallest change, run focused tests, then the relevant regression suite.
- All Python changes require Python-specialist and general code review. User-input, public-route, signing, or file-loading changes also require security review.
- Preserve dirty worktrees and unrelated changes. Use clean worktrees and the codex/ branch prefix.
- A stage stops immediately when its engineering-hour cap, founder-time cap, cash cap, or deadline is hit. There are no automatic deadline extensions. A resource-cap stop is PROGRAM_STOP with evidence INCONCLUSIVE or PARKED; it is not falsely relabeled as a strategy KILL.

---

## Executive forensic verdict

Claude's correction is substantively right about sequencing:

- The prior 5–7 week plan built an excellent assurance system before proving that the strategy deserved it.
- It omitted a separately disclosed daily-analysis book.
- It lacked a hard budget, deadline, owner-time limit, and kill criterion.
- It assumed an independent reviewer without naming or funding one.

Four corrections to Claude's correction are required:

1. The daily-analysis nominal loss is simulated, not cash the project lost.
2. The two books must be disclosed separately, not pooled into an all-automated result.
3. The open 6-up/5-down observation includes seven TRIM rows and describes raw underlying price direction, not valid trade profitability.
4. The available evidence does not support the claim that exits are mainly destroying good entries.

### Evidence frozen during this planning review

All figures below are point-in-time diagnostics, not portfolio returns.

| Evidence | Reconciled result |
|---|---:|
| Public tracker, as of 2026-08-03 | 20 closed; 6 wins, 12 losses, 2 breakevens; +$134.05 nominal realized P&L; $12,357.94 gross entry notional; 0 open |
| Tracker source split | consensus: 18 closes, 6 wins, +$151.49; market_scan: 2 closes, 0 wins, -$17.44 |
| Daily journal, as of 2026-08-02 | 24 closed rows, 0 marked wins, -$6,263.27 nominal row-level P&L; 11 open rows |
| Daily BUY rows recorded | 25 |
| Recorded-but-rejected contamination | IDs 29–32; all rejected because quote age was 463 seconds versus the 300-second limit |
| Legacy BUY rows reconciled to APPROVED decisions | 21 rows: 17 closed and 4 open; the legacy rows still lack durable intent foreign keys |
| Recent date-cut daily BUY cohort from 2026-07-27 | 6 rows; only 2 closed and no 5- or 10-session maturity at the review cutoff; descriptive only because rows have no durable strategy version |

Legacy BUY IDs reconciled to APPROVED decisions:

    21, 22, 23, 34, 35, 36, 37, 38, 39, 40, 41,
    42, 43, 45, 47, 49, 50, 51, 55, 56, 60

Recorded-but-rejected sensitivity IDs:

    29, 30, 31, 32

Excluded TRIM IDs:

    33, 44, 46, 48, 52, 53, 54, 57, 58, 59

The common ten-session overlap-purged mature cohort used in the frozen 5/10-session sensitivity is:

    21, 22, 23, 35, 36, 37, 38, 39, 40, 41, 42, 43

The purge retains the earliest eligible symbol observation and admits a later same-symbol row only after the prior retained row's tenth subsequent session has closed. It is intentionally a common ten-session purge, not a different cohort selected independently for each horizon.

Exploratory fixed-horizon results for all decision-reconciled APPROVED BUY rows, without truncating price history at the recorded exit. The SPY figures subtract a same-session-close SPY move from a symbol return that begins at a stored intraday price. That mixed baseline is not a valid matched active return; it is shown only to document what was inspected and cannot satisfy any formal gate:

| Horizon | Mature rows | Mean raw | Median raw | Raw positive | Mixed-baseline SPY delta, not gate-eligible |
|---|---:|---:|---:|---:|---:|
| 1 session | 20 | -2.35% | -2.19% | 5/20 | -1.82% |
| 3 sessions | 18 | -5.24% | -4.80% | 2/18 | -4.97% |
| 5 sessions | 15 | -5.83% | -4.42% | 4/15 | -3.89% |
| 10 sessions | 13 | -9.32% | -6.98% | 2/13 | -5.01% |

The overlap-purged sensitivity remains negative:

| Horizon | Mature rows | Median raw | Raw positive | Mixed-baseline SPY delta, not gate-eligible |
|---|---:|---:|---:|---:|
| 5 sessions | 12 | -4.93% | 2/12 | -5.06% |
| 10 sessions | 12 | -6.89% | 2/12 | -4.93% |

An exploratory provisional continuous barrier-touch replay of the daily 5% stop / 12% target against a no-stop fixed hold also contradicts the exit-damage story. This is a counterfactual OHLC policy, not a faithful reproduction of the observed point-sampled implementation:

| Horizon | Provisional barrier 5/12 mean | Fixed-hold mean | Mean 5/12 minus hold | 5/12 better / tie / worse |
|---|---:|---:|---:|---:|
| 3 sessions | -2.88% | -5.24% | +2.36 pp | 9 / 4 / 5 |
| 5 sessions | -3.38% | -5.83% | +2.45 pp | 7 / 1 / 7 |
| 10 sessions | -3.51% | -9.32% | +5.81 pp | 9 / 1 / 3 |

This replay is exploratory and still needs a second provider, corporate-action verification, stored fixtures, and unit tests. Its use is limited to program sequencing: do not assume that removing or loosening exits will fix the daily strategy.

The existing tracker stop grid was also inspected. It already tests live ratchet, no ratchet, three ATR stops, and time-only. Its current raw report shows live-ratchet expectancy +1.978% and profit factor 2.357 over 19 resolved positions, while the other resolved variants are negative. That comparison is censored because each policy has a different unresolved population, so it is not a promotion decision. It is enough to prohibit an immediate exit change and to count all six policies as tried variants.

Frozen daily evidence identity:

- last complete market session: 2026-07-31;
- database SHA-256: 5a6d560c3b614b3cb03df500fb8512e9bc38c209bff3d39bb656edd5c39c2629;
- database file timestamp observed: 2026-08-03T00:08:16+0300.

Production tests must not assume that live counts remain 24 closed and 11 open. Those values belong to this dated snapshot and its aggregate fixture.

### Immediate decision

- KILL the proposed large exit-rule audit for the legacy daily cohort.
- Treat the corrected daily method as INCONCLUSIVE. The existing ledger cannot identify its cohort reliably because it stores no strategy version; a date cut is descriptive, not version proof.
- Keep the tracker live exit rule unchanged.
- HOLD the full institutional trust rig behind the gates in this plan.
- Implement only P0 and E0 before requesting new authorization.

---

## Program contract, budget, and ownership

**Named decision user:** Georgi Gaydarov, founder/operator.

**Expected value:** spend at most 18 engineering hours and $0 to prevent a public-source regression, close the scope omission, and determine whether another roughly 70–130 engineering hours is justified. Revenue value is treated as $0 until a named prospect or customer confirms that verified performance changes a purchase or renewal decision.

| Stage | Authorization | Engineering cap | Founder cap | Cash cap | Deadline |
|---|---|---:|---:|---:|---|
| P0 Public truth boundary | Authorized first tranche | 10 h | 45 min | $0 | 2026-08-06 18:00 Europe/Sofia |
| E0 Reproducible edge/exit falsification | Authorized first tranche | 8 h | 45 min | $0 | 2026-08-07 18:00 Europe/Sofia |
| O0 Daily observation integrity | Conditional on continued collection | 16 h | 1 h | $0 | 7 days after approval |
| C0 Current-method observation window | Conditional, passive | 4 h total | 30 min/week | $0 | 2026-09-30 |
| T0 Tracker paired-comparison repair | Conditional | 8 h | 30 min | $0 | 7 days after approval |
| S0 One-candidate paired shadow | Conditional on T0 GO | 16 h | 30 min/week | $100 | 8 weeks or 30 paired closes |
| A0 Thin evidence-account MVP | Conditional on S0 GO | 24 h | 2 h | $300 | Build: 6 weeks after authorization; evidence: 30 account closes or 90 days |
| T1 Institutional trust rig | Separate authorization | 40 h | 3 h | $500 | 8 weeks after all T1 entry gates and authorization |
| R0 Independent technical/legal review | Separate authorization | 8 h | 2 h | $2,500 | 30 days after eligibility |

Only P0 and E0 are authorized by adopting this plan: 18 engineering hours, 90 founder minutes, and $0. Conditional work through A0 is capped at 86 engineering hours and $400 total. T1 and R0 require a new written decision.

Routine operation after setup must remain exception-only and below 30 founder minutes per week.

Engineering hours mean active human or agent implementation/review time, excluding passive sample collection and unattended waits, rounded up to 0.25 hour. The decision/cost ledger records started_at, deadline, active engineering hours, founder minutes, cash spent, recurring monthly commitment, approver, and remaining budget after every task. The full conditional maximum through R0 is 134 engineering hours and $3,400 before any recurring subscription cost.

---

## Dependency and stop flow

~~~mermaid
flowchart TD
    P0["P0: source/live truth boundary"] --> E0["E0: reproducible entry/exit falsification"]
    E0 -->|Legacy KILL| D0["No legacy exit audit"]
    E0 -->|Current method inconclusive| O0["O0: versioned observation integrity"]
    O0 --> C0["C0: passive forward cohort"]
    C0 -->|KILL| PARK["Park daily performance research"]
    C0 -->|GO| DAILYREQ["Record evidence and request a separately budgeted daily-strategy program"]
    P0 --> T0["T0: repair tracker paired comparison"]
    T0 -->|No qualifying candidate| BASE["Keep tracker baseline unchanged"]
    T0 -->|Exactly one candidate qualifies| S0["S0: paired forward shadow"]
    S0 -->|KILL| BASE
    S0 -->|GO| A0["A0: thin cash/NAV/benchmark account"]
    A0 -->|KILL| HOLD["Keep disclosure; stop assurance expansion"]
    A0 -->|GO plus named customer| T1["T1: signed artifact trust rig"]
    T1 --> R0["R0: funded independent review"]
~~~

---

## Repository and interface map

### Product repository

Path: /Users/georgigaydarov/Projects/gesh-project

Existing seams:

- proof_page.py: public statistics loader and HTML renderer.
- server.py: /track-record route.
- trade_log.py: legacy trade and paper_trades schema and P&L calculations.
- paper_trading.py: legacy fixed 5%/12% paper session and point-price exits.
- run_paper_gate2.py: daily action plan, risk-gate causality, and recorder call.
- run_manifest.py: run ID, input source, snapshot time, and per-symbol quote times.
- tests/test_proof_page.py and tests/test_track_record_presentation.py: public semantic contract.
- tests/test_paper_gate_causality.py: gate-first recording contract.

New product modules:

- track_record_disclosures.py
- daily_signal_ledger.py
- daily_disclosure_artifact.py
- scripts/smoke_track_record.py
- tests/test_track_record_disclosures.py
- tests/test_daily_signal_ledger.py
- docs/DECISIONS/track_record_program_v2.md

### Trading/proof sidecar repository

Path: /Users/georgigaydarov/Projects/gesh-project-trading

Existing reusable seams:

- trading/historical_settle.py::settle_position: pure, conservative path replay.
- trading/stop_policy_grid.py::run_grid: read-only policy measurement.
- trading/cpcv.py::cpcv_report: measurement-only robustness distribution.
- trading/variant_backtester.py::build_registry_payload: per-variant return registry.
- trading/variant_registry.py: existing tracker variant governance.
- consensus_shadow.py: isolated shadow-table pattern.
- strategy_variant_registry.json: existing tracker trial history; do not use it for daily variants.
- tracker_exit_trial_registry.json: new measurement-only exit-policy trial history; never an autonomy-gate input.

New sidecar modules:

- trading/daily_edge_diagnostic.py
- scripts/run_daily_edge_diagnostic.py
- tests/test_daily_edge_diagnostic.py
- trading/paired_exit_shadow.py
- tests/test_paired_exit_shadow.py
- daily_strategy_trial_registry.json

Deferred A0/T1 modules from the prior program:

- trading/track_record_protocol.py
- trading/performance_schema.py
- trading/paper_account.py
- trading/nav_evidence.py
- trading/performance_claim_gate.py
- trading/track_record_artifact.py
- trading/track_record_signing.py
- scripts/publish_track_record.py
- scripts/verify_track_record.py

---

## Task 0: Preserve the corrected public source before any restart

**Why first:** at the 2026-08-03 review, the process listening on 127.0.0.1:9000 had been started on 2026-08-02 and served the corrected dollar-P&L page from memory. Main at aec5cb3 did not contain that correction. Commit fb64033 contains the reviewed proof-page files but is local-only and is not an ancestor of that main. Restarting from an uncorrected checkout can restore the misleading presentation.

**Files:**

- Restore selectively: proof_page.py
- Restore selectively: tests/test_proof_page.py
- Restore selectively: tests/test_track_record_presentation.py
- Create immediately: docs/DECISIONS/track_record_program_v2.md
- Do not cherry-pick the whole fb64033 commit.

- [ ] Record read-only baselines for both repositories: status, branch, HEAD, remote HEAD, and active worktrees.
- [ ] Create the decision/cost ledger before implementation. Record the first tranche's started_at, deadlines, 18-hour/$0 budget, approver, and remaining budget; update it after every task.
- [ ] Preserve fb64033 with a local codex/archive-track-record-proof-fix branch before it can be garbage-collected.
- [ ] Create a clean product worktree and branch from current origin/main:

    git fetch origin
    git worktree add ../gesh-project-track-record-v2 -b codex/track-record-accuracy-v2 origin/main

- [ ] Run the existing focused baseline and save the failing/old semantic output outside the repository:

    python -m pytest -q \
      tests/test_proof_page.py \
      tests/test_proof_page_gate.py \
      tests/test_track_record_presentation.py

- [ ] Restore only the three reviewed files:

    git restore --source=fb64033 -- \
      proof_page.py \
      tests/test_proof_page.py \
      tests/test_track_record_presentation.py

- [ ] Inspect the complete diff. Reject any owner data, absolute private path, credential, unrelated doc, or deployment change.
- [ ] Confirm the restored source renders:
  - engine dollar P&L and named gross-entry-notional denominator;
  - sum of individual returns explicitly labeled not a portfolio return;
  - Wilson interval;
  - 20/50 bp cost stress;
  - SPY as calendar context only;
  - mutable SQLite and unaudited warnings;
  - autonomy-gate failures without hiding them.
- [ ] Add a regression assertion that the forbidden phrases are absent:
  - 41.3% vs SPY;
  - outperformed SPY;
  - portfolio return when referring to a sum of trade returns.
- [ ] Run focused tests, Ruff, Python review, and general code review.
- [ ] Add a tested emergency fallback: when TRACK_RECORD_NUMERIC_CLAIMS_ENABLED=0, the route returns a no-numeric-claims unavailable page/503 and never renders cached positive metrics. Use this only if the corrected renderer cannot be reconciled.
- [ ] Commit this preservation step independently before adding new disclosure behavior.
- [ ] Define SOURCE_FIXED only after the correction is merged into the actual release branch. A commit on an unmerged worktree is not restart-safe.
- [ ] Do not restart port 9000 in this task. Define LIVE_FIXED only after an explicitly authorized deploy and the Task 1 semantic smoke pass.

**Exit evidence:** SOURCE_FIXED means corrected behavior is merged into the release branch and all focused tests pass. LIVE_FIXED additionally means an authorized deployment serves presentation version 2 and the semantic smoke passes. If deployment is not authorized, P0 remains pending and the restart prohibition stays in force.

**Program stop:** if the selective diff cannot be reconciled within 5 of the 10 allocated P0 hours, land the tested emergency no-numeric-claims/503 path, keep restart prohibited until that path is merged and authorized for deployment, classify P0 INCOMPLETE, and stop.

---

## Task 1: Make the two-book scope explicit without allowing aggregation

**Product files:**

- Create: track_record_disclosures.py
- Create: tests/test_track_record_disclosures.py
- Create: scripts/smoke_track_record.py
- Create: tests/test_smoke_track_record.py
- Modify: proof_page.py
- Modify: tests/test_proof_page.py
- Modify: tests/test_track_record_presentation.py

### Required interface

~~~python
@dataclass(frozen=True, slots=True)
class LedgerDisclosure:
    ledger_id: str
    strategy_version: str
    cohort_predicate: str
    as_of: datetime | None
    closed: int | None
    wins: int | None
    losses: int | None
    breakevens: int | None
    open_count: int | None
    nominal_realized_pnl: Decimal | None
    accounting_basis: Literal["row_sum_unverified", "verified_nav"]
    gate_authority: bool
    limitations: tuple[str, ...]


def load_daily_disclosure(db_path: Path) -> LedgerDisclosure: ...
def tracker_disclosure_from_reconciled_model(
    track_record: Mapping[str, Any],
    gate_dto: Mapping[str, Any],
) -> LedgerDisclosure: ...
def render_disclosures(items: Sequence[LedgerDisclosure]) -> str: ...
~~~

There is deliberately no sum, combine, aggregate, or all-automated interface.

- [ ] Write failing tests with two temporary SQLite fixtures.
- [ ] Require SQLite URI mode=ro plus PRAGMA query_only=ON. Do not use immutable=1 against a live WAL database because it can ignore concurrent/WAL state. A missing database returns an unavailable disclosure with the book's existence and scope still visible; it must not silently omit the book.
- [ ] Prove that loading disclosures creates no database, table, WAL, or SHM file.
- [ ] Daily predicate:

    scanner_source = 'daily-analysis'

- [ ] Do not create a second tracker SQL/statistics authority. Adapt the already reconciled proof-page model and authoritative gate DTO into LedgerDisclosure, and assert exact equality between the displayed tracker row count and gate cohort count. Any cohort cutoff remains in the existing single versioned tracker protocol/model.

- [ ] Daily nominal P&L is the sum of realized_pnl for closed rows and always carries accounting_basis=row_sum_unverified.
- [ ] Daily limitations must include:
  - owner-specific Kelly/rebalancing experiment;
  - no common cash account or NAV;
  - no fees/slippage model;
  - mixed legacy strategy versions;
  - TRIM is misbooked as a synthetic short in the legacy ledger;
  - four recorded BUY rows in the dated legacy snapshot were subsequently risk-rejected and remain in the raw nominal sum;
  - not comparable to and never counted in the autonomy gate.
- [ ] Tracker limitations must include:
  - paper/simulated;
  - mutable SQLite;
  - no verified NAV yet;
  - not independently audited.
- [ ] Render this exact stable scope statement:

    Scope: this page covers only the consensus/market_scan tracker book used by
    the autonomy gate. GESH also runs a separate owner-specific daily-analysis
    simulation with different sizing, execution, exit, and P&L rules. It is not
    comparable, is excluded from every tracker metric above, and never counts
    toward the gate.

- [ ] Add an aggregate-only frozen fixture for the dated 2026-08-02/03 reviewed snapshot. It must reproduce 24 closed rows, 0 marked wins, -$6,263.27 nominal simulated row-level P&L, 11 open rows, last_complete_session=2026-07-31, and the frozen database hash. It must contain no symbol or holding.
- [ ] Label 0 marked wins as a legacy ledger outcome count, not causal strategy accuracy, because the raw population includes recorded-but-rejected rows and invalid TRIM-short semantics.
- [ ] Production renders freshly queried values with loaded_at, latest ledger-row timestamp, and accounting basis. Never hard-code the frozen counts into production HTML and never make a production test depend on a changing live database.
- [ ] Never calculate or render open P&L without timestamped marks and an explicit direction convention.
- [ ] Change Complete gate-eligible closed-trade ledger to Complete within the declared tracker cohort.
- [ ] Define TRACK_RECORD_PRESENTATION_VERSION = 2 and render it in a stable data-k attribute.
- [ ] Add HTML data attributes for book ID, strategy version, basis, as-of, and gate authority.
- [ ] Add tests proving:
  - neither book's values appear in the other's HTML node;
  - no combined total exists;
  - unavailable daily data does not imply that no daily book exists;
  - HTML escaping applies to every limitation and cohort string;
  - public output contains no symbol or owner holding.
- [ ] Run:

    python -m pytest -q \
      tests/test_track_record_disclosures.py \
      tests/test_smoke_track_record.py \
      tests/test_proof_page.py \
      tests/test_proof_page_gate.py \
      tests/test_track_record_presentation.py

- [ ] Run Ruff, Python review, general review, and security review.
- [ ] Commit separately from Task 0.
- [ ] The semantic smoke accepts expected aggregate values as inputs and verifies presentation version, forbidden phrases, tracker values, daily scope panel, absence of a combined metric, and HTTP failure behavior. It must never discover expected numbers by trusting the page it is testing.
- [ ] Deployment remains a separately authorized operation. After deployment, run the smoke against the live page and record the exact presentation version and response hash.

**Exit evidence:** an outside reader can see that another automated experiment exists, can see its separately labeled adverse legacy diagnostic, and cannot mistake it for or combine it with the tracker cohort.

**P0 stage gate:** Tasks 0–1 must finish within 10 engineering hours and $0. SOURCE_FIXED and LIVE_FIXED are recorded separately. Otherwise performance claims fail closed and the program stops.

---

## Task 2: Implement the reproducible Stage E0 daily entry/exit diagnostic

**Sidecar files:**

- Create: trading/daily_edge_diagnostic.py
- Create: scripts/run_daily_edge_diagnostic.py
- Create: tests/test_daily_edge_diagnostic.py
- Create: docs/DAILY_EDGE_DIAGNOSTIC_PROTOCOL_V1.md
- Create: daily_strategy_trial_registry.json
- Reuse without behavior change: trading/historical_settle.py

### Pure contracts

~~~python
@dataclass(frozen=True, slots=True)
class PositionObservation:
    row_id: int
    symbol: str
    entry_at: datetime
    entry_price: Decimal
    action: Literal["BUY"]
    stop_loss: Decimal | None
    take_profit: Decimal | None
    gate_verdict: Literal["APPROVED", "REJECTED", "UNKNOWN"]
    strategy_version: str


@dataclass(frozen=True, slots=True)
class HorizonOutcome:
    row_id: int
    horizon_sessions: int
    stored_entry_return: Decimal | None
    matched_close_return: Decimal | None
    matched_spy_return: Decimal | None
    matched_active_return: Decimal | None
    missing_reason: str | None


def select_daily_buy_cohort(
    paper_rows: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
) -> tuple[PositionObservation, ...]: ...


def evaluate_exit_edge(
    positions: Sequence[PositionObservation],
    bars: Mapping[str, Sequence[Bar]],
    spy_bars: Sequence[Bar],
    horizons: Sequence[int] = (5, 10),
) -> EdgeExperimentResult: ...
~~~

- [ ] Open the live source as mode=ro with PRAGMA query_only=ON, use SQLite's backup API to create a consistent owner-readable temporary snapshot, hash it, and close the source. Open only that completed snapshot as:

    file:/private/snapshot/gesh_financial.db?mode=ro&immutable=1

- [ ] Never import trade_log.py in the diagnostic because importing it can probe/create schema.
- [ ] Reconcile legacy rows by a documented one-to-one symbol/action/timestamp match against trade_intents and risk_decisions. Call the result decision-reconciled, not causally linked: approval did not durably cause the legacy row and there is no foreign key. Ambiguous or unmatched rows become UNKNOWN and cannot enter the primary cohort.
- [ ] Assert that the reviewed snapshot produces exactly the 21 decision-reconciled APPROVED IDs listed above and exactly four recorded-but-rejected IDs.
- [ ] Convert TRIM to a diagnostic exclusion, never to a short position.
- [ ] Include OPEN BUY observations. Do not inspect outcome when selecting an entry.
- [ ] Count horizons in subsequent regular-session bars, not calendar days.
- [ ] Exclude the entry-day full candle because entry occurred intraday.
- [ ] Do not truncate bars at the recorded exit.
- [ ] Report two non-mixed return families:
  - stored-entry absolute return: stored symbol entry price to the 5th/10th future session close;
  - matched close-to-close active return: both symbol and SPY start at the entry-session close and end on identical future session dates.
- [ ] A timestamp-matched intraday active return may be added only when existing provider data supplies synchronized symbol and SPY marks. A mixed intraday-symbol versus close-SPY delta is exploratory-only and cannot satisfy GO.
- [ ] For E0, charge 50 bp round trip to the strategy leg only and label the SPY leg gross passive context. A0 later applies its declared cost policy independently to both implementable accounts.
- [ ] Compute 5/10-session absolute and matched active returns, 20/50 bp stress, the full cohort, the exact common ten-session overlap-purged cohort, and leave-one-entry-session-out sign stability.
- [ ] Replay three paths on a common horizon:
  1. observed ledger exit, labeled implementation diagnostic only;
  2. continuous barrier-touch 5% stop / 12% target counterfactual with worst-first same-candle precedence, gap-through stop fill, target-level fill, and fixed-horizon mark if still open;
  3. no-stop fixed-horizon hold.
- [ ] Adapt historical_settle.settle_position rather than create a second fill convention, but call it with apply_ratchet=False. Clip input to H subsequent trading bars, suppress its calendar-day time stop, and mark an unresolved position at the Hth close. Never pass H as max_hold_days because that parameter is calendar-day age.
- [ ] Use the existing direct yfinance and direct Robinhood provider seams separately; do not use a fallback-combined fetch as evidence of two-provider agreement. Pin the same raw/adjusted basis for both, normalize explicit split/dividend actions, and classify an unresolved corporate-action mismatch as data-quality failure. Provider disagreement over 25 bp, missing session, or date alignment is unresolved, not averaged away.
- [ ] Store fetched OHLC fixtures and the private row-level report under an ignored, owner-readable artifact directory. The aggregate report includes:
  - database SHA-256;
  - query/cohort version;
  - code commit;
  - provider and adjustment mode;
  - data-fetch time;
  - last complete session;
  - expected and observed maturity count;
  - missing rows and reasons;
  - every inspected policy and trial count;
  - result classification.
- [ ] Pre-seed daily_strategy_trial_registry.json with every horizon and barrier/fixed-hold comparison already inspected in this review. Never initialize an empty registry that erases exploratory exposure.
- [ ] Tests must cover:
  - open-row inclusion;
  - rejected-row exclusion;
  - TRIM exclusion;
  - causality ambiguity;
  - entry-day exclusion;
  - trading-session horizons;
  - missing provider data;
  - split/corporate-action flag;
  - same-bar stop and target with stop precedence;
  - gap-through stop;
  - SPY timestamp and date alignment;
  - overlap purge;
  - cost stress;
  - deterministic JSON.
- [ ] Run:

    python -m pytest -q tests/test_daily_edge_diagnostic.py

    python -m pytest -q \
      tests/test_historical_settle.py \
      tests/test_stop_policy_grid.py

- [ ] Run the CLI once against the completed read-only snapshots; never analyze a changing live file with immutable=1 and never use a writable production URI.
- [ ] Independently recompute aggregate rows from the emitted JSON.
- [ ] Run Ruff, Python review, general review, and security review.

MFE/MAE, Wilson intervals, new provider adapters, broader cluster reports, and persistent fetch automation are deferred to O0 and are built only if data quality makes E0 INCONCLUSIVE.

### E0 operational decision rule

The historical sample has already been viewed, so this rule is an operational falsification threshold, not a fresh statistical preregistration.

Classify in this order:

1. Data quality and sample sufficiency.
2. Economic GO/KILL rule.
3. Program resource status.

INCONCLUSIVE takes precedence when:

- valid OHLC coverage is below 90% of expected mature rows;
- direct providers disagree materially after adjustment normalization;
- full and purged cohorts disagree on sign;
- fewer than 12 rows are mature at 10 sessions for the legacy falsification;
- or results fall between GO and KILL.

GO to a larger exit study only when both 5- and 10-session results satisfy in both full and overlap-purged cohorts:

- median stored-entry absolute return greater than 0;
- stored-entry positive-return fraction at least 60%;
- median matched close-to-close active return greater than 0 after charging the strategy leg 50 bp;
- median no-stop uplift over the continuous barrier-touch 5/12 counterfactual at least 2 percentage points;
- no-stop improves at least 60% of rows;
- worst loss does not worsen by more than 1 percentage point.

KILL only when the data-quality gate passes and either condition holds in both the full and overlap-purged cohorts:

- both 5- and 10-session stored-entry medians are at or below 0 and positive fractions are at or below 50%; or
- 10-session stored-entry median is at or below -1% and no more than 40% are positive.

Expected branch from the reviewed snapshot: the legacy/full cohort satisfies the economic KILL thresholds if direct-provider and adjustment reconciliation passes; otherwise the formal result is INCONCLUSIVE. It does not satisfy GO under either branch. The recent date-cut cohort and corrected method remain INCONCLUSIVE, with only a future durable V2 cohort eligible for confirmation.

**E0 stage gate:** finish within 8 engineering hours, 45 founder minutes, $0, and the stated deadline. Hitting a resource cap produces PROGRAM_STOP with evidence INCONCLUSIVE; it does not manufacture a strategy KILL and it does not authorize more tooling.

---

## Task 3: Record and enforce the decision before any strategy work

**Product file:**

- Modify: docs/DECISIONS/track_record_program_v2.md

- [ ] Record input hashes, aggregate evidence, known exploratory exposure, classification, date, owner, budgets consumed, and the exact next authorized stage.
- [ ] Reconcile the running time/cost ledger and record recurring commitments before the decision.
- [ ] Record these immediate decisions:
  - daily legacy exit-only thesis: KILL;
  - current daily method: INCONCLUSIVE;
  - tracker exit change: NO CHANGE;
  - full trust rig: HOLD.
- [ ] Record that yfinance is research-only and that no public claim may use this diagnostic.
- [ ] Add a CI text check that no plan, page, or report calls -$6,263.27 an actual cash loss or combines the books.
- [ ] Do not create implementation tickets for a killed branch.

**Exit evidence:** the repository has a machine-readable human decision that prevents another agent from interpreting INCONCLUSIVE as GO.

---

## Task 4: If daily collection continues, replace the corrupt observation semantics

This task is conditional. If Georgi does not need the owner-specific daily experiment, the lower-cost action is to preserve the legacy rows, stop publishing their metrics, and disable only new daily paper-record creation through an explicitly approved configuration change. Do not disable the daily analysis report itself.

**Product files:**

- Create: daily_signal_ledger.py
- Create: tests/test_daily_signal_ledger.py
- Modify: run_paper_gate2.py
- Modify: tests/test_paper_gate_causality.py
- Modify: paper_trading.py only to route legacy recording behind an explicit compatibility flag
- Modify: trade_log.py only if required to make legacy mode read-only/frozen

### New observation model

~~~python
@dataclass(frozen=True, slots=True)
class DailySignalObservation:
    observation_uuid: str
    intent_uuid: str
    intent_id: int
    decision_id: int
    run_id: str
    strategy_version: str
    symbol: str
    action: Literal["BUY", "TRIM"]
    quantity_micros: int
    entry_price_micros: int
    observed_at: datetime
    quote_provider: str
    quote_as_of: datetime
    config_digest: str
~~~

New tables:

- daily_strategy_versions
- daily_signal_observations
- daily_long_shadow_positions
- daily_long_shadow_outcomes

- [ ] Write schema tests in a temporary database before production migration code.
- [ ] Store price and quantity as integer millionths; use Decimal and ROUND_HALF_EVEN at boundaries.
- [ ] Add uniqueness on intent_uuid and on the exact run/strategy/symbol/action observation identity.
- [ ] Thread verdict.intent_id and verdict.decision_id from gate_and_execute into the recorder.
- [ ] Thread run_manifest.run_id, input_source, and per-symbol quote_as_of into every observation.
- [ ] Derive strategy_version from canonical JSON over:
  - code commit;
  - Kelly and portfolio-sizing configuration;
  - universe;
  - gate configuration;
  - exit policy;
  - cost policy.
- [ ] Only APPROVED decisions may create observations.
- [ ] BUY can open a daily_long_shadow_position.
- [ ] TRIM records a recommendation observation only. It never opens a short, gets a stop/target, or creates row-level trade P&L.
- [ ] Freeze existing paper_trades rows as daily-kelly-legacy-unversioned. Do not update their outcomes or relabel their P&L.
- [ ] Put the new writer behind DAILY_SIGNAL_LEDGER_V2=1, default off until a reviewed migration and explicit production authorization.
- [ ] Add a dry-run migration command that prints counts and schema changes without writing.
- [ ] Back up the production database with SQLite's backup API immediately before an authorized migration; verify restore in a temporary location before enabling V2.
- [ ] Prove with tests:
  - rejected decisions produce no observation;
  - duplicate retries are idempotent;
  - TRIM cannot create a shadow position;
  - every position has a durable observation and strategy version;
  - stale/missing quotes fail closed;
  - current owner holdings are never serialized into public output;
  - legacy table contents remain byte-for-byte unchanged in migration fixtures.
- [ ] Run focused tests, then the paper/risk regression set:

    python -m pytest -q \
      tests/test_daily_signal_ledger.py \
      tests/test_paper_gate_causality.py \
      tests/test_paper_book.py \
      tests/test_risk_gate.py

- [ ] Run Ruff and all required reviews.

**O0 GO:** one offline replay plus one explicitly authorized clean paper session produce causal, versioned, provenance-complete observations with zero legacy mutation and no synthetic short.

**O0 program stop:** if migration cannot meet the 16-hour cap, changes the risk gate, requires daily founder repair, or writes TRIM P&L, classify the observation implementation as failed/incomplete, disable new daily paper recording after explicit authorization, and preserve the analysis engine. This is not evidence that the strategy itself is bad.

---

## Task 5: Run CURRENT_METHOD_ENTRY_EDGE_V1 on a passive forward cohort

**Files:**

- Extend: trading/daily_edge_diagnostic.py
- Extend: scripts/run_daily_edge_diagnostic.py
- Extend: tests/test_daily_edge_diagnostic.py
- Append only: daily_strategy_trial_registry.json

- [ ] Filter only the exact V2 strategy_version; never blend legacy rows.
- [ ] Require at least 15 BUY observations mature through 10 sessions, across at least 10 entry sessions and 8 symbols.
- [ ] Run no new parameter search during collection.
- [ ] Produce an exception-only weekly aggregate. Founder review is at most 30 minutes.
- [ ] Stop at the first of:
  - 15 mature ten-session BUY observations;
  - 2026-09-30;
  - any data-integrity incident;
  - the founder-time cap.
- [ ] Apply this entry-edge gate once. It does not choose or authorize an exit variant.

**C0 GO:** 5- and 10-session median stored-entry absolute returns and matched close-to-close active returns are positive after charging the strategy leg 50 bp, at least 60% of rows are positive, full and purged cohorts agree, and no single symbol/session supplies more than 35% of total positive return. Record the result and request a separately budgeted daily-strategy program; do not route daily rows into tracker S0 or A0.

**C0 KILL:** the data-quality gate passes, the minimum sample is reached, and the economic rule fails. Archive the strategy version and stop performance work on the daily book.

**C0 INCONCLUSIVE/PARKED:** data integrity fails or valid cadence cannot reach the minimum by the deadline. Do not extend the window automatically and do not call this a strategy KILL.

---

## Task 6: Repair the tracker stop-grid comparison before selecting any candidate

The current six-policy report compares different resolved populations. That censorship can make a faster-closing policy look better or worse. Repair measurement before interpreting it.

**Sidecar files:**

- Modify: trading/stop_policy_grid.py
- Modify: tests/test_stop_policy_grid.py
- Create and append only: tracker_exit_trial_registry.json
- Do not modify: strategy_variant_registry.json

- [ ] Write failing tests where policies resolve different subsets.
- [ ] Add two explicit comparison modes:
  - paired_mature: only positions with a complete common evaluation window;
  - common_mark: every position valued at the same as-of session if still open.
- [ ] Report per-position paired differences, not just policy-level averages.
- [ ] Add entry and exit cost stress at 20 and 50 bp total round trip.
- [ ] Add common-cohort count, unresolved IDs, symbol/session clusters, and leave-one-cluster-out sign.
- [ ] Record the already inspected six policies in the separate tracker_exit_trial_registry.json. Do not reset the counter and do not change the gate's Deflated Sharpe input during a measurement-only study.
- [ ] If a later human authorizes a live strategy-version cutover, reconcile the separate exit trials into the official multiple-testing/DSR governance in a way that can only make the statistic equally or more conservative. That gate-input change is a separate reviewed migration.
- [ ] Keep CPCV measurement-only and return insufficient when the sample cannot support its groups.
- [ ] Verify no write to paper_positions, paper_outcomes, strategy_variant_registry.json, or any autonomy-gate input.
- [ ] Run:

    python -m pytest -q \
      tests/test_stop_policy_grid.py \
      tests/test_historical_settle.py \
      tests/test_cpcv.py \
      tests/test_variant_registry.py

- [ ] Run Ruff and required reviews.

**T0 decision:** keep live_ratchet unless exactly one independently motivated candidate has:

- at least 90% common-window coverage;
- positive expectancy after 50 bp;
- paired median uplift at least 1 percentage point;
- improvement on at least 60% of positions;
- positive result in both chronological halves;
- no worse worst loss or drawdown proxy;
- result not reversed by removing any one symbol/session cluster.

No current candidate has earned this decision. If none passes, stop and keep the baseline.

---

## Task 7: If exactly one candidate earns T0 GO, run an isolated paired shadow

**Sidecar files:**

- Create: trading/paired_exit_shadow.py
- Create: tests/test_paired_exit_shadow.py
- Modify only at an injection seam: trading/autopilot.py
- Reuse: trading/historical_settle.py
- Reuse for pattern only: consensus_shadow.py

### Required property

One immutable signal observation fans out to baseline and candidate. Both versions receive the same symbol, entry time, entry price, notional, quote provenance, and bar stream.

- [ ] Create separate tables:
  - paired_shadow_versions;
  - paired_shadow_signals;
  - paired_shadow_positions;
  - paired_shadow_outcomes.
- [ ] Foreign-key every position to the same paired_shadow_signal.
- [ ] Make version definitions immutable after their first position.
- [ ] Permit exactly baseline plus one candidate in this experiment.
- [ ] Use deterministic candle/path exits with entry-day exclusion, worst-first ambiguity, and provider provenance.
- [ ] Apply the same fixed notional and 50 bp stress to both.
- [ ] Never import these rows into paper_outcomes and never expose them as autonomy-gate progress.
- [ ] Tests must prove:
  - identical fanout inputs;
  - retry idempotency;
  - no gate-table writes;
  - version isolation;
  - deterministic replay;
  - missing bar fails closed;
  - same-bar ambiguity is conservative;
  - reporting pairs by signal ID, not by row order.
- [ ] Run focused tests, autopilot regressions, Ruff, Python review, general review, and security review.

**S0 GO:** at least 30 paired closes are reached within 8 weeks, with all of:

- candidate expectancy positive after 50 bp;
- profit factor at least 1.2;
- paired median uplift at least 1 percentage point;
- at least 60% of pairs improve;
- positive candidate expectancy in both chronological halves;
- no worse maximum single-trade loss;
- no unresolved data incident;
- operations below 30 founder minutes per week.

**S0 KILL:** the paired sample is sufficient and an economic/tail condition fails, or the candidate requires a manual exception. Archive it; do not tune a replacement in the same program.

**S0 PARKED/PROGRAM_STOP:** fewer than 30 paired closes by eight weeks is PARKED for insufficient cadence; an unresolved data-integrity incident or resource-cap breach fails the experiment implementation. Neither is mislabeled as economic KILL.

---

## Task 8: After S0 GO, freeze the thin forward account protocol

This is the earliest point at which the prior institutional plan becomes active.

**Sidecar files:**

- Create: docs/TRACK_RECORD_PROTOCOL_V1.md
- Create: trading/track_record_protocol.py
- Create: tests/test_track_record_protocol.py
- Create: trading/performance_schema.py
- Create: tests/test_performance_schema.py

- [ ] Freeze account ID gesh-engine-forward-v1 and start with $100,000.00.
- [ ] Include only post-inception consensus/market_scan BUY entries for the winning strategy version.
- [ ] Exclude manual rows, daily-analysis rows, legacy open rows, crypto, zero-quantity rows, retired rows, short entries, and partial trims.
- [ ] Freeze entry/exit slippage, fees, mark clock, quote-staleness threshold, benchmark, sizing, universe, and corporate-action policy.
- [ ] A behavior change seals the account and starts V2; no retroactive recomputation.
- [ ] Use cents, price millionths, and quantity millionths with Decimal conversion.
- [ ] Add append-only source events, cash ledger, lots, marks, and reconciliation schema.
- [ ] Preserve the existing autonomy gate unchanged.
- [ ] Run protocol/schema tests, fixed-point property tests, Ruff, and reviews.

**Exit evidence:** the account definition is machine-readable, forward-only, cash-constrained, and cannot ingest either legacy book accidentally.

---

## Task 9: Build only the accounting needed to measure net edge

**Sidecar files:**

- Create: trading/paper_account.py
- Create: trading/nav_evidence.py
- Create: scripts/report_evidence_account.py
- Create: tests/test_paper_account.py
- Create: tests/test_nav_evidence.py

- [ ] Keep A0 internal. Do not add a public publisher, product validator, signing, or external anchor in this stage.
- [ ] Project approved source events from the Task 8 schema into cash and lots idempotently.
- [ ] Reject insufficient cash rather than borrowing silently.
- [ ] Apply entry/exit costs and record them separately.
- [ ] Record session-dated provider marks, price-as-of, adjustment basis, and corporate actions.
- [ ] Missing/stale marks block NAV promotion.
- [ ] Compute daily net NAV and time-weighted return from the account, not from summed trade returns.
- [ ] Run an exposure- and cash-flow-matched SPY shadow account with the same costs and timestamps.
- [ ] Reconcile:
  - cash plus marked lots equals NAV;
  - event replay equals materialized state;
  - source position count and account lots agree;
  - all marks and actions have provenance.
- [ ] Add 20/50 bp stress and exact denominator labels.
- [ ] Emit an internal deterministic JSON evidence report with input hashes, protocol/account/strategy version, reconciliations, and metrics. It is not a customer artifact.
- [ ] Run focused tests, full accounting regressions, property tests, Ruff, database review, Python review, code review, and security review.

**A0 operating gate:** 20 consecutive sessions reconcile automatically with zero unresolved drift and less than 30 founder minutes per week.

**A0 build gate:** the internal account/replay/report implementation finishes within 24 active engineering hours and six weeks after authorization.

**A0 evidence gate:** observe until the first of 30 exact-account closes or 90 days after account inception. GO requires positive net TWR, positive matched active return after 50 bp, positive results in both chronological halves, and no gate/risk violation.

**A0 program stop:** unresolved accounting drift, a build-cap breach, or operations above the weekly cap fails the implementation. **A0 strategy KILL:** data integrity passes and net/matched evidence is non-positive at the decision point. **A0 PARKED:** the 90-day evidence deadline arrives without 30 account closes.

---

## Task 10 (T1): Publish independent per-ledger artifacts after the T1 entry gate

**Sidecar files:**

- Create: trading/track_record_artifact.py
- Create: scripts/publish_track_record.py
- Create: tests/test_track_record_artifact.py

**Product files:**

- Create: track_record_artifact.py
- Create: daily_disclosure_artifact.py
- Modify: proof_page.py
- Modify: server.py
- Create: tests/test_track_record_artifact.py
- Create: tests/test_track_record_route_failure.py

- [ ] Emit a tracker-only canonical JSON artifact from the sidecar with schema version, account/protocol/strategy ID, generation time, last mark time, NAV/TWR, benchmark, gate DTO, and reconciliation status.
- [ ] Emit a separate daily-disclosure artifact from the product daily chain. It contains its own version, aggregate diagnostics, limitations, generation time, and source hash, but no tracker metric.
- [ ] If a top-level manifest is used, it may reference the two artifact paths/hashes and presentation version only. It contains no cross-ledger count, P&L, return, or completeness claim.
- [ ] Include SHA-256 and atomically promote through same-directory rename.
- [ ] Product validates each artifact independently for schema, checksum, age, book/account ID, cohort predicate, and reconciliation/basis before rendering.
- [ ] Product never imports the sibling package or opens either trading database in artifact-required mode.
- [ ] Missing, stale, malformed, mismatched, or unreconciled artifact returns a truthful unavailable state or 503; it never falls back to an old positive claim.
- [ ] Keep each ledger as a distinct producer-owned object. There is no combined return and neither producer embeds the other ledger's disclosure.
- [ ] Add a semantic smoke that checks exact data attributes and forbidden comparative language.
- [ ] Run focused tests, full route/security regressions, Ruff, and all reviews.

**Exit evidence:** port 9000 presents two independently validated, producer-owned artifacts without database access during a public request.

---

## Task 11: Activate the deferred institutional trust rig only after a customer gate

T1 does not start merely because engineering is possible. Entry requires:

- A0 evidence gate passes;
- the autonomy gate remains unchanged and passes on its own cohort;
- at least 100 closes exist for the exact A0 account_id, protocol version, and immutable strategy version, plus 90 complete matching marked sessions;
- one named prospect/customer states in writing that verified performance affects purchase or renewal;
- documented commercial-use rights cover the selected market-data source, derived-performance publication, retention, exchange coverage, and expected volume within the T1 $500 stage cap;
- a separate 40-hour/$500 authorization is recorded.

Then implement the deferred components from the 2026-08-02 plan:

- append-only event hash chain and outbox;
- Ed25519 signing and key separation;
- externally retained daily seal;
- artifact-only signed product validator;
- semantic production smoke and alerts;
- backup/restore drill;
- unsupported-corporate-action fail-closed behavior;
- forward-validation report.

Do not provision every conceivable corporate action before it occurs. Support the declared V1 set; unknown actions block claims.

The T1 eight-week implementation clock starts only after every entry condition above and written authorization are present.

**T1 GO:** 30 consecutive signed publications, successful restore/replay, external seal continuity, no semantic smoke failure, and evidence remains positive.

**T1 claim KILL:** claim evidence turns non-positive or a material reconciliation/signature discrepancy invalidates the evidence. **T1 program stop:** operational burden exceeds the cap or external retention is unreliable.

---

## Task 12: Fund and name independent review before using a comparative claim

R0 entry requires a signed quote naming:

- an independent technical reviewer for source-to-artifact recalculation;
- an independent Bulgarian/EU-qualified legal reviewer for customer-facing performance wording.

Budget reserve:

- $1,500 maximum technical review;
- $1,000 maximum legal wording review.

- [ ] Give the technical reviewer read-only snapshots, protocol, code commit, artifacts, trial registry, and exact SQL/cohort predicates.
- [ ] Require independent recomputation of cash, lots, NAV, TWR, costs, benchmark, gate metrics, hash chain, and signature.
- [ ] Give the legal reviewer only the final intended claims plus supporting evidence and limitations.
- [ ] Publish the scope and date of review, not a vague audited label.
- [ ] A material discrepancy disables comparative claims until corrected forward; never rewrite history silently.

If no named reviewer signs within the budget and 30-day window, the product may continue showing factual paper diagnostics but may not use outperformance, audited, independently verified, or equivalent wording.

---

## Test and review matrix

| Risk | Required evidence |
|---|---|
| Source/live regression | selective restore test, forbidden-copy test, live semantic smoke after authorized deploy |
| Hidden second book | separate disclosure test, unavailable-book test, no-aggregation test |
| Causal contamination | exact approved/rejected cohort test, ambiguity fail-closed test |
| Survivor bias | OPEN entries included; no outcome predicate |
| Look-ahead | entry-day bar excluded; history not truncated at ledger exit |
| Exit ambiguity | stop-first same bar; gap-through penalty; no target gap bonus |
| Benchmark mismatch | identical entry/session dates; matched account in A0 |
| Multiple testing | append-only tracker and daily trial registries |
| Strategy drift | immutable version digest and forward cohort boundary |
| TRIM accounting | recommendation only; cannot create short/P&L |
| Public privacy | aggregate-only output and customer-leak sweep |
| Mutable database | read-only URI for research; artifact-only public path in A0 |
| Portfolio-return claim | cash/NAV/TWR required; sums of trades always labeled diagnostic |
| Operational burden | automated exception-only report; 30-minute weekly cap |

---

## Data-provider and research rationale

- The yfinance project states that it is open source, intended for research/education, and that Yahoo data is for personal use. It is therefore a private falsification source, not the sole customer-proof source: [yfinance documentation](https://ranaroussi.github.io/yfinance/).
- Massive documents that flat files are unadjusted and that split/dividend handling must use adjusted REST data or explicit corporate-action adjustments. Its current individual plans range from free to $199/month; business use has separate licensing: [Massive flat-file documentation](https://massive.com/docs/flat-files/stocks/overview?assetClass=stocks&license=personal&name=stocks_basic), [Massive pricing](https://massive.com/pricing?product=stocks).
- Alpaca documents free IEX coverage versus broader paid consolidated coverage. Provider coverage must be part of the protocol, not silently mixed: [Alpaca Market Data API](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api).
- Kaminski and Lo show that stop-loss rules can subtract value under random-walk behavior and add value under momentum. Exit value is empirical and regime-dependent, not assumed: [When Do Stop-Loss Rules Stop Losses?](https://www.sciencedirect.com/science/article/abs/pii/S138641811300030X).
- Bailey and López de Prado's Deflated Sharpe Ratio addresses selection bias, multiple testing, and non-normality. Every inspected candidate must remain in the trial count: [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
- GIPS guidance says materially different mandates/strategies must not share a composite. GESH does not claim GIPS compliance; this is the methodological reason to keep the two books separate: [GIPS Standards Handbook for Firms](https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/).
- SEC marketing guidance emphasizes substantiation, fair balance, limitations, and avoiding selective performance presentation. Applicability requires counsel; this is a disclosure-quality benchmark, not a legal opinion: [SEC Investment Adviser Marketing guide](https://www.sec.gov/resources-small-businesses/small-business-compliance-guides/investment-adviser-marketing).

---

## Final acceptance criteria

The program is successful even if the strategy is killed. Success means:

- no misleading public claim or source/live regression;
- every automated book is disclosed with a precise boundary;
- no invalid combined metric exists;
- a cheap, reproducible test decides whether more work is warranted;
- failed or insufficient evidence actually stops spending;
- any surviving strategy is measured forward with causal, versioned, cost-aware data;
- portfolio return appears only after a real cash/NAV account exists;
- institutional assurance is purchased only after edge and customer value are both demonstrated.

The program is not successful merely because the page stays online, P&L is positive in dollars, or an audit rig is technically impressive.

---

## Execution handoff

Recommended execution method: use superpowers:subagent-driven-development for P0 and E0, one task at a time, with review after each commit. Do not dispatch O0 or later tasks until docs/DECISIONS/track_record_program_v2.md records the preceding gate as GO.

First authorized sequence:

1. Task 0 — preserve corrected public source.
2. Task 1 — separate scope disclosures.
3. Task 2 — reproducible Stage E0 diagnostic.
4. Task 3 — record KILL/INCONCLUSIVE/NO-CHANGE decisions.
5. Stop and request a new decision.
