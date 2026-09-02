"""First execution tests for thematic alpha benchmarking.

`benchmarks.py` feeds weekly reviews, the daily report, and Claude prompt
injection. A missing Close series must stay None (audit D-06) — inventing
0.0% makes a name look like it matched SPY. Grade bands and vs-market sort
are the only ranking the report shows.
"""

from __future__ import annotations

import pandas as pd
import pytest

import benchmarks as bm


def _close_holder(closes: dict[str, list[float]]) -> dict:
    """Object that supports ``download(...)['Close']`` like yfinance."""
    return {"Close": pd.DataFrame(closes)}


@pytest.mark.parametrize(
    "ticker,etf",
    [
        ("nvda", "SMH"),
        ("NVDA", "SMH"),
        ("AAPL", "XLK"),
        ("HOOD", "BITQ"),
        ("UNKNOWNXYZ", "SPY"),
        ("spy", "SPY"),
    ],
)
def test_get_sector_benchmark_maps_and_defaults(ticker, etf):
    assert bm.get_sector_benchmark(ticker) == etf


@pytest.mark.parametrize(
    "vs_market,vs_sector,grade",
    [
        (10.0, 0.0, "A"),  # score 5.0 inclusive
        (9.98, 0.0, "B"),  # 4.99
        (4.0, 0.0, "B"),  # 2.0 inclusive
        (3.98, 0.0, "C"),  # 1.99
        (0.0, 0.0, "C"),  # 0.0 inclusive
        (-0.002, 0.0, "D"),
        (-6.0, 0.0, "D"),  # -3.0 inclusive
        (-6.02, 0.0, "F"),
    ],
)
def test_alpha_grade_band_edges(vs_market, vs_sector, grade):
    assert bm._alpha_grade(vs_market, vs_sector) == grade


def test_period_return_missing_is_none_never_zero():
    """D-06: absent / short / all-NaN series must not become 0.0%."""
    frame = pd.DataFrame({"NVDA": [100.0, 110.0], "SPY": [100.0, float("nan")]})
    assert bm._period_return(frame, "NVDA") == pytest.approx(10.0)
    assert bm._period_return(frame, "SPY") is None
    assert bm._period_return(frame, "SMH") is None
    assert bm._period_return(None, "NVDA") is None
    assert bm._period_return(pd.DataFrame({"NVDA": [100.0]}), "NVDA") is None
    assert bm._period_return(object(), "NVDA") is None


def test_get_alpha_computes_vs_spy_and_sector(monkeypatch):
    # NVDA 100→110 = +10%; SPY 100→105 = +5%; SMH 100→108 = +8%
    holder = _close_holder({"NVDA": [100.0, 110.0], "SPY": [100.0, 105.0], "SMH": [100.0, 108.0]})
    monkeypatch.setattr(bm.price_source, "locked_yf_download", lambda *a, **k: holder)

    out = bm.get_alpha("nvda", period="1mo")
    assert out["ticker"] == "NVDA"
    assert out["sector_etf"] == "SMH"
    assert out["stock_return_pct"] == pytest.approx(10.0)
    assert out["spy_return_pct"] == pytest.approx(5.0)
    assert out["sector_return_pct"] == pytest.approx(8.0)
    assert out["vs_market_pct"] == pytest.approx(5.0)
    assert out["vs_sector_pct"] == pytest.approx(2.0)
    assert out["alpha_grade"] == "B"  # score (5+2)/2 = 3.5
    assert "Grade B" in out["summary"]
    assert "error" not in out


def test_get_alpha_missing_peer_is_unavailable_not_zero(monkeypatch):
    holder = _close_holder({"NVDA": [100.0, 110.0], "SPY": [100.0, 105.0]})
    monkeypatch.setattr(bm.price_source, "locked_yf_download", lambda *a, **k: holder)

    out = bm.get_alpha("NVDA")
    assert out["alpha_grade"] == "n/a"
    assert "error" in out
    assert "SMH" in out["error"]
    assert out.get("vs_market_pct") is None
    assert "0.0%" not in out.get("summary", "")


def test_get_alpha_download_exception_is_typed_error(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("yahoo 401")

    monkeypatch.setattr(bm.price_source, "locked_yf_download", _boom)
    out = bm.get_alpha("NVDA")
    assert out["ticker"] == "NVDA"
    assert "yahoo 401" in out["error"]
    assert "alpha_grade" not in out


def test_batch_alpha_empty_and_sorts_unavailable_last(monkeypatch):
    assert bm.batch_alpha([]) == []

    holder = _close_holder(
        {
            "AAPL": [100.0, 120.0],  # +20 vs SPY +5 = +15
            "MSFT": [100.0, 108.0],  # +8 vs SPY +5 = +3
            "SPY": [100.0, 105.0],
            "XLK": [100.0, 110.0],
        }
    )
    monkeypatch.setattr(bm.price_source, "locked_yf_download", lambda *a, **k: holder)

    rows = bm.batch_alpha(["msft", "ZZZZ", "aapl"])
    assert [r["ticker"] for r in rows] == ["AAPL", "MSFT", "ZZZZ"]
    assert rows[0]["vs_market_pct"] == pytest.approx(15.0)
    assert rows[1]["vs_market_pct"] == pytest.approx(3.0)
    assert rows[2]["vs_market_pct"] is None
    assert rows[2]["alpha_grade"] == "n/a"
    assert rows[2]["stock_return_pct"] is None


def test_batch_alpha_download_failure_marks_all_unavailable(monkeypatch):
    monkeypatch.setattr(
        bm.price_source,
        "locked_yf_download",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
    )
    rows = bm.batch_alpha(["NVDA", "AAPL"])
    assert len(rows) == 2
    assert all(r["vs_market_pct"] is None for r in rows)
    assert all(r["alpha_grade"] == "n/a" for r in rows)


def test_alpha_summary_text_empty_and_na_rows(monkeypatch):
    assert bm.alpha_summary_text([]) == "Alpha benchmarking unavailable."

    holder = _close_holder({"NVDA": [100.0, 110.0], "SPY": [100.0, 105.0], "SMH": [100.0, 108.0]})
    monkeypatch.setattr(bm.price_source, "locked_yf_download", lambda *a, **k: holder)
    text = bm.alpha_summary_text(["NVDA", "ZZZZ"], period="3mo")
    assert "3mo" in text
    assert "NVDA" in text
    assert "+5.0%" in text
    assert "ZZZZ" in text
    assert "n/a" in text
