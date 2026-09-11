"""Astra R4: UNIQUE client_order_id must not look like a new paper fill.

_safe_log swallows the UNIQUE collision and returns None. place() must then
return the existing row as DUPLICATE and skip stop-book writes and fill alerts.
Minute-bucket identity is unchanged: distinct generated_at minutes or target
pct remain separate orders.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from trading import execution, state
from trading.config import RiskConfig
from trading.contract import manual
from trading.risk_gate import GateVerdict


@pytest.fixture
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "test_state.db")
    state.init_trading_tables()
    return state


def _snap(equity: float, watch_sym: str, watch_price: float) -> dict:
    return {
        "equity_summary": {"portfolio_equity": equity},
        "holdings": [],
        "watchlist": [{"symbol": watch_sym, "last_trade_price": watch_price}],
    }


def _approved() -> GateVerdict:
    return GateVerdict(approved=True, reason=None, gates={}, intent_id=1, decision_id=1)


def _cfg() -> RiskConfig:
    return RiskConfig(paper_mode=True, paper_cash_usd=100_000.0)


def _count(sql: str, params: tuple = ()) -> int:
    with sqlite3.connect(state.DB_PATH) as con:
        return int(con.execute(sql, params).fetchone()[0])


def test_paper_place_replay_returns_duplicate_not_new_fill(sandbox_db, monkeypatch):
    alerts: list[tuple] = []
    monkeypatch.setattr(execution, "alert_order_fill", lambda *a, **k: alerts.append(a))

    intent = replace(
        manual("HOOD", "BUY", confidence=0.9, target_position_pct=0.02),
        generated_at="2026-09-11T14:01:00+00:00",
    )
    snap = _snap(10_000.0, "HOOD", 100.0)
    first = execution.place(intent, _approved(), snap, _cfg())
    second = execution.place(intent, _approved(), snap, _cfg())

    assert first.status == "SIMULATED"
    assert first.ok is True
    assert first.order_row_id is not None

    assert second.status == "DUPLICATE"
    assert second.ok is False
    assert second.order_row_id == first.order_row_id
    assert second.fill_price == first.fill_price
    assert second.fill_qty == first.fill_qty
    assert second.client_order_id == first.client_order_id

    coid = first.client_order_id
    assert _count("SELECT COUNT(*) FROM orders WHERE client_order_id = ?", (coid,)) == 1
    assert _count("SELECT COUNT(*) FROM paper_stops WHERE symbol = 'HOOD' AND active = 1") == 1
    assert len(alerts) == 1


def test_paper_place_distinct_intents_do_not_share_client_order_id(sandbox_db, monkeypatch):
    monkeypatch.setattr(execution, "alert_order_fill", lambda *a, **k: None)
    snap = _snap(10_000.0, "HOOD", 100.0)
    base = manual("HOOD", "BUY", confidence=0.9, target_position_pct=0.02)
    a = replace(base, generated_at="2026-09-11T14:01:00+00:00")
    b = replace(base, generated_at="2026-09-11T14:02:00+00:00", target_position_pct=0.03)

    first = execution.place(a, _approved(), snap, _cfg())
    second = execution.place(b, _approved(), snap, _cfg())

    assert first.status == "SIMULATED"
    assert second.status == "SIMULATED"
    assert first.ok is True
    assert second.ok is True
    assert first.client_order_id != second.client_order_id
    assert first.order_row_id != second.order_row_id
    assert _count("SELECT COUNT(*) FROM orders WHERE status = 'SIMULATED'") == 2


def test_paper_place_concurrent_same_logical_order_is_one_fill(sandbox_db, monkeypatch):
    alerts: list[tuple] = []
    monkeypatch.setattr(execution, "alert_order_fill", lambda *a, **k: alerts.append(a))

    intent = replace(
        manual("HOOD", "BUY", confidence=0.9, target_position_pct=0.02),
        generated_at="2026-09-11T14:01:00+00:00",
    )
    snap = _snap(10_000.0, "HOOD", 100.0)
    cfg = _cfg()
    verdict = _approved()

    def _place():
        return execution.place(intent, verdict, snap, cfg)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _place(), range(2)))

    statuses = sorted(r.status for r in results)
    assert statuses == ["DUPLICATE", "SIMULATED"]
    simulated = next(r for r in results if r.status == "SIMULATED")
    duplicate = next(r for r in results if r.status == "DUPLICATE")
    assert simulated.ok is True
    assert duplicate.ok is False
    assert duplicate.order_row_id == simulated.order_row_id
    assert (
        _count(
            "SELECT COUNT(*) FROM orders WHERE client_order_id = ?", (simulated.client_order_id,)
        )
        == 1
    )
    assert _count("SELECT COUNT(*) FROM paper_stops WHERE symbol = 'HOOD' AND active = 1") == 1
    assert len(alerts) == 1
