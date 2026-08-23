# Customer Surface Runbook

The self-serve customer product on :9000 (PRs #18–#24, 2026-07-08; book panes
and cash sliders through PR #235, 2026-08-10): any-ticker research, a per-user
watchlist and portfolio book, mechanical book engines (consensus / ECG /
scanner / intelligence), the full engine suite on the customer's own book, a
printable report, and metering/quotas underneath. Everything here is per-user
isolated and derived from public market data plus the customer's own inputs —
**zero owner data**.

## Route map

| Route | Method | Auth | LLM spend | Notes |
|---|---|---|---|---|
| `/api/regime/{sym}` | GET | session¹ | no | ADX/BB/RSI + FRED for any ticker |
| `/api/fundamentals/{sym}` | GET | session¹ | no | Robinhood primary, yfinance fallback |
| `/api/analyst/{sym}` | GET | session¹ | no | RH buy/hold/sell primary, yfinance targets fallback. ETFs legitimately 404 |
| `/api/news/{sym}` | GET | session¹ | no | Alpaca primary (key-gated), RSS fallback. Headlines only — no sentiment score |
| `/api/chart/{sym}?period=` | GET | session¹ | no | 1D/5D = Alpaca 5-min bars (trimmed to the requested sessions); daily ranges = Yahoo/RH |
| `/api/symbol-search?q=` | GET | session¹ | no | Company-name→ticker autocomplete (RH primary, static fallback). Feeds the Analyze search dropdown |
| `/api/momentum/{sym}` | GET | session¹ | no | RSI + MACD + Volume → BUY/SELL/NEUTRAL signal. Pure TA, no LLM |
| `/api/levels/{sym}` | GET | session¹ | no | Support/resistance (pivots + fractal swings) → long setup entry/stop/target/RR. Pure price math |
| `/api/earnings/{sym}` | GET | session¹ | no | EPS beat/miss history + surprise %. Finnhub primary (VPN-safe), yfinance fallback; honest "unavailable" without a source |
| `/api/analyze-ticker` | POST | customer-or-owner² | **yes** | The 🧠 AI research card. Price-grounded, 404-before-spend, 15-min/symbol cache, single-flight |
| `/api/consensus/{sym}` | GET | session¹ | no | Stateless 11-node consensus on any ticker. **`allow_ecg=False`** — the owner's `equity_snapshots` must never vote here |
| `/api/signals/history/{sym}` | GET | customer-or-owner² | no | 30-day consensus sparkline. Customers: watchlist/book symbols only |
| `/api/dcf/cached` | GET | customer-or-owner² | no | Owner daily-chain DCF (reporting-only) — **not** the customer's book |
| `/api/watchlist` | GET/POST/DELETE `{sym}` | session | no | 50-symbol cap |
| `/api/mybook` | GET/POST/DELETE `{sym}` | session | no | The customer's own positions (30 cap), valued live |
| `/api/mybook/import-watchlist` | POST | session | no | Missing watchlist symbols added at 1 share (placeholder) |
| `/api/mybook/analyze` | POST | customer-or-owner² | **yes** | Deep analysis → verdict-aware Kelly + Monte Carlo + radar. **202 + poll**, never 429-for-busy |
| `/api/mybook/history` | GET | session | no | Index of this user's last 5 persisted analyses (`customer_analyses`) |
| `/api/mybook/history/{id}` | GET | session | no | Replay one past run — `cached: true`, zero new LLM. Other users' ids → 404 (no existence oracle) |
| `/api/mybook/consensus` | POST | customer-or-owner² | no | Book consensus. ECG node **ABSTAINS** (owner curve). Cap 12 symbols |
| `/api/mybook/ecg` | GET | session | no | Equity Curve Gate over **this user's** `customer_equity_snapshots` — never `/api/ecg` |
| `/api/mybook/deploy-cash` | POST | session | no | Size last Kelly ADD gaps against `{cash}`. Advisory; 404 if no analysis |
| `/api/mybook/radar-cash` | POST | session | no | Size persisted radar shortlist (new names) against `{cash}`. See below |
| `/api/mybook/scanner` | POST | session | no | Sector → consolidation scan on the customer's symbols (5-min cache, cap 20) |
| `/api/mybook/intelligence` | POST | session | no | SEC + cached news + fundamentals on the customer's symbols (5-min cache) |
| `/mybook/report` | GET | session | no | Print-friendly report of book + last persisted analysis |
| `/usage` · `/api/usage/summary` | GET | **owner only** | no | Per-customer metering counters |

¹ "session" = behind the login wall when `GESH_AUTH_ENABLED=1`; open in solo/dev mode.
² `require_customer_or_owner`: any authenticated session when the wall is ON; with the
wall OFF it requires the `X-API-Key` header (`GESH_API_KEY`) and otherwise fails closed
(503) — these endpoints are never an anonymous LLM-spend surface.

## Environment

```
GESH_AUTH_ENABLED=1           # login wall — unlocks the AI card + book analysis for sessions
GESH_AUTH_SECRET=...          # cookie HMAC secret
ALPACA_API_KEY= / ALPACA_SECRET_KEY=   # 1D/5D intraday charts + Recent News (dormant if unset)
FINNHUB_API_KEY=              # VPN-safe earnings history for /api/earnings (yfinance fallback is Yahoo-blocked behind GlobalProtect; card shows "unavailable" without it)
QUOTA_DAILY_TICKER_CARD=25    # base cap: AI cards / customer / rolling 24h (0 = off)
QUOTA_DAILY_BOOK_ANALYSIS=10  # base cap: book analyses / customer / rolling 24h
```

## Quotas & tiers

Caps apply to **live analysis calls only** — cached hits are metered separately and
are always free, and the check runs *before* any spend (a capped user costs
nothing). A Grok/Gemini failover hop still consumes the reservation: the slot
is taken inside `BEGIN IMMEDIATE` before `llm_gateway` runs. The billing
webhook sets `users.tier` on provision; quotas scale automatically:

| Tier | Multiplier | Ticker cards/day (base 25) | Book analyses/day (base 10) |
|---|---|---|---|
| (none) / `observer` | 1× | 25 | 10 |
| `trader` | 2× | 50 | 20 |
| `desk` ($399 Trading Desk) | 5× | 125 | 50 |

The owner is always exempt. Quota enforcement fails open if metering errors — it can
never take the product down.

## Storage (gesh_auth.db)

| Table | Contents |
|---|---|
| `watchlist` | (email, symbol) saved tickers |
| `customer_positions` | (email, symbol, shares, avg_cost) — the book |
| `customer_analyses` | last 5 analysis results per user (powers `/mybook/report` + `/api/mybook/history`) |
| `customer_equity_snapshots` | per-user equity history for `/api/mybook/ecg` — **not** `equity_snapshots` |
| `usage_events` | (email, event, detail, at) metering rows |

All tables auto-create via the idempotent DDL on every connection — no migration step.

## Book analysis — the 202 + poll contract

`POST /api/mybook/analyze` returns **202 (async enqueue), never 429-for-busy**.
The client polls the **same** endpoint every 3s (`attempt < 110` ≈ 5.5 min,
sized to outlast the Cloudflare ~100s limit). A finished job with
`delivered_at IS NULL` is a deliberate hold — the next POST returns it `200`
and marks it delivered. Quota lives in `usage_reservations` (reserved inside
`BEGIN IMMEDIATE` before any LLM call), not `usage_events`.

Diagnosing "nothing happened":

| Question | Where |
|---|---|
| Did it enqueue? | `grep 'POST /api/mybook/analyze' /tmp/gesh_main.log` → **202 = fine** |
| Did it finish? | `sqlite3 gesh_auth.db "SELECT job_id,status,delivered_at FROM mybook_jobs ORDER BY created_at DESC LIMIT 3"` |
| Is the result there? | `sqlite3 gesh_auth.db "SELECT created_at,length(result_json) FROM customer_analyses ORDER BY created_at DESC LIMIT 3"` |

Navigating away kills the in-page poll; the result is still held. `/mybook/report`
detects a done, undelivered, fingerprint-matching job and says a newer analysis
is ready (no new AI usage) instead of the stale-book warning.

## Isolation — owner tables the customer must never read

| Owner table / route | Customer substitute | Why |
|---|---|---|
| `equity_snapshots` / `GET /api/ecg` | `GET /api/mybook/ecg` | owner curve is keyed by symbol with no user column |
| `run_consensus_holding(allow_ecg=True)` | `/api/consensus/{sym}` forces `allow_ecg=False`; `/api/mybook/consensus` uses `run_consensus_for_customer` | ECG would vote the owner's drawdown on any shared ticker |
| `GET /api/history` (`analysis_runs`) | `GET /api/mybook/history` | owner deep-analysis ledger |
| `get_latest_fundamental_scan` (~95-name owner run) | `/api/mybook/intelligence` batch-fetches only the customer's symbols | owner universe must not leak |

A customer book-consensus payload therefore has the ECG node **ABSTAIN**
(max coverage 89% vs the owner's 100%) and states the exclusion so a verdict
never quietly counted a node the customer did not receive.

`GET /api/{consensus,kelly,monte-carlo,screener,deep-analysis,dcf}/cached`
serve `engine_results.json` from the **owner daily chain**, not the caller's
book. Customer panes must use `/api/mybook/*`. Do not wire a customer UI to
the cached chain routes.

## Cash sliders (advisory, no LLM)

Both endpoints need a persisted analysis first (404 otherwise). Body:
`{"cash": <number>}` with `0 < cash ≤ 10_000_000`.

| Route | Sizes | Cache interaction |
|---|---|---|
| `/api/mybook/deploy-cash` | last Kelly **ADD gaps** on names the customer **already holds** | arithmetic on cached Kelly |
| `/api/mybook/radar-cash` | Diversification Radar shortlist — **new, non-held** names | radar is computed **cash-free** at analyze time so the slider does not bust the (user, book-fingerprint) cache. Sizing uses persisted `resize_state` |

`invest_radar.WHAT_IF_MAX` ($1,000,000) hard-caps the radar slider.
`deployable_cash` is what was actually sized; `cash` is echoed as asked —
a silent clamp would look like a bug. `stale: true` when the book fingerprint
no longer matches. Leftover is vs the amount asked, not the clamp.

## Scanner + intelligence panes

Mechanical, 5-minute per-user cache, no LLM:

- **Scanner** (`POST /api/mybook/scanner`) — sector pre-filter then
  consolidation-zone scan. Optional body: `timeframe` (`5MIN` default via
  `get_scanner_params`), `min_rr` (clamped 0–10, default 2.0),
  `include_watchlist` (default true). Cap
  `GESH_MYBOOK_SCANNER_MAX_SYMBOLS` (default 20); `truncated: true` when cut.
- **Intelligence** (`POST /api/mybook/intelligence`) — three isolated legs
  (one dead leg does not blank the panel):
  - SEC: stored filings first, LLM-free EDGAR metadata fallback, re-filtered
    to the entitled set (shared table). Cap 6 symbols, min 30 days.
  - News: **cached** sentiment only; rows with `article_count=0` are dropped
    (never fabricate NEUTRAL). Allow-listed fields match `/api/news/{sym}`.
  - Fundamentals: batch fetcher + pre-score on the customer's symbols —
    never `run_fundamental_scan` (Claude) and never the owner's latest scan.

## Privacy / masking invariants

- `/api/mybook*` and `/api/analyze-ticker` are **mask-exempt** (`_MASK_EXEMPT_PREFIXES`):
  they carry the customer's own dollars / public per-share prose, and masking would
  redact the customer's own data back at them. **Only add a path there if it provably
  contains no owner position/cash data** — the leak sweep's categorization test forces
  every new customer GET to be justified.
- `/usage` is in `_OWNER_ONLY_PATHS` (customers 403 at the middleware) with a second
  owner check in the handler.
- The report page is server-rendered outside the dashboard's audited JS builders, so
  **every dynamic string is `html.escape`d** — a test injects `<script>` via a fake
  LLM result and asserts it renders inert.

## Owner Robinhood session fence (PR #258)

Customer traffic must never restore or spend the owner's `robin_stocks` pickle.
`price_source` defaults `_ALLOW_AUTHENTICATED_RH` to **True** so the daily chain
can fall back to the authenticated historicals after a Yahoo 401 / Invalid Crumb.
Until #258, only `/api/explainability/{symbol}` opted out. A customer hit on
`/api/consensus/{symbol}`, `/api/mybook/*`, `/api/chart/{symbol}`, or any other
engine path that goes through `yf_download` / `rh_fallback` could call
`_ensure_rh_session()` and restore the owner pickle — rate-limiting or locking
the same session the 10:30 chain and live dashboard depend on.

| Surface | How the fence is applied |
|---|---|
| Authenticated customer HTTP (wall ON) | `_customer_view_middleware` wraps the request in `unauthenticated_rh_fallback()`, including mask-exempt `/api/mybook` and `/api/analyze-ticker` |
| `POST /api/mybook/analyze` worker | The HTTP handler returns **202** and exits the middleware ContextVar. `_mybook_analyze_worker` re-enters the same wrap before payload + Kelly/MC/radar |
| `run_consensus_portfolio` thread pool | `ThreadPoolExecutor` does not copy ContextVars. The caller snapshots `contextvars.copy_context()` and each holding runs `ctx.run(...)` so workers keep `_ALLOW_AUTHENTICATED_RH=False` |
| Owner / wall-OFF / auth-disabled | Unchanged — authenticated fallback stays on. That is the chain's resilient path |

When the flag is False, `rh_fallback` does **not** call `_ensure_rh_session()` and
`_rh_history_one` skips `robin_stocks.get_stock_historicals`. Daily intervals
still use the **public** (unauthenticated) RH historicals endpoint; other
intervals return no RH bars. Yahoo remains primary.

**Still on main — do not treat as closed.** `market_data.rh_analyst` /
`rh_fundamentals` (`GET /api/analyst/{symbol}`, `GET /api/fundamentals/{symbol}`)
call `robin_stocks` directly and do not read `_ALLOW_AUTHENTICATED_RH`. They
cannot *restore* a pickle (that is `_ensure_rh_session`), but if an owner
session is already live in this process they can still spend it. Coverage for
that leftover is draft #261, not this contract.

Pins: `tests/test_price_source_fallback.py` (customer GET sees False, owner GET
sees True, mybook worker re-wraps, consensus pool inherits the flag, customer
fallback never calls `_ensure_rh_session`).

## Smoke test

After any deploy / env change, on the host:

```bash
./venv/bin/python3 smoke_customer_surface.py                    # wall OFF
./venv/bin/python3 smoke_customer_surface.py --email E --password P   # wall ON
./venv/bin/python3 smoke_customer_surface.py --with-llm        # + 2 real Claude calls
```

One PASS/FAIL/SKIP line per step; SKIPs name the missing config (e.g. Alpaca keys).
Restores the watchlist/book to their prior state. Exit ≠ 0 ⇒ something is actually broken.

## Troubleshooting

| Symptom | Cause |
|---|---|
| 🧠 AI card shows "🔒 Sign in to unlock" | Wall OFF without `GESH_API_KEY`, or anonymous session — fail-closed by design |
| "⏳ Daily limit reached" | Rolling-24h quota hit — cached results still serve; tier multiplier may be missing (`users.tier` NULL) |
| 1D/5D chart says intraday unavailable | `ALPACA_API_KEY/SECRET` unset — daily ranges unaffected |
| Analyst card 404 for an index/ETF | Correct — no analyst ratings exist; not a bug |
| Report says "No analysis yet" | The user never ran a live analyze (or the persisted rows were pruned) — run Analyze My Portfolio once |
| Customer consensus/chart after Yahoo 401 tries MFA / touches the owner pickle | Fence missed that path — HTTP should be inside `_customer_view_middleware`; the 202 mybook worker must wrap itself; consensus pool must `copy_context()` |
| Fundamentals/analyst cards still hit RH while Yahoo is dark | Those two cards call `robin_stocks` directly (`market_data.rh_*`) and are **outside** the #258 ContextVar. An already-live owner session in this process can still be spent. |
| Analyze spinner then "rate limit" / tester left the page | 202 + poll is working; navigating away kills the JS loop. Result is held — click Analyze again for the 200 handoff |
| Customer ECG / consensus shows a huge drawdown on a name they barely hold | You hit `GET /api/ecg` or the owner `POST /api/consensus` — those read the owner curve. Use `/api/mybook/ecg` and `/api/mybook/consensus`. Per-symbol `/api/consensus/{sym}` already forces `allow_ecg=False` |
| Radar slider amount ≠ allocations | `deployable_cash` was clamped at `$1,000,000` (`WHAT_IF_MAX`) or names rounded under 1 share. Check `leftover` and `stale` |
| Intelligence news all empty | Cache has no `article_count>0` rows for those symbols — honest empty, not a 0-score mood |
