"""Batched close-cache warm for the customer book (_customer_book_payload).

The per-symbol warm used to fan out over a ThreadPoolExecutor, but
price_source._YF_DOWNLOAD_LOCK (the yfinance shared._DFS race fix) serializes
single-ticker downloads anyway — a cold 40-symbol bulk import degraded to 40
sequential upstream fetches. The warm must instead issue ONE multi-symbol
price_source.yf_download (a single batched call is internally safe: one
shared._DFS reset), populate price_cache's close cache from the returned frame
with the same bad-tick guard as _latest_close_meta, and fall back to the
per-symbol path only for symbols the batch failed to serve.

Same offline rules as the rest of the suite: yfinance is stubbed, no network.
"""

from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

import price_cache
import price_source
import server


def _batch_frame(prices: dict[str, list[float]]) -> pd.DataFrame:
    """A multi-symbol yfinance-shaped frame: MultiIndex (Price, Ticker) columns."""
    n = max(len(v) for v in prices.values())
    idx = pd.date_range("2026-06-01", periods=n, freq="D")
    cols = {}
    for sym, closes in prices.items():
        padded = [closes[0]] * (n - len(closes)) + list(closes)
        for field in ("Open", "High", "Low", "Close"):
            cols[(field, sym)] = padded
        cols[("Volume", sym)] = [1_000_000] * n
    df = pd.DataFrame(cols, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns, names=["Price", "Ticker"])
    return df


def _single_frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-06-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                         "Close": closes, "Volume": [1_000_000] * len(closes)}, index=idx)


def _positions(symbols: list[str]) -> list[dict]:
    return [{"symbol": s, "shares": 1.0, "avg_cost": 10.0} for s in symbols]


@pytest.fixture(autouse=True)
def _cold_caches():
    price_cache.clear()
    price_source._YF_CACHE.clear()
    yield
    price_cache.clear()
    price_source._YF_CACHE.clear()


# ─── one batched call, not N serialized single-ticker calls ──────────────────

def test_cold_book_warms_with_one_batched_download(monkeypatch):
    syms = [f"SY{i:02d}" for i in range(40)]
    calls: list[object] = []

    def fake_yf_download(symbols, **kwargs):
        calls.append(symbols)
        if isinstance(symbols, str):
            return _single_frame([100.0] * 10)
        return _batch_frame({s: [100.0 + i] * 10 for i, s in enumerate(symbols)})

    monkeypatch.setattr(price_source, "yf_download", fake_yf_download)

    payload = server._customer_book_payload(_positions(syms))

    batch_calls = [c for c in calls if isinstance(c, (list, tuple))]
    single_calls = [c for c in calls if isinstance(c, str)]
    assert len(batch_calls) == 1, f"expected ONE batched download, got calls={calls!r}"
    assert sorted(batch_calls[0]) == sorted(syms)
    assert single_calls == [], "no per-symbol fetches when the batch served everything"
    assert payload["unpriced"] == []
    prices = {h["symbol"]: h["current_price"] for h in payload["holdings"]}
    assert prices["SY00"] == 100.0 and prices["SY39"] == 139.0


# ─── bad-tick guard parity with _latest_close_meta ────────────────────────────

def test_batch_warm_applies_bad_tick_guard(monkeypatch):
    # Steady $200 closes with a $17.16 flash on the last bar (the audited NVDA
    # case): the batch warm must substitute the most recent in-band close, never
    # cache the outlier.
    closes = [200.0, 201.0, 202.0, 203.0, 204.0, 17.16]

    def fake_yf_download(symbols, **kwargs):
        assert isinstance(symbols, (list, tuple))
        return _batch_frame({"BADTK": closes, "OKAAY": [50.0] * 6})

    monkeypatch.setattr(price_source, "yf_download", fake_yf_download)

    payload = server._customer_book_payload(_positions(["BADTK", "OKAAY"]))
    prices = {h["symbol"]: h["current_price"] for h in payload["holdings"]}
    assert prices["BADTK"] == 204.0          # last in-band close, not 17.16
    assert prices["OKAAY"] == 50.0


# ─── symbols missing from the batch fall back to the per-symbol path ─────────

def test_batch_miss_falls_back_to_per_symbol_path(monkeypatch):
    calls: list[object] = []

    def fake_yf_download(symbols, **kwargs):
        calls.append(symbols)
        if isinstance(symbols, (list, tuple)):
            # Batch serves only 2 of the 3 symbols — MISSD has no column at all.
            return _batch_frame({"AAA": [10.0] * 6, "BBB": [20.0] * 6})
        return _single_frame([30.0] * 6) if symbols == "MISSD" else None

    monkeypatch.setattr(price_source, "yf_download", fake_yf_download)

    payload = server._customer_book_payload(_positions(["AAA", "BBB", "MISSD"]))
    prices = {h["symbol"]: h["current_price"] for h in payload["holdings"]}
    assert prices == {"AAA": 10.0, "BBB": 20.0, "MISSD": 30.0}
    assert payload["unpriced"] == []
    assert "MISSD" in [c for c in calls if isinstance(c, str)]


def test_batch_total_failure_falls_back_per_symbol(monkeypatch):
    def fake_yf_download(symbols, **kwargs):
        if isinstance(symbols, (list, tuple)):
            return None                      # Yahoo AND RH dead for the batch
        return _single_frame([float(len(symbols))] * 6)

    monkeypatch.setattr(price_source, "yf_download", fake_yf_download)

    payload = server._customer_book_payload(_positions(["AAA", "BBBB"]))
    prices = {h["symbol"]: h["current_price"] for h in payload["holdings"]}
    assert prices == {"AAA": 3.0, "BBBB": 4.0}


# ─── before/after wall time for a 40-symbol cold book ────────────────────────

def test_batch_warm_wall_time_40_symbol_cold_book(monkeypatch):
    """Measure the serialized per-symbol warm vs the batched warm.

    The stub charges every yf_download call one fixed 'network' delay under the
    REAL _YF_DOWNLOAD_LOCK — exactly how production serializes. Before: 40 cold
    symbols = 40 serialized delays. After: one batched call = one delay.
    """
    delay = 0.02
    syms = [f"TM{i:02d}" for i in range(40)]

    def fake_yf_download(symbols, **kwargs):
        with price_source._YF_DOWNLOAD_LOCK:
            time.sleep(delay)
        if isinstance(symbols, str):
            return _single_frame([100.0] * 10)
        return _batch_frame({s: [100.0] * 10 for s in symbols})

    monkeypatch.setattr(price_source, "yf_download", fake_yf_download)

    # BEFORE — the old warm: ThreadPoolExecutor fan-out over _cached_close,
    # which the download lock reduces to 40 sequential fetches.
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(server._cached_close, syms))
    before = time.perf_counter() - t0
    assert threading.active_count() < 50     # sanity: pool wound down

    # AFTER — cold caches again, the new batched warm inside the payload builder.
    price_cache.clear()
    price_source._YF_CACHE.clear()
    t0 = time.perf_counter()
    payload = server._customer_book_payload(_positions(syms))
    after = time.perf_counter() - t0

    assert payload["unpriced"] == []
    # 40 serialized delays vs ~1: demand a decisive margin, not a photo finish.
    assert after < before / 5, (
        f"batched warm not faster: before={before:.3f}s after={after:.3f}s")
    print(f"\n40-symbol cold book warm: before={before*1000:.0f}ms "
          f"after={after*1000:.0f}ms ({before/after:.1f}x faster)")
