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

```bash
# Restart this service
launchctl bootout gui/$(id -u)/com.gesh.main
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gesh.main.plist

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
│   ├── consensus.py         — 10-node consensus voting engine
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
KILL_SWITCH_PATH=~/.gesh/KILL  # create this file to block all orders immediately
MAX_POSITION_PCT=0.02     # max 2% of equity per symbol
MAX_DAILY_LOSS_PCT=0.01   # max 1% daily loss before gate blocks all orders
MAX_ORDERS_PER_DAY=3      # daily order rate cap
MIN_CASH_BUFFER_USD=500   # minimum cash floor
MIN_CONFIDENCE=0.75       # minimum consensus confidence to trade
MAX_PRICE_AGE_SECONDS=300 # reject stale quotes > 5 min old

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
| `/api/consensus` | POST | 10-node consensus engine |
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

## Consensus engine (consensus.py) — 10 nodes

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
- Use `account["cash"]` — always use `profile["withdrawable_amount"]`

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

## Disclaimer
This tool is for informational and educational purposes. Not financial advice.
All trading decisions remain the user's responsibility.
PAPER_MODE=true by default. Never flip to live execution without weeks of paper validation.
