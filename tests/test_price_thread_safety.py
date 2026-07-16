"""Cross-symbol price contamination guards (the audit's NVDA=$136.08=TSLA bug).

Root cause: yfinance's ``download()`` assembles results through the module-global
``yfinance.shared._DFS`` and its completion check is ``while len(shared._DFS) <
len(tickers)`` — so CONCURRENT single-ticker downloads (the cold-book warm in
``_customer_book_payload`` runs 8 at once) can hand thread A the frame thread B
downloaded, silently returning ticker B's bars for ticker A's request. The wrong
close then sticks in the 5-minute cache and prices the whole book.

Three layers under test:
  1. price_source serializes ``yf.download`` calls (the root-cause fix).
  2. server._latest_close_meta never flattens a multi-ticker Close frame into a
     wrong-symbol price (defense in depth).
  3. trading.robinhood_quotes.fetch_daily_ohlcv rejects a response whose echoed
     symbol is not the one requested (defense in depth on the RH fallback).
"""
from __future__ import annotations

import sys
import threading
import time
import types

import pandas as pd
import pytest

import price_source


@pytest.fixture(autouse=True)
def _clear_yf_cache():
    price_source._YF_CACHE.clear()
    yield
    price_source._YF_CACHE.clear()


# ─── 1. yf.download must never run concurrently ──────────────────────────────

def test_yf_download_serializes_concurrent_calls(monkeypatch):
    """Six threads fetching six symbols: the fake yfinance.download tracks how
    many callers are inside it simultaneously. Without serialization this
    reliably observes overlap (>1); the lock must keep it at exactly 1."""
    state = {"active": 0, "max_active": 0}
    guard = threading.Lock()

    def _fake_download(symbols, **kwargs):
        with guard:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.02)  # widen the race window
        with guard:
            state["active"] -= 1
        idx = pd.date_range("2026-06-01", periods=10, freq="D")
        return pd.DataFrame({"Close": [100.0 + i for i in range(10)],
                             "Open": 100.0, "High": 101.0, "Low": 99.0,
                             "Volume": 1e6}, index=idx)

    fake_yf = types.SimpleNamespace(download=_fake_download)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    threads = [threading.Thread(target=price_source.yf_download, args=(f"SYM{i}",),
                                kwargs={"period": "1mo", "interval": "1d"})
               for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max_active"] == 1, (
        f"yf.download ran {state['max_active']}-way concurrent — yfinance's "
        "shared._DFS global makes that cross-contaminate symbols")


# ─── 2. _latest_close_meta must not flatten a multi-ticker frame ─────────────

def _contaminated_frame(syms: list[str], last_closes: dict[str, float]) -> pd.DataFrame:
    """A frame shaped like a contaminated yf.download return: MultiIndex
    (Price, Ticker) columns carrying MORE tickers than were requested."""
    idx = pd.date_range("2026-06-01", periods=10, freq="D")
    pieces = {}
    for s in syms:
        base = last_closes[s]
        pieces[("Close", s)] = [base - (9 - i) for i in range(10)]
    df = pd.DataFrame(pieces, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns, names=["Price", "Ticker"])
    return df


def test_latest_close_meta_picks_requested_symbol_from_multi_frame(monkeypatch):
    import server
    frame = _contaminated_frame(["NVDA", "TSLA"], {"NVDA": 212.0, "TSLA": 136.0})
    monkeypatch.setattr(price_source, "yf_download",
                        lambda *a, **k: frame, raising=True)
    price, meta = server._latest_close_meta("NVDA")
    assert price == 212.0, f"NVDA request returned {price} — the OTHER ticker's close"


def test_latest_close_meta_returns_none_when_symbol_absent(monkeypatch):
    import server
    frame = _contaminated_frame(["TSLA"], {"TSLA": 136.0})
    monkeypatch.setattr(price_source, "yf_download",
                        lambda *a, **k: frame, raising=True)
    price, meta = server._latest_close_meta("NVDA")
    assert price is None, (
        f"NVDA request served {price} from a frame that only contains TSLA")


# ─── 3. RH historicals fallback must verify the echoed symbol ────────────────

def test_fetch_daily_ohlcv_rejects_mismatched_symbol_echo(monkeypatch):
    import io
    import json as _json

    from trading import robinhood_quotes as rq

    bars = [{"begins_at": f"2026-06-{d:02d}T00:00:00Z", "open_price": "1", "high_price": "2",
             "low_price": "0.5", "close_price": "1.5", "volume": "100"} for d in range(1, 11)]
    payload = {"results": [{"symbol": "TSLA", "historicals": bars}]}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(rq, "_urlopen",
                        lambda url, timeout: _Resp(_json.dumps(payload).encode()),
                        raising=True)
    assert rq.fetch_daily_ohlcv("NVDA") is None
    assert rq.fetch_daily_ohlcv("TSLA") is not None  # matching echo still serves
