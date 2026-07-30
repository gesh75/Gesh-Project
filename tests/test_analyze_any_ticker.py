"""Customer 'analyze any ticker' v1 — per-symbol GET endpoints, /api/me role, and
the Alpaca-backed intraday chart path. These endpoints return only PUBLIC market
data (signals on public prices) so a customer can analyze any stock they choose,
stateless — no owner data. See docs/superpowers/specs/2026-07-06-customer-analyze-any-ticker-design.md.
"""

from __future__ import annotations

import pandas as pd
from starlette.testclient import TestClient

import price_source
import server


def _client(monkeypatch) -> TestClient:
    # focus on the endpoints, not the auth wall (its own tests cover the wall)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: False)
    return TestClient(server.app, raise_server_exceptions=False)


# ── Task 1: per-ticker regime + fundamentals ─────────────────────────────────

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


def test_regime_symbol_no_data_is_404(monkeypatch):
    monkeypatch.setattr("regime.detect_regimes",
                        lambda syms: {"regimes": [], "regime_summary": "none"}, raising=False)
    r = _client(monkeypatch).get("/api/regime/NVDA")
    assert r.status_code == 404


def test_fundamentals_symbol_returns_single_row(monkeypatch):
    monkeypatch.setattr("market_data.rh_fundamentals", lambda s: None, raising=False)  # force yfinance fallback
    monkeypatch.setattr("fundamental_agent._fetch_fundamentals_batch",
                        lambda syms: [{"symbol": syms[0], "momentum_gap": "LOW", "score": 62}],
                        raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/aapl")
    assert r.status_code == 200
    assert r.json()["data"]["symbol"] == "AAPL"
    assert r.json()["data"]["score"] == 62


def test_fundamentals_symbol_error_row_is_404(monkeypatch):
    monkeypatch.setattr("market_data.rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr("fundamental_agent._fetch_fundamentals_batch",
                        lambda syms: [{"symbol": syms[0], "error": "no data"}], raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/AAPL")
    assert r.status_code == 404


def test_fundamentals_prefers_robinhood(monkeypatch):
    """Robinhood is primary — when it returns data, yfinance is not consulted."""
    monkeypatch.setattr("market_data.rh_fundamentals",
                        lambda s: {"pe_ratio": 30.5, "market_cap": 1.2e12, "sector": "Tech"},
                        raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/NVDA")
    assert r.status_code == 200
    assert r.json()["data"]["pe_ratio"] == 30.5
    assert r.json()["data"]["sector"] == "Tech"


# ── GESH-6: `sector` is a VENDOR classification — attribute it to the real vendor ──
# The two vendors disagree (RH is non-GICS: it calls TSLA "Consumer Durables"), so the
# UI must say whose taxonomy it is. The endpoint knows which branch fired, so the source
# is reported, never guessed.

def test_sector_attributed_to_robinhood_when_rh_serves(monkeypatch):
    monkeypatch.setattr("market_data.rh_fundamentals",
                        lambda s: {"pe_ratio": 30.5, "sector": "Consumer Durables"},
                        raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/TSLA")
    assert r.json()["data"]["sector_source"] == "robinhood"


def test_sector_attributed_to_yfinance_on_fallback(monkeypatch):
    monkeypatch.setattr("market_data.rh_fundamentals", lambda s: None, raising=False)
    monkeypatch.setattr("fundamental_agent._fetch_fundamentals_batch",
                        lambda syms: [{"symbol": syms[0], "sector": "Consumer Cyclical"}],
                        raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/TSLA")
    assert r.json()["data"]["sector_source"] == "yfinance"


def test_no_sector_source_claimed_when_no_sector(monkeypatch):
    """Never attribute a taxonomy we didn't actually receive."""
    monkeypatch.setattr("market_data.rh_fundamentals",
                        lambda s: {"pe_ratio": 30.5}, raising=False)
    r = _client(monkeypatch).get("/api/fundamentals/NVDA")
    assert "sector_source" not in r.json()["data"]


def test_rh_fundamentals_maps_robinhood_fields(monkeypatch):
    import market_data
    fake = [{"pe_ratio": "30.50", "pb_ratio": "12.0", "market_cap": "1200000000000",
             "high_52_weeks": "200.00", "low_52_weeks": "90.00", "dividend_yield": "0.03",
             "sector": "Technology", "industry": "Semiconductors"}]
    monkeypatch.setattr("robin_stocks.robinhood.stocks.get_fundamentals",
                        lambda s: fake, raising=False)
    out = market_data.rh_fundamentals("NVDA")
    assert out["pe_ratio"] == 30.5 and out["high_52"] == 200.0 and out["sector"] == "Technology"


def test_rh_fundamentals_none_on_failure(monkeypatch):
    import market_data

    def _boom(s):
        raise RuntimeError("rh down")
    monkeypatch.setattr("robin_stocks.robinhood.stocks.get_fundamentals", _boom, raising=False)
    assert market_data.rh_fundamentals("NVDA") is None


# ── Task 2: /api/me role ─────────────────────────────────────────────────────

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


# ── Task 3: Alpaca intraday chart path ───────────────────────────────────────

def test_chart_intraday_uses_intraday_5m(monkeypatch):
    df = pd.DataFrame({"Close": [10.0, 10.5, 11.0, 10.8, 11.2]},
                      index=pd.date_range("2026-07-06 09:30", periods=5, freq="5min"))
    monkeypatch.setattr(price_source, "intraday_5m", lambda sym, span: df, raising=False)
    r = _client(monkeypatch).get("/api/chart/NVDA?period=1d")
    assert r.status_code == 200
    assert len(r.json()["prices"]) == 5


def test_chart_intraday_absent_is_honest_not_500(monkeypatch):
    monkeypatch.setattr(price_source, "intraday_5m", lambda sym, span: None, raising=False)
    r = _client(monkeypatch).get("/api/chart/NVDA?period=5d")
    assert r.status_code == 200
    assert r.json().get("intraday_unavailable") is True


def test_intraday_5m_no_keys_returns_none(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    assert price_source.intraday_5m("NVDA", "1d") is None


# ── analyst targets + news headlines per-ticker (v1.1 enrichment) ─────────────

def test_analyst_prefers_robinhood(monkeypatch):
    """Robinhood is primary — buy/hold/sell consensus, no yfinance needed."""
    monkeypatch.setattr("market_data.rh_analyst",
                        lambda s: {"recommendation": "Buy", "num_buy": 62, "num_hold": 3,
                                   "num_sell": 1, "num_analysts": 66}, raising=False)
    r = _client(monkeypatch).get("/api/analyst/nvda")
    assert r.status_code == 200
    assert r.json()["data"]["recommendation"] == "Buy"
    assert r.json()["data"]["num_analysts"] == 66


def test_analyst_symbol_returns_targets(monkeypatch):
    monkeypatch.setattr("market_data.rh_analyst", lambda s: None, raising=False)  # force yfinance fallback
    monkeypatch.setattr(
        "market_data.get_analyst_targets",
        lambda syms: {syms[0]: {"target_mean": 200.0, "num_analysts": 40,
                                "recommendation": "buy", "upside_pct": 12.5, "current": 178.0}},
        raising=False)
    r = _client(monkeypatch).get("/api/analyst/nvda")
    assert r.status_code == 200
    assert r.json()["data"]["target_mean"] == 200.0
    assert r.json()["data"]["num_analysts"] == 40


def test_analyst_symbol_no_data_404(monkeypatch):
    monkeypatch.setattr("market_data.rh_analyst", lambda s: None, raising=False)
    monkeypatch.setattr("market_data.get_analyst_targets", lambda syms: {}, raising=False)
    r = _client(monkeypatch).get("/api/analyst/NVDA")
    assert r.status_code == 404


def test_analyst_symbol_error_row_404_not_leaked(monkeypatch):
    # yfinance failure returns {sym: {"error": "..."}} — must 404, never 200-with-error
    monkeypatch.setattr("market_data.rh_analyst", lambda s: None, raising=False)
    monkeypatch.setattr("market_data.get_analyst_targets",
                        lambda syms: {syms[0]: {"error": "curl (35) Connection reset by peer"}},
                        raising=False)
    r = _client(monkeypatch).get("/api/analyst/NVDA")
    assert r.status_code == 404
    assert "Connection reset" not in r.text   # internal error never reaches the client


def test_rh_analyst_maps_ratings(monkeypatch):
    import market_data
    monkeypatch.setattr("robin_stocks.robinhood.stocks.get_ratings",
                        lambda s: {"summary": {"num_buy_ratings": 62, "num_hold_ratings": 3,
                                               "num_sell_ratings": 1}}, raising=False)
    out = market_data.rh_analyst("NVDA")
    assert out == {"recommendation": "Buy", "num_buy": 62, "num_hold": 3,
                   "num_sell": 1, "num_analysts": 66}


def test_rh_analyst_none_when_no_ratings(monkeypatch):
    import market_data
    monkeypatch.setattr("robin_stocks.robinhood.stocks.get_ratings",
                        lambda s: {"summary": {}}, raising=False)
    assert market_data.rh_analyst("NVDA") is None


def test_news_prefers_alpaca(monkeypatch):
    """Alpaca is primary (VPN-safe); RSS is not consulted when Alpaca returns items."""
    monkeypatch.setattr("news_agent._fetch_alpaca_news",
                        lambda sym, limit=8: [{"title": "AI chip news", "url": "http://a",
                                               "source": "benzinga", "published": "2026-07-07"}],
                        raising=False)
    r = _client(monkeypatch).get("/api/news/NVDA")
    assert r.status_code == 200
    assert r.json()["data"][0]["source"] == "benzinga"


def test_news_symbol_returns_trimmed_headlines(monkeypatch):
    monkeypatch.setattr("news_agent._fetch_alpaca_news", lambda sym, limit=8: [], raising=False)  # RSS fallback
    monkeypatch.setattr(
        "news_agent._fetch_all_news",
        lambda sym, days_back=7: [{"title": "NVDA soars", "url": "http://x", "source": "Yahoo",
                                   "published": "2026-07-06", "description": "long body"}],
        raising=False)
    r = _client(monkeypatch).get("/api/news/NVDA")
    assert r.status_code == 200
    items = r.json()["data"]
    assert items[0]["title"] == "NVDA soars"
    assert items[0]["source"] == "Yahoo"
    assert "description" not in items[0]   # headlines only — no heavy body, no LLM


def test_news_symbol_empty_is_ok_not_404(monkeypatch):
    monkeypatch.setattr("news_agent._fetch_alpaca_news", lambda sym, limit=8: [], raising=False)
    monkeypatch.setattr("news_agent._fetch_all_news", lambda sym, days_back=7: [], raising=False)
    # Third fallback (Recent News fix): stored articles from the news DB — must be
    # stubbed too or the test reads REAL cached articles from the developer's DB.
    monkeypatch.setattr("news_agent.get_cached_articles", lambda sym: [], raising=False)
    r = _client(monkeypatch).get("/api/news/NVDA")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_fetch_alpaca_news_maps_shape(monkeypatch):
    import news_agent
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")

    # Real Alpaca shape: articles under res.data['news'], each a dict (no .news attr).
    class _Res:
        data = {"news": [{"headline": "NVDA up", "url": "http://x", "source": "benzinga",
                          "created_at": "2026-07-07T10:00:00+00:00"}]}

    class _Client:
        def __init__(self, *a):
            pass

        def get_news(self, req):
            return _Res()
    monkeypatch.setattr("alpaca.data.historical.news.NewsClient", _Client, raising=False)
    out = news_agent._fetch_alpaca_news("NVDA")
    assert out[0]["title"] == "NVDA up" and out[0]["source"] == "benzinga"
    assert out[0]["published"] == "2026-07-07T10:00"   # trimmed to minute


def test_fetch_alpaca_news_no_keys_empty(monkeypatch):
    import news_agent
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    assert news_agent._fetch_alpaca_news("NVDA") == []


# ── inspection fixes 2026-07-08: intraday span trim, GET caching, rate bucket ──

def test_last_sessions_trims_1d_to_single_session():
    """The Alpaca fetch starts days+2 back (weekend buffer) — a '1d' chart must
    still render ONLY the most recent session, not 3 days of bars."""
    idx = (list(pd.date_range("2026-07-06 09:30", periods=3, freq="5min", tz="UTC"))
           + list(pd.date_range("2026-07-07 09:30", periods=3, freq="5min", tz="UTC"))
           + list(pd.date_range("2026-07-08 09:30", periods=3, freq="5min", tz="UTC")))
    df = pd.DataFrame({"Close": range(9)}, index=pd.DatetimeIndex(idx))
    out = price_source._last_sessions(df, 1)
    assert len(out) == 3
    assert out.index.normalize().unique().tolist() == [pd.Timestamp("2026-07-08", tz="UTC")]


def test_last_sessions_keeps_up_to_n_sessions():
    idx = (list(pd.date_range("2026-07-07 09:30", periods=2, freq="5min", tz="UTC"))
           + list(pd.date_range("2026-07-08 09:30", periods=2, freq="5min", tz="UTC")))
    df = pd.DataFrame({"Close": range(4)}, index=pd.DatetimeIndex(idx))
    assert len(price_source._last_sessions(df, 5)) == 4   # fewer sessions than asked → all kept
    assert len(price_source._last_sessions(df, 1)) == 2


def test_regime_symbol_second_hit_served_from_cache(monkeypatch):
    """Watchlist chips make repeat clicks common — the per-ticker GETs must not
    re-fetch upstream (yfinance+FRED) within the TTL."""
    calls = {"n": 0}

    def _fake(syms):
        calls["n"] += 1
        return {"regimes": [{"symbol": syms[0], "regime": "TRENDING", "adx": 30.0,
                             "rsi": 55.0, "pct_b": 0.5, "bb_width": 0.1,
                             "icon": "📈", "description": "up"}], "regime_summary": "x"}
    monkeypatch.setattr("regime.detect_regimes", _fake, raising=False)
    c = _client(monkeypatch)
    assert c.get("/api/regime/NVDA").status_code == 200
    assert c.get("/api/regime/NVDA").status_code == 200
    assert calls["n"] == 1


def test_regime_symbol_404_is_not_cached(monkeypatch):
    """A transient upstream outage must not stick for the whole TTL."""
    calls = {"n": 0}

    def _fake(syms):
        calls["n"] += 1
        return {"regimes": [], "regime_summary": "none"}
    monkeypatch.setattr("regime.detect_regimes", _fake, raising=False)
    c = _client(monkeypatch)
    assert c.get("/api/regime/NVDA").status_code == 404
    assert c.get("/api/regime/NVDA").status_code == 404
    assert calls["n"] == 2                                # miss → re-query, no negative cache


def test_per_ticker_gets_are_heavy_rate_limited():
    """The per-symbol variants sit in the 30/min heavy bucket (each miss is a live
    upstream fetch) — but the exact '/api/regime' (dashboard-polled, chain-cached)
    must stay in the normal bucket."""
    for path in ("/api/regime/NVDA", "/api/fundamentals/NVDA",
                 "/api/analyst/NVDA", "/api/news/NVDA"):
        assert path.startswith(server._HEAVY_PREFIXES), path
    assert not "/api/regime".startswith(server._HEAVY_PREFIXES)


# ── _latest_close bad-tick guard (audit fix set 2: price consistency) ──────────

def _close_df(values):
    return pd.DataFrame({"Close": values})


def test_latest_close_normal_series(monkeypatch):
    monkeypatch.setattr(price_source, "yf_download",
                        lambda *a, **k: _close_df([200, 201, 203, 202, 205]), raising=False)
    assert server._latest_close("NVDA") == 205.0


def test_latest_close_bad_tick_falls_back(monkeypatch):
    # the observed $17.16 flash against a ~$205 book → outlier, return prior good close
    monkeypatch.setattr(price_source, "yf_download",
                        lambda *a, **k: _close_df([203, 204, 205, 206, 17.16]), raising=False)
    assert server._latest_close("NVDA") == 206.0


def test_latest_close_real_move_is_kept(monkeypatch):
    # a genuine 40% run stays inside the 0.5x-2x band → NOT clipped
    monkeypatch.setattr(price_source, "yf_download",
                        lambda *a, **k: _close_df([100, 110, 120, 130, 140]), raising=False)
    assert server._latest_close("X") == 140.0


def test_latest_close_short_series_unguarded(monkeypatch):
    # < 5 points → no median to judge against, return the last close as-is
    monkeypatch.setattr(price_source, "yf_download",
                        lambda *a, **k: _close_df([100, 5]), raising=False)
    assert server._latest_close("X") == 5.0


# ── GESH-8: per-card provenance — a REAL feed or nothing, never a constant ──────
# _ticker_get_store used to default source="live". Because a cache hit returns the
# stored dict verbatim, that constant claimed "live" on a 5-minute-old cached serve
# and could never say otherwise. It is gone; endpoints that OBSERVE the serving feed
# set `source` explicitly, and the rest omit it.

def test_store_no_longer_stamps_a_constant_live_source():
    resp = server._ticker_get_store("t", "X", {"status": "ok", "data": {}})
    assert "source" not in resp                  # never a constant claim
    assert resp["as_of"]                          # fetch time IS truthful → age


def test_store_never_clobbers_an_observed_source():
    resp = server._ticker_get_store("t", "Y", {"status": "ok", "data": {}, "source": "robinhood"})
    assert resp["source"] == "robinhood"


def test_fundamentals_envelope_names_the_serving_feed(monkeypatch):
    monkeypatch.setattr("market_data.rh_fundamentals",
                        lambda s: {"pe_ratio": 1.0, "sector": "Tech"}, raising=False)
    assert _client(monkeypatch).get("/api/fundamentals/NVDA").json()["source"] == "robinhood"


def test_analyst_envelope_names_the_serving_feed(monkeypatch):
    monkeypatch.setattr("market_data.rh_analyst",
                        lambda s: {"recommendation": "Hold", "num_analysts": 20}, raising=False)
    assert _client(monkeypatch).get("/api/analyst/NVDA").json()["source"] == "robinhood"


def test_news_envelope_names_alpaca_when_alpaca_served(monkeypatch):
    import news_agent
    monkeypatch.setattr(news_agent, "_fetch_alpaca_news",
                        lambda s: [{"title": "t", "url": "u", "source": "benzinga",
                                    "published": "p"}], raising=False)
    assert _client(monkeypatch).get("/api/news/NVDA").json()["source"] == "alpaca"


def test_news_envelope_names_rss_on_fallback(monkeypatch):
    import news_agent
    monkeypatch.setattr(news_agent, "_fetch_alpaca_news", lambda s: [], raising=False)
    monkeypatch.setattr(news_agent, "_fetch_all_news",
                        lambda s: [{"title": "t", "url": "u", "source": "yahoo",
                                    "published": "p"}], raising=False)
    assert _client(monkeypatch).get("/api/news/NVDA").json()["source"] == "rss"


def test_news_envelope_claims_no_feed_when_nothing_served(monkeypatch):
    import news_agent
    monkeypatch.setattr(news_agent, "_fetch_alpaca_news", lambda s: [], raising=False)
    monkeypatch.setattr(news_agent, "_fetch_all_news", lambda s: [], raising=False)
    monkeypatch.setattr(news_agent, "get_cached_articles", lambda s: [], raising=False)
    # NVDA is a REAL symbol with a price, so empty news is a legitimate
    # "no recent news" result (status ok, data []), NOT symbol_not_found. Give it
    # market identity so the offline test reflects production (GATE 2 gates the
    # unknown-symbol path on the absence of a price).
    monkeypatch.setattr(server, "_cached_close", lambda s: 500.0, raising=True)
    body = _client(monkeypatch).get("/api/news/NVDA").json()
    assert body["status"] == "ok"
    assert body["data"] == [] and "source" not in body
