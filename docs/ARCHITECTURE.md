<p align="center">
  <img src="assets/hero.svg" alt="GESH Financial — architecture" width="100%">
</p>

# 🏛️ GESH Financial — Architecture

GESH Financial is a single-node Python platform that turns a **live Robinhood portfolio** into AI-driven trading intelligence. A FastAPI server (port `9000`) ingests holdings via `robin_stocks`, runs a stack of quantitative engines (ADX/Bollinger/RSI **regime** with a live FRED macro layer, **Monte Carlo**, **Kelly** sizing, **FFT cycles**, EWMA/GARCH volatility) and a **12-node consensus** voting engine, then routes structured prompts to Claude (Sonnet/Haiku), Gemini, and a local Ollama model for deep analysis and contrarian second opinions. Every signal is funneled through a typed **`TradeIntent`** contract and an **8-gate risk gate** before a paper-mode execution layer simulates orders — with the whole `signal → gate → execute` chain audited to SQLite and scheduled by an APScheduler autopilot. The Chart.js dashboard is the customer-facing product; the trading engine runs silently to accumulate paper win-rate proof.

On top of the owner engine sits a **per-user customer surface** (the *Analyze* tab): any-ticker research cards (company-name→ticker search, 12-node consensus, momentum RSI/MACD/Volume, support/resistance trade setup, EPS beat/miss earnings, regime, cycle, fundamentals, analyst, news), a price-grounded **AI research card** (one Claude call/symbol/15 min), a per-user **watchlist + portfolio book**, and a full engine run on the customer's own book — all money-masked so a customer never sees the owner's real account dollars. Customer HTTP also runs inside `unauthenticated_rh_fallback()` so a Yahoo 401 cannot restore the owner's `robin_stocks` pickle (PR #258). See [`CUSTOMER_SURFACE.md`](CUSTOMER_SURFACE.md).

---

## Table of Contents

1. [System Context](#1-system-context)
2. [Container & Component Map](#2-container--component-map)
3. [Primary Flow — Deep Analysis (sequence)](#3-primary-flow--deep-analysis-sequence)
4. [Signal → Gate → Execute Pipeline (sequence)](#4-signal--gate--execute-pipeline-sequence)
5. [Data Flow](#5-data-flow)
6. [12-Node Consensus Engine (flow)](#6-12-node-consensus-engine-flow)
7. [Autopilot Trading Day (state)](#7-autopilot-trading-day-state)
8. [Persistence Model (ER)](#8-persistence-model-er)
9. [Tech Stack](#9-tech-stack)

---

## 1. System Context

The platform sits between a single **operator/trader** and a fan of external market, intelligence, and AI APIs — pulling live portfolio + market data in, and pushing structured analysis to a dashboard and Telegram alerts out.

```mermaid
flowchart TB
    operator(["Operator or Trader"]):::actor
    gesh{{"GESH Financial - FastAPI port 9000"}}:::core

    subgraph MKT["Market and Brokerage"]
        rh["Robinhood - robin_stocks"]:::ext
        yf["yfinance or Yahoo"]:::ext
        fred["FRED macro CSV"]:::ext
    end

    subgraph AI["AI Models"]
        claude["Anthropic Claude - Sonnet and Haiku"]:::ai
        gemini["Google Gemini 2.5"]:::ai
        ollama["Ollama llama3.2"]:::ai
    end

    subgraph INTEL["Intelligence Feeds"]
        news["Finnhub, Polymarket, Yahoo RSS"]:::ext
        edgar["SEC EDGAR"]:::ext
        tg["Telegram Bot"]:::alert
    end

    operator -->|"dashboard, skills"| gesh
    gesh -->|"holdings, equity, quotes"| rh
    gesh -->|"history, fundamentals"| yf
    gesh -->|"rates, yield curve, CPI"| fred
    gesh -->|"forced-tool JSON"| claude
    gesh -->|"second opinion"| gemini
    gesh -->|"offline fallback"| ollama
    gesh -->|"sentiment, macro"| news
    gesh -->|"10-K, 10-Q, 8-K"| edgar
    gesh -->|"fills, rejections, EOD PnL"| tg

    classDef actor  fill:#0f766e,stroke:#5eead4,color:#fff
    classDef core   fill:#b45309,stroke:#fbbf24,color:#fff
    classDef ai     fill:#7c3aed,stroke:#c4b5fd,color:#fff
    classDef ext    fill:#475569,stroke:#94a3b8,color:#fff
    classDef alert  fill:#be123c,stroke:#fda4af,color:#fff
```

---

## 2. Container & Component Map

Inside the single `server.py` process, work is layered: **ingestion** feeds **quant engines** and **intelligence agents**, which feed the **consensus + AI routing** layer, which hands verdicts to the **trading execution** subpackage — all backed by **dual SQLite stores**.

```mermaid
flowchart TB
    subgraph EDGE["Edge"]
        api["server.py - 60+ api routes, auth, caches"]:::core
        dash["dashboard.html - Chart.js, 8 tabs"]:::core
        invest["/invest - diversification radar panel"]:::core
        cli["main.py CLI"]:::core
    end

    subgraph INGEST["Ingestion"]
        fetch["auth and data_fetcher to cache.json"]:::svc
    end

    subgraph QUANT["Quant Engines"]
        regime["regime, monte_carlo, kelly, fft, volatility"]:::data
        radar["invest_radar - diversification radar (advisory, new-name screen)"]:::data
    end

    subgraph AGENTS["Intelligence Agents"]
        intel["news, sec, fundamental, gemini, ollama"]:::ai
    end

    subgraph BRAIN["Consensus and AI Routing"]
        cons["consensus.py - 12 nodes"]:::data
        ai["ai_analyzer.py - Claude routing and schemas"]:::ai
    end

    subgraph EXEC["trading Execution"]
        gate["contract, risk_gate, execution, autopilot, alerts"]:::danger
    end

    store[("SQLite - gesh_financial.db and gesh.db")]:::store

    dash --> api
    cli --> fetch
    api --> fetch --> regime
    fetch --> intel
    regime --> cons
    intel --> cons
    cons --> ai
    ai --> gate
    cons --> gate
    regime --> radar
    radar --> ai
    radar --> invest
    api --> invest
    regime --> store
    intel --> store
    gate --> store

    classDef core   fill:#b45309,stroke:#fbbf24,color:#fff
    classDef svc    fill:#0e7490,stroke:#67e8f9,color:#fff
    classDef data   fill:#047857,stroke:#6ee7b7,color:#fff
    classDef ai     fill:#7c3aed,stroke:#c4b5fd,color:#fff
    classDef danger fill:#be123c,stroke:#fda4af,color:#fff
    classDef store  fill:#475569,stroke:#cbd5e1,color:#fff
```

---

## 3. Primary Flow — Deep Analysis (sequence)

A deep-analysis request fans out across ingestion, regime detection, Claude forced-tool JSON, and the parallel consensus/Kelly/Monte-Carlo engines, then persists the run and returns sanitized JSON the dashboard renders.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant API as server.py
    participant DF as data_fetcher
    participant RG as regime.py
    participant AI as ai_analyzer
    participant CL as Claude Sonnet
    participant CN as consensus.py
    participant DB as gesh_financial.db

    Op->>API: POST /api/deep-analysis
    API->>DF: build_data_payload or live cache
    DF-->>API: holdings, equity, quotes
    API->>RG: detect_regimes plus FRED macro
    RG-->>API: regime_summary string
    API->>AI: deep_analyze_portfolio pruned plus regime
    AI->>CL: forced return_portfolio_analysis tool
    CL-->>AI: structured per-holding JSON
    AI-->>API: scores, fair value, risks
    par parallel engines
        API->>CN: run_consensus_portfolio
        CN-->>API: verdict plus confidence per symbol
    end
    API->>DB: log_analysis plus warm engine cache
    API-->>Op: sanitized JSON to Chart.js dashboard
```

---

## 4. Signal → Gate → Execute Pipeline (sequence)

A chosen verdict becomes a typed `TradeIntent`, runs through the eight ordered risk gates (each persisted), and on approval writes a SIMULATED paper order with an idempotency key — optionally firing a Telegram alert.

```mermaid
sequenceDiagram
    autonumber
    participant SRC as Consensus / Manual
    participant CT as contract.TradeIntent
    participant API as /api/trading/consensus-order
    participant RG as risk_gate.evaluate
    participant EX as execution.place
    participant ST as trading/state.py
    participant TG as Telegram

    SRC->>CT: from_consensus verdict
    CT->>API: typed TradeIntent
    API->>RG: evaluate intent
    Note over RG: 8 gates - kill-switch, hours, confidence, cap, rate, cash, daily-loss, data-fresh
    RG->>ST: write risk_decisions row
    alt all gates pass
        RG-->>API: APPROVED
        API->>EX: place intent
        EX->>ST: orders row, status=SIMULATED
        EX->>TG: fill alert
    else any gate fails
        RG-->>API: REJECTED plus reason
        API->>TG: rejection alert
    end
```

---

## 5. Data Flow

Live brokerage and market data converge with external intelligence, feed the quant + consensus engines and Claude, then split into a persisted audit trail and a rendered result surface.

```mermaid
flowchart LR
    rh["Robinhood"]:::ext --> cache["gesh_live_cache.json - atomic snapshot"]:::svc
    yf["yfinance .info - 15-min cache"]:::ext --> eng["quant engines - regime, MC, kelly, FFT"]:::data
    fred["FRED macro"]:::ext --> eng
    cache --> eng
    news["news, SEC, fundamentals"]:::ai --> gdb[("gesh.db - sentiment")]:::store
    gdb --> cons["consensus - 12 nodes to verdict"]:::data
    eng --> cons
    cons --> claude["Claude forced-JSON - deep analysis"]:::ai
    claude --> persist[("gesh_financial.db - analysis, orders")]:::store
    cons --> persist
    persist --> ui["Chart.js dashboard plus cached api"]:::core
    persist --> cliout["CLI report"]:::core

    classDef ext   fill:#475569,stroke:#94a3b8,color:#fff
    classDef svc   fill:#0e7490,stroke:#67e8f9,color:#fff
    classDef data  fill:#047857,stroke:#6ee7b7,color:#fff
    classDef ai    fill:#7c3aed,stroke:#c4b5fd,color:#fff
    classDef store fill:#334155,stroke:#cbd5e1,color:#fff
    classDef core  fill:#b45309,stroke:#fbbf24,color:#fff
```

---

## 6. 12-Node Consensus Engine (flow)

Ten intentionally-uncorrelated signal nodes each vote BULLISH/NEUTRAL/BEARISH/ABSTAIN; the normalized score is thresholded into STRONG/BUY/SELL or HOLD verdicts.

```mermaid
flowchart TB
    sym(["Symbol"]):::core
    subgraph NODES["10 Uncorrelated Nodes"]
        n1["Momentum"]:::node
        n2["Trend"]:::node
        n3["Rel. Strength"]:::node
        n4["Fundamentals"]:::node
        n5["Analyst"]:::node
        n6["News"]:::node
        n7["Kelly"]:::node
        n8["ECG"]:::node
        n9["Volume"]:::node
        n10["FFT Cycle"]:::node
    end
    vote["score = sum of votes over voting-nodes"]:::data
    thr{"abs score threshold"}:::gate
    strong["STRONG BUY or SELL - over 0.55"]:::buy
    weak["BUY or SELL - over 0.25"]:::buy
    hold["HOLD or NEUTRAL"]:::neutral

    sym --> n1
    n1 --> vote
    vote --> thr
    thr -->|"high"| strong
    thr -->|"mid"| weak
    thr -->|"low"| hold

    classDef core    fill:#b45309,stroke:#fbbf24,color:#fff
    classDef node    fill:#7c3aed,stroke:#c4b5fd,color:#fff
    classDef data    fill:#047857,stroke:#6ee7b7,color:#fff
    classDef gate    fill:#0e7490,stroke:#67e8f9,color:#fff
    classDef buy     fill:#047857,stroke:#34d399,color:#fff
    classDef neutral fill:#475569,stroke:#94a3b8,color:#fff
```

---

## 7. Autopilot Trading Day (state)

The APScheduler autopilot replays the `scan → execute → rebalance → EOD` pipeline on an Eastern-Time cron, looping each weekday and pushing a Telegram P&L summary at close.

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> PreMarketScan: 08:30 ET
    PreMarketScan --> OpenWindow: candidates stored
    OpenWindow --> Rebalance: 09:35 gate and execute
    Rebalance --> CloseRiskCheck: 12:30 drift over 5 pct
    CloseRiskCheck --> Idle: 15:55 EOD sweep and alert
    OpenWindow --> Halted: kill-switch or daily-loss
    Rebalance --> Halted: kill-switch or daily-loss
    Halted --> Idle: next session reset
    Idle --> [*]: shutdown
```

---

## 8. Persistence Model (ER)

The audit chain links every signal to its gate verdict and resulting simulated order across the `gesh_financial.db` analysis and trading tables.

```mermaid
erDiagram
    ANALYSIS_RUNS  ||--o{ EQUITY_SNAPSHOTS : produces
    TRADE_INTENTS  ||--|| RISK_DECISIONS   : evaluated_by
    RISK_DECISIONS ||--o| ORDERS           : approves
    NEWS_SENTIMENT }o--o{ TRADE_INTENTS    : informs

    ANALYSIS_RUNS {
        int    id PK
        string model
        json   result
        string created_at
    }
    TRADE_INTENTS {
        string intent_id PK
        string symbol
        string action
        float  confidence
    }
    RISK_DECISIONS {
        int    id PK
        string intent_id FK
        string verdict
        string failed_gate
    }
    ORDERS {
        string idempotency_key PK
        string intent_id FK
        string status
        string mode
    }
    NEWS_SENTIMENT {
        string symbol PK
        int    score
        string updated_at
    }
    EQUITY_SNAPSHOTS {
        int    id PK
        string symbol
        float  equity
    }
```

---

## 9. Tech Stack

| Layer | Technologies |
|---|---|
| **API / Server** | FastAPI · uvicorn · threading · hmac (X-API-Key) |
| **Ingestion** | robin_stocks · pyotp · pandas · atomic JSON cache |
| **Quant Engines** | numpy · pandas · yfinance · FRED CSV API |
| **AI Routing** | Anthropic Claude (Sonnet/Haiku) · forced tool-use · prompt caching |
| **Alt Intelligence** | Google Gemini 2.5 · Ollama llama3.2 · Finnhub · Polymarket · SEC EDGAR · VADER |
| **Execution** | dataclasses · APScheduler · zoneinfo · SHA1 idempotency · Telegram Bot API |
| **Persistence** | sqlite3 — `gesh_financial.db` · `gesh.db` |
| **Presentation** | Chart.js dashboard (8 tabs) · `.claude/skills/*` playbooks · ANSI CLI |
| **Runtime** | Python 3.12+ · macOS launchd auto-start |

---

<p align="center"><sub>Generated architecture documentation · diagrams render natively on GitHub.</sub></p>
