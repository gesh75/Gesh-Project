"""Tests for price_source.py — the Yahoo→Robinhood OHLCV fallback adapter.

No network: yfinance is monkeypatched to simulate the 401/crumb storm, and the
Robinhood layer is stubbed at the `robin_stocks.robinhood` import boundary plus
a forced-ready session. Asserts the fallback returns a frame shaped exactly like
`yfinance.download()` so downstream callers (`raw["Close"].squeeze()`, multi-
symbol `frame["Close"]`) work unchanged.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

import price_source
import server


@pytest.fixture(autouse=True)
def _clear_yf_cache():
    """price_source carries an in-process TTL cache for period-based frames
    (rule #42). Clear it around every test so a frame cached by one test never
    bleeds into another's monkeypatched yfinance.download result."""
    price_source._YF_CACHE.clear()
    yield
    price_source._YF_CACHE.clear()


# ─── Fake robin_stocks.robinhood with deterministic historicals ───────────────


def _fake_rows(symbol: str, n: int = 60) -> list[dict]:
    base = {"NVDA": 120.0, "SPY": 540.0, "MU": 100.0}.get(symbol, 50.0)
    rows = []
    for i in range(n):
        px = base + i * 0.5
        rows.append(
            {
                "begins_at": f"2026-0{1 + i % 3}-{1 + i % 27:02d}T00:00:00Z",
                "open_price": str(px),
                "high_price": str(px + 1),
                "low_price": str(px - 1),
                "close_price": str(px + 0.25),
                "volume": str(1_000_000 + i),
                "symbol": symbol,
            }
        )
    return rows


@pytest.fixture
def fake_rh(monkeypatch):
    mod = types.ModuleType("robin_stocks.robinhood")
    mod.get_stock_historicals = lambda sym, interval="day", span="year", bounds="regular": (
        _fake_rows(sym)
    )
    pkg = types.ModuleType("robin_stocks")
    pkg.robinhood = mod
    monkeypatch.setitem(sys.modules, "robin_stocks", pkg)
    monkeypatch.setitem(sys.modules, "robin_stocks.robinhood", mod)
    # Force the session memo "ready" so we don't touch the real pickle.
    monkeypatch.setattr(price_source, "_RH_SESSION_READY", True)
    return mod


def _yahoo_dead(monkeypatch):
    """Make the inline `import yfinance` raise a 401 on download()."""
    fake_yf = types.ModuleType("yfinance")

    def _boom(*a, **k):
        raise RuntimeError("401 Client Error: Unauthorized — Invalid Crumb")

    fake_yf.download = _boom
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)


# ─── Period/interval translation ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "period,expected",
    [
        ("1d", "day"),
        ("5d", "week"),
        ("1mo", "month"),
        ("60d", "3month"),
        ("90d", "3month"),
        ("3mo", "3month"),
        ("1y", "year"),
        ("252d", "year"),
        ("2y", "5year"),
        ("max", "5year"),
    ],
)
def test_span_from_period(period, expected):
    assert price_source._span_from_period(period, None, None) == expected


def test_span_from_start_end():
    assert price_source._span_from_period(None, "2025-01-01", "2026-01-01") == "year"


def test_interval_map():
    assert price_source._interval_to_rh("1d") == "day"
    assert price_source._interval_to_rh("1h") == "hour"
    assert price_source._interval_to_rh("5m") == "5minute"
    assert price_source._interval_to_rh(None) == "day"


# ─── Single-symbol fallback shape (matches yf.download single) ─────────────────


def test_rh_fallback_single_symbol_shape(fake_rh):
    df = price_source.rh_fallback("NVDA", period="2y", interval="1d")
    assert df is not None
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert len(df) >= 5
    # The downstream idiom must work:
    closes = df["Close"].squeeze()
    assert closes.dtype == float


# ─── Multi-symbol fallback shape (MultiIndex like yf.download multi) ───────────


def test_rh_fallback_multi_symbol_shape(fake_rh):
    df = price_source.rh_fallback(["NVDA", "SPY"], period="1y")
    assert df is not None
    assert isinstance(df.columns, pd.MultiIndex)
    close = df["Close"]  # the monte_carlo / vol_models idiom
    assert isinstance(close, pd.DataFrame)
    assert set(close.columns) == {"NVDA", "SPY"}
    assert (close["NVDA"].dropna() > 0).all()


# ─── No session → graceful None (sandbox behaviour preserved) ─────────────────


def test_rh_fallback_no_session_returns_none(monkeypatch):
    monkeypatch.setattr(price_source, "_RH_SESSION_READY", False)
    assert price_source.rh_fallback("NVDA", period="1y") is None


# ─── yf_download: Yahoo dies → RH serves the frame ────────────────────────────


def test_yf_download_falls_back_on_yahoo_401(monkeypatch, fake_rh):
    _yahoo_dead(monkeypatch)
    df = price_source.yf_download("NVDA", period="1y", interval="1d")
    assert df is not None
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) >= 5


def test_customer_http_request_disables_authenticated_rh_fallback(tmp_path, monkeypatch):
    """Customer GETs must enter the unauthenticated-RH context.

    Found 2026-08-29: only ``/api/explainability/{symbol}`` wrapped
    ``unauthenticated_rh_fallback()``. Every other customer engine path
    (``/api/consensus/{symbol}``, ``/api/mybook/*``, ``/api/chart/*``)
    inherited the process default (True) and restored the owner's
    robin_stocks pickle on a Yahoo 401.
    """
    from starlette.testclient import TestClient

    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    _au.create_user("cust@x.com", "test-password")
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", _au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "cust@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: False)

    seen: list[bool] = []

    def _spy(*_a, **_k):
        seen.append(price_source._ALLOW_AUTHENTICATED_RH.get())
        raise RuntimeError("spy-stop")

    monkeypatch.setattr(price_source, "yf_download", _spy)
    monkeypatch.setattr("consensus.price_source.yf_download", _spy, raising=False)

    client = TestClient(server.app, raise_server_exceptions=False)
    client.cookies.set(_au.COOKIE_NAME, "fake-session")
    client.get("/api/consensus/AAPL")
    assert seen, "customer /api/consensus/{symbol} never reached yf_download"
    assert seen == [False] * len(seen)


def test_owner_http_request_keeps_authenticated_rh_fallback(tmp_path, monkeypatch):
    """The owner/chain path must still be allowed to use the pickle session."""
    from starlette.testclient import TestClient

    import auth_users as _au

    monkeypatch.setattr(_au, "AUTH_DB_PATH", tmp_path / "auth.db")
    _au.init_auth_db()
    _au.create_user("owner@x.com", "test-password")
    monkeypatch.setattr(server.auth_users, "AUTH_DB_PATH", _au.AUTH_DB_PATH)
    monkeypatch.setattr(server.auth_users, "auth_enabled", lambda: True)
    monkeypatch.setattr(server.auth_users, "authenticate_session", lambda tok: "owner@x.com")
    monkeypatch.setattr(server.auth_users, "gate_decision", lambda p, c: "allow")
    monkeypatch.setattr(server.auth_users, "is_owner", lambda e: True)

    seen: list[bool] = []

    def _spy(*_a, **_k):
        seen.append(price_source._ALLOW_AUTHENTICATED_RH.get())
        raise RuntimeError("spy-stop")

    monkeypatch.setattr(price_source, "yf_download", _spy)
    monkeypatch.setattr("consensus.price_source.yf_download", _spy, raising=False)

    client = TestClient(server.app, raise_server_exceptions=False)
    client.cookies.set(_au.COOKIE_NAME, "fake-session")
    client.get("/api/consensus/AAPL")
    assert seen, "owner /api/consensus/{symbol} never reached yf_download"
    assert all(flag is True for flag in seen)


def test_customer_safe_fallback_never_uses_authenticated_session(monkeypatch, fake_rh):
    """A request-scoped fallback cannot spend the owner's broker session quota."""
    _yahoo_dead(monkeypatch)
    called = {"authenticated": False, "public": False}
    fake_rh.get_stock_historicals = lambda *a, **k: called.__setitem__("authenticated", True)
    monkeypatch.setattr(
        price_source,
        "_ensure_rh_session",
        lambda: pytest.fail("customer fallback attempted to restore the owner session"),
    )

    def _public_history(symbol, span):
        called["public"] = True
        rows = _fake_rows(symbol)
        return pd.DataFrame(
            [
                {
                    "Open": float(r["open_price"]),
                    "High": float(r["high_price"]),
                    "Low": float(r["low_price"]),
                    "Close": float(r["close_price"]),
                    "Volume": float(r["volume"]),
                }
                for r in rows
            ],
            index=pd.to_datetime([r["begins_at"] for r in rows]),
        )

    monkeypatch.setattr(price_source, "_rh_history_one_unauth", _public_history)
    with price_source.unauthenticated_rh_fallback():
        df = price_source.yf_download("OBSCURE", period="1y")

    assert df is not None
    assert called == {"authenticated": False, "public": True}


def test_explainability_is_heavy_and_rejects_malformed_symbols():
    assert "/api/explainability/NVDA".startswith(server._HEAVY_PREFIXES)
    with pytest.raises(server.HTTPException) as exc:
        server._safe_symbol("BAD$$$")
    assert exc.value.status_code == 400


# ─── yf_download: Yahoo healthy → its frame is returned untouched ──────────────


def test_yf_download_prefers_yahoo_when_healthy(monkeypatch, fake_rh):
    good = pd.DataFrame(
        {
            "Open": np.arange(10.0),
            "High": np.arange(10.0),
            "Low": np.arange(10.0),
            "Close": np.arange(10.0),
            "Volume": np.arange(10.0),
        },
        index=pd.date_range("2026-01-01", periods=10),
    )
    fake_yf = types.ModuleType("yfinance")
    fake_yf.download = lambda *a, **k: good
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    called = {"rh": False}
    orig = price_source.rh_fallback
    monkeypatch.setattr(
        price_source, "rh_fallback", lambda *a, **k: called.__setitem__("rh", True) or orig(*a, **k)
    )

    df = price_source.yf_download("NVDA", period="1y")
    pd.testing.assert_frame_equal(df, good)
    assert called["rh"] is False  # fallback never invoked on a healthy frame
