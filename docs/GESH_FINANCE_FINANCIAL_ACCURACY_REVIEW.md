# GESH Financial — Financial Accuracy Review

**Date:** 2026-07-18

---

## Calculation Inventory

### Portfolio Valuation

| Calculation | Implementation | Source | Input Data | Verification | Accuracy Risk | Required Tests |
|---|---|---|---|---|---|---|
| Market value | `qty × current_price` | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L2607 | customer_positions + live price from yfinance/RH | Correct formula | Low — straightforward multiplication | Covered in test_mybook.py |
| Portfolio allocation % | `equity / total × 100` | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L2618 | Per-holding equity, total portfolio equity | Correct formula; division-by-zero guarded | Low | Covered in test_mybook.py |
| Day change | `curr_equity - prev_close` | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L1402-1403 | portfolio_equity, adjusted_equity_previous_close | Correct formula; zero-division guarded | Low | Should add dedicated test |
| Day change % | `(curr - prev) / prev × 100` | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L1403 | Same as above | Correct; guarded: `if prev else 0` | Low | Should add dedicated test |
| Cost basis (customer) | User-supplied `avg_cost` | [auth_users.py](file:///Users/georgigaydarov/Projects/gesh-project/auth_users.py) customer_positions | User input | Stored as-is; not independently verified | **Medium** — user may enter incorrect cost basis | Add validation bounds |
| Unrealized gain % | `(price - avg_cost) / avg_cost × 100` | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L2608 | current_price, avg_cost | Correct when avg_cost provided; 0.0 when absent | Low | Covered |
| Equal-weight notional | `$100,000 / count(priced symbols)` | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L2599 | Position count | Correct 1/N weighting | Low | Covered in test_mybook.py |

### Consensus Engine

| Calculation | Implementation | Source | Verification | Accuracy Risk |
|---|---|---|---|---|
| Weighted consensus score | `Σ(vote_i × weight_i) / Σ(|weight_i|)` for voting nodes | [consensus.py](file:///Users/georgigaydarov/Projects/gesh-project/consensus.py) L22 | Tested in test_consensus.py (10KB) + 6 phase tests | Low — well-tested |
| Regime-conditional weights | Base weights × regime multiplier | [consensus.py](file:///Users/georgigaydarov/Projects/gesh-project/consensus.py) L67-80 | Tested in test_consensus_phase7.py | Low |
| RSI momentum node | Standard RSI(14) calculation | consensus.py via yfinance TA | Industry-standard formula | Low |
| ADX trend node | ADX + Bollinger regime classification | consensus.py | Standard TA indicators | Low |
| Relative strength | Performance vs SPY over lookback | consensus.py | Relative to benchmark | Low |
| Fundamentals node | Revenue growth + margins + PE quality | consensus.py | yfinance .info data | **Medium** — depends on yfinance data completeness |
| Analyst node | Wall St consensus + price target upside | consensus.py | yfinance analyst data | **Medium** — analyst data may be stale/incomplete |
| News sentiment node | Cached sentiment from news_agent DB | consensus.py | RSS + Finnhub | **Medium** — sentiment model accuracy varies |

### Monte Carlo

| Calculation | Implementation | Source | Verification | Accuracy Risk |
|---|---|---|---|---|
| EWMA volatility | `returns.ewm(span=30).var()` → `√ewma_var` | [monte_carlo.py](file:///Users/georgigaydarov/Projects/gesh-project/monte_carlo.py) L49-62 | Tested in test_monte_carlo_calibration.py (8KB) | Low — RiskMetrics standard |
| Arithmetic daily drift | `mean(log returns) + ½ × var(log returns)` | [monte_carlo.py](file:///Users/georgigaydarov/Projects/gesh-project/monte_carlo.py) L65-76 | Documented bias fix (2026-07); GBM-correct | Low |
| GBM simulation | `S_t × exp((μ - ½σ²)Δt + σ√Δt × Z)` | monte_carlo.py | 1000 paths, standard log-normal model | Low |
| VaR 95% | 5th percentile of simulated terminal values | monte_carlo.py | Standard quantile calculation | Low |
| CVaR 95% | Mean of values below VaR 95% | monte_carlo.py | Standard expected shortfall | Low |
| Regime-conditional drift | Historical drift × regime multiplier (0.2–1.2) | monte_carlo.py L35-44 | Reasonable heuristic multipliers | **Medium** — multipliers are empirical, not backtested |
| Fallback daily vol | 0.018 (1.8% daily) for missing symbols | monte_carlo.py L31 | Flagged in calibration_degraded_symbols | Low |

### Kelly Criterion

| Calculation | Implementation | Source | Verification | Accuracy Risk |
|---|---|---|---|---|
| Kelly fraction | Via gesh-core kelly module | [kelly.py](file:///Users/georgigaydarov/Projects/gesh-project/kelly.py) shim | Tested in test_kelly.py (8KB) + test_kelly_invariants.py (11KB) | Low — extensively tested |
| Half-Kelly sizing | Standard Kelly / 2 for conservative sizing | kelly.py | Tested | Low |
| Position cap enforcement | max_position_pct from risk config | allocation_config.py + risk_gate.py | Tested in multiple test files | Low |

### Paper Trading P&L

| Calculation | Implementation | Source | Verification | Accuracy Risk |
|---|---|---|---|---|
| Entry price | yfinance current price at time of paper trade | [paper_trading.py](file:///Users/georgigaydarov/Projects/gesh-project/paper_trading.py) | From live feed; logged | **Medium** — no slippage model |
| Stop loss | `entry × (1 - DEFAULT_STOP_PCT)` = 5% below | paper_trading.py L38 | Fixed 5% default | Low |
| Take profit | `entry × (1 + DEFAULT_TARGET_PCT)` = 12% above | paper_trading.py L39 | Fixed 12% default | Low |
| Realized P&L | `(exit_price - entry_price) × shares` | trade_log.py | Tested in test_paper_book.py | Low |
| Win rate | `wins / total_closed × 100` | trade_log.py `get_win_rate()` | Tested | Low |
| Track record (proof page) | Reads paper_outcomes from trading engine DB | [proof_page.py](file:///Users/georgigaydarov/Projects/gesh-project/proof_page.py) | Gate requires 100 engine-cohort closes | Low |

### Customer Book Analysis

| Calculation | Implementation | Source | Verification | Accuracy Risk |
|---|---|---|---|---|
| Book valuation | `Σ(shares × current_price)` per symbol | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) _customer_book_payload() | Correct | Low |
| Deep analysis on customer book | Same engine as owner (AI + Kelly + MC) | server.py _mybook_analyze_worker() | Tested in test_mybook.py (52KB) | Low |
| Analysis staleness | 12h for ≤5 positions, 6h for 6-15, 3h for >15 | server.py _analysis_stale_for() | Reasonable heuristic | Low |

### Model-Book Masking (Customer View)

| Calculation | Implementation | Source | Verification | Accuracy Risk |
|---|---|---|---|---|
| Scale factor | `$100,000 / real_total_equity` | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L512-520 | Fail-closed: returns 0.0 if real total unknown | Low — tested in leak sweep |
| Dollar masking | `value × scale_factor` | server.py L523-562 | Recursive with semantic keys | Tested in test_customer_leak_sweep.py (24KB) |
| Prose redaction | `$amounts → "$•••"`, `share counts → "a position"` | server.py L506-509 | Regex-based; edge cases tested | **Medium** — regex may miss novel AI phrasings |

---

## Key Financial Accuracy Risks

### Risk 1: No Slippage Model in Paper Trading
**Current:** Paper trades execute at the exact yfinance current price.
**Reality:** Real trades face bid-ask spread, partial fills, and market impact.
**Impact:** Paper trading win rate may overstate real trading performance.
**Mitigation:** The ToS and proof page clearly state "HYPOTHETICAL / SIMULATED."
**Recommendation:** Add configurable slippage (e.g., 0.1-0.5% per trade) for more realistic simulation.

### Risk 2: Stale Prices in Analysis
**Current:** yfinance data can be delayed; Robinhood session can expire.
**Impact:** Analysis based on stale prices could produce misleading signals.
**Mitigation:** `MAX_PRICE_AGE_SECONDS` (300s) in risk gate; `stale_snapshot` flag for dashboard; cross-source price quality check (price_sanity.py).
**Assessment:** Well-mitigated for the current use case.

### Risk 3: Monte Carlo Regime Drift Multipliers
**Current:** Empirical multipliers (0.2–1.2×) applied to historical drift based on regime classification.
**Impact:** Over/understated risk projections in extreme regime conditions.
**Mitigation:** Multipliers are conservative; 1000 paths provide distribution.
**Recommendation:** Add backtesting validation of regime multipliers.

### Risk 4: AI Hallucination in Financial Analysis
**Current:** Claude generates price targets, risk assessments, and buy/sell signals.
**Impact:** AI may state price targets or risk levels not supported by data.
**Mitigation:** Structured prompts with forced JSON tool use; provenance stamping; "AI-generated" labeling; disclaimer.
**Recommendation:** Add output validation (e.g., verify AI price targets are within 52-week range).

---

## Missing Financial Calculations

| Calculation | Status | Priority |
|---|---|---|
| Time-weighted return (TWR) | ⬜ Missing | P2 — standard portfolio performance metric |
| Money-weighted return (MWR/IRR) | ⬜ Missing | P2 — relevant for portfolios with cash flows |
| Sharpe ratio (portfolio-level) | ⬜ Missing at customer level; computed for paper trades | P2 |
| Maximum drawdown (customer book) | ⬜ Missing | P2 |
| Diversification score | ⚠ Partially — invest_radar.py has correlation-based checks | P2 |
| Fee impact modeling | ⬜ Missing | P3 |
| Tax-loss harvesting (customer) | ⬜ Missing — only owner portfolio | P3 |
| Dividend yield tracking | ⬜ Missing | P3 |
