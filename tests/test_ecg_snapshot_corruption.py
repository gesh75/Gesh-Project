"""Regression tests for the ECG equity-snapshot corruption (external audit 2026-07-18).

Symptom: /api/ecg reported NVDA "SUSPENDED — severe drawdown -96.8% — consider exiting"
because equity_snapshots had accumulated corrupt rows (current_price=0 + a stale
constant equity, or a 293.3-style sentinel). compute_ecg takes MAX(series) as the peak,
so ONE real value among sentinels reads as a ~-96% crash — a false customer panic alarm.

Two guards, both here:
  * write side  — _maybe_log_snapshot / _is_real_observation never persist price<=0
    or non-finite/<=0 equity rows.
  * read side   — /api/ecg's history_fn drops non-observations before compute_ecg, so
    an already-polluted DB degrades to "insufficient history / HEALTHY", never SUSPENDED.
"""
import pytest

import server


@pytest.fixture(autouse=True)
def _no_live_cache_thread(monkeypatch):
    # _maybe_log_snapshot spawns fetch_and_cache in a daemon thread (real RH login) —
    # neutralize it so the writer tests stay offline and quiet.
    import fetch_live_data
    monkeypatch.setattr(fetch_live_data, "fetch_and_cache", lambda **kw: None)


# ─────────────────────────── the validity predicate ─────────────────────────

@pytest.mark.parametrize("h,ok", [
    ({"current_price": 205.2, "equity": 9308.6}, True),
    ({"current_price": 0.0, "equity": 101.98}, False),      # price=0 → not an observation
    ({"current_price": 205.2, "equity": 0.0}, False),       # equity=0 → not an observation
    ({"current_price": -1.0, "equity": 100.0}, False),      # negative price
    ({"current_price": float("nan"), "equity": 100.0}, False),
    ({"current_price": float("inf"), "equity": 100.0}, False),
    ({"current_price": True, "equity": 100.0}, False),      # bool is not a real price
    ({"equity": 100.0}, False),                             # missing price
    ({}, False),
])
def test_is_real_observation(h, ok):
    assert server._is_real_observation(h) is ok


# ─────────────────────────── write side: no garbage in ──────────────────────

def test_writer_skips_price_zero_rows(monkeypatch):
    captured = {}
    monkeypatch.setattr(server, "log_equity_snapshot",
                        lambda holdings: captured.setdefault("rows", holdings))
    monkeypatch.setattr(server, "_last_snapshot_at", 0.0)
    holdings = [
        {"symbol": "NVDA", "current_price": 0.0, "equity": 101.98},   # corrupt
        {"symbol": "QQQ", "current_price": 700.0, "equity": 12238.0},  # real
    ]
    server._maybe_log_snapshot(holdings)
    assert [h["symbol"] for h in captured["rows"]] == ["QQQ"]         # NVDA dropped


def test_writer_skips_entirely_when_nothing_real(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(server, "log_equity_snapshot",
                        lambda holdings: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(server, "_last_snapshot_at", 0.0)
    server._maybe_log_snapshot([{"symbol": "NVDA", "current_price": 0.0, "equity": 293.3}])
    assert called["n"] == 0                                           # no write at all


# ─────────────────────────── read side: no false crash ──────────────────────

def _run_ecg(history_rows):
    """Drive compute_ecg through the same sanitizing history_fn /api/ecg uses."""
    import gesh_core.ecg as ge

    def history_fn(symbol):
        return [r for r in history_rows if server._is_real_observation(r)]

    holdings = [{"symbol": "NVDA", "equity": 9308.6, "current_price": 205.2}]
    return ge.compute_ecg(holdings, history_fn)


def _row(equity, price, at):
    return {"snapshot_at": at, "equity": equity, "percent_change": 0.0, "current_price": price}


def test_sentinel_dominated_series_is_not_a_false_crash():
    # The exact shape from the audit: many sentinels (price 0) + one real value.
    rows = [_row(293.3, 0.0, f"2026-07-{d:02d}") for d in range(1, 29)]
    rows.insert(20, _row(9294.78, 205.2, "2026-07-21"))    # the one real observation
    result = _run_ecg(rows)
    st = next(s for s in result["states"] if s["symbol"] == "NVDA")
    assert st["state"] != "SUSPENDED"                      # was falsely SUSPENDED -96.8%
    assert st["drawdown_pct"] > -50                        # no fabricated catastrophic loss


def test_all_zero_price_series_degrades_to_healthy():
    rows = [_row(101.98, 0.0, f"2026-07-{d:02d}") for d in range(1, 15)]
    result = _run_ecg(rows)
    st = next(s for s in result["states"] if s["symbol"] == "NVDA")
    assert st["state"] == "HEALTHY"                        # insufficient real data, not a crash
    assert st["drawdown_pct"] == 0.0


def test_genuine_drawdown_still_fires():
    # A REAL crash must still trip the gate — the guard removes fabrications, it must not
    # blunt true signals. history is NEWEST-first (like get_equity_history DESC), so the
    # most-recent point (i=0) is the trough and the oldest (i=19) is the peak: a monotonic
    # peak→trough decline. Both loss-streak and peak-drawdown are then unambiguous.
    rows = [_row(4300 + i * 300, 90.0 + i * 5, f"2026-07-{20-i:02d}") for i in range(20)]
    result = _run_ecg(rows)
    st = next(s for s in result["states"] if s["symbol"] == "NVDA")
    assert st["drawdown_pct"] < -30                        # real ~-57% drawdown preserved
    assert st["state"] in ("COOLING", "SUSPENDED")
