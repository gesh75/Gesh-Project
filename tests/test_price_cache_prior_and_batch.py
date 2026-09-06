"""First-execution tests for price_cache.prior_close + warm_close_cache_batch.

These two paths value customer books and day-change without importing server.
They were untested: a missing prior must DISABLE day change (never default to
today), a $0/outlier prior must not be cached as a real close, and a batched
multi-ticker frame must not price NVDA off TSLA's column.

Offline: yf_download is stubbed. No gesh-core. Safe under --noconftest.
"""

from __future__ import annotations

import pandas as pd
import pytest

import price_cache
import price_source


@pytest.fixture(autouse=True)
def _clear_price_cache():
    price_cache.clear()
    yield
    price_cache.clear()


def _flat_close(values: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-06-01", periods=len(values), freq="D")
    return pd.DataFrame({"Close": values}, index=idx)


def _multi_close(last_by_sym: dict[str, float], n: int = 10) -> pd.DataFrame:
    """yfinance-shaped MultiIndex (Price, Ticker) frame."""
    idx = pd.date_range("2026-06-01", periods=n, freq="D")
    pieces: dict[tuple[str, str], list[float]] = {}
    for sym, last in last_by_sym.items():
        closes = [last - (n - 1 - i) for i in range(n)]
        pieces[("Close", sym)] = closes
        pieces[("Open", sym)] = closes
    df = pd.DataFrame(pieces, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns, names=["Price", "Ticker"])
    return df


# ── _prior_close_from_series ──────────────────────────────────────────────────


def test_prior_close_from_series_returns_second_to_last_bar():
    series = _flat_close([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])["Close"]
    assert price_cache._prior_close_from_series(series) == 14.0


def test_prior_close_from_series_single_bar_is_none_not_today():
    """A missing prior must DISABLE day change — never echo today's close."""
    series = _flat_close([100.0])["Close"]
    assert price_cache._prior_close_from_series(series) is None


def test_prior_close_from_series_zero_or_negative_is_none():
    assert price_cache._prior_close_from_series(_flat_close([10.0, 0.0, 12.0])["Close"]) is None
    assert price_cache._prior_close_from_series(_flat_close([10.0, -1.0, 12.0])["Close"]) is None


def test_prior_close_from_series_outlier_is_none_not_substituted():
    """A $17 flash as the PRIOR bar must not invent a substituted prior.
    Latest-close substitutes; prior-close refuses — day change goes unavailable."""
    values = [200.0] * 8 + [17.16, 201.0]
    assert price_cache._prior_close_from_series(_flat_close(values)["Close"]) is None


def test_prior_close_from_series_in_band_prior_kept_when_latest_is_flash():
    values = [200.0] * 9 + [17.16]
    assert price_cache._prior_close_from_series(_flat_close(values)["Close"]) == 200.0


def test_prior_close_from_series_empty_or_all_nan_is_none():
    idx = pd.date_range("2026-06-01", periods=3, freq="D")
    empty = pd.Series([float("nan"), float("nan"), float("nan")], index=idx)
    assert price_cache._prior_close_from_series(empty) is None


# ── prior_close (download + cache + contamination) ────────────────────────────


def test_prior_close_empty_frame_is_none(monkeypatch):
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: pd.DataFrame(), raising=True)
    assert price_cache.prior_close("NVDA") is None


def test_prior_close_download_exception_is_none(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("yahoo 401")

    monkeypatch.setattr(price_source, "yf_download", boom, raising=True)
    assert price_cache.prior_close("NVDA") is None


def test_prior_close_picks_requested_symbol_from_multi_frame(monkeypatch):
    frame = _multi_close({"NVDA": 212.0, "TSLA": 136.0})
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: frame, raising=True)
    # last=212, prior = 212 - 1 = 211 on a 10-bar ramp
    assert price_cache.prior_close("NVDA") == 211.0


def test_prior_close_refuses_frame_labeled_with_a_different_ticker(monkeypatch):
    """Same contamination class as _latest_close_meta: a TSLA-only frame must
    not become NVDA's prior close (that would invent a day-change vs the
    wrong name)."""
    frame = _multi_close({"TSLA": 136.0})
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: frame, raising=True)
    assert price_cache.prior_close("NVDA") is None


def test_prior_close_caches_within_ttl(monkeypatch):
    calls = {"n": 0}

    def fetch(*_a, **_k):
        calls["n"] += 1
        return _flat_close([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

    monkeypatch.setattr(price_source, "yf_download", fetch, raising=True)
    assert price_cache.prior_close("QQQ") == 104.0
    assert price_cache.prior_close("QQQ") == 104.0
    assert calls["n"] == 1


def test_prior_close_does_not_cache_a_missing_value(monkeypatch):
    monkeypatch.setattr(
        price_source, "yf_download", lambda *a, **k: _flat_close([100.0]), raising=True
    )
    assert price_cache.prior_close("MSTR") is None
    # A later successful fetch must not be blocked by a cached None
    monkeypatch.setattr(
        price_source,
        "yf_download",
        lambda *a, **k: _flat_close([10.0, 11.0, 12.0, 13.0, 14.0, 15.0]),
        raising=True,
    )
    assert price_cache.prior_close("MSTR") == 14.0


# ── warm_close_cache_batch ────────────────────────────────────────────────────


def test_warm_batch_empty_or_none_frame_is_noop(monkeypatch):
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: None, raising=True)
    price_cache.warm_close_cache_batch(["NVDA"])
    assert price_cache.has_cached_close("NVDA") is False

    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: pd.DataFrame(), raising=True)
    price_cache.warm_close_cache_batch(["NVDA"])
    assert price_cache.has_cached_close("NVDA") is False


def test_warm_batch_download_exception_is_noop(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("curl 401")

    monkeypatch.setattr(price_source, "yf_download", boom, raising=True)
    price_cache.warm_close_cache_batch(["NVDA", "TSLA"])
    assert price_cache.has_cached_close("NVDA") is False
    assert price_cache.has_cached_close("TSLA") is False


def test_warm_batch_prices_each_symbol_from_its_own_column(monkeypatch):
    frame = _multi_close({"NVDA": 212.0, "TSLA": 136.0})
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: frame, raising=True)
    price_cache.warm_close_cache_batch(["NVDA", "TSLA"])
    assert price_cache._cached_close("NVDA") == 212.0
    assert price_cache._cached_close("TSLA") == 136.0
    assert price_cache.prior_close("NVDA") == 211.0
    assert price_cache.prior_close("TSLA") == 135.0


def test_warm_batch_missing_column_stays_cold_not_other_ticker(monkeypatch):
    """Asking for NVDA+TSLA against a TSLA-only frame must not invent an NVDA close."""
    frame = _multi_close({"TSLA": 136.0})
    monkeypatch.setattr(price_source, "yf_download", lambda *a, **k: frame, raising=True)
    price_cache.warm_close_cache_batch(["NVDA", "TSLA"])
    assert price_cache.has_cached_close("NVDA") is False
    assert price_cache._cached_close("TSLA") == 136.0


def test_warm_batch_flat_frame_for_multi_symbol_ask_caches_nothing(monkeypatch):
    monkeypatch.setattr(
        price_source, "yf_download", lambda *a, **k: _flat_close([100.0] * 10), raising=True
    )
    price_cache.warm_close_cache_batch(["NVDA", "TSLA"])
    assert price_cache.has_cached_close("NVDA") is False
    assert price_cache.has_cached_close("TSLA") is False


def test_warm_batch_single_symbol_flat_frame_is_cached(monkeypatch):
    monkeypatch.setattr(
        price_source,
        "yf_download",
        lambda *a, **k: _flat_close([100.0] * 9 + [110.0]),
        raising=True,
    )
    price_cache.warm_close_cache_batch(["NVDA"])
    assert price_cache._cached_close("NVDA") == 110.0


def test_warm_batch_zero_price_is_not_cached(monkeypatch):
    monkeypatch.setattr(
        price_source, "yf_download", lambda *a, **k: _flat_close([0.0] * 10), raising=True
    )
    price_cache.warm_close_cache_batch(["HOOD"])
    assert price_cache.has_cached_close("HOOD") is False


def test_warm_batch_outlier_uses_same_guard_as_per_symbol(monkeypatch):
    """A $17 flash against a ~$200 series must not land in the book cache."""
    values = [200.0] * 25 + [17.16]
    monkeypatch.setattr(
        price_source, "yf_download", lambda *a, **k: _flat_close(values), raising=True
    )
    price_cache.warm_close_cache_batch(["NVDA"])
    cached = price_cache._cached_close("NVDA")
    assert cached != 17.16
    assert cached == 200.0
