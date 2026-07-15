"""Deterministic, CI-gating customer-isolation sweep (Codex 4th-pass P1).

The prior version ran only against a live :9000 server, so in GitHub Actions every
case SKIPPED — CI gated the offline suite but never customer isolation. It also
accepted 401/403/404/timeout as "pass" and rounded its sentinels. This rewrite
fixes all of that:

  * IN-PROCESS via Starlette TestClient with a seeded fixture — no port, no
    network, no skips. Runs in CI as a required job.
  * ROUTE INVENTORY derived from server.app.routes: a customer-reachable
    GET /api/* that nobody categorized FAILS test_all_customer_gets_categorized,
    so new endpoints can't silently bypass the sweep.
  * PER ENDPOINT asserts: status == 200 (a 401/403/404/5xx FAILS — never a pass),
    the masked `view == "model_book"` tell is present, and no EXACT owner sentinel
    (unrounded total, cash, fractional quantity, per-lot equity, historical-series
    value) appears in the body.
  * EXACT structured sentinels — the synthetic raw leaks Codex injected
    (6.078929, 101.988616, 5843.32) are detected, not rounded away.

The masking middleware (server.py) masks EVERY customer /api JSON dict and stamps
view=model_book, failing CLOSED (500) if scaling throws — so `view` present +
200 together prove the response actually routed through masking.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest
from starlette.testclient import TestClient

import server

# ── Owner sentinels: EXACT values, no rounding. None may appear in a customer body.
OWNER = {
    "total": 37522.83,       # portfolio equity
    "cash": 509.70,          # settled cash / deployable
    "qty": 6.078929,         # a fractional share count (Codex: rounding hid this)
    "equity": 101.988616,    # a per-lot dollar equity (Codex synthetic leak)
    "series": [5843.32, 5901.11, 6012.44, 5980.10],  # historical equity curve
    # 8th-pass (Fable audit): owner-$ keys the mask OMITTED → leaked raw. The mock
    # holding previously used the covered name `pnl_usd` while production emits
    # `equity_change`, so the sweep was structurally blind. Seed the REAL keys.
    "pos_change": 3211.44,   # per-position unrealized $ P&L (data_fetcher `equity_change`)
    "xhours": 41988.17,      # extended-hours account equity (data_fetcher `extended_hours_equity`)
    "trade_pnl": 1287.44,    # realized trade $ P&L (trade_log `realized_pnl`/`total_realized_pnl`)
}
SCALE = 2.5  # deterministic model/real factor (avoids identity so scaled != sentinel)


def _sentinel_strings() -> list[str]:
    vals: set[str] = set()
    for v in (OWNER["total"], OWNER["cash"], OWNER["qty"], OWNER["equity"],
              OWNER["pos_change"], OWNER["xhours"], OWNER["trade_pnl"], *OWNER["series"]):
        vals.add(f"{v}")          # raw repr  -> "6.078929", "5843.32"
        vals.add(f"{v:.2f}")      # 2-dp      -> "509.70"
        vals.add(f"{v:,.2f}")     # grouped   -> "5,843.32"
    return sorted(vals)


def _owner_payload() -> dict:
    """Fresh copy each call (handlers may read/iterate it)."""
    return json.loads(json.dumps({
        "as_of": "2026-07-04T13:35:00+00:00", "source": "test",
        "equity_summary": {"total": OWNER["total"], "portfolio_equity": OWNER["total"],
                           "cash": OWNER["cash"], "deployable": OWNER["cash"],
                           # real data_fetcher key — account-level owner equity
                           "extended_hours_equity": OWNER["xhours"]},
        "holdings": [{"symbol": "NVDA", "quantity": OWNER["qty"], "price": 194.51,
                      "equity": OWNER["equity"], "average_buy_price": 180.0,
                      # real data_fetcher key — per-position owner $ P&L (was fake `pnl_usd`)
                      "percent_change": -3.51, "equity_change": OWNER["pos_change"]}],
        "watchlist": [{"symbol": "SPY", "price": 612.0}],
    }))


def _cached(key: str) -> dict:
    if key == "monte_carlo":
        data = {"median": OWNER["total"], "current_equity": OWNER["total"],
                "buckets": [{"label": f"${OWNER['series'][0]:,.2f}–${OWNER['series'][1]:,.2f}",
                             "center": OWNER["equity"], "pct": 4.0}]}
    else:
        data = {"total_equity": OWNER["total"],
                "holdings": [{"symbol": "NVDA", "equity": OWNER["equity"],
                              "quantity": OWNER["qty"], "price": 194.51}]}
    return {"status": "ok", "cached": True, "data": data}


_CHART_DF = pd.DataFrame(
    {"Close": [100.0, 101.0, 102.0, 101.5, 103.0]},
    index=pd.date_range("2026-06-01", periods=5, freq="D"),
)


@pytest.fixture
def client(monkeypatch):
    """In-process app with all data seams seeded (owner sentinels where a real
    endpoint would carry owner dollars; static market fixtures elsewhere)."""
    monkeypatch.setenv("GESH_AUTH_ENABLED", "1")
    monkeypatch.setattr(server, "_customer_scale_factor", lambda: SCALE)

    # broad server-module seams
    monkeypatch.setattr(server, "build_data_payload", lambda *a, **k: _owner_payload())
    monkeypatch.setattr(server, "_resolved_payload", lambda *a, **k: _owner_payload(), raising=False)
    monkeypatch.setattr(server, "_cached_response", _cached)
    monkeypatch.setattr(server, "_load_engine_results",
                        lambda *a, **k: {"regime": {"NVDA": {"regime": "TRENDING"}},
                                         "regime_ran_at": "2026-07-04T13:00:00+00:00"}, raising=False)
    monkeypatch.setattr(server, "get_all_market_data", lambda syms: {s: {"price": 100.0} for s in syms}, raising=False)
    monkeypatch.setattr(server, "batch_fft_cycles", lambda syms: {s: {"signal": "NEUTRAL"} for s in syms}, raising=False)
    # Owner trade rows carry raw realized-$ P&L (latent today: owner is paper-only,
    # but /api/trades + /api/trades/stats are customer-reachable and unmasked keys
    # would leak the instant a real trade is logged — 8th-pass, Fable audit).
    monkeypatch.setattr(server, "get_trade_history",
                        lambda *a, **k: [{"symbol": "NVDA", "realized_pnl": OWNER["trade_pnl"],
                                          "pnl_pct": -4.2, "outcome": "LOSS"}], raising=False)
    monkeypatch.setattr(server, "trade_summary_for_report",
                        lambda *a, **k: {"win_rate_pct": 50.0, "total_realized_pnl": OWNER["trade_pnl"],
                                         "best_trade": {"symbol": "NVDA", "realized_pnl": OWNER["trade_pnl"],
                                                        "pnl_pct": 12.0},
                                         "worst_trade": None}, raising=False)
    monkeypatch.setattr(server, "get_paper_summary", lambda *a, **k: {"trades": 0}, raising=False)
    monkeypatch.setattr(server, "detect_consolidation", lambda *a, **k: {"found": False}, raising=False)
    monkeypatch.setattr(server, "is_sector_hot", lambda *a, **k: {"hot": False}, raising=False)
    monkeypatch.setattr(server, "get_analysis_history",
                        lambda *a, **k: [{"id": 1, "created_at": "2026-07-04", "model": "sonnet"}], raising=False)
    # Mirror db.get_analysis_by_id: it PARSES result_json into `result` (a dict)
    # and pops the raw string, so masking scales it. A stored owner snapshot here
    # proves /api/history/{id} masks the embedded analysis.
    monkeypatch.setattr(server, "get_analysis_by_id",
                        lambda rid: {"id": rid, "created_at": "2026-07-04",
                                     "result": _owner_payload(), "payload": {}}, raising=False)
    monkeypatch.setattr(server, "get_filing_history", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(server, "get_gemini_status", lambda *a, **k: {"available": False}, raising=False)

    # source-module fns imported *inside* handlers (patch at the source)
    monkeypatch.setattr("db.get_portfolio_equity_history",
                        lambda *a, **k: [{"total_equity": v} for v in OWNER["series"]], raising=False)
    monkeypatch.setattr("consensus.run_consensus_holding",
                        lambda *a, **k: {"symbol": "NVDA", "verdict": "BUY", "regime": "NEUTRAL", "score": 0.3}, raising=False)
    monkeypatch.setattr("fft_cycles.detect_fft_cycles",
                        lambda *a, **k: {"symbol": "NVDA", "signal": "NEUTRAL", "period_days": 21}, raising=False)
    # per-ticker "analyze any share" surfaces (public data, no owner dollars)
    monkeypatch.setattr("regime.detect_regimes",
                        lambda syms: {"regimes": [{"symbol": syms[0], "regime": "NEUTRAL",
                                      "adx": 20.0, "rsi": 50.0, "pct_b": 0.5, "bb_width": 0.1,
                                      "icon": "•", "description": "flat"}], "regime_summary": "x"},
                        raising=False)
    monkeypatch.setattr("fundamental_agent._fetch_fundamentals_batch",
                        lambda syms: [{"symbol": syms[0], "momentum_gap": "LOW", "score": 50}],
                        raising=False)
    monkeypatch.setattr("market_data.get_analyst_targets",
                        lambda syms: {syms[0]: {"target_mean": 200.0, "num_analysts": 40,
                                     "recommendation": "buy", "upside_pct": 12.0, "current": 178.0}},
                        raising=False)
    monkeypatch.setattr("news_agent._fetch_all_news",
                        lambda sym, days_back=7: [{"title": "headline", "url": "http://x",
                                                   "source": "Yahoo", "published": "2026-07-06"}],
                        raising=False)
    monkeypatch.setattr("auth_users.get_watch", lambda email: ["NVDA", "TSLA"], raising=False)
    monkeypatch.setattr("benchmarks.batch_alpha", lambda *a, **k: [], raising=False)
    monkeypatch.setattr("crypto_correlation.crypto_risk_summary", lambda *a, **k: {"crypto_pct": 0.0}, raising=False)
    monkeypatch.setattr("defi_yield.get_yield_landscape", lambda *a, **k: {"options": []}, raising=False)
    monkeypatch.setattr("tax_harvester.get_harvest_summary", lambda *a, **k: {"candidates": []}, raising=False)
    monkeypatch.setattr("volatility_models.compare_vol_models", lambda *a, **k: {"ewma": 0.2, "garch": 0.2}, raising=False)
    monkeypatch.setattr("explainability.build_transparency_report", lambda *a, **k: {"explanation": "test"}, raising=False)
    monkeypatch.setattr("price_source.yf_download", lambda *a, **k: _CHART_DF, raising=False)
    monkeypatch.setattr("yfinance.download", lambda *a, **k: _CHART_DF, raising=False)
    monkeypatch.setattr("security.api_verifier.all_api_health", lambda *a, **k: {}, raising=False)
    # some endpoints kick a background Robinhood-refresh thread; no-op it so the
    # run stays fully offline and deterministic (no stray thread network attempt).
    monkeypatch.setattr("fetch_live_data.fetch_and_cache", lambda *a, **k: {}, raising=False)

    return TestClient(server.app, raise_server_exceptions=False)


def _as_customer(monkeypatch):
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "customer@example.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda path, cookie: "allow")


# ── concrete request URL for every customer-reachable GET (path params filled) ──
COVERED: dict[str, str] = {
    "/api/portfolio": "/api/portfolio",
    "/api/ecg": "/api/ecg",
    "/api/regime": "/api/regime",
    "/api/history": "/api/history?limit=3",
    "/api/history/{run_id}": "/api/history/1",
    "/api/market-data": "/api/market-data",
    "/api/agents/sec/history": "/api/agents/sec/history",
    "/api/agents/news/history": "/api/agents/news/history",
    "/api/agents/fundamentals/latest": "/api/agents/fundamentals/latest",
    "/api/chart/{symbol}": "/api/chart/NVDA?period=1mo",
    "/api/deep-analysis/cached": "/api/deep-analysis/cached",
    "/api/consensus/cached": "/api/consensus/cached",
    "/api/consensus/{symbol}": "/api/consensus/NVDA",
    "/api/kelly/cached": "/api/kelly/cached",
    "/api/monte-carlo/cached": "/api/monte-carlo/cached",
    "/api/screener/cached": "/api/screener/cached",
    "/api/portfolio/history": "/api/portfolio/history?points=30",
    "/api/sections/status": "/api/sections/status",
    "/api/fft-cycles": "/api/fft-cycles",
    "/api/fft-cycles/{symbol}": "/api/fft-cycles/NVDA",
    "/api/regime/{symbol}": "/api/regime/NVDA",
    "/api/momentum/{symbol}": "/api/momentum/NVDA",
    "/api/levels/{symbol}": "/api/levels/NVDA",
    "/api/earnings/{symbol}": "/api/earnings/NVDA",
    "/api/fundamentals/{symbol}": "/api/fundamentals/NVDA",
    "/api/analyst/{symbol}": "/api/analyst/NVDA",
    "/api/watchlist": "/api/watchlist",
    "/api/gemini/status": "/api/gemini/status",
    "/api/config": "/api/config",
    "/api/scanner/consolidation/{symbol}": "/api/scanner/consolidation/NVDA",
    "/api/scanner/sector/{symbol}": "/api/scanner/sector/NVDA",
    "/api/trades": "/api/trades",
    "/api/trades/stats": "/api/trades/stats",
    "/api/paper/summary": "/api/paper/summary",
    "/api/tax/harvest": "/api/tax/harvest",
    "/api/benchmarks/{period}": "/api/benchmarks/1mo",
    "/api/explainability/{symbol}": "/api/explainability/NVDA",
    "/api/crypto-correlation": "/api/crypto-correlation",
    "/api/yield/landscape": "/api/yield/landscape",
    "/api/security/health": "/api/security/health",
    "/api/volatility/compare/{symbol}": "/api/volatility/compare/NVDA",
}
# GET /api routes deliberately not swept, each with a reason.
EXCLUDED: dict[str, str] = {
    "/api/ollama/status": "410 Gone — endpoint permanently removed; carries no data",
    "/api/me": "returns only a session role string (owner|customer) — no owner financial data",
    "/api/symbol-search": "public reference data (ticker + company name) from the Alpaca "
                          "asset universe — no owner positions/dollars, no LLM",
    "/api/usage/summary": "OWNER-ONLY metering view (403s customer sessions — locked by "
                          "tests/test_mybook.py); the sweep's 200-with-masking contract "
                          "cannot apply to a route customers cannot read",
    "/api/mybook": "the CUSTOMER's own entered positions valued from public quotes — zero "
                   "owner data, mask-EXEMPT by design (masking would redact the customer's "
                   "own dollars back at them); per-user isolation + raw-dollar contract "
                   "locked by tests/test_mybook.py",
    "/api/trading/gate-status": "OWNER-ONLY paper-trading order cap (require_api_key); "
                                "reads owner trade_log counts — not a customer surface",
    "/api/long-term-macro": "public macro sentiment overlay (VIX / Fear-&-Greed / aggregate "
                            "US margin debt from FINRA) — no owner or customer dollars; "
                            "internally cached, identical for every viewer",
    "/api/long-term-macro/cached": "cached variant of /api/long-term-macro — same public "
                                   "macro sentiment, no owner or customer dollars",
    "/api/news/{symbol}": "PUBLIC market news headlines (title/url/source/published) from "
                          "RSS/Alpaca — mask-EXEMPT by design (GESH-7): the mask redacts by "
                          "semantic path and 'title' is a KEEP-class value, so public analyst "
                          "price targets were being redacted to '$•••' while protecting "
                          "nothing. The owner-only sentiment SCORE is never in this body. "
                          "The no-owner-data contract is locked by "
                          "tests/test_news_public_contract.py (payload keys are allow-listed, "
                          "so this route is NOT unguarded — it is guarded by shape instead of "
                          "by masking)",
}


def _registered_customer_gets() -> set[str]:
    out: set[str] = set()
    for r in server.app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        if "GET" in methods and path.startswith("/api/"):
            out.add(path)
    return out


def test_all_customer_gets_categorized():
    """Route coverage is derived from app.routes: a new customer GET that nobody
    added to COVERED/EXCLUDED fails here, so it can't silently skip the sweep."""
    registered = _registered_customer_gets()
    uncategorized = registered - set(COVERED) - set(EXCLUDED)
    assert not uncategorized, (
        "Customer GET route(s) not categorized in the leak sweep — add to COVERED "
        f"(with a concrete request) or EXCLUDED (with a reason): {sorted(uncategorized)}"
    )
    # and the sweep list must not rot: no stale entries for removed routes
    stale = (set(COVERED) | set(EXCLUDED)) - registered
    assert not stale, f"Sweep lists reference routes that no longer exist: {sorted(stale)}"


@pytest.mark.parametrize("url", sorted(set(COVERED.values())))
def test_customer_get_masks_and_gates(monkeypatch, client, url):
    _as_customer(monkeypatch)
    r = client.get(url)
    # 200 REQUIRED — a 401/403/404/5xx (incl. the fail-closed 500 masking error) FAILS.
    assert r.status_code == 200, f"{url} returned {r.status_code}, expected 200 (never treat as pass)"
    assert "application/json" in r.headers.get("content-type", ""), f"{url} not JSON"
    body = r.json()
    assert isinstance(body, dict), f"{url} body not a dict"
    # The masking middleware stamps this on every customer dict; its presence proves
    # the response actually routed through _scale_money.
    assert body.get("view") == "model_book", f"{url} missing model_book marker — bypassed masking"
    blob = json.dumps(body)
    leaked = [s for s in _sentinel_strings() if s in blob]
    assert not leaked, f"OWNER VALUE LEAKED at {url}: {leaked}"


def test_wrong_auth_is_rejected_not_passed(monkeypatch, client):
    """An unauthenticated request must 401 — proving the sweep never mistakes a
    401 for success (the prior version's core flaw)."""
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: None)
    r = client.get("/api/portfolio")
    assert r.status_code == 401, f"expected 401 for unauthenticated /api, got {r.status_code}"


def test_scale_money_masks_every_exact_sentinel():
    """Direct masking proof with EXACT structured sentinels: fractional quantity,
    per-lot equity, and every historical-series value scale away — none survive."""
    fixture = {
        "equity_summary": {"total": OWNER["total"], "cash": OWNER["cash"]},
        "holdings": [{"quantity": OWNER["qty"], "equity": OWNER["equity"], "price": 194.51}],
        "equity_history": list(OWNER["series"]),
        "series": list(OWNER["series"]),
    }
    out = server._scale_money(fixture, SCALE)
    blob = json.dumps(out)
    survivors = [s for s in _sentinel_strings() if s in blob]
    assert not survivors, f"sentinel(s) survived masking: {survivors}"
    # model-book values are present and correctly scaled
    assert out["equity_summary"]["total"] == pytest.approx(OWNER["total"] * SCALE)
    # quantity is rounded to 4dp by the masker — compare with matching tolerance
    assert out["holdings"][0]["quantity"] == pytest.approx(OWNER["qty"] * SCALE, abs=1e-3)
    assert out["holdings"][0]["price"] == 194.51  # per-share price untouched
    assert out["equity_history"][0] == pytest.approx(OWNER["series"][0] * SCALE)
    assert out["series"][-1] == pytest.approx(OWNER["series"][-1] * SCALE)


def test_scale_money_masks_position_account_and_trade_dollars():
    """8th-pass (Fable audit): owner-$ keys the mask previously OMITTED — per-position
    `equity_change`, account `extended_hours_equity`, and trade `realized_pnl` /
    `total_realized_pnl` (incl. nested best/worst_trade rows) — must scale to the model
    book, not leak raw. Percentages and per-share prices must stay untouched."""
    fixture = {
        "equity_summary": {"portfolio_equity": OWNER["total"],
                           "extended_hours_equity": OWNER["xhours"]},
        "holdings": [{"symbol": "NVDA", "equity": OWNER["equity"],
                      "equity_change": OWNER["pos_change"], "percent_change": -3.5,
                      "average_buy_price": 180.0}],
        "data": {"total_realized_pnl": OWNER["trade_pnl"],
                 "best_trade": {"symbol": "NVDA", "realized_pnl": OWNER["trade_pnl"],
                                "pnl_pct": 4.2}},
    }
    out = server._scale_money(fixture, SCALE)
    survivors = [s for s in _sentinel_strings() if s in json.dumps(out)]
    assert not survivors, f"owner-$ sentinel survived masking: {survivors}"
    assert out["equity_summary"]["extended_hours_equity"] == pytest.approx(OWNER["xhours"] * SCALE)
    assert out["holdings"][0]["equity_change"] == pytest.approx(OWNER["pos_change"] * SCALE)
    assert out["data"]["total_realized_pnl"] == pytest.approx(OWNER["trade_pnl"] * SCALE)
    assert out["data"]["best_trade"]["realized_pnl"] == pytest.approx(OWNER["trade_pnl"] * SCALE)
    # percentages and per-share prices are NOT owner dollars — must stay raw
    assert out["holdings"][0]["percent_change"] == -3.5
    assert out["holdings"][0]["average_buy_price"] == 180.0
    assert out["data"]["best_trade"]["pnl_pct"] == 4.2


def test_public_insider_shares_kept_raw():
    """Insider transactions are PUBLIC market data, not owner sizing. The key `shares`
    scales owner holdings (fetch_live_data / paper_trading), but under the public
    `insider_trades` subtree it must be kept RAW — scaling by the model-book factor
    corrupts a public share count shown to the customer (8th-pass, Fable audit)."""
    fixture = {
        "insider_trades": {"NVDA": [{"name": "Jensen", "shares": 100000, "value": 5000000}]},
        "holdings": [{"shares": 1000}],
    }
    out = server._scale_money(fixture, SCALE)
    # public insider figures untouched...
    assert out["insider_trades"]["NVDA"][0]["shares"] == 100000
    assert out["insider_trades"]["NVDA"][0]["value"] == 5000000
    # ...but a genuine owner holding `shares` still scales (owner-sizing mask intact)
    assert out["holdings"][0]["shares"] == pytest.approx(1000 * SCALE, abs=1e-3)
