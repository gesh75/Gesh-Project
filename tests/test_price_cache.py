"""price_cache extraction contract (debt item, external reviews 2026-07-17).

The shared close-price cache (_cached_close / _latest_close / _latest_close_meta
+ the ("close", sym) TTL slice) moves out of server.py into price_cache.py so
cron consumers (run_weekly_digest) can price a book WITHOUT importing the whole
FastAPI app — an unrelated server import-time failure used to silently zero
customers' book values in the digest.

Three contracts locked here:
  1. price_cache is a real standalone cache (TTL, None-not-cached, clear()).
  2. server keeps back-compat seams: monkeypatching server._latest_close /
     server._latest_close_meta still steers server._cached_close /
     server._latest_close exactly as before the extraction.
  3. run_weekly_digest._close_for prices via price_cache and NEVER drags
     server into the process (subprocess-verified, clean sys.modules).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import price_cache
import price_source
import server

REPO = Path(__file__).resolve().parent.parent


# ── 1. the standalone cache ───────────────────────────────────────────────────


def test_cached_close_fetches_once_within_ttl(monkeypatch):
    calls: list[str] = []

    def fake_fetch(sym: str) -> float:
        calls.append(sym)
        return 42.5

    monkeypatch.setattr(price_cache, "_latest_close", fake_fetch, raising=True)
    assert price_cache._cached_close("NVDA") == 42.5
    assert price_cache._cached_close("NVDA") == 42.5
    assert calls == ["NVDA"], "second call must be served from the cache"


def test_cached_close_none_uses_short_negative_ttl(monkeypatch):
    """Missing tickers do not refetch until the short negative TTL expires."""
    calls = 0
    results = iter([None, 99.0])

    def fetch(_sym):
        nonlocal calls
        calls += 1
        return next(results)

    monkeypatch.setattr(price_cache, "_latest_close", fetch, raising=True)
    assert price_cache._cached_close("MSTR") is None
    assert price_cache._cached_close("MSTR") is None
    assert calls == 1
    assert price_cache.has_cached_close("MSTR") is True
    with price_cache._close_lock:
        ts, price = price_cache._close_cache["MSTR"]
        price_cache._close_cache["MSTR"] = (ts - price_cache.NEGATIVE_CLOSE_TTL_SEC - 1, price)
    assert price_cache._cached_close("MSTR") == 99.0
    assert calls == 2


def test_cached_close_expired_entry_refetches(monkeypatch):
    monkeypatch.setattr(price_cache, "_latest_close", lambda sym: 10.0, raising=True)
    assert price_cache._cached_close("SPY") == 10.0
    # Age the entry past the TTL, then verify a fresh fetch happens.
    with price_cache._close_lock:
        ts, price = price_cache._close_cache["SPY"]
        price_cache._close_cache["SPY"] = (ts - price_cache.CLOSE_TTL_SEC - 1, price)
    monkeypatch.setattr(price_cache, "_latest_close", lambda sym: 11.0, raising=True)
    assert price_cache._cached_close("SPY") == 11.0


def test_has_cached_close_and_clear(monkeypatch):
    monkeypatch.setattr(price_cache, "_latest_close", lambda sym: 5.0, raising=True)
    assert price_cache.has_cached_close("QQQ") is False
    price_cache._cached_close("QQQ")
    assert price_cache.has_cached_close("QQQ") is True
    price_cache.clear()
    assert price_cache.has_cached_close("QQQ") is False


def test_latest_close_is_meta_wrapper(monkeypatch):
    monkeypatch.setattr(
        price_cache,
        "_latest_close_meta",
        lambda sym: (123.45, {"source": "yahoo", "substituted": False}),
        raising=True,
    )
    assert price_cache._latest_close("NVDA") == 123.45


def test_latest_close_meta_implementation_lives_here(monkeypatch):
    """The moved implementation still reads bars through price_source (same
    VPN-safe RH-fallback path) and reports the frame's real provenance."""
    frame = price_source._stamp(pd.DataFrame({"Close": np.linspace(100.0, 110.0, 30)}), "robinhood")
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: frame, raising=True)
    price, meta = price_cache._latest_close_meta("NVDA")
    assert price == 110.0
    assert meta == {"source": "robinhood", "substituted": False}


# ── 2. server back-compat seams ───────────────────────────────────────────────


def test_server_cached_close_resolves_patched_latest_close(monkeypatch):
    """The historical seam: tests patch server._latest_close and expect
    server._cached_close (book valuation, fundamentals grounding) to follow."""
    monkeypatch.setattr(server, "_latest_close", lambda sym: 77.0, raising=True)
    assert server._cached_close("XYZ") == 77.0


def test_server_latest_close_resolves_patched_meta(monkeypatch):
    monkeypatch.setattr(
        server,
        "_latest_close_meta",
        lambda sym: (55.0, {"source": "yahoo", "substituted": False}),
        raising=True,
    )
    assert server._latest_close("XYZ") == 55.0


def test_server_cached_close_stores_into_price_cache(monkeypatch):
    monkeypatch.setattr(server, "_latest_close", lambda sym: 31.0, raising=True)
    assert server._cached_close("ABC") == 31.0
    assert price_cache.has_cached_close("ABC") is True
    # And the cached value is shared: a direct price_cache read hits, no fetch.
    monkeypatch.setattr(
        price_cache,
        "_latest_close",
        lambda sym: (_ for _ in ()).throw(AssertionError("fetched")),
        raising=True,
    )
    assert price_cache._cached_close("ABC") == 31.0


# ── 3. the cron consumer never imports server ─────────────────────────────────


def test_weekly_digest_close_for_prices_without_server(tmp_path):
    """run_weekly_digest._close_for used to `from server import _cached_close` —
    dragging the FastAPI app + billing + trading into a cron process, where any
    unrelated import-time failure silently zeroed customers' book values."""
    code = (
        "import sys\n"
        "import price_cache\n"
        "price_cache._latest_close = lambda sym: 101.0\n"
        "import run_weekly_digest\n"
        "assert run_weekly_digest._close_for('NVDA') == 101.0\n"
        "assert 'server' not in sys.modules, '_close_for imported server'\n"
        "print('ISOLATED-OK')\n"
    )
    # Fresh interpreter = conftest's in-process DB-path isolation does NOT apply;
    # point both DB env knobs at throwaways so a future import-time DB touch in
    # run_weekly_digest/auth_users/signal_history can never write real files.
    env = {
        **os.environ,
        "GESH_AUTH_DB": str(tmp_path / "auth.db"),
        "GESH_DB_PATH": str(tmp_path / "gesh.db"),
    }
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, env=env, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert "ISOLATED-OK" in out.stdout
