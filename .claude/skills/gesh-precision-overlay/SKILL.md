---
name: gesh-precision-overlay
description: Use when improving GESH paper win rate or profit factor, diagnosing losses on pay.geshfinance.com/track-record, raising min_entry_score, adding a precision overlay or meta-label on consensus BUYs, or deciding whether a BUY should actually enter.
---

# GESH precision overlay

Consensus is recall (BUY at `raw_score ≥ 0.25`). A BUY verdict is not an entry. Two independent levers, both BUY-only; SELL/HOLD never blocked. Neither feeds `check_autonomy_gate` (rule #47).

## Two levers

| Lever | Default | Gates |
|---|---|---|
| `min_entry_score` | **0.50** (was 0.35) | `raw_score` — conviction. 0.35 sat under the live BUY cluster 0.39–0.53. |
| `precision_overlay_blocks` | ON (`PRECISION_OVERLAY`) | votes — act/skip. Does not pick side. |

Canonical: `gesh_core.trading.autopilot_core`. Engine re-export: `trading.precision_overlay`. Product callers: `run_paper_gate2.py` and `trading/autopilot.py`.

## Overlay vetoes (when votes are present)

1. Rel.Strength == −1 — even STRONG BUY (highest-IC node).
2. Momentum and Trend both not +1, if either key is present.
3. Net (bullish − bearish) < 2. Skip Kelly and FFT_Cycle.
4. Macro == −1 unless verdict is STRONG BUY **and** Rel.Strength == 1.

Missing/empty votes fail OPEN. The floor still applies.

## Wiring

Engine `trading.autopilot`: after the floor, around held-symbol dedup, in `pre_market_scan`, `_inject_market_signals`, and `open_window` (re-check the stored row). Product: same overlay call in `trading/autopilot.py` and `run_paper_gate2.py`. Floor: `run_paper_gate2` must `load_risk_config().min_entry_score` — never restore dead `RiskConfig.from_env()` (always 0.35).

Engine book (`source in consensus, market_scan`) vs product daily-analysis book: never merge stats (rule #44). Never hide losers on the public ledger.

## Hard no

- Train an ML meta-labeler on the live engine book before a sealed holdout (rule #61).
- Raise win rate by omitting losers from track-record.
- Weaken fail-closed risk gates, `check_autonomy_gate`, `trial_ledger`, or `forward_quarantine`.
- `auto_trade=True` or `PAPER_MODE=false`.
- **REQUIRED BACKGROUND:** pinned-package-fail-open (new gesh-core symbols on long-lived jobs).

## Ship order (only when asked to commit)

1. Commit gesh-core first (canonical overlay + floor).
2. Bump **both** consumer `requirements.txt` 40-char SHA pins.
3. Path-limited engine, then product. Never `git add -A`.

Kill-switch: `PRECISION_OVERLAY=0`. Stricter floor: `MIN_ENTRY_SCORE=0.55`.

## Common mistakes

| Excuse | Reality |
|---|---|
| "Gate on confidence instead of raw_score" | confidence ≈ coverage, not strength (rule #49). |
| "Product already uses min_entry_score" | `run_paper_gate2` used to ignore it. Load `RiskConfig`. |
| "I'll train a classifier on the live closes" | Overfit. Rules only until a sealed holdout exists. |
| "Missing votes should fail closed" | Halts the funnel on scan rows without votes. Floor remains. |
