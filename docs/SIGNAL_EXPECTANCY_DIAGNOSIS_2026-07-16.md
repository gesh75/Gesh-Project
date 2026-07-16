# Signal Expectancy Diagnosis — why the engine cohort loses money

**Date:** 2026-07-16 · **Cohort:** paper `paper_outcomes`, sources ≠ `manual`
(`consensus` + `market_scan`) · **Reproduce:** `python3 scripts/diagnose_signal_expectancy.py`
(reads the tracker DB read-only; `$GESH_TRACKER_DB` or the sibling `gesh-project-trading/gesh_financial.db`).

> **Bottom line:** this is not a bug to tune — it is a **signal that has not demonstrated an
> edge.** The engine cohort is losing, its out-of-sample performance retains **0%** of its
> in-sample edge, and its one profitable name is indistinguishable from luck at n=14. Do **not**
> go live. The commercial read: keep selling the research tooling, not signal accuracy.

## The numbers (live gate DTO + raw outcomes, 2026-07-16)

| Metric | Value | Gate bar | |
|---|---|---|---|
| Engine closed trades | **14** | ≥ 100 | ❌ (and ~1009 needed for a meaningful Deflated Sharpe) |
| Expectancy / trade | **−0.51%** | > 0 | ❌ negative edge |
| Win rate | **14.3%** (2 W / 10 L / 2 BE) | descriptive | ℹ️ |
| Avg win / avg loss | +11.7% / −3.05% → **payoff 3.85:1** | — | payoff is *healthy* |
| **Break-even win rate for that payoff** | **20.6%** | — | **actual 14.3% is BELOW it** |
| Profit factor | 0.67 | ≥ 1.5 | ❌ |
| Sharpe | **−0.67** | ≥ 0.5 | ❌ negative |
| Prob. Sharpe P(true SR>0) | 0.38 | ≥ 0.95 | ❌ more likely than not, no edge |
| OOS consistency | **0.0** ("OOS PF retains 0% of IS") | ≥ 0.4 | ❌ |
| Max drawdown | 26.5% | < 10% | ❌ |
| Monte-Carlo profitable fraction | 0.30 | ≥ 0.90 | ❌ |
| Agreement rate | 0.96 (26 ratings) | ≥ 0.70 | ✅ (the one pass) |

## What the trade tape actually shows

Per-symbol P&L (engine cohort), sorted:

| Symbol | Entries | W | L | P&L (USD) |
|---|---|---|---|---|
| GS | 2 | 0 | 2 | **−135.78** |
| TSM | 2 | 0 | 1 | −24.89 |
| GE | 3 | 0 | 3 | −23.74 |
| SPY | 1 | 0 | 1 | −15.18 |
| HOOD | 2 | 0 | 2 | −7.40 |
| CAT | 2 | 0 | 1 | −1.28 |
| **UNH** | 2 | 2 | 0 | **+138.54** |

**Single-name dependence:** cohort total is −$69.74. Remove the one winner (UNH) and it is
**−$208.28**. UNH *is* the strategy's entire apparent edge; everything else lost.

## Root cause — ranked, evidence-backed

### 1. No demonstrated predictive edge on entry (the primary cause)
OOS consistency **0.0** — the out-of-sample profit factor keeps **none** of the in-sample edge;
prob-Sharpe **0.38**; Sharpe **−0.67**. The consensus BUY does not forecast forward returns on
this sample. This is the statistical signature of *no edge / overfit*, not of a good signal
executed badly.

### 2. Win rate is structurally below the payoff break-even
The payoff ratio (3.85:1) is *good* — winners are ~4× the size of losers. The problem is
**frequency**: at 3.85:1 you need to win **20.6%** of the time to break even; the engine wins
**14.3%**. Either entry precision must rise above ~21%, or the payoff must widen further (let
winners run / cut losers sooner). Tuning risk sizing cannot fix a below-break-even hit rate.

### 3. Concentration + repeated bets on losing names
GS (−$135.78 over 2 entries) and GE (−$23.74 over 3) dominate the loss. The engine re-backs
names it is already wrong about. Notably, `gesh_core.kelly.run_kelly_analysis` **already** has a
correlation haircut + gross cap — but that governs *sizing at the book level*, not the
**entry/re-entry decision**. The signal keeps issuing BUYs on the same declining tickers.

### 4. Long-only into a chop/down tape
100% of engine actions are BUY. The June–July entries got chopped. There is no regime veto on
entries and no short side, so a ranging/bearish stretch is pure downside for the cohort.

### 5. Sample far too small to conclude anything anyway
n=14 vs. the gate's own **~1009** trades needed for a trustworthy Deflated Sharpe. Even the +$138
UNH result is 2 correlated entries — noise, not proof. The gate is working correctly; it should
stay closed.

## Recommended next diagnostics (in priority order)

1. **Signal-precision backtest, decoupled from fills.** For every historical consensus BUY,
   compute forward return at +5/+10/+20 trading days. Question: is the raw hit-rate > 20.6%?
   If not, the signal itself is the defect — stop tuning execution.
2. **Per-name entry cap + re-entry cooldown.** Forbid >1 concurrent unit per name and re-entry
   within N days of a stop. Replay the existing 14 trades with duplicate GS/GE entries removed
   to quantify the recovered P&L.
3. **Regime veto on entries.** Wire `regime.py` to suppress BUYs when the regime is
   ranging/bearish; measure the expectancy delta on the tape.
4. **Winner-capture audit.** UNH ran 23–24 days for +12%. Check exit reasons on the losers — are
   stops/targets cutting would-be winners early, suppressing the win rate below break-even?
5. **Report edge with confidence intervals.** Publish expectancy ± CI so "no edge yet (n=14)" is
   unmistakable, and keep `/track-record` honest per the proof-page standard.

## The honest conclusion

The consensus engine, as it stands, **does not have a provable edge and is losing on the paper
cohort.** The most likely correct next move is to **treat the signal as unproven and rebuild or
re-scope it** (precision-first entries, regime-gated, dedup'd), not to tune sizing around it —
and to keep the commercial story on the research/analysis surface that already works, exactly as
the Decision Integrity Lab Phase 0 plan argues.
