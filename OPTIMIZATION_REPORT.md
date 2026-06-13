# GESH Financial — Optimization Report
**Date:** April 19, 2026 | **Analyst:** Claude Sonnet 4.6 | **Version:** v5 (implemented)**

---

## TL;DR

- **Portfolio Manager (:9000)** — has 11 tabs, 3 AI providers, duplicated macro/news pulls. Cut to 5 tabs, drop Ollama entirely, cache yfinance. The tools are mostly good but overlapping and expensive.
- **Trading Engine (:9001)** — architecturally sound skeleton but has **3 critical bugs** that would cause crashes or wrong trades on first real run. 6 of 11 consensus nodes are noise. No learning loop. Not safe for live capital yet.

---

## PORTFOLIO MANAGER (:9000) — Full Analysis

### Current feature count
11 tabs · 3 AI providers (Claude, Gemini, Ollama) · 40+ REST endpoints

### What to KEEP (genuinely useful)
| Feature | Why |
|---|---|
| Deep AI Analysis (Claude Sonnet) | Core value — snowflake scores, fair value, predictions |
| 11-node Consensus | Best reasoning tool in the stack |
| Monte Carlo (Risk tab) | Good stress test, but needs wiring into decisions |
| Kelly Criterion | Good for position sizing — misused as a vote (see Trading section) |
| SEC filings watcher | High-value — 8-K alerts are genuinely useful |
| Fundamentals scan | Useful weekly — not daily |
| ECG gate | Solid behavioral circuit breaker |
| Regime detection (ADX/BB/RSI) | Good context injection for Claude prompts |
| FRED macro context | Useful overlay in regime.py |
| Paper Trades | Good audit trail |

### What to CUT
| Feature | Why |
|---|---|
| **Ollama / llama3.2:3b tab** | A 3B model cannot challenge Sonnet. "Second opinion" is random disagreement, not signal. Adds complexity, zero alpha. |
| **Scanner tab (consolidation zones)** | 5-minute interval scans for a position-holding portfolio manager is day-trader noise. Move to trading engine only. |
| **Polymarket scraper** (news_agent.py lines 42-200) | Overlaps entirely with FRED macro in regime.py. Two macro overlay systems doing the same job. Pick FRED — it's more reliable. |

### Redundancies to fix
| Problem | Files | Fix |
|---|---|---|
| News computed 3× | news_agent.scan_news + node_news in consensus + Polymarket | One DB read; don't re-fetch if fresh < 90 min |
| Kelly computed twice | /api/kelly endpoint + node_kelly in consensus | Consensus already runs kelly internally; /api/kelly is for display only — fine, but don't re-download price history |
| FRED macro in 2 places | regime.py get_macro_context() + trading's macro_connector.py | Portfolio manager should use trading's richer macro_connector (has VIX + HY OAS) |
| OHLCV downloaded per-node | consensus.py (portfolio version) | **Backport the single-fetch optimization from trading/consensus.py** — reduces network calls by ~5× per symbol |

### Gemini — keep or cut?
**Keep, but rewire.** The current implementation shows Claude's analysis to Gemini and asks "what did Claude miss?" — that's anchored, not independent. The only way Gemini adds value is if it sees the **raw portfolio payload without Claude's output first**, producing a genuinely independent verdict. If rewired that way, it's worth keeping as a periodic sanity check. If not rewired, cut it.

### Tab structure — collapse from 11 to 5

| New Tab | Replaces | What's in it |
|---|---|---|
| **Portfolio** | Overview + part of Intelligence | Holdings, equity summary, price chart, analyst targets inline per row |
| **Decision Engine** | AI Analysis + Signals | Deep Claude analysis + consensus votes + Gemini second-opinion as optional toggle |
| **Risk & Sizing** | Risk | Monte Carlo + ECG + Kelly clearly labeled (3 sub-cards answering 3 different questions) |
| **Intelligence** | Radar + Intelligence | SEC filings + News + Fundamentals (de-duped with consensus cache) |
| **Paper Trades** | Paper Trades | Unchanged |

**Cut:** Ollama tab, Scanner tab (consolidation), Gemini as a standalone tab (fold into Decision Engine as toggle)

### What's MISSING that a human PM actually needs
1. **"What changed since yesterday"** — new STRONG BUYs, sentiment flips, ECG newly SUSPENDED, regime changes. A diff view.
2. **Earnings calendar** — days-till-earnings per holding. Not in the stack at all.
3. **Correlation matrix** — you have Monte Carlo but don't show the user how concentrated they are in a single factor.
4. **"Decision summary card"** per holding: current price, cost basis, consensus verdict, Kelly-optimal %, nearest stop from ATR — all in one view instead of spread across 5 tabs.
5. **Macro condition indicator** — one banner showing current FRED regime (RISK_ON/OFF, yield curve state) prominently on every tab.

### API cost per deep analysis run
- 1 Sonnet call (30k input / 8k output) ≈ $0.07
- 10× news Haiku calls if Radar-News run ≈ $0.01
- ~25-30 yfinance calls (mostly OHLCV)
- 3 FRED CSV pulls

**Without the OHLCV optimization**: ~50 yfinance calls. With it: ~12. Fix this first.

---

## TRADING ENGINE (:9001) — Full Analysis

### 3 Critical Bugs — Fix Before Anything Else

#### Bug 1 — `autopilot.py` function signature mismatch 🔴 CRASH
`pre_market_scan` (line ~134) and `midday_rebalance` (line ~245) call `run_consensus_holding(sym, snapshot)` but the actual function signature is `run_consensus_holding(symbol, equity, total_portfolio, avg_cost, current_price)`. **The autopilot will crash on first run.** Nothing else matters until this is fixed.

#### Bug 2 — `execution.py` SELL logic closes entire position 🔴 WRONG TRADE
`_estimate_qty` for SELL action uses `qty = holding.quantity` (the entire position) instead of computing the delta to reach `target_position_pct`. A consensus saying "trim to 2%" executes as "close 100%." This is a trade-blowing error.

#### Bug 3 — `risk_gate.py` DATA_FRESH gate is fail-open 🟡 SAFETY RISK
When quote timestamp is missing, `_quote_age_seconds` returns None and the gate **passes** (line ~200). Should be fail-closed: unknown data age = stale data = reject.

---

### Consensus Engine — Node Quality Assessment

| Node | Quality | Keep? | Notes |
|---|---|---|---|
| **Momentum** (RSI + MA cross) | Medium | ✅ | Standard, decent in trending markets |
| **Trend** (ADX + %B) | Good | ✅ | Regime-conditional — genuinely useful |
| **Rel.Strength** vs SPY | Good | ✅ | Best node. Academically proven cross-sectional momentum |
| **Fundamentals** (yf.info) | Weak | ⚠️ Keep as slow filter | yf.info is stale. Use as quality pre-screen, not daily signal |
| **Analyst** | Weak | ❌ Demote to 0.5× weight | Lagging indicator, already priced in |
| **News** (Reddit VADER) | Noise | ❌ Demote to attention proxy | VADER misreads financial vocabulary; ticker matching is noisy |
| **Kelly** | Misapplied | ❌ Remove as vote | Kelly is sizing output, not directional. Redundant with Momentum. Use for sizing only. |
| **ECG** | OK | ✅ Keep as gate, not vote | Better as a position-suspension trigger than a consensus vote |
| **Volume** | Good | ✅ | Volume confirmation is valid accumulation signal |
| **FFT_Cycle** | Noise | ❌ Cut or demote | Spurious in non-stationary series. Will always find a "cycle" but it has no out-of-sample predictive validity |
| **Macro** (FRED/VIX) | OK overlay | ⚠️ Restructure | Same vote for every symbol — it doesn't differentiate holdings. Better as a portfolio-level gate than an equal-weighted per-symbol vote |

**Real signal nodes: 4 of 11** (Rel.Strength, Trend, Volume, Momentum)
**Noise/misapplied nodes: 4 of 11** (FFT, Kelly-as-vote, Analyst, Reddit)
**Context/slow-filter nodes: 3 of 11** (Macro, Fundamentals, ECG)

**Proposed fix:** Weighted consensus instead of equal-weight voting
```
Rel.Strength: 2.0×
Trend (ADX):  2.0×
Volume:       1.5×
Momentum:     1.5×
Fundamentals: 0.75× (quality filter)
Analyst:      0.5×
Macro:        portfolio gate, not a vote
ECG:          position gate, not a vote
Reddit News:  0.25× (attention proxy only)
Kelly:        sizing only, removed from vote
FFT:          removed
```
With this: quorum of 5 meaningful weighted-votes (not 5 of 11 equal nodes).

### Reddit Sentiment — Why it fails for trading
1. **VADER was trained on Twitter/movie reviews** — misreads financial vocabulary ("crushes earnings" = bearish to VADER)
2. **Ticker false-positives** — symbols like "A", "T", "X" match enormous non-relevant text
3. **WSB posts are often contrarian signal at extremes** — weighting them positively is backwards at sentiment peaks
4. **Fix**: Use as an **attention/volume-of-mention proxy** only. High mention count = heightened volatility expected. Not a directional vote.

### Risk Gate — Missing Gates
| Gap | Risk | Fix |
|---|---|---|
| Macro regime ignored by gate | RISK-OFF macro doesn't block trades | Add: in RISK-OFF, raise min_confidence from 0.75 to 0.85 and halve max_position_pct |
| No sector concentration cap | Can be 100% tech with gates all passing | Add sector exposure gate: reject if single sector > 40% of portfolio |
| No intraday stop enforcement | Positions blow through stops between 09:35 and 15:55 | Poll open positions every 15 min or use broker-side OCO orders |
| No consecutive-loss circuit breaker | Bot keeps trading after 5 straight loss days | Auto-suspend after 3 consecutive loss days |
| No earnings proximity gate | Buying into earnings = volatility explosion | Block new BUYs within 48h of earnings report |
| `count_orders_today` counts rejected orders | Legitimate trades blocked by failed orders | Count only APPROVED orders toward daily rate cap |

### Paper Trading — No Learning Loop
Currently: trades logged → win rate computed → **nothing feeds back into signals**.

A real feedback loop needs:
1. At trade close, record which nodes voted bullish/bearish at entry
2. Compute per-node hit rate over rolling 90 days
3. Reweight nodes in consensus by empirical predictive accuracy
4. Weekly report: "Which node was most accurate this week?"

Without this, the "paper trading phase" is just watching numbers, not teaching the bot anything.

### Slippage Model — Paper trades are too optimistic
Currently fills at `last_price` with zero slippage. Live trades will underperform paper by:
- Bid-ask spread: 0.05-0.10% per round trip
- Market impact on buys (you push price up)
- SEC/FINRA sell fees: ~$0.023 per $1000

Add minimum realistic model: `fill_price = last_price × (1 + 0.001)` on BUY, `× (1 - 0.001)` on SELL.

### Monte Carlo — Display widget, not wired into decisions
VaR/CVaR from Monte Carlo is shown on dashboard but:
- Not consumed by risk_gate
- Not used in position sizing
- Not used in autopilot

**Fix**: Add a gate: if projected 5%-CVaR for a new position exceeds 3% of total portfolio equity, reduce position size proportionally.

---

## Summary — Priority Queue

### ✅ DONE (April 19, 2026)

#### 🔴 Critical bugs — FIXED
1. ✅ `autopilot.py` — `run_consensus_holding` call signature fixed in `pre_market_scan` + `midday_rebalance`. Both now extract `(sym_equity, total_portfolio, sym_avg_cost, sym_price)` from holdings/watchlist before calling.
2. ✅ `execution.py` — SELL qty now computes delta-to-target (`(current_equity - target_dollars) / price`), not close-entire-position. Full close still works when `target_position_pct=0`.
3. ✅ `risk_gate.py` — DATA_FRESH gate is now fail-closed: missing timestamp → REJECTED.

#### 🟡 High value — DONE
4. ✅ `:9000 consensus.py` already had single-OHLCV-fetch in `run_consensus_holding`. Confirmed. yfinance `.info` cache (15 min TTL) added for Fundamentals + Analyst nodes — eliminates duplicate HTTP calls.
5. ✅ Ollama removed from `:9000` — `/api/ollama/*` endpoints return `410 Gone` with redirect to `/api/gemini/second-opinion`. `ollama_agent` import removed.
6. ✅ FFT_Cycle removed from consensus vote in both engines (not in NODE_WEIGHTS). Kelly removed from directional vote — returned separately as `kelly_vote` / `kelly_detail` for sizing display.
7. ✅ Same as #6 — Kelly removed from vote in both `:9000` and `:9001` consensus engines.
8. ✅ Slippage model added to paper execution: `fill_price = last × (1 + 0.001)` on BUY, `× (1 - 0.001)` on SELL.
9. ✅ Earnings-proximity gate added to `risk_gate.py` — blocks BUY within 48h of earnings via yfinance `earningsTimestamp`.
10. ✅ yfinance `.info` cache (15-min TTL, `_INFO_CACHE` dict) added to `:9000 consensus.py`.

#### 🟡 Additional — DONE
- ✅ **Weighted consensus** implemented in BOTH engines. NODE_WEIGHTS: Rel.Strength 2.0×, Trend 2.0×, Volume 1.5×, Momentum 1.5×, Fundamentals 0.75×, Analyst 0.5×, News 0.25×.
- ✅ **Sector concentration gate** added to `risk_gate.py` — blocks BUY if single GICS sector > 40% of portfolio.
- ✅ **Consecutive-loss circuit breaker** added to `risk_gate.py` — blocks all orders after 3 straight loss days.
- ✅ **Macro-regime confidence floor** — in RISK_OFF, min_confidence raised from 0.75 → 0.85 for BUY orders.
- ✅ **Polymarket removed** from `:9000 news_agent.py` — replaced with FRED macro signal from `regime.py` (more reliable, same signal, adds VIX + HY OAS).

#### 🎨 GUI Improvements — DONE (April 19, 2026)

##### :9000 Portfolio Manager Dashboard (`dashboard.html`)
- ✅ **Default chart period** fixed: 1D → 1M (matches `currentPeriod='1mo'` JS variable)
- ✅ **Alert banner** added between stats bar and tab bar — shows ECG-suspended positions (red) + low cash warning (yellow) in real-time
- ✅ **Macro regime banner** added — live RISK_ON/OFF/NEUTRAL bar fed from `/api/regime` showing FRED context: yield curve, VIX, Fed rate
- ✅ **Persistent alert state**: `_lastCash` and `_ecgStates` globals updated after every refresh; `updateAlertBar()` called from both `renderStats()` and `loadECG()` 
- ✅ **Tab active indicator fix**: `switchTab()` rewritten with index-based fallback so programmatic tab switches (Chrome MCP, JS calls) correctly highlight the active tab
- ✅ **Cached analysis auto-load**: opening "AI Analysis" tab now calls `tryLoadCachedAnalysis()` — auto-displays the last result with a "CACHED RESULT" badge, no wasted API calls
- ✅ **Watchlist overflow fixed**: sidebar widened 370px→420px, "Chng %" column shortened to "Δ%", td padding reduced, `min-width:0` on right column — all columns now fit without horizontal scroll
- ✅ **Paper Win Rate display**: shows `—` (muted) when 0 trades instead of red "0%" which was misleading
- ✅ **Placeholder cards compacted**: deep-placeholder padding 44px→28px, icon 32px→22px, text 20px→16px — less dead space on first load
- ✅ **ECG + Regime loaded at init**: `loadECG()` and `loadRegime()` called at page load so banners populate immediately

##### :9001 Trading Engine Dashboard (`trading.html`)
- ✅ **Market countdown banner**: live ET clock + real-time countdown to open (or close), updates every second. Shows hours:minutes:seconds remaining. Weekend-aware.
- ✅ **Autopilot schedule bar**: four job tiles (08:30 Pre-mkt, 09:35 Execute, 12:30 Rebalance, 15:55 EOD) with cyan highlight on next upcoming job — so you always know when the bot will act next
- ✅ **Kill Switch label fixed**: "ARMED" (ambiguous — sounds dangerous) → **"READY"** when kill file absent (trading enabled). "ENGAGED" when kill file present (all orders blocked). Sub-label explains state in plain English.
- ✅ **Orders Today progress bar**: visual progress bar under the orders count card showing % of daily cap used. Color: green → yellow (>67%) → red (cap hit). Sub-label changes to "cap reached — gate blocked" when full.
- ✅ **Order rate cap display improved**: shows `N / cap` format (e.g. "2 / 3") instead of just "2". Color-coded: text → yellow → red.
- ✅ **Config panel — human-readable labels**: env var key names (`max_position_pct`, `max_daily_loss_pct` etc.) replaced with plain English labels ("Max Position Size", "Daily Loss Limit" etc.). Color-coded values: green for safe booleans, yellow for warnings.
- ✅ **Candidate rows color-coded**: APPROVED rows have green-tint background, REJECTED rows have red-tint background — instantly readable scan results without reading every gate pill
- ✅ **Order rows color-coded**: SIMULATED/FILLED rows purple-tinted, ERROR/REJECTED rows red-tinted
- ✅ **Verdict boxes improved**: "APPROVED" → "✓ APPROVED", "REJECTED" → "✗ REJECTED" — clearer visual language
- ✅ **Section labels added**: visual separators between "Trade Intent Builder", "Consensus Engine · Signal Candidates", and "Order History" sections — reduces visual blur
- ✅ **Mode sub-labels upgraded**: "paper by default" → "safe mode — no real fills" (paper) or "⚠ LIVE EXECUTION" (live). Form header also reflects live mode in red.
- ✅ **"How it picks" text upgraded**: mentions 11 nodes by name including Reddit sentiment and macro regime, more informative for new users
- ✅ **Live countdown via 1s interval**: market clock ticks in real-time (separate from 15s refresh cycle for orders/config)

### 🟢 Next Sprint (still pending)
11. Implement per-node hit-rate tracking → feed back into node weights dynamically
12. Collapse :9000 dashboard from 11 tabs to 5 tabs (UI work)
13. Rewire Gemini to see raw payload only (independent from Claude's output)
14. Add earnings calendar per holding to dashboard
15. Add "What changed since yesterday" diff view

### 🔵 When Paper Trading is Profitable for 30+ Days
16. Add intraday stop enforcement (15-min poll or broker OCO)
17. Add correlation-aware position caps
18. Wire Monte Carlo CVaR into position sizing
19. Consider live execution (currently `NotImplementedError` — correct)

---

## What Makes a Bot "Eventually Gain a Little"

The minimum viable autonomous trading system that could produce small consistent gains needs:

1. **3-4 high-quality signals** (not 11) — Rel.Strength + Trend + Volume + Fundamentals-quality-screen
2. **Regime-conditional logic** — in TRENDING regime, follow momentum; in RANGING, mean-revert. Don't use same logic in all regimes.
3. **Conservative sizing** — Kelly at 0.25× (not 0.5×) until 90+ days of validated paper performance
4. **Realistic stops** — ATR-based, not arbitrary % thresholds
5. **Transaction costs in the model** — at high frequency, fees eat alpha
6. **A genuine feedback loop** — the bot must track what worked and weight those signals higher
7. **Know what you don't know** — sector events, earnings, macro shocks are not predictable. Respect position limits religiously.

The architecture today has the right shape. The execution bugs fixed + noise nodes removed + a 60-day clean paper run with realistic slippage = a system that could start earning small gains in live mode.

---

*NOT FINANCIAL ADVICE — FOR INFORMATIONAL PURPOSES ONLY*
*Analysis produced by Claude Sonnet 4.6 · April 19, 2026*
