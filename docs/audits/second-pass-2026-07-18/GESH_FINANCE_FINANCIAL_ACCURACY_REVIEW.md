# GESH Financial — Financial Accuracy Review

**Baseline:** 2026-07-18, `main` at `dc969264`.
**Purpose:** distinguish mechanically correct calculations from incomplete accounting, heuristics and unvalidated predictions. This is a software audit, not investment, tax or legal advice.

## Accuracy verdict

GESH has a good invariant-tested **research-math layer**, but it does not have a complete portfolio-accounting layer. The platform can safely display many inputs and educational scenarios if it labels provenance, freshness, missingness and methodology. It cannot currently promise accurate customer returns, tax outcomes, risk suitability, intrinsic value or predictive probabilities.

## Calculation register

| Calculation/output | Status | Formula/data path | Accuracy assessment | Required guard |
|---|---|---|---|---|
| Customer market value | Reliable within input limits | `shares × current_price`, summed in customer book payload | Correct for positive USD positions with a valid current quote; no cash, FX or corporate actions. | Quote timestamp/source/quality; Decimal at accounting boundary; missing is not zero. |
| Position allocation | Incomplete | position equity / sum(position equity) | Mathematically correct for invested positions only; misleading as total portfolio allocation because cash/other assets are excluded. | Label “% of tracked positions” or include cash/all assets. |
| Unrealized P&L | Partially reliable | `(price − average_cost) × quantity` | Basic formula is correct; average basis lacks lots, fees, FX and corporate actions. Crypto may use only the first cost-basis item. | Lot-aware reconciled basis, currency and fee treatment; missing basis state. |
| Realized P&L | Unreliable / absent for customers | legacy trade close math only | No customer transaction ledger. Legacy SELL/TRIM semantics can reverse economic meaning. | Single order/position accounting engine; immutable fills/lots; realized-P&L properties. |
| Total return/TWR/MWR | Missing | none | Cannot be inferred correctly from current snapshots when deposits/withdrawals/dividends are unknown. | Cash-flow ledger; TWR/MWR methodology and benchmark alignment tests. |
| Canonical fair-value delta | Reliable given inputs | `(estimate − current) / abs(current) × 100` | Convention and zero handling are centralized and property-tested. Reliability of the **estimate** is separate and weak. | Always label estimate source/model/version and data date. |
| Fair-value estimate | Not decision-grade | generally AI/model output | Not an auditable DCF/comps calculation; source financials and assumptions are not mandatory/cited. | Deterministic valuation model or label as AI scenario; show assumptions/range/citations. |
| Trade levels | Mechanically reliable heuristic | pivots/swing levels; stop below support; nearest target; post-check `stop < entry < target` | Invariants are strong. Levels are technical heuristics, not likelihood estimates. | Data freshness, liquidity/gap warning, no “guaranteed setup” language. |
| Risk bands | Mechanically reliable heuristic | volatility-tier multipliers and clamps | Pure calculations and ordering are tested; calibration to loss probabilities is not established. | Explain heuristic and historical window; do not label VaR/confidence interval. |
| Kelly sizing | Partially reliable research math | blend of continuous/discrete Kelly, half-Kelly, autocorrelation shrink, correlation haircut, 25% cap | Algebra/caps have good properties. Inputs are short-window/provider-dependent and AI views can influence weights; walk-forward utility is unproven. | Fresh aligned returns, explicit probability/edge assumptions, uncertainty penalty, approved policy cap, no suitability claim. |
| Execution position size | Mechanically enforced | risk gate defaults around 2% max | Safer than analytical Kelly, but the 25% research cap versus 2% execution cap is inconsistent. | One policy hierarchy and UI separation between raw research and permitted action. |
| Monte Carlo paths | Internally consistent scenario simulation | 1,000 seeded GBM paths, log returns, EWMA covariance, optional GARCH/regime multipliers | Deterministic and invariant-tested. Fixed 1.8% fallback vol, zero drift and heuristic multipliers are not calibrated forecasts. | Coverage/fallback badge; distributions labeled scenario—not probability forecast; backtest calibration. |
| VaR/CVaR from simulation | Consistent with paths, model-risk high | loss from final simulated total; CVaR tail average | Math is reasonable conditional on paths; model assumptions dominate result. | Confidence level/horizon/method/data date; missing-data fail closed; historical/stress comparison. |
| Benchmark return | Partially reliable | simple price return vs SPY/sector | Appropriate as price return only with aligned valid dates. Some missing-provider paths become 0, producing false grades. | Missing state, minimum observations, total-return series if claims include dividends. |
| Track-record win rate/P&L | Transparent but provisional | closed simulated trades | Computation is inspectable; sample is small and cohorts differ. Aggregate at audit: 27 closed, 18.52% wins, profit factor 1.27, max drawdown 27.2%. | Cohort/version/time labels, costs/slippage, preregistered inclusion rules. |
| Track-record Sharpe | Methodologically limited | per-trade return annualized by observed trade frequency | Not directly comparable to conventional daily equity-curve Sharpe. | Label method/frequency/sample or calculate from daily marked equity. |
| ECG alert | Broken on current main | position equity history and change detection | Invalid/non-observation rows can look like a ~96% crash. | Only validated observations, robust baseline, minimum coverage, property/regression tests; merge #96 only when green. |
| Crypto correlation risk | Incomplete | average holding correlations | Unweighted average ignores position size and covariance contribution. | Exposure-weighted covariance/risk contribution and missing-data coverage. |
| Tax-loss candidate amount | Mechanically correct screening | Decimal loss from average basis; threshold; sale-side 30-day clock | Cent math and unknown-basis behavior are good. Wash-sale correctness is incomplete without purchase lots and substantially-identical analysis. | Rename as screening; lot/purchase-aware rules and tax-professional review before definitive “safe” language. |
| Risk/diversification score | Not auditable | AI output in deep analysis | No stable deterministic formula, calibration or decomposition. | Deterministic documented scoring or qualitative AI label with cited observations. |
| AI verdict/price target | Not decision-grade | structured LLM result | Schema and price grounding reduce formatting error, not hallucinated fundamentals or predictive error. | Mandatory evidence citations, fact validation, uncertainty, abstention and versioned model evaluation. |

## Portfolio accounting gaps

The customer “portfolio” is a **research book**, not an accounting system. To claim reliable portfolio performance, implement the following dependency order:

1. Immutable accounts, instruments, currencies and transaction/fill schema.
2. Deposits, withdrawals, cash, dividends, interest, fees, taxes and transfers.
3. Lots and corporate actions with broker reconciliation.
4. Daily positions/cash valuation with price provenance and stale/missing handling.
5. Realized/unrealized P&L plus TWR/MWR and benchmark methodology.
6. Reconciliation tolerances, audit events and correction/version rules.

Until then, UI copy should say **tracked research positions**, **estimated current value**, and **position-only weight**.

## Model-risk analysis

### Kelly

The implementation’s half-Kelly and exposure/correlation caps are prudent engineering, but the result is only as valid as the estimated edge. Short histories, nonstationary regimes, raw provider calls and AI conviction make that edge highly uncertain. The model needs walk-forward tests against simple equal-weight and fixed-fraction baselines, with turnover, drawdown and calibration—not just formula tests.

### Monte Carlo

The engine is useful for scenario exploration. It should not present a 21-day probability as empirically calibrated until coverage, regime multipliers, covariance and fallback behavior are backtested. A deterministic seed improves reproducibility, not truth.

### Consensus and AI fair value

Agreement among correlated signals/models is not independent evidence. Store each node’s data lineage, age, historical calibration and abstention reason. Separate “consensus strength” from “probability correct.” Numeric AI fair values require a visible valuation method or should be presented as narrative scenarios.

## Data-quality contract required for every financial output

Every response/card/job should carry:

- `as_of`, source and market/session context;
- observed/cached/fallback status and cache age;
- requested versus valid symbol/weight coverage;
- currency and adjustment basis;
- calculation/model version;
- missing/excluded inputs and why;
- confidence/calibration meaning, or an explicit “not calibrated” label;
- `actionable=false` when coverage/freshness thresholds fail.

No path should convert missing price to zero or silently reuse a suspect quote downstream.

## Financial verification evidence

- Fair-value, trade-level, risk-band, Kelly exposure and Monte Carlo invariants passed in the 1,214-test suite.
- SQLite integrity is `ok`, but data provenance/reconciliation and restore are different guarantees.
- Current simulated proof is honest but not commercially persuasive: engine cohort 16 closed trades, 12.5% wins and negative total P&L; autonomy gate fails at 16/100.
- No live customer cash-flow/account reconciliation, walk-forward validation or independent model validation was found.

## Release rules for high-trust numbers

1. A number without provenance, freshness and method is not releasable as a financial fact.
2. AI interpretation must never overwrite an observed fact.
3. Missing data must propagate as missing, not zero.
4. Estimated/modelled/simulated/live must be visually distinct.
5. Any customer-specific sizing output requires compliance approval and deterministic policy caps.
6. Performance claims require a frozen protocol, sufficient sample, costs, benchmark and cohort/version labels.
