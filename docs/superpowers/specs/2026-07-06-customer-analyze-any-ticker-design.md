# Design — Customer "Analyze Any Ticker" (v1)

_2026-07-06 · branch `feat/customer-analyze-any-ticker`_

## Problem

The paid customer view is the owner's single-tenant book: every tab shows the
owner's fixed 10 positions, masked
to a $100K model book, served from the 10:30 daily run (7h+ stale). A paying
stranger can't analyze a stock they care about, can't chart anything outside the
10, and the 1D/5D charts are blank. This is not sellable — confirmed by the owner
across 10 dashboard screenshots.

## Goal

A customer types **any US ticker** and gets a **live, stateless** engine read on
it — no owner data, no portfolio, no dollars, no Claude narrative. The customer's
centerpiece becomes *their* ticker search, not the owner's book.

## Non-goals (explicitly deferred to v1.1+)

- **Personal saved watchlist** (per-user persistence). The Overview "Watchlist"
  widget is hidden for customers in v1; saved watchlists are the immediate v1.1.
- **Claude AI deep-dive per ticker** — paid API call per analysis; later, behind a
  per-user daily quota + per-ticker cache.
- **Fresh news-sentiment scoring per ticker** — uses an LLM (Haiku); consensus
  already folds in cached news. Later.
- **User portfolio input / brokerage connect** — out of scope entirely for now.

## Scope — four items

| # | Item | Summary |
|---|------|---------|
| 1 | Analyze any ticker | New customer **Analyze** view: free-text ticker → live consensus + regime + FFT cycle + fundamentals + chart for that symbol. |
| 2 | Chart: any symbol | The Overview PRICE CHART symbol picker (locked to the 10 holdings) becomes a free ticker entry for customers. |
| 3 | Chart: fix 1D/5D | Add an **Alpaca 5-minute intraday adapter** to `price_source`; key-gated. Activates when `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` are in `.env`; honest fallback otherwise. |
| 4 | Hide owner book from customers | Owner-only nav/sections: Overview-holdings, Allocation, Risk/Monte-Carlo, Kelly, Scanner, Intelligence, Radar, Daily Analysis, Paper Trades. Customer sees **Analyze + Chart + Track Record**. Owner keeps everything. |

## Architecture

### Endpoints

All new/used analysis endpoints are **GET, `_safe_symbol()`-validated, return only
public market data** (signals computed on public prices — no owner dollars, so the
masking layer is a non-issue for them; it still applies defensively).

**Already customer-reachable (reuse as-is):**
- `GET /api/consensus/{symbol}` — 11-node verdict, stateless (`run_consensus_holding` with no position context). Already live.
- `GET /api/fft-cycles/{symbol}` — dominant cycle.
- `GET /api/chart/{symbol}?period=` — price series.

**New (thin wrappers, customer-reachable GET):**
- `GET /api/regime/{symbol}` — wraps `regime.detect_regimes([sym])`; returns that stock's ADX/BB/RSI regime + shared FRED macro block.
- `GET /api/fundamentals/{symbol}` — wraps `fundamental_agent._fetch_fundamentals_batch([sym])`; returns the single-ticker momentum row.
- `GET /api/me` — tiny role endpoint: `{role: "owner"|"customer"}`. Lets the customer nav render correctly on load (deterministic, not racing the portfolio fetch). No secrets.

### Intraday provider (item 3)

`price_source` gains a pluggable intraday path:
- New `price_source.intraday_5m(symbol, span)` → tries providers in order:
  1. **Alpaca** (`alpaca-py`, keys from env) — free intraday stock bars, not Yahoo-blocked. Primary.
  2. (future) authed Robinhood historicals — omitted in v1 (session often dead).
- `GET /api/chart/{symbol}` for `period in (1d,5d)` calls `intraday_5m`; if no provider is configured/returns data, it returns the current honest `{intraday_unavailable: true}` state (frontend already renders "try a daily range").
- **Dependency:** `alpaca-py` added to `requirements.txt`. Env: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (data-only; paper keys suffice). Absent key → feature dormant, daily ranges unaffected.

### Cost & abuse controls (the reason this is safe to open)

- **Per-ticker shared cache**: each analysis endpoint caches by `(symbol, …)` with a
  ~15-min TTL (chart already has a 30s cache; consensus/regime/fundamentals get a
  short TTL cache keyed by symbol). N users analyzing AAPL = one computation.
- **Per-session rate limit**: reuse the existing per-IP heavy-endpoint limiter on
  the analyze endpoints (e.g. the existing 30/min heavy-AI bucket). Zero Claude
  calls in the hot path → marginal cost ≈ yfinance/Alpaca bandwidth.
- **Symbol hygiene**: `_safe_symbol` already uppercases + strips; reject empty/junk
  with a clean 4xx, never a 500.

### Frontend (customer vs owner)

- **Role gate**: on load, `GET /api/me`. `role==="customer"` → render the **customer
  nav** (Analyze, Chart, Track Record) and hide every owner-only section. Keep the
  existing `window.__isCustomer` (set from `view:'model_book'`) as a defense-in-depth
  fallback so a customer never sees owner UI even if `/api/me` is slow.
- **Analyze view**: a prominent ticker input → on submit, fetch the 5 per-symbol
  endpoints in parallel and render: consensus card, regime badge, FFT cycle, a
  fundamentals row, and the chart. Empty state before first search ("Enter a ticker
  to analyze — e.g. AAPL").
- **Chart symbol picker**: for customers, replace the holdings `<select>` with a
  free-text ticker input bound to the same `loadChart()`.
- **Owner unchanged**: owner sees the full 8-tab dashboard exactly as today.

### Security

- New endpoints return public data only → add them to `tests/test_customer_leak_sweep.py`
  COVERED so the sweep proves no owner value can appear.
- Any new rendered string (symbol, verdict, sector, regime label) goes through
  `daEsc()` — the deny-by-default XSS guard (`tests/test_dashboard_xss_wiring.py`)
  will flag anything missed; fix at the sink.
- Owner-only endpoints stay `require_api_key`. `/api/me` reveals only a role, no PII.

## Component breakdown (isolable units)

1. **`price_source.intraday_5m`** — purpose: fetch 5m bars for a symbol via Alpaca;
   interface: `(symbol, span) -> DataFrame|None`; deps: `alpaca-py`, env keys. Pure
   fetch, returns None on any failure (never raises).
2. **`GET /api/regime/{symbol}`, `/api/fundamentals/{symbol}`** — thin adapters over
   existing engine functions; interface: symbol → JSON row; deps: `regime`,
   `fundamental_agent`.
3. **`GET /api/me`** — role resolver; interface: session cookie → `{role}`; deps:
   `auth_users.is_owner` + `authenticate_session`.
4. **Chart intraday wiring** — `price_chart` handler routes 1d/5d to `intraday_5m`.
5. **Frontend Analyze view + role gate** — new tab + nav filter; deps: `/api/me`,
   the 5 per-symbol endpoints, `daEsc`.

## Testing (TDD — failing test first for each)

- **Per endpoint**: valid ticker → 200 + expected shape; junk ticker → clean 4xx
  (not 500); customer-reachable (no 401/403); no owner sentinel in body (leak-sweep).
- **`/api/me`**: owner session → `owner`; customer session → `customer`; no session
  → 401 (behind the wall).
- **Intraday**: with a stub Alpaca provider → 1d/5d returns bars; with no provider →
  `intraday_unavailable` (not a 500), daily ranges still return data.
- **Cache**: two calls within TTL → one upstream fetch (shared).
- **Rate limit**: N+1 rapid calls → 429.
- **Frontend guard**: extend `test_dashboard_xss_wiring.py` to the new renders; a
  customer-role render hides owner sections (assert nav set).
- Full suite + both CI jobs green; pre-push gate green.

## Rollout

- One feature branch, PR per logical unit where clean (endpoints, intraday, frontend).
- `alpaca-py` added to requirements; 1D/5D dormant until the owner adds Alpaca keys —
  the rest ships and works without it.
- No change to owner experience, gates, PAPER_MODE, or masking/XSS/leak guards
  (extended, never loosened).

## Risks / open

- **Intraday depends on the owner adding Alpaca keys.** Mitigation: adapter is
  key-gated and dormant-safe; 1D/5D shows the honest existing message until keys land.
- **Opening the engine to arbitrary tickers** raises compute/bandwidth vs the fixed
  10. Mitigation: per-ticker shared cache + per-session rate limit; no AI in the path.
- **`/api/consensus/{symbol}` stateless run** omits Kelly/ECG/position nodes (they
  need a position) — those nodes abstain, which is correct for a stateless read; the
  UI labels them "n/a (no position)".
