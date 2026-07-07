# Customer Analyze-Any-Ticker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a paying customer type any US ticker and get a live, stateless engine read (consensus + regime + FFT cycle + fundamentals + chart) instead of the owner's fixed 10-position book.

**Architecture:** Reuse existing customer-reachable per-symbol GETs (`/api/consensus/{symbol}`, `/api/fft-cycles/{symbol}`, `/api/chart/{symbol}`); add three thin GETs (`/api/regime/{symbol}`, `/api/fundamentals/{symbol}`, `/api/me`); add an Alpaca 5-min intraday adapter to `price_source` for 1D/5D charts; gate the frontend nav on role so customers see Analyze + Chart + Track Record while the owner keeps everything.

**Tech Stack:** FastAPI, yfinance, alpaca-py (new, for intraday), vanilla JS dashboard, pytest + Starlette TestClient.

## Global Constraints

- Python 3.14; `./venv/bin/python` runs tests. Type hints on every new signature.
- New per-symbol endpoints are **GET, `_safe_symbol()`-validated, customer-reachable (no `require_api_key`), return only PUBLIC market data** (no owner dollars).
- Never loosen masking/XSS/leak guards, gates, or PAPER_MODE. New renders escaped via `daEsc()`.
- Every render string added to `templates/dashboard.html` must pass `tests/test_dashboard_xss_wiring.py`.
- CI runs `ruff check tests/` then pytest — keep `tests/` ruff-clean.
- Commit with explicit paths; branch `feat/customer-analyze-any-ticker` (already created).
- Intraday adapter is key-gated: dormant + honest fallback when `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` absent; daily ranges unaffected.

---

### Task 1: `/api/regime/{symbol}` + `/api/fundamentals/{symbol}` endpoints

**Files:**
- Modify: `server.py` (add two GET handlers near the other per-symbol GETs, ~line 1439 after fft-cycles)
- Test: `tests/test_analyze_any_ticker.py` (create)

**Interfaces:**
- Consumes: `regime.detect_regimes([sym]) -> {"regimes":[{symbol,regime,adx,bb_width,rsi,pct_b,icon,description,...}], "regime_summary":str}`; `fundamental_agent._fetch_fundamentals_batch([sym]) -> list[{symbol, momentum_gap, ...}|{symbol,error}]`; `server._safe_symbol(str)->str`.
- Produces: `GET /api/regime/{symbol}` → `{"status":"ok","data":{symbol, regime, adx, rsi, ...}}`; `GET /api/fundamentals/{symbol}` → `{"status":"ok","data":{symbol, ...}}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_analyze_any_ticker.py
from __future__ import annotations

import server
from starlette.testclient import TestClient


def _client(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)  # focus endpoints, not the wall
    return TestClient(server.app, raise_server_exceptions=False)


def test_regime_symbol_returns_single_stock_regime(monkeypatch):
    monkeypatch.setattr(
        "regime.detect_regimes",
        lambda syms: {"regimes": [{"symbol": syms[0], "regime": "TRENDING",
                                   "adx": 31.2, "rsi": 55.0, "pct_b": 0.6,
                                   "bb_width": 0.1, "icon": "📈", "description": "up"}],
                      "regime_summary": "x"}, raising=False)
    r = _client(monkeypatch).get("/api/regime/nvda")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["symbol"] == "NVDA"        # _safe_symbol uppercased
    assert body["data"]["regime"] == "TRENDING"


def test_regime_symbol_bad_ticker_is_clean_4xx_not_500(monkeypatch):
    monkeypatch.setattr("regime.detect_regimes",
                        lambda syms: {"regimes": [], "regime_summary": "none"}, raising=False)
    r = _client(monkeypatch).get("/api/regime/@@@")
    assert r.status_code in (400, 404)             # never 500


def test_fundamentals_symbol_returns_single_row(monkeypatch):
    monkeypatch.setattr("fundamental_agent._fetch_fundamentals_batch",
                        lambda syms: [{"symbol": syms[0], "momentum_gap": "LOW", "score": 62}],
                        raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/aapl")
    assert r.status_code == 200
    assert r.json()["data"]["symbol"] == "AAPL"
    assert r.json()["data"]["score"] == 62
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_analyze_any_ticker.py -q`
Expected: FAIL (404 — routes not defined yet).

- [ ] **Step 3: Add the two endpoints in `server.py`** (after the `/api/fft-cycles/{symbol}` handler)

```python
@app.get("/api/regime/{symbol}")
def regime_symbol(symbol: str):
    """Market regime (ADX/BB/RSI + FRED macro) for ANY ticker — the customer
    'analyse any share' regime surface. Public data; no owner context."""
    sym = _safe_symbol(symbol)
    try:
        import regime as _regime
        out = _regime.detect_regimes([sym])
        rows = out.get("regimes") or []
        if not rows:
            raise HTTPException(status_code=404, detail="No regime data")
        return {"status": "ok", "data": rows[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)


@app.get("/api/fundamentals/{symbol}")
def fundamentals_symbol(symbol: str):
    """Fundamental momentum for ANY ticker (public yfinance data)."""
    sym = _safe_symbol(symbol)
    try:
        import fundamental_agent
        rows = fundamental_agent._fetch_fundamentals_batch([sym])
        if not rows or rows[0].get("error"):
            raise HTTPException(status_code=404, detail="No fundamentals")
        return {"status": "ok", "data": rows[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_analyze_any_ticker.py -q`
Expected: PASS (3 tests). Note: `_safe_symbol("@@@")` — confirm it raises HTTPException(400) or yields an empty→404; adjust the assertion to the real behavior if needed (both are non-500, which is the invariant).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_analyze_any_ticker.py
git commit -m "feat(api): per-ticker regime + fundamentals GETs (analyze any share)"
```

---

### Task 2: `/api/me` role endpoint

**Files:**
- Modify: `server.py` (add near the auth routes, ~line 475 area)
- Test: `tests/test_analyze_any_ticker.py` (append)

**Interfaces:**
- Consumes: `auth_users.auth_enabled()`, `auth_users.authenticate_session(token)`, `auth_users.is_owner(email)`, `auth_users.COOKIE_NAME`.
- Produces: `GET /api/me` → `{"role":"owner"}` | `{"role":"customer"}`; 401 when auth enabled and no valid session.

- [ ] **Step 1: Write the failing tests**

```python
def test_me_owner(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda t: "owner@x.com")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: True)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.get("/api/me").json()["role"] == "owner"


def test_me_customer(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda t: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False)
    c = TestClient(server.app, raise_server_exceptions=False)
    c.cookies.set(server.auth_users.COOKIE_NAME, "tok")
    assert c.get("/api/me").json()["role"] == "customer"


def test_me_auth_disabled_is_owner(monkeypatch):
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    c = TestClient(server.app, raise_server_exceptions=False)
    assert c.get("/api/me").json()["role"] == "owner"   # solo/dev mode = owner
```

- [ ] **Step 2: Run to verify fail**

Run: `./venv/bin/python -m pytest tests/test_analyze_any_ticker.py -k me -q` → FAIL (404).

- [ ] **Step 3: Add the endpoint** (must be exempt from the wall — add path to `auth_users.PUBLIC_PREFIXES`? No — it needs the cookie. It's a normal `/api/*` GET, so the wall lets it through only with a valid session, which is what we want.)

```python
@app.get("/api/me")
def whoami(request: Request):
    """Session role for the frontend nav. No PII, no secrets."""
    if not auth_users.auth_enabled():
        return {"role": "owner"}
    email = auth_users.authenticate_session(request.cookies.get(auth_users.COOKIE_NAME))
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"role": "owner" if auth_users.is_owner(email) else "customer"}
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_analyze_any_ticker.py -k me -q` → PASS (3).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_analyze_any_ticker.py
git commit -m "feat(api): /api/me role endpoint for customer/owner nav"
```

---

### Task 3: Alpaca intraday adapter + chart 1D/5D wiring

**Files:**
- Modify: `price_source.py` (add `intraday_5m`)
- Modify: `server.py` (`price_chart` handler ~line 1259 routes 1d/5d to `intraday_5m`)
- Modify: `requirements.txt` (add `alpaca-py`)
- Test: `tests/test_analyze_any_ticker.py` (append)

**Interfaces:**
- Produces: `price_source.intraday_5m(symbol: str, span: str) -> "pandas.DataFrame|None"` (columns incl. `Close`; None on any failure/no-key). `span` in `("1d","5d")`.
- `price_chart` for `period in ("1d","5d")` calls `intraday_5m`; on None returns `{"symbol":sym,"period":period,"intraday_unavailable":True,"dates":[],"prices":[]}` (frontend already renders the honest message).

- [ ] **Step 1: Write the failing tests** (stub Alpaca so no network/keys needed)

```python
import pandas as pd
import price_source


def test_chart_intraday_uses_intraday_5m(monkeypatch):
    df = pd.DataFrame({"Close": [10.0, 10.5, 11.0, 10.8, 11.2]},
                      index=pd.date_range("2026-07-06 09:30", periods=5, freq="5min"))
    monkeypatch.setattr(price_source, "intraday_5m", lambda sym, span: df, raising=False)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    r = TestClient(server.app, raise_server_exceptions=False).get("/api/chart/NVDA?period=1d")
    assert r.status_code == 200
    assert len(r.json()["prices"]) == 5


def test_chart_intraday_absent_is_honest_not_500(monkeypatch):
    monkeypatch.setattr(price_source, "intraday_5m", lambda sym, span: None, raising=False)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    r = TestClient(server.app, raise_server_exceptions=False).get("/api/chart/NVDA?period=5d")
    assert r.status_code == 200
    assert r.json().get("intraday_unavailable") is True


def test_intraday_5m_no_keys_returns_none(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    assert price_source.intraday_5m("NVDA", "1d") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `./venv/bin/python -m pytest tests/test_analyze_any_ticker.py -k "intraday or chart_intraday" -q` → FAIL.

- [ ] **Step 3a: Add `intraday_5m` to `price_source.py`**

```python
def intraday_5m(symbol: str, span: str):
    """5-minute intraday bars for `symbol` via Alpaca (Yahoo is VPN-blocked here).
    Returns a DataFrame with a `Close` column, or None on any failure / missing
    keys. Never raises. span in ('1d','5d')."""
    import os
    key = os.getenv("ALPACA_API_KEY")
    sec = os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        return None
    try:
        from datetime import datetime, timedelta, timezone
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        days = 5 if span == "5d" else 1
        client = StockHistoricalDataClient(key, sec)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(5, TimeFrame.Unit.Minute),
            start=datetime.now(timezone.utc) - timedelta(days=days + 2),
        )
        bars = client.get_stock_bars(req).df
        if bars is None or bars.empty:
            return None
        # multi-index (symbol, timestamp) → drop symbol level; rename close→Close
        if hasattr(bars.index, "nlevels") and bars.index.nlevels > 1:
            bars = bars.xs(symbol, level=0)
        return bars.rename(columns={"close": "Close"})[["Close"]]
    except Exception:
        return None
```

- [ ] **Step 3b: Wire `price_chart` in `server.py`** — replace the intraday branch so 1d/5d use the adapter:

```python
    interval  = "5m" if period in ("1d","5d") else "1d"
    if period in ("1d", "5d"):
        import price_source
        raw = price_source.intraday_5m(sym, period)
        if raw is None or getattr(raw, "empty", True):
            result = {"symbol": sym, "period": period, "intraday_unavailable": True,
                      "dates": [], "prices": [], "pct_change": 0, "current": None, "start": None}
            with _chart_cache_lock:
                _chart_cache[key] = (now, result)
            return result
    else:
        import price_source
        raw = price_source.yf_download(sym, period=yf_period, interval=interval,
                                       auto_adjust=True, progress=False)
        if raw is None or raw.empty:
            raise HTTPException(status_code=404, detail="No data")
    # (existing close/dates/prices parsing continues unchanged)
```

- [ ] **Step 3c: Add dependency** — append `alpaca-py` to `requirements.txt`. Install: `./venv/bin/pip install alpaca-py`.

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_analyze_any_ticker.py -q` → PASS (all).

- [ ] **Step 5: Commit**

```bash
git add price_source.py server.py requirements.txt tests/test_analyze_any_ticker.py
git commit -m "feat(chart): Alpaca 5m intraday adapter for 1D/5D (key-gated)"
```

---

### Task 4: Leak-sweep coverage for the new endpoints

**Files:**
- Modify: `tests/test_customer_leak_sweep.py` (add `/api/regime/{symbol}`, `/api/fundamentals/{symbol}` to `COVERED`; `/api/me` to `EXCLUDED` with reason)

**Interfaces:**
- Consumes: the sweep's `COVERED`/`EXCLUDED` dicts + `_registered_customer_gets()` categorization test.

- [ ] **Step 1: Extend the sweep fixture + coverage** — in the `client` fixture add seams:

```python
    monkeypatch.setattr("regime.detect_regimes",
                        lambda syms: {"regimes": [{"symbol": syms[0], "regime": "NEUTRAL",
                                      "adx": 20.0, "rsi": 50.0, "pct_b": 0.5, "bb_width": 0.1,
                                      "icon": "•", "description": "flat"}], "regime_summary": "x"},
                        raising=False)
    monkeypatch.setattr("fundamental_agent._fetch_fundamentals_batch",
                        lambda syms: [{"symbol": syms[0], "momentum_gap": "LOW", "score": 50}],
                        raising=False)
```

And add to `COVERED`:

```python
    "/api/regime/{symbol}": "/api/regime/NVDA",
    "/api/fundamentals/{symbol}": "/api/fundamentals/NVDA",
```

And to `EXCLUDED`:

```python
    "/api/me": "returns only a session role string (owner|customer) — carries no owner financial data",
```

- [ ] **Step 2: Run to verify categorization + no-leak pass**

Run: `./venv/bin/python -m pytest tests/test_customer_leak_sweep.py -q`
Expected: PASS — `test_all_customer_gets_categorized` green (new routes categorized), new URLs return 200/model_book with no owner sentinel.

- [ ] **Step 3: Commit**

```bash
git add tests/test_customer_leak_sweep.py
git commit -m "test(isolation): sweep the new per-ticker regime/fundamentals GETs"
```

---

### Task 5: Frontend — role gate + Analyze view + free-ticker chart

**Files:**
- Modify: `templates/dashboard.html`

**Interfaces:**
- Consumes: `GET /api/me` → `{role}`; `GET /api/consensus/{symbol}`, `/api/regime/{symbol}`, `/api/fft-cycles/{symbol}`, `/api/fundamentals/{symbol}`, `/api/chart/{symbol}`; existing `daEsc()`, `switchTab()`, `loadChart()`, `window.__isCustomer`.
- Produces: an `analyze` tab + `analyzeTicker(sym)` JS; a customer nav filter; a free-ticker chart input for customers.

- [ ] **Step 1: Role gate on load** — near the existing `window.__isCustomer` block, add a fetch that sets the nav. In the boot/init function:

```javascript
async function applyRoleNav(){
  let role='owner';
  try{ const r=await fetch('/api/me'); if(r.ok) role=(await r.json()).role; }catch(e){}
  if(role==='customer'){
    document.body.classList.add('customer');            // CSS hides owner-only nav/sections
    window.__isCustomer=true;
    switchTab('analyze');                                // customers land on Analyze
  }
}
```

Add CSS: `.customer .owner-only{display:none!important}` and tag the owner-only nav items + sections (Overview-holdings, Risk, Scanner, Intelligence, Radar, Daily Analysis, Signals, Paper Trades) with `class="owner-only"`. Keep **Track Record** link + the new **Analyze** tab visible.

- [ ] **Step 2: Add the Analyze tab markup** — a new tab pane with a ticker input:

```html
<div id="tab-analyze" class="tab-pane">
  <div class="card">
    <input id="analyze-input" placeholder="Enter a ticker — e.g. AAPL" autocomplete="off"
           onkeydown="if(event.key==='Enter')analyzeTicker(this.value)">
    <button onclick="analyzeTicker(document.getElementById('analyze-input').value)">Analyze</button>
    <div id="analyze-results"><div class="empty">Enter a ticker to analyze.</div></div>
  </div>
</div>
```

- [ ] **Step 3: `analyzeTicker(sym)` — fetch the 5 endpoints, render escaped**

```javascript
async function analyzeTicker(raw){
  const sym=(raw||'').trim().toUpperCase().replace(/[^A-Z0-9.\-]/g,'');
  const box=document.getElementById('analyze-results');
  if(!sym){box.innerHTML='<div class="empty">Enter a ticker.</div>';return;}
  box.innerHTML='<div class="spin"></div>';
  try{
    const [cons,reg,fft,fund]=await Promise.all([
      fetch(`/api/consensus/${sym}`).then(r=>r.json()).catch(()=>null),
      fetch(`/api/regime/${sym}`).then(r=>r.json()).catch(()=>null),
      fetch(`/api/fft-cycles/${sym}`).then(r=>r.json()).catch(()=>null),
      fetch(`/api/fundamentals/${sym}`).then(r=>r.json()).catch(()=>null),
    ]);
    const d=cons&&cons.data, rg=reg&&reg.data, fu=fund&&fund.data;
    box.innerHTML=`
      <h2>${daEsc(sym)}</h2>
      <div>Consensus: <b>${daEsc(d?d.verdict:'n/a')}</b> (score ${daEsc(d?d.score:'—')})</div>
      <div>Regime: ${daEsc(rg?rg.regime:'n/a')} · RSI ${daEsc(rg?rg.rsi:'—')}</div>
      <div>Fundamentals momentum: ${daEsc(fu?fu.momentum_gap:'n/a')}</div>
      <div id="analyze-chart"></div>`;
    // reuse the existing chart loader against a free symbol:
    loadChartFor(sym);
  }catch(e){ box.innerHTML='<div class="empty">'+daEsc('Could not analyze '+sym)+'</div>'; }
}
```

(If `d.verdict`/`d.score` field names differ, read them from the real `/api/consensus/{symbol}` response during implementation and adjust — verify with `curl` against the running server.)

- [ ] **Step 4: Free-ticker chart for customers** — add `loadChartFor(sym)` that fetches `/api/chart/${sym}?period=1mo` and renders into `#analyze-chart` (mirror the existing `loadChart()` render). For the Overview chart, when `document.body.classList.contains('customer')`, swap the `#chart-sym` `<select>` for a text input bound to the same fetch.

- [ ] **Step 5: `node --check` the extracted `<script>`, then run the XSS guard**

```bash
./venv/bin/python -m pytest tests/test_dashboard_xss_wiring.py -q
```
Expected: PASS. If it flags an interpolation, wrap that sink in `daEsc()` and re-run.

- [ ] **Step 6: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(ui): Analyze-any-ticker view + role-gated customer nav"
```

---

### Task 6: Full verification + PR

- [ ] **Step 1: Run the whole suite + guards**

```bash
./venv/bin/python -m pytest tests/ -q
./venv/bin/ruff check tests/
```
Expected: all green; ruff clean.

- [ ] **Step 2: Manual smoke against the running server** (owner session): `curl -s localhost:9000/api/regime/NVDA`, `/api/fundamentals/NVDA`, `/api/me` → sane JSON.

- [ ] **Step 3: `git fetch` + sync check, push, open PR**

```bash
git fetch origin -q && git status
git push -u origin feat/customer-analyze-any-ticker
gh pr create --title "feat: customers analyze any ticker (v1)" --body "…"
```

---

## Self-Review

- **Spec coverage:** item 1 (analyze any ticker) → Tasks 1–3 + 5; item 2 (chart any symbol) → Task 5 step 4; item 3 (1D/5D) → Task 3; item 4 (hide owner book) → Task 5 steps 1; security (leak-sweep + XSS) → Tasks 4 + 5 step 5; `/api/me` → Task 2. Deferred (watchlist/AI/news) — correctly absent. ✓
- **Placeholders:** the consensus field names (`verdict`/`score`) are flagged for verify-against-real-response in Task 5 step 3 — acceptable (the real shape is confirmed at implementation via curl; the plan says exactly how). No TBD/TODO. ✓
- **Type consistency:** `intraday_5m(symbol, span)` used identically in Task 3 test, impl, and chart wiring; `/api/me` returns `{role}` used in Task 2 + Task 5 role gate. ✓
