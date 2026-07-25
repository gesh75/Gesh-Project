# GESH Financial Observability — Project Instructions

## Project purpose
AI-powered financial observability and trading tool for Robinhood portfolios. Combines live
portfolio data with multiple AI analysis engines to produce deep trading insights,
position sizing, risk management, news sentiment, SEC filing alerts, market screening,
consolidation zone detection, sector pre-filtering, paper trading, and a full
signal→gate→execution pipeline. Built with FastAPI + SQLite + Anthropic Claude + yfinance.

**v2 additions (from FinceptTerminal research):**
- FRED macro layer in `regime.py` — live Fed funds rate, yield curve, CPI injected into Claude prompts
- Polymarket sentiment in `news_agent.py` — prediction market macro signal blended with news mood
- Gemini multi-LLM agent `gemini_agent.py` — Google Gemini 2.5 Flash/Pro for sentiment + second opinions

## Commercial Role — Port 9000 is THE PRODUCT ★

> **Port 9000 (this service) is what customers pay for.** It is the GESH intelligence dashboard —
> 8 tabs covering signals, risk, regime, Kelly sizing, Monte Carlo, SEC alerts, fundamentals, and
> daily analysis. This is the only layer that ever faces paying users.
>
> Port 9003/trading is the **internal engine** — runs silently, accumulates paper trade win-rate
> data that becomes the public proof of signal accuracy. Customers never see or touch it.
>
> Port 9003/crypto is the **premium add-on** — on-chain signals and BTC/ETH options flow,
> bundled into the $399 Trading Desk tier or offered as a $49 add-on.
>
> See `gesh-project-trading/GESH_COMMERCIAL_ROADMAP.md` for the full 12-month plan.

## Owner
Georgi — Network Engineer, active trader, Robinhood user via robin_stocks.

---

## Infrastructure — Permanent Ports & Auto-Start

| Service | Port | URL | Log |
|---------|------|-----|-----|
| **gesh-project (this)** | **9000** | http://localhost:9000 | /tmp/gesh_main.log |
| gesh-project-trading | 9003 | http://localhost:9003 | /tmp/gesh_trading.log |
| crypto scheduler | — | http://localhost:9003/crypto | /tmp/gesh_crypto_scheduler.log |
| trading autopilot | — | background jobs | /tmp/gesh_autopilot.log |

### macOS LaunchAgents

Service label: `com.gesh.main` → `~/Library/LaunchAgents/com.gesh.main.plist`  
Start script: `start_gesh_main.sh` (plist handles log output to `/tmp/gesh_main.log`)

| Label | Schedule | Script | Log | Purpose |
|---|---|---|---|---|
| `com.gesh.main` | RunAtLoad | `start_gesh_main.sh` | `/tmp/gesh_main.log` | the :9000 server |
| `com.gesh.daily-analysis` | 10:30 ET Mon–Fri | `run_daily_analysis.sh` | `/tmp/gesh_fullchain.log` | daily analysis chain |
| `com.gesh.onboarding-sweep` | every 15 min (`StartInterval 900`) | `sweep_onboarding.sh` | `/tmp/gesh_onboarding_sweep.log` | re-send onboarding magic links to paid-but-un-onboarded subscribers (`billing.resend_owed_onboarding`; idempotent) |
| `com.gesh.dashboard-snapshot` | every 30 min (`StartInterval 1800`), market-hours-guarded | `refresh_dashboard_snapshot.sh` | `/tmp/gesh_dashboard_snapshot.log` | intraday MCP refresh of `live_portfolio_snapshot.json` (the :9000 `/api/portfolio` offline fallback) — keeps the dashboard live via the Robinhood MCP (`fetch_snapshot_mcp.sh`, no robin_stocks/MFA) instead of only the 10:30 chain. Off-hours ticks are a cheap no-op; `GESH_SNAPSHOT_FORCE=1` bypasses the guard for an ad-hoc run |

```bash
# Restart this service
launchctl bootout gui/$(id -u)/com.gesh.main
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gesh.main.plist

# (Re)load the onboarding sweep
launchctl bootout gui/$(id -u)/com.gesh.onboarding-sweep 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gesh.onboarding-sweep.plist

# Fix all 4 services at once
bash /Users/georgigaydarov/Projects/gesh-project-trading/fix_and_reload_services.sh
```

---

## Architecture overview

```
server.py (FastAPI · Port 9000)
│
├── SIGNAL & ANALYSIS LAYER
│   ├── auth.py              — Robinhood login/logout (MFA-aware)
│   ├── data_fetcher.py      — Live portfolio: holdings, equity, watchlist quotes
│   ├── ai_analyzer.py       — All Claude AI prompts + schemas (3 analyzers)
│   ├── market_data.py       — Market screening universe + analyst targets
│   ├── fundamental_agent.py — Fundamental momentum scanner (80+ stocks, yfinance)
│   ├── news_agent.py        — News sentiment (Yahoo Finance RSS + Finnhub + Polymarket macro)
│   ├── sec_agent.py         — SEC EDGAR filing watcher (10-K, 10-Q, 8-K)
│   ├── regime.py            — Market regime detection (ADX + Bollinger + RSI + FRED macro)
│   ├── monte_carlo.py       — Portfolio stress test (1000 GBM simulations)
│   ├── kelly.py             — Kelly Criterion position sizer
│   ├── invest_radar.py      — Diversification radar: advisory NEW-name screen (5-factor, cash-aware, read-only)
│   ├── consensus.py         — 11-node consensus voting engine
│   ├── ecg.py               — Equity Curve Gate (drawdown circuit breaker)
│   ├── fft_cycles.py        — FFT dominant price cycle detection
│   ├── ollama_agent.py      — Local LLM second opinion (Ollama / llama3.2:3b)
│   ├── gemini_agent.py      — Google Gemini AI (2.5 Flash + Pro) — sentiment + second opinions
│   └── db.py                — SQLite persistence (gesh_financial.db)
│
├── TRADING PIPELINE LAYER (NEW)
│   ├── consolidation_zone.py — 10+ candle tight range detector (Black Bot TA)
│   ├── sector_filter.py      — ETF sector volume pre-filter (1.2× 20d avg)
│   ├── allocation_config.py  — Config: caps, gates, scanner timeframe (gesh_config.json)
│   ├── trade_log.py          — Full trade lifecycle logging + win rate tracking
│   └── paper_trading.py      — Paper trade simulation (no real money)
│
├── EXECUTION TIER (trading/ subpackage) (NEW)
│   ├── trading/__init__.py
│   ├── trading/config.py     — RiskConfig dataclass (env-var driven, ultra-conservative defaults)
│   ├── trading/contract.py   — TradeIntent dataclass + from_consensus() + manual() adapters
│   ├── trading/state.py      — trade_intents / risk_decisions / orders SQLite tables
│   ├── trading/risk_gate.py  — 8-gate risk gate (kill-switch → hours → conf → cap → rate → cash → loss → freshness)
│   ├── trading/execution.py  — Paper/live broker wrapper with idempotency (PAPER_MODE=true default)
│   ├── trading/alerts.py     — Telegram alerts (@gesh755bot) for fills, rejections, EOD P&L
│   ├── trading/tracker.py ★  — Signal Accuracy Tracker: paper_positions/paper_outcomes/signal_overrides
│   │                           win_rate/Sharpe/max_drawdown/per-source stats, autonomy gate check,
│   │                           manual signal rating. Source of truth for live-trading readiness.
│   │                           ★★ Fully wired into autopilot (2026-05-23): record_entry on BUY fills,
│   │                              record_exit on SELL/stop-loss — autonomy gate now accumulates automatically.
│   └── trading/autopilot.py  — APScheduler: 08:30 scan / 09:35 execute / 12:30 rebalance / 15:55 EOD
│                               ★★ Tracker-hooked: open_window, midday_rebalance, close_risk_check
│
└── templates/dashboard.html  — Web UI (Chart.js, 8 tabs: Overview, Analysis, Screener, Kelly, Monte Carlo, Regime, SEC/News, 🔬 Daily Analysis)
```

---

## Database: gesh_financial.db (SQLite)

### Core tables
| Table | Contents |
|-------|----------|
| analysis_log | Deep AI analysis runs (JSON results, model, timestamps) |
| equity_snapshots | Per-symbol equity per refresh — used by ECG |
| news_sentiment | Per-symbol sentiment scores (−100→+100) |
| sec_filings | Analyzed SEC filing results |
| fundamental_scans | Weekly fundamental scan results |

### Trading tables (NEW)
| Table | Contents |
|-------|----------|
| trade_log | Real executed trades: symbol, action, shares, entry/exit price, stop, target, P&L, outcome, scanner_source |
| paper_trades | Simulated trades (same schema + simulated_portfolio_value) |
| trade_intents | Every TradeIntent produced by any signal engine — full audit chain |
| risk_decisions | Every gate verdict (APPROVED/REJECTED) with per-gate breakdown and config snapshot |
| orders | Every simulated or real broker submission with idempotency key and fill details |

---

## Config file: gesh_config.json (NEW)
Managed by `allocation_config.py`. Key fields:
- `max_stock_per_trade` — hard cap per stock trade (default $5,000)
- `max_options_per_trade` — hard cap per options trade (default $2,000)
- `max_open_positions` — max simultaneous open positions (default 5)
- `min_cash_buffer` — never deploy below this cash level (default $500)
- `auto_trade` — live execution gate (default False)
- `paper_mode` — routes actions to paper_trades (default False)
- `extended_hours` — pre/after-market (default True)
- `scanner_timeframe` — active scanner mode (daily/1HR/5MIN/premarket/stock/forex-*)

---

## Scanner timeframe modes
| Mode | Period | Interval | Use case |
|------|--------|----------|----------|
| daily | 60d | 1d | Key levels + sweep setups (default) |
| 1HR | 5d | 1h | Multi-timeframe intraday swings |
| 5MIN | 1d | 5m | High-frequency momentum |
| premarket | 1d | 5m | Unusual volume + gap moves |
| stock | 30d | 1d | Penny stocks $0.50–$10 |
| forex-1HR | 5d | 1h | Forex majors/minors/metals |
| forex-4HR | 10d | 4h | Higher TF forex setups |
| forex-daily | 60d | 1d | Daily forex trends |

Use `get_scanner_params()` from `allocation_config.py` to get correct yfinance params.

---

## Scanner pipeline order (NEW)
For 5MIN / 1HR scanner modes, always run in this order before full engine analysis:
```python
from sector_filter import batch_sector_check
from consolidation_zone import scan_consolidations

# Step 1 — sector pre-filter (cheap, one ETF fetch per sector)
sector_report = batch_sector_check(candidate_symbols)
hot_symbols = [r["symbol"] for r in sector_report["hot"]]

# Step 2 — consolidation zone scan (pure price math, fast)
cz_report = scan_consolidations(hot_symbols, interval="5m", period="1d", min_rr=2.0)

# Step 3 — full engines only on symbols with valid zone + hot sector
zone_symbols = [r["symbol"] for r in cz_report["found"]]

# Inject context into Claude prompts:
prompt += "\n" + sector_report["summary"]
prompt += "\n" + cz_report["summary"]
```

### Consolidation zone trade levels
| Field | Value | Rule |
|-------|-------|------|
| entry | zone midpoint | Buy at midpoint of consolidation |
| stop | zone_low − 0.5% | Below zone floor with buffer |
| target | prev zone high (or entry + 2× risk) | Black Bot methodology |
| min R/R | 2.0× | Setups below this filtered out |

---

## Continuous Improvement
See the living `GAPS_AND_IMPROVEMENTS_2026-07-09.md` (created by deep Grok analysis 2026-07-09). It ranks gaps and contains the prioritized roadmap to make GESH the best product. Update it after major milestones.

## Agent harness model (Grok / Cursor)

**GESH coding agent:** use **Grok 4.5** — model id `grok-build` (alias `grok-4.5`). Do not use `grok-composer-2.5-fast` for analysis or implementation in this repo.

| Harness | How |
|---------|-----|
| Cursor | Model picker → **grok-build** or **grok-4.5** |
| Grok CLI | `grok --agent gesh` or `grok -m grok-build` |
| Project rules | `AGENTS.md`, `.grok/agents/gesh.md`, `.grok/rules/grok-model.md` |

Analysis skills under `.claude/skills/` pin `model: grok-build` in frontmatter.

---

## AI models in use
| Model | Provider | Used for | Why |
|-------|----------|----------|-----|
| claude-sonnet-4-6 | Anthropic | Deep portfolio, screener, SEC, fundamentals | Best reasoning |
| claude-haiku-4-5-20251001 | Anthropic | News sentiment batch | Fast + cheap |
| gemini-2.5-flash | Google | Batch news sentiment (alternative to Haiku), market commentary, quick analysis | Fast, Georgi has subscription |
| gemini-2.5-pro | Google | Contrarian second opinions on STRONG BUY signals | Independent reasoning from Claude |
| llama3.2:3b (Ollama) | Local | Portfolio commentary, offline fallback | Zero API cost, private |

Model selection: `CLAUDE_MODEL` env var (default: "sonnet")
Gemini selection: set `GEMINI_API_KEY` in `.env` — auto-detected at runtime

---

## Environment variables (.env)
```
ANTHROPIC_API_KEY=        # Required — all Claude calls
FINNHUB_API_KEY=          # Optional — enhances news with Finnhub
CLAUDE_MODEL=sonnet       # Optional — "sonnet" or "haiku"
GEMINI_API_KEY=           # Optional — enables gemini_agent.py (Georgi has subscription)
                          # Get key: https://aistudio.google.com/app/apikey
                          # SDK: pip install google-genai --break-system-packages

# Trading execution layer
PAPER_MODE=true           # true = paper mode (default, NEVER change without weeks of paper testing)
REQUIRE_APPROVAL=true     # true = every intent queued for manual confirm before live
PAPER_CASH_USD=100000     # simulated cash for paper-mode CASH_FLOOR/DAILY_LOSS gates (rules #32/#36)
KILL_SWITCH_PATH=~/.gesh/KILL  # create this file to block all orders immediately
MAX_POSITION_PCT=0.02     # max 2% of equity per symbol
MAX_DAILY_LOSS_PCT=0.01   # max 1% daily loss before gate blocks all orders
MAX_ORDERS_PER_DAY=3      # daily order rate cap
MIN_CASH_BUFFER_USD=500   # minimum cash floor
MIN_CONFIDENCE=0.75       # minimum consensus confidence to trade
MAX_PRICE_AGE_SECONDS=300 # reject stale quotes > 5 min old

# Customer LLM quotas (rolling 24h, LIVE Claude calls only — cached hits are
# free and never consume quota; the OWNER is exempt; 0 disables a cap)
QUOTA_DAILY_TICKER_CARD=25    # /api/analyze-ticker AI research cards per customer
QUOTA_DAILY_BOOK_ANALYSIS=10  # /api/mybook/analyze runs per customer

# Telegram alerts
TELEGRAM_BOT_TOKEN=       # @gesh755bot token
TELEGRAM_CHAT_ID=5015900830
TELEGRAM_ALERTS=true
```

---

## Trading AI Skills

Before performing any trading analysis task, read the matching skill file:

| Task | Skill file |
|------|-----------|
| Deep portfolio analysis, scores, predictions | `.claude/skills/portfolio-analysis/SKILL.md` |
| Stock screening, weekly picks | `.claude/skills/stock-screener/SKILL.md` |
| Risk analysis, VaR, Monte Carlo, stress test | `.claude/skills/risk-manager/SKILL.md` |
| News sentiment, market mood | `.claude/skills/news-sentiment/SKILL.md` |
| SEC filings, EDGAR alerts | `.claude/skills/sec-watcher/SKILL.md` |
| Market regime (ADX/RSI/Bollinger) | `.claude/skills/regime-detector/SKILL.md` |
| Kelly Criterion position sizing | `.claude/skills/kelly-sizer/SKILL.md` |
| Fundamental momentum scanning | `.claude/skills/fundamental-scanner/SKILL.md` |
| Trade signals, consolidation zones | `.claude/skills/trade-signal-generator/SKILL.md` |
| Paper trading, risk gate, execution | `.claude/skills/paper-trading/SKILL.md` |

---

## API endpoints (server.py) — Full Reference

### Core analysis
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard HTML |
| `/api/portfolio` | GET | Live portfolio + equity summary |
| `/api/analyze` | POST | Quick AI risk analysis |
| `/api/deep-analysis` | POST | Full deep analysis with regime injection |
| `/api/screener` | POST | AI stock screener (top 10 from 95+ stocks) |
| `/api/regime` | GET | Market regime detection (ADX/BB/RSI) |
| `/api/kelly` | POST | Kelly position sizing |
| `/api/monte-carlo` | POST | Portfolio stress test |
| `/api/consensus` | POST | 11-node consensus engine |
| `/api/ecg` | GET | Equity Curve Gate status |
| `/api/fft-cycles` | GET | FFT cycle detection (all holdings) |
| `/api/fft-cycles/{symbol}` | GET | FFT for single symbol |
| `/api/chart/{symbol}` | GET | Price chart data (1d/5d/1mo/3mo/6mo/1y) |
| `/api/market-data` | GET | Analyst targets + insider + institutional |

### Intelligence agents
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents/sec` | POST | Watch SEC EDGAR filings |
| `/api/agents/sec/history` | GET | Cached SEC analysis |
| `/api/agents/news` | POST | Scan news sentiment |
| `/api/agents/news/history` | GET | Cached news scores |
| `/api/agents/fundamentals` | POST | Run full fundamental scan |
| `/api/agents/fundamentals/latest` | GET | Latest cached fundamental scan |
| `/api/ollama/status` | GET | Ollama local LLM status |
| `/api/ollama/analyze` | POST | Ollama portfolio commentary |
| `/api/ollama/second-opinion/{symbol}` | POST | Contrarian second opinion |

### Gemini AI (NEW v2)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/gemini/status` | GET | Check Gemini API key + model availability |
| `/api/gemini/analyze` | POST | Gemini Flash portfolio commentary (same as /api/ollama/analyze) |
| `/api/gemini/second-opinion/{symbol}` | POST | Gemini Pro contrarian review (body: {analysis, verdict}) |
| `/api/gemini/market-commentary` | POST | Macro market narrative from regime + FRED data |

### History
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/history` | GET | Analysis run history |
| `/api/history/{id}` | GET | Specific analysis run |

### Config (NEW)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Current allocation config |
| `/api/config` | POST | Update config keys |

### Scanner pipeline (NEW)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/scanner/consolidation/{symbol}` | GET | CZ detection for one symbol |
| `/api/scanner/consolidation` | POST | Batch CZ scan (body: {symbols, interval, period, min_rr}) |
| `/api/scanner/sector/{symbol}` | GET | Sector ETF volume check |
| `/api/scanner/sector` | POST | Batch sector pre-filter |

### Trade log (NEW)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/trades` | GET | Trade history (?paper=true for paper) |
| `/api/trades/stats` | GET | Win rate + P&L + scanner performance |
| `/api/trades/log` | POST | Log a new trade entry |
| `/api/trades/{id}/close` | POST | Close a trade with exit price |

### Paper trading (NEW)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/paper/run` | POST | Simulate action plan in paper mode |
| `/api/paper/check-closes` | POST | Auto-close positions that hit stop/target |
| `/api/paper/summary` | GET | Paper vs live performance summary |

### Execution layer (NEW)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/trading/dry-run` | POST | Gate-check an intent without executing |
| `/api/trading/paper-order` | POST | Gate + paper execute an intent |
| `/api/trading/consensus-order` | POST | Full signal→gate→paper pipeline for one symbol |

---

## Consensus engine (consensus.py) — 11 nodes

| Node | Signal source | Method |
|------|--------------|--------|
| MOMENTUM | RSI + 20/50 MA crossover | 90d daily data |
| TREND | ADX + Bollinger regime | regime.py |
| RELATIVE_STR | Performance vs SPY | relative strength |
| FUNDAMENTALS | Revenue growth + margins + PE | yfinance |
| ANALYST | Wall St consensus + target upside | yfinance |
| NEWS | Cached sentiment score | news_agent DB |
| KELLY | Position sizing signal | kelly.py |
| ECG | Equity Curve Gate health | ecg.py |
| VOLUME | Volume trend + liquidity | yfinance |
| FFT_CYCLE | Dominant price cycle phase | fft_cycles.py |

Voting: BULLISH=+1, NEUTRAL=0, BEARISH=-1, ABSTAIN=None
Strong signal: ≥5 nodes, score ≥0.55 → STRONG BUY/SELL, ≥0.25 → BUY/SELL

### Signal reconciliation rules (MANDATORY)
When engines conflict, apply in order:
1. Kelly TRIM + AI undervalued → weight signal only, trim to optimal size
2. Consensus BUY + AI overvalued → hold/add but watch RSI >72
3. Analyst STRONG BUY + Kelly AVOID → Kelly wins for sizing
4. Regime TRENDING + Kelly TRIM → Kelly wins (trend ≠ correct allocation size)

---

## Risk gate (trading/risk_gate.py) — 8 gates in priority order
1. KILL_SWITCH — file at `~/.gesh/KILL` → reject all
2. MARKET_HOURS — outside 09:30–16:00 ET → reject
3. CONFIDENCE_FLOOR — intent.confidence < MIN_CONFIDENCE → reject
4. POSITION_CAP — target_pct > MAX_POSITION_PCT → reject
5. ORDER_RATE — orders today ≥ MAX_ORDERS_PER_DAY → reject
6. CASH_FLOOR — post-trade cash < MIN_CASH_BUFFER_USD → reject
7. DAILY_LOSS — portfolio P&L ≤ -MAX_DAILY_LOSS_PCT → reject
8. DATA_FRESH — last quote > MAX_PRICE_AGE_SECONDS old → reject

Every decision (approve or reject) is persisted to `risk_decisions` before returning.

---

## Key rules for all AI work in this project

### Always do before analysis
1. Read the relevant skill file from `.claude/skills/`
2. Inject regime context into any deep portfolio analysis
3. Check news sentiment direction vs technical regime
4. Cross-reference Kelly sizing with fundamental verdict
5. For scanner: run sector_filter → consolidation_zone → full engines (in that order)

### Never do
- Fabricate sentiment when no news is found (article_count=0)
- Cluster scoring — spread scores across full 0-100 range realistically
- Skip Monte Carlo when declaring a portfolio "safe"
- Use vague language — always cite specific dollar amounts, percentages, dates
- Set `auto_trade=True` or `PAPER_MODE=false` without weeks of clean paper history
- Size against `account["buying_power"]` — on Gold accounts it is the **leveraged margin**
  figure (observed $21,632 while settled cash was $1,718). The deployable figure is
  `account["portfolio_cash"]` (settled, unleveraged — matches the RH MCP's
  `buying_power.buying_power`). Never `account["cash"]` (negative margin-accounting figure).
  `withdrawable_amount` reads $0 while a deposit clears, so it is not a deployability proxy.
  `fetch_live_snapshot.py` emits the canonical `deployable` key (settled, leverage-capped)
  plus `settled_cash` / `pending_deposits` for report decomposition — size against `deployable`.

### Output standards
- Risk amounts always in dollars: "$4,200 at risk at 95% VaR"
- Predictions must have rationale tied to fundamentals, not just technical
- Regime must be injected before deep_analyze_portfolio is called
- Portfolio recommendations must be P1/P2/P3 prioritized
- Present tables, not walls of text — Symbol | Score | Action | Why
- Share count formulas: BUY = floor((kelly_pct/100 × total − current_equity) / price), TRIM = ceil

### Warning thresholds
| Metric | Warning level |
|--------|--------------|
| VaR 95% | > 15% of portfolio → RED |
| Sector concentration | > 40% single sector → WARNING |
| Portfolio beta | > 1.5 → amplified drawdown warning |
| Cash | < 5% → no dry powder warning |
| Kelly total allocated | > 100% → over-conviction warning |
| Sentiment swing | > 30 pts vs 7-day rolling → ALERT |
| Daily P&L | ≤ −1% → risk gate blocks all orders |
| Paper edge vs live | > 5% → consider scaling live allocation |

---

## Autopilot schedule (trading/autopilot.py)
| Time (ET) | Job | Description |
|-----------|-----|-------------|
| 08:30 | pre_market_scan | Consensus over watchlist, store BUY/SELL candidates |
| 09:35 | open_window | Execute candidates through risk gate (paper mode) |
| 12:30 | midday_rebalance | Re-run consensus on held positions, check >5% drift |
| 15:55 | close_risk_check | EOD stop-loss sweep + P&L alert |

Run: `python -m trading.autopilot` or test one job: `python -m trading.autopilot --job pre_market_scan`

---

---

## v2 Features (FinceptTerminal-inspired)

### Feature 1 — FRED Macro Layer (`regime.py`)
**Function:** `get_macro_context()` — called automatically inside `detect_regimes()`

Fetches 3 live macro indicators from FRED (St. Louis Fed — free, no API key):
| Series | FRED ID | Signal |
|--------|---------|--------|
| Fed Funds Rate | DFF | RISING / FALLING / FLAT |
| 10Y-2Y Yield Spread | T10Y2Y | INVERTED / FLAT / NORMAL / STEEP |
| CPI YoY % | CPIAUCSL | ACCELERATING / DECELERATING / STABLE |

Output: injected as the first block of `regime_summary` before ADX/BB/RSI context.
Claude receives the full macro + technical picture in every deep analysis prompt.

**When INVERTED yield curve**: historically precedes recessions by 6-18 months.
Always note this in risk analysis and reduce position size recommendations.

---

### Feature 2 — Polymarket Macro Sentiment (`news_agent.py`)
**Function:** `_fetch_polymarket_macro()` — called at end of `scan_news()`

Fetches active prediction market prices from Polymarket's free Gamma API.
Filters for macro-relevant markets: Fed rate, recession, S&P 500, inflation.

| Field | Description |
|-------|-------------|
| `macro_risk_score` | 0–100 composite bearish signal (50 = neutral) |
| `macro_signal` | RISK_ON / NEUTRAL / RISK_OFF |
| `markets` | Top 5-8 relevant markets with YES probability + volume |

**Signal blending logic:**
- News RISK_ON + Polymarket RISK_OFF → combined = NEUTRAL (prediction markets lead news)
- News NEUTRAL + Polymarket RISK_OFF → combined = RISK_OFF
- News NEUTRAL + Polymarket RISK_ON → combined = RISK_ON

`scan_news()` now returns `market_mood` (combined), `news_mood` (news-only), and `polymarket` block.

---

### Feature 3 — Gemini Multi-LLM Agent (`gemini_agent.py`)
**SDK:** `google-genai` (new official SDK, NOT deprecated `google-generativeai`)

| Function | Model | Use case |
|----------|-------|----------|
| `gemini_quick_analysis(portfolio_summary)` | gemini-2.5-flash | Fast daily portfolio commentary |
| `gemini_news_sentiment(symbol, articles)` | gemini-2.5-flash | Batch news scoring (drop-in for Haiku) |
| `gemini_second_opinion(symbol, analysis, verdict)` | gemini-2.5-flash | Contrarian second opinion (independent from Claude) |
| `gemini_market_commentary(regime, macro, portfolio)` | gemini-2.5-flash | Macro narrative synthesis |
| `get_gemini_status()` | — | Key + model availability check |

**Fallback chain:** On 429 quota errors, auto-retries with `gemini-2.0-flash-lite`.

**When to use Gemini vs Claude:**
- Use Gemini Flash for high-volume batch tasks (news sentiment for 20+ symbols)
- Use Gemini second opinion AFTER Claude gives STRONG BUY — different training = different blind spots
- Claude remains primary for all deep portfolio analysis and execution decisions

---

## Development notes

### Running the app
```bash
cd gesh-project
source venv/bin/activate
python server.py          # uvicorn on port 9000
```

### Running CLI analysis
```bash
python main.py            # quick CLI risk report
```

### Running autopilot (paper mode)
```bash
PAPER_MODE=true TELEGRAM_ALERTS=true python -m trading.autopilot
```

### Installing new dependencies
```bash
pip install apscheduler requests python-dotenv --break-system-packages
```

### Dependencies (key)
- `robin_stocks` — Robinhood API
- `anthropic` — Claude AI
- `yfinance` — market data
- `fastapi` + `uvicorn` — web server
- `pandas`, `numpy` — data processing
- `requests` — news + EDGAR + Telegram fetching
- `apscheduler` — autopilot scheduler (new)
- `zoneinfo` — market hours timezone (stdlib Python 3.9+)

### Watchlist default (data_fetcher.py)
```python
DEFAULT_WATCHLIST = ["MSTR", "SPY", "QQQ"]
```

### VirtioFS / SQLite note
`gesh_financial.db` may live on a macOS-shared FUSE mount. `trade_log.py` auto-detects
this at import and falls back to `/tmp/gesh_trades.db` for trade_log / paper_trades tables
if DDL fails. Analysis data always writes to the primary DB via db.py.

### DB corruption recovery (2026-06-02)
A daily-analysis write to `gesh_financial.db` failed with `database disk image is malformed`
— this is **real corruption** (`PRAGMA integrity_check` showed a page cross-link "2nd
reference to page 597" + broken indexes), NOT the VirtioFS DDL limitation above. Do not
confuse the two: DDL-block falls back to `/tmp` and is harmless; "malformed" means the file
needs rebuilding. Recovery procedure (run on the **host**, where the file is plain APFS):
```bash
cp -p gesh_financial.db gesh_financial.db.corrupt.bak
sqlite3 gesh_financial.db '.recover' | sqlite3 gesh_financial_recovered.db
sqlite3 gesh_financial_recovered.db 'PRAGMA integrity_check'   # must print: ok
# verify rowcounts >= original, then swap:
mv gesh_financial.db gesh_financial.db.corrupt.orig && mv gesh_financial_recovered.db gesh_financial.db
```
`.recover` walks raw pages and restores records the corrupt b-tree can't reach (this run:
analysis_runs 39 → 119 rows recovered, equity_snapshots intact at 28,442). The DB is
gitignored (`*.db`) so recovery never needs a commit.

### Sandbox Yahoo 401 / Invalid Crumb
yfinance 401s in the **sandbox** are burst rate-limiting from the shared egress IP, not a
hard block — single calls succeed; the consensus engine's many parallel calls trip the
limiter. The **host venv** has clean Yahoo access. For daily analysis, run the engine suite
on the host (`./venv/bin/python`) for complete data rather than degraded sandbox results.
(PR105-RUN-01: always the venv's GENERIC interpreter — this repo declares Python 3.12 in
`.python-version`, so a hardcoded `python3.14` path does not exist in a conformant env.)

---

## UI Changes — 2026-05-02

### 🔬 Daily Analysis Tab (8th tab in dashboard.html)

Added on-demand daily portfolio analysis to the main dashboard. Button: **▶ Run Daily Analysis**.

**What it does:**
- Calls `/api/deep-analysis` + `/api/kelly` + `/api/portfolio` in parallel (`Promise.all`)
- Renders 5 stat cards: Portfolio Score, Portfolio Type, Risk Score, Diversification, AI Verdict
- Market context block (Claude narrative)
- **Kelly trade plan table** with exact share counts: Δ SHARES, $ IMPACT, NOW % vs KELLY % columns
  - TRIM rows: `ceil(abs(delta_usd)/price)` shares, red tint, P1
  - ADD rows: `floor(abs(delta_usd)/price)` shares, green tint, P2
- Holdings cards: snowflake score bars (VAL/GRO/HLT/MOM/DIV), fair value pill, risks/rewards, 3M/12M predictions
- Portfolio risks list with severity coloring
- Sector breakdown bars
- History dropdown — replays any past run from `/api/history` without re-running Claude

**JS functions:** `daRunAnalysis()`, `daRender(data, meta, kellyData, portData)`, `daLoadHistory()`, `daLoadRun()`

---

## Execution Layer Notes — Updated 2026-05-21 ★★★

### Paper Trading Table Clarification (CRITICAL)

The **`orders` table in `gesh_financial.db`** is the source of truth for paper fills — NOT `paper_trades`.

- `orders` table: 707+ rows, `status='SIMULATED'` for actual fills, `mode='paper'` — written by `trading/execution.py`
- `paper_trades` table: 0 rows — legacy table from `paper_trading.py`, not used by the autopilot pipeline
- Any analytics, win-rate, or weekly review queries must use: `orders WHERE status='SIMULATED' AND mode='paper'`

The `weekly-portfolio-review` skill currently reads `paper_trades` (empty). When running the skill manually, supplement with:
```python
conn = sqlite3.connect('gesh_financial.db')
fills = conn.execute(
    "SELECT * FROM orders WHERE status='SIMULATED' AND mode='paper' ORDER BY placed_at DESC"
).fetchall()
```

### Known Bug Fixes Applied to gesh-project-trading (2026-05-21)

Six compounding bugs blocked all paper fills for 34 days. All fixed — see `gesh-project-trading/CLAUDE.md` rules 20–25 for the full list. Key ones affecting this project's analytics:

1. `MAX_ORDERS_PER_DAY` raised from 3 → 15 (`.env`)
2. REJECTED orders no longer count toward daily cap (`trading/state.py`)
3. Market scanner BUY intents now include `target_position_pct` (`trading/autopilot.py`)

### Autonomy Gate

Live trading on Alpaca requires: ≥30 closed paper trades · win_rate ≥55% · Sharpe ≥0.5 · max drawdown &lt;10%.
Current: 0 closed trades. ETA: Aug–Sep 2026.

---

## Commercial Surface — 2026-06 ★ (proof page · auth wall · routes · /progress)

New modules + routes on :9000 (full writeups: `../gesh-project-trading/docs/SYSTEM_OVERVIEW.md` + `RUNBOOK.md`):

| Module | Route | Purpose |
|---|---|---|
| `proof_page.py` | `GET /track-record` (public) | Signal-accuracy proof page — the #1 sales asset; reads the -trading tracker DB read-only; honest "day X/180, N/30 closed" |
| `legal_pages.py` | `GET /terms` `/disclaimer` (public) | Renders `TERMS_OF_SERVICE.md`; **strips the internal "Compliance notes" section** — never serve it |
| `auth_users.py` | login wall (env-gated **OFF**) | Per-user credentials for the paid alpha; `GESH_AUTH_ENABLED=1` + `GESH_AUTH_SECRET` to enable; immediate revoke via `auth_users.py disable`. Stdlib-only (PBKDF2 + HMAC cookie), no new deps. `gesh_auth.db`. **Typed tokens** (`typ='session'`/`'magic'` bound into the HMAC payload — a magic URL token can't be replayed as a session cookie). Billing cols + `list_owed_onboarding()` added 2026-06-26. |
| `billing.py` | `POST /api/billing/webhook` (public, HMAC-gated) | **Lemon Squeezy** (merchant-of-record) webhook → provision/revoke on `auth_users`. Fail-closed HMAC-SHA256 (raw body, constant-time, case-folded); outbox-style **idempotency** (owner-fenced reclaim); **past-due grace** (revoke ONLY on `subscription_expired`, never on first failed charge); sub-id-authoritative resolution + email-change move. Magic-link onboarding via `email_sender.py`. **Async onboarding safety net:** `resend_owed_onboarding()` (CLI `python3 billing.py sweep-onboarding`, scheduled 15-min — see LaunchAgents) re-sends to anyone still owed a link after LS exhausts its retries; `_alert_dead_letter()` fires a Telegram alert on a stuck user. See `docs/PAYMENT_INTEGRATION_PLAN.md` §10. |
| `email_sender.py` | — | Transactional email (Resend HTTP API, no new SDK). `RESEND_API_KEY` + `EMAIL_FROM`; dev no-op (logs, returns False) when key unset so the webhook path stays testable. |
| `progress_view.py` | `GET /progress` (internal, wall-gated) | Founder cockpit — weekly burndown to the Sep alpha; reads `weekly_snapshots` (written by the -trading Friday job) |
| `invest_radar.py` + `invest_view.py` | `GET /invest` `/invest/data` (internal, wall-gated) | **Diversification Radar** — advisory-only daily scan of NEW (non-held) names with a 5-factor score + cash-aware sizing; surfaces book sector concentration + swap ideas. Computed in `run_ai2.py` (the 10:30 job), persisted to `engine_results.json`, rendered in the daily report + `/invest`. **Never auto-trades; read-only; no PII.** Full writeup: `docs/DIVERSIFICATION_RADAR.md` |
| `db.py::_safe_json` | `/api/history/{id}` | Malformed `result_json` degrades to `parse_error` instead of HTTP 500 |

Public routes are exempt from the login wall (`auth_users.PUBLIC_PREFIXES`); `/progress` is **not** (internal).
The product is single-tenant (one Robinhood account); the alpha model is "10 users view the owner's research."

## Customer Surface — 2026-07-08 ★★ (PRs #18–#24: research → book → report → quotas)

The customer product beyond "view the owner's research": self-serve research and
portfolio analysis, fully isolated per user. Full runbook: `docs/CUSTOMER_SURFACE.md`.
Live smoke after any deploy: `./venv/bin/python3 smoke_customer_surface.py [--with-llm]`.

| Surface | Routes | Notes |
|---|---|---|
| Analyze any ticker | `GET /api/{regime,fundamentals,analyst,news}/{symbol}` | Mechanical cards, no LLM. 5-min TTL cache + heavy rate bucket (trailing-slash prefixes — exact `/api/regime` stays normal). RH primary / yfinance fallback (VPN) |
| AI research card | `POST /api/analyze-ticker` | One Claude call/symbol/15min (cache + per-symbol single-flight). `require_customer_or_owner` — fail-closed 503 when wall OFF without `GESH_API_KEY`. Price-grounded; 404-before-spend on unknown tickers. **Mask-exempt** (public per-share prose) |
| Watchlist | `GET/POST /api/watchlist`, `DELETE /api/watchlist/{sym}` | Per-user, 50 cap, `watchlist` table in gesh_auth.db |
| My Portfolio (book) | `GET/POST /api/mybook`, `DELETE /api/mybook/{sym}`, `POST /api/mybook/import-watchlist` | Customer's OWN positions (30 cap), valued live via cached closes. **Mask-exempt** — their own dollars |
| Book analysis | `POST /api/mybook/analyze` | Owner engine suite on their book: regime-injected deep analysis → **verdict-aware Kelly** (same ai_verdicts map as /api/kelly) + Monte Carlo. Cached per (user, book fingerprint) 15 min, single-flight per user, Kelly/MC best-effort. Persisted to `customer_analyses` (newest 5) |
| Report | `GET /mybook/report` | Print-friendly (→PDF) book + last analysis; server-rendered, ALL strings escaped; links `/track-record`. Session-gated, never spends LLM |
| Metering | `usage_events` table | `ticker_card[_cached]`, `book_analysis[_cached]`, `book_report` — `log_usage` NEVER raises |
| Quotas | `_check_customer_quota` (server.py) | Rolling-24h caps on LIVE LLM calls only (cached free, checked before any spend). Env base × tier multiplier: observer 1× / trader 2× / **desk 5×** (`users.tier`, set by the billing webhook). Owner exempt; 0 disables; fail-open |
| Owner metering view | `GET /usage` (owner-only) + `GET /api/usage/summary` | In `_OWNER_ONLY_PATHS`; customers 403 |

Frontend: the **Analyze** tab (dashboard.html) is the whole customer UI — role-gated nav
(`body.customer` hides `.owner-only`), watchlist chips, book table, progressive AI card.
XSS guard: HTML builders must be `build*`-named (audited) and callers pass RAW values to
`buildRow` (it escapes internally). Test isolation: conftest clears server caches,
`_RATE_HITS`, flight locks, and points `AUTH_DB_PATH` at a throwaway per test.

## Daily Analysis Chain — 2026-06-10 ★★★ (the system's heartbeat)

The daily portfolio analysis is fully deterministic — a scheduled Claude agent's only
role is verify + narrate (see `.claude/skills/daily-portfolio-analysis/SKILL.md`).
NEVER hand-run engines, hand-edit gesh_report.html, or hand-write SQL.

**Orchestrator:** `run_daily_analysis.sh` — launchd `com.gesh.daily-analysis`, 10:30 ET
Mon–Fri, ~4–6 min, logs to `/tmp/gesh_fullchain.log`, PID-liveness lock, self-asserting
(snapshot ≤30 min, report ≥60KB, DB row), publishes to the trading repo only on clean runs.
Missed-during-sleep runs fire once at wake (off-hours prices + MARKET_HOURS rejections = normal).

| Step | Script | Output |
|---|---|---|
| 1/5 | fetch_snapshot_mcp.sh → build_snapshot_from_mcp.py (MCP-first; `fetch_live_snapshot.py` legacy fallback) | live_portfolio_snapshot.json (+ adjusted_equity_previous_close) |
| 2/5 | run_engines2.py | engine_results.json — regime+FRED macro, 11-node consensus, Kelly, MC, vols, alpha, tax, explainability, yield, crypto. MERGE-writes (never clobber) + stamps `*_ran_at` (consensus/kelly/monte_carlo/regime/ai_result) for the sidebar freshness badges |
| 3/5 | run_ai2.py | radar + one Claude deep-analysis call (bounded client: 240s timeout, max_tokens 10000 + truncation guard) + db.log_analysis/log_equity_snapshot (path-pinned to THIS repo's db.py — plain `import db` resolves to the trading copy) |
| 4/5 | run_paper_gate2.py | auto-close check → action plan from Kelly+overrides → paper session → 8-gate audit (`action_plan`/`gate_results`/`paper_session` in engine_results.json) → Telegram signal alert (@gesh755bot) |
| 5/5 | gen_report2.py | gesh_report.html (~73KB, incl. Paper & Risk-Gate Audit section) — served at `GET /report` |

**Snapshot source assertion (step 1/5, added 2026-06-29):** the snapshot is now MCP-first.
`fetch_snapshot_mcp.sh` writes a raw RH MCP dump to `/tmp/gesh_mcp_raw.json`, then
`build_snapshot_from_mcp.py` builds `live_portfolio_snapshot.json` with `source: "robinhood_mcp"`,
pulling cash from `portfolio.buying_power.buying_power` (settled, UNLEVERAGED). **The MCP exposes
NO Gold-margin figure**, so the builder pins `buying_power_leveraged == margin_buying_power ==
withdrawable_amount == deployable` — they are back-compat keys only (report/gates read them), carrying
no independent margin info. This *structurally* eliminates the old ~$21,632 leveraged-BP trap rather
than guarding against it downstream. If the MCP path fails, the chain falls back to the legacy
`fetch_live_snapshot.py` (robin_stocks), which stamps `source: "robinhood_live"` and **repopulates the
real leveraged figure** — sizing-safe by code (`deployable = min(settled, bp_leveraged)`) but a different
data path. The staleness block in `run_daily_analysis.sh` now reads `d['source']`, prints it on the
`snapshot age:` line, and emits a **non-fatal `WARNING:`** when it is not `robinhood_mcp`. **`source` is
the canonical tell for which path ran** — a `robinhood_live` source in a briefing means the MCP fallback
fired; verify `deployable` before trusting any sizing. Kept non-fatal because the legacy fallback is a
legitimate degraded path, not a failure.

**sys.path shadowing (critical):** run_engines2/run_ai2 put gesh-project-trading at
`sys.path[0]` — ALL engine modules + ai_analyzer + db resolve to the TRADING copies.
Edit those copies for engine behavior; pin to this repo's files via importlib when needed.

**Paper-mode risk gates (rules #32/#36 ported 2026-06-10, commit 27721a0):** this repo's
`trading/risk_gate.py` now mirrors the trading repo — PAPER mode gates CASH_FLOOR on
`cfg.paper_cash_usd` ($100k, env `PAPER_CASH_USD`) and DAILY_LOSS on the paper book's own
realized P&L (`state.paper_realized_pnl_usd_today`); LIVE mode unchanged. Keep both copies
in sync (same hazard class as trading rule #37). Guard tests: `test_risk_gate_paper_mode.py`
— never call `evaluate()` from tests without sandboxing `trading.state.DB_PATH`.
First production approval: 2026-06-11 (3/3 BUYs, all 9 gates).

**Two paper books (deliberate):** trade_log's `paper_trades` (this repo's DB) = the daily
chain + manual sims; the trading repo's tracker (`paper_positions`/`paper_outcomes`) = the
autopilot's consensus signals and the ONLY autonomy-gate counter, powering /watch +
/track-record. Do not merge them — mixing strategies muddies the gate proof. The book was
reset 2026-06-11 to production entries only (test rows backed up in `_archive/`); only the
chain should call run_paper_gate2.py.

**Path conventions (2026-06-10):** zero hardcoded /Users paths in tracked code. Shell
scripts derive roots from `$(dirname $0)`; Python uses the sibling-dir convention with env
overrides `GESH_TRADING_HOME` (trading repo root) and `GESH_TRACKER_DB` (tracker DB path).
launchd plists stay absolute by design (machine templates).

**Caching:** `/api/regime` serves the chain's cached regime when <4h fresh
(`?force_live=true` bypasses). `/api/consensus/cached` + `/api/kelly/cached` normalize the
chain's per-symbol dicts to `{holdings:[...]}`. Dashboard endpoints return `{status,data}`
envelopes — frontend readers must unwrap (a whole class of "renders zeros" bugs, fixed
2026-06-10).

**Proof page (/track-record):** upgraded to the signal-verification standard — timestamped
ledger (entry_at/exit_at), all losses visible, avg win/loss + profit factor, SPY
same-period benchmark (1h cache, fail-soft), per-source labeling, forward-out-of-sample
methodology block. Never paywall it or signal explainability.

**ECG:** loss streaks derive from consecutive negative equity CHANGES — never feed it
`percent_change` (a vs-cost level), which falsely SUSPENDs every underwater holding.

## Gate Coherence + ECG Repair — 2026-07-17 ★★★ (Cowork session)

Five related fixes landed in one session; all backups sit next to the originals
as `*.bak_20260717_*` / `*.bak_20260718_*`.

1. **SKILL.md narration contract** (`.claude/skills/daily-portfolio-analysis/SKILL.md`):
   Step 3 now opens with a MANDATORY signal-vs-gate banner (item 0) — one plain
   sentence reconciling `action_plan` vs `gate_results` before anything else —
   and forbids blank Gate/Rationale cells in the action table.
2. **Report banner** (`gen_report2.py`): new `_signal_gate_banner()` renders the
   same reconciliation in `gesh_report.html`, injected above the AI Verdict via
   the existing override-banner pattern. Verified against 2026-07-17 engine data:
   "signals lean ADD on 5 names, gate blocked all 5, net actionable: 0."
3. **Position cap** (`.env`): `MAX_POSITION_PCT=0.18` (was code-default 0.02).
   Root cause: the gate caps POST-TRADE TOTAL position, and with a 10-position
   real book (positions already 2–15% each) a 2% cap rejected every Kelly-sized
   ADD by construction — the daily gate lane was a permanent rejection log.
   0.18 > largest position (15.4%) and > half-Kelly ceiling (16.3%). SCOPED:
   only `run_paper_gate2.py` load_dotenv()s this file; the autopilot launchd
   plist never reads .env (rule #33) and keeps 0.02 for its own entry sizing —
   raising it globally would have resized autopilot entries 9x. Do NOT move
   this var into shared/launchd env without rechecking that coupling.
4. **ECG node revived** (`consensus.py` node_ecg): still imported the pre-Phase-3.2
   names (`_drawdown/_streak/_gate_status`) → ImportError → ABSTAIN every run
   (trading repo copy was already fixed; this copy was missed). Now mirrors
   `gesh_core.ecg.compute_ecg` exactly: reverse newest-first history before the
   running-peak math, feed `_loss_streak` per-snapshot % CHANGES (never raw
   equity levels — always positive, streak pinned to 0).
5. **equity_snapshots decontaminated**: 224 frozen NVDA rows (equity $101.99,
   current_price=0.0, several/day since 2026-07-04 = the RH password-change
   session zombie) purged; 0 bad rows remain, DB backed up first. The D-07
   zero-price guard (gesh-core v0.5.3) is installed in the venv but the
   long-running :9000 server predates it — **PENDING: restart via
   `launchctl kickstart -k gui/$(id -u)/com.gesh.main`** or it keeps writing
   junk. Until restarted, treat any new price<=0 row as the same bug.

**Verification watch:** the 2026-07-20 (Mon) 10:30 ET chain run is the first
under the new cap — the report banner should show partial approvals (Kelly-sized
adds ≤16.3% clear; >18% still reject) instead of blanket rejection. FINNHUB_API_KEY
placeholder line also removed from `.env` (was shadow-defined twice).

## Analyze-progress UX — 2026-07-19 ★★ (the async 202+poll contract, and how to not misread it)

**Read this before ever filing `/api/mybook/analyze` as broken.** It returns **202
(async enqueue), NEVER 429-for-busy**. A QA audit filed "Test 4 FAILED — daily rate
limit exhausted"; nothing was broken. The run had **completed**.

**The contract:** 202 → the client polls the SAME endpoint every 3s, `attempt<110`
= a **5.5-minute budget** (sized to outlast the Cloudflare tunnel's ~100s limit).
A 40-position book takes ~3 min. **Navigating away kills the in-page poll loop** —
that is what the tester did (left at 1m13s of a 3m02s run).

**Diagnosing a "nothing happened" report — in this order:**
| Question | Where to look |
|---|---|
| Did it enqueue? | `grep 'POST /api/mybook/analyze' /tmp/gesh_main.log` → **202 = fine**, 429 = real quota |
| Did it finish? | `sqlite3 gesh_auth.db "SELECT job_id,status,delivered_at,created_at,updated_at FROM mybook_jobs ORDER BY created_at DESC LIMIT 3"` |
| Is the result there? | `sqlite3 gesh_auth.db "SELECT created_at,length(result_json) FROM customer_analyses ORDER BY created_at DESC LIMIT 3"` |

**`delivered_at IS NULL` on a `done` job is NOT a leak** — it is a deliberate
handoff (`/api/mybook/analyze`): the next POST returns that result `200` and marks it
delivered. **A finished analysis is HELD; one click retrieves it with zero new Claude spend.**

**Quota lives in `usage_reservations`, not `usage_events`.** `start_mybook_job`
reserves the slot inside `BEGIN IMMEDIATE` **before** any LLM call (the Gap #7
concurrency-bypass fix). **The absence of a `book_analysis` usage_event for a live
run is EXPECTED** — do not read it as un-metered spend.

**40→8 detailed-card truncation is by `equity` DESC**, disclosed in the UI, and
`book_meta.note` forces portfolio verdict/sector/diversification from
`all_holdings_summary` — **scores reflect the FULL book**; Kelly does too.

### What this session changed (PR: `fix/analyze-progress-ux`)
1. **Live elapsed counter** on the analyze spinner (`mybook-elapsed`, `setInterval`,
   cleared in `finally`) + copy stating the result survives navigating away. Static
   text could not distinguish *working* from *hung*; the ticking number proves liveness.
2. **`_ready_analysis_pending(email)`** — `/mybook/report` now detects a **done,
   undelivered, fingerprint-MATCHING** job and shows "a newer analysis is ready …
   no new AI usage" instead of the alarming "reflect the OLD portfolio" banner.
   Fingerprint-matched on purpose: a held result for a *different* book keeps the
   honest stale warning. Fail-quiet (never 500s a print page).
3. **`_throttled_log_due(key, interval)`** — the offline-RH retry logged **26,445
   identical lines** (~6% of a 21MB unrotated `/tmp/gesh_main.log`) from a 60s retry
   that cannot succeed off-hours. First hit still logs immediately; repeats collapse
   into a 5-min line that **reports the suppressed count** (a quiet log must never
   imply a quiet system). Reset via `_reset_throttled_logs()` in tests.

Guard tests: `tests/test_analyze_progress_ux.py` (17). Suite **1,367 passed**.
**QA rule learned:** never hardcode price bands in a test sheet — the audit's INTC
`$100–120` / AMD `$500–600` were written against 7/16 closes and both fell 7/17, so
correct app values ($95.04 / $495.76, penny-accurate vs the broker) read as failures.
Derive expectations at runtime from the RH feed.

## Disclaimer
This tool is for informational and educational purposes. Not financial advice.
All trading decisions remain the user's responsibility.
PAPER_MODE=true by default. Never flip to live execution without weeks of paper validation.
