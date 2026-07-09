# GESH Consensus Engine — Win-Rate Audit Brief (external review)

*Prepared for an external reviewer (Grok). This is a companion to `GROK_REVIEW_2026-07-08.md` (the customer surface). It covers a different subsystem: the **paper-trading consensus engine** that must prove itself before any live trading. Findings were produced by a multi-agent audit and **spot-verified against the real code** (verified facts are marked ✅). The engine code lives in **`gesh-project-trading/`** (`trading/autopilot.py`, `consensus.py`, `trading/risk_gate.py`, `macro_connector.py`) and **`gesh-core/`** (`src/gesh_core/trading/{config,contract,levels}.py`, `trading/tracker.py`) — separate repos from the `:9000` product.*

---

## 1. What to review & why

GESH's public sales asset is a **signal-accuracy track record**: a paper-trading engine runs a consensus signal on autopilot, and its win rate is the proof shown to customers. The autonomy gate to go live requires **≥30 closed trades, win rate ≥55%, Sharpe ≥0.5, max-drawdown <10%**. Right now the automated **consensus signal is 0 wins in 7 closed trades** (the owner's *manual* entries are 3/9). This brief diagnoses *why* and proposes fixes. **Review the diagnosis for correctness and the fix plan for soundness — and flag what is overfit to a 7-trade sample.**

---

## 2. Ground truth — the 16 closed paper trades

From `paper_outcomes` (the autonomy-gate ledger). **Consensus source: 0 W / 5 L / 2 BE, avg −3.82%, every trade a BUY:**

| symbol | entry → exit | P&L | outcome | opened |
|---|---|---|---|---|
| GS | 1097.81 → 1011.30 | −7.9% | LOSS | 06-19 |
| TSM | 473.09 → 444.36 | −6.1% | LOSS | 06-22 |
| CAT | 1015.61 → 1014.93 | −0.1% | BE | 06-22 |
| GS | 1109.02 → 1011.30 | −8.8% | LOSS | 06-22 |
| SPY | 748.82 → 720.97 | −3.7% | LOSS | 06-22 |
| TSM | 444.46 → 444.36 | 0.0% | BE | 06-23 |
| CAT | 982.71 → 981.43 | −0.1% | LOSS | 06-23 |

**Manual source: 3 W / 5 L / 1 BE, avg +2.74%** — winners were HOOD (+25.3%, +22.6%) and SPY (+4.5%). Aggregate: **win rate 19%, avg win +37.5%, avg loss −4.2%, profit factor 0.96.**

Two patterns jump out and drive everything below: (a) **long-only, clustered 06-19→06-23 into a falling market**, and (b) **the same names re-bought as they fell** (GS ×3, TSM ×2, CAT ×2).

**Key statistical tell:** every losing/breakeven consensus trade scored `raw_score ≤ 0.64`; the one big winner (HOOD, manual) scored **0.81 / 0.86**. The score *does* separate — but on 7 trades that separation is not yet significant (see §6).

---

## 3. Verified facts (checked against the real code)

- ✅ `min_entry_score` default = **0.35** (`gesh-core/src/gesh_core/trading/config.py:65`, env default also 0.35 at `:111`); enforced at `trading/autopilot.py:440`.
- ✅ The per-symbol **regime is never consulted in the autopilot BUY path** — `grep -c regime trading/autopilot.py` = **0**.
- ✅ **No cooldown / re-entry / loss-memory anywhere in `trading/`** — grep for `cooldown|re-entry|last_loss|blackout` returns nothing.
- ✅ `from_consensus()` builds every intent with `stop_loss=None, take_profit=None` — exits come entirely from the ATR ladder in `levels.py`.
- ✅ `node_relative_strength` votes BULLISH on `rs > 3` with **no `s_ret > 0` requirement** (`consensus.py:263`) — a stock *falling less than SPY* votes bullish.

---

## 4. Root-cause findings (grouped; file:line + trade evidence)

### A. The exit makes a win structurally impossible — **0/7 ever hit target**
`_atr_levels` sets `reward = 5×ATR` (an independent raw-ATR multiple, not an R-multiple of the clamped `3×ATR` stop) — `gesh-core/.../levels.py:72-76`. The grid was OOS-validated on a "fresh momentum universe" but is applied to low-vol mega-caps, so the target is never reached and losers ride to the wide stop. **0 of 7 consensus trades touched their take-profit.** Fix: `reward = TARGET_R × clamped_stop` (TARGET_R≈2.0), cut stop mult 3.0→~1.75. *This is the precondition for any non-zero win rate.*

### B. No regime / market veto — it bought momentum into a decline
Entry gates in `pre_market_scan` are only: confidence ≥ min_confidence (`autopilot.py:434`) → `raw_score ≥ min_entry_score` (`:440`) → open-book dedup (`:446`). Regime only *soft-reweights* nodes in `consensus.py:_regime_weights` (RANGING merely trims Momentum 1.5→1.0); it never blocks. The only market brake, `_market_regime_blocks_buy` (`risk_gate.py:190`), fires only when **SPY < its 200-day MA** — far too slow to catch the 4-day −3.7% slide, and **fails open** on data gaps. All 7 losers were RANGING into a falling SPY. Fix: hard per-symbol regime veto for new longs (require trending / block RANGING) + a fast market brake (SPY < 20/50-day MA or 5-day return < 0); fail regime-detection **safe** to caution weights (`consensus.py:734` currently falls back to full-momentum weights).

### C. No cooldown / no averaging-down — it re-bought the same falling names
GS was bought 06-19, 06-22 **and** 06-23 (both legs closed to the identical 1011.30); TSM and CAT twice each. The open-book dedup (`autopilot.py:446`) only blocks a *currently-open* name, fails open on any tracker error (`_held_syms = set()`), and is evaluated at 08:30 queue time, not 09:35 execute time. There is **no** per-name loss cooldown and **no** "don't buy below an open leg" rule. Fix: per-name cooldown (skip if entered/lost within N days) + reject a BUY below the average cost of any open leg of the same symbol; make the dedup fail **closed** for BUYs.

### D. The signal is a momentum-chaser that buys tops
Four of the highest-weighted directional nodes are collinear restatements of "price went up recently": `node_momentum` (RSI>55 + MA cross), `node_trend` (%B>55 = upper Bollinger half), `node_relative_strength` (outperformed SPY), `node_volume` (up on volume) — together ~72% of the vote weight (`consensus.py:691-701`). There is **no overbought/extension veto** (`pct_from_high` is computed at `consensus.py:213` but never used), and `_regime_weights` is **pro-cyclical** — it *boosts* Momentum to 2.5 in the "MOMENTUM-HIGH" regime that `regime.py` itself labels "extremely overbought, risk of near-term pullback." Rel.Strength's missing `s_ret>0` (fact §3) manufactures bullish votes on names that merely fall less than SPY. Fixes: require `s_ret>0` for a bullish RS vote (one line); add an RSI>70 / within-2%-of-high veto; down-weight (not boost) Momentum when overbought; require ≥1 non-price node (fundamentals/analyst) non-bearish before a BUY.

### E. Conviction floor & sizing are too permissive / pro-cyclical
`min_entry_score=0.35` sits **below** the live BUY cluster (0.39–0.53) and far below the STRONG-BUY line (0.55); it removes only the empty 0.25–0.35 band. `from_consensus` sizes purely by verdict bucket (STRONG BUY→2%, BUY→1%) with **no trend discount** — so the two full-2% GS entries (both raw_score 0.64) were the two biggest dollar losers. Drawdown escalation (`risk_gate.py:326`) tightens the *data-coverage* floor, not the *conviction* floor. No per-day cap on new correlated entries (`MAX_ORDERS_PER_DAY=15` counts all orders, not new names) — 4 correlated longs opened in one 09:35 batch. Fixes: raise `min_entry_score` (conservatively to ~0.50, and/or escalate it in RISK_OFF); multiply target size by a trend/regime factor (0 or 0.5 when the name is below its 50-day MA); add `MAX_NEW_ENTRIES_PER_DAY`.

---

## 5. Ranked fix plan (by impact × ease × robustness)

**Tier 1 — principled, cheap, high-impact (recommended first; NOT overfit to 7 trades):**
1. **Reachable target** (A) — `reward = 2R` of the clamped stop; stop mult 3.0→1.75. *Precondition for any win.*
2. **Per-name cooldown + no-average-down** (C) — removes 4–6 of the 7 (the GS/TSM/CAT re-buys).
3. **Regime veto for new longs** (B) — every loser was RANGING into a falling SPY.
4. **Rel.Strength `and s_ret>0`** (D) — one line; stops "falls less than SPY" = bullish.

**Tier 2 — bigger levers, more care:** conviction floor 0.35→~0.50 + RISK_OFF escalation (E); trend-aware sizing (E); reachable breakeven trail + partial scale-out; per-day new-entry cap; move the symbol-agnostic Macro node to a book-level throttle.

**Tier 3 — structural (schedule, validate hard):** de-correlate the vote (the 4 collinear momentum nodes = 72% of weight → collapse to one capped trend factor + require a non-price confirmer).

---

## 6. The overfitting caveat (read this before trusting any number)

This is **7 closed consensus trades.** Claims like "a 0.70 floor removes all 7 losers and keeps HOOD" are real *for this sample* and **statistically meaningless** as a forward prediction — that is curve-fitting. The **direction** is trustworthy because the defects are structural and verified in code (unreachable targets, no regime veto, no cooldown, collinear momentum nodes); the **magnitude** is not. Ship Tier 1 as sound risk hygiene — not as a proven win-rate fix — keep the paper engine running, and **re-measure at ≥30 trades** before touching the conviction floor or the Tier-3 re-architecture.

---

## 7. Review questions for Grok

1. **Is finding A (unreachable 5×ATR target) really why 0/7 won, or is it entry quality?** If targets were 2R-reachable, would any of these 7 have banked a win, or were the entries so bad that even a reachable target stops out first?
2. **Regime veto vs. entry velocity.** A "require trending / block RANGING" veto would have skipped all 7 — but also skips a lot of legitimate mean-reversion setups. Is a hard veto right, or should RANGING just shrink size?
3. **Which Tier-1 fixes are principled vs. sample-fit?** Challenge each: is "no average-down," "reachable target," "s_ret>0," and "per-name cooldown" defensible on trading logic alone, independent of these 7 trades?
4. **Collinearity claim (finding D).** The docstring claims the 11 nodes are "intentionally uncorrelated," but 4 of them key off recent price. Is ~72% collinear weight the real master defect, and does collapsing them risk throwing away genuine signal?
5. **Fail-open safety.** Multiple gates fail *open* on data errors (regime detection → full-momentum weights; SPY-MA brake; open-book dedup → empty set). In a volatile decline (when Yahoo throttling clusters failures) this disables protection exactly when it matters. Should these fail *safe/closed*?
6. **Statistical honesty.** With 7 trades and a clean raw_score separation, what is the responsible minimum sample before acting on the conviction-floor lever — and is shipping Tier-1 (structural, not sample-fit) now the right call while that accumulates?
7. **Sizing pro-cyclicality.** Verdict-bucket sizing put full 2% on the two highest-scoring names, which were the two biggest losers. Is a trend/regime size multiplier the right fix, or should all consensus entries be flat-sized until the win rate is established?
