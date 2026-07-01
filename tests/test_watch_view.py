"""Unit tests for watch_view — the internal live paper-trading cockpit (/watch).

Mirrors the proof_page.py conventions: read-only DB, honest empty states, never
raise on a missing/empty DB. All DB access here is against throwaway fixture DBs;
the price fetcher is injected so no test ever hits yfinance or the production DB.
"""
from __future__ import annotations

import sqlite3

import pytest

import watch_view

_SCHEMA = """
CREATE TABLE paper_positions(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INT, intent_id INT,
  symbol TEXT, action TEXT, qty REAL, entry_price REAL, entry_at TEXT, source TEXT,
  stop_loss REAL, take_profit REAL, status TEXT);
CREATE TABLE paper_outcomes(id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INT, symbol TEXT,
  source TEXT, action TEXT, qty REAL, entry_price REAL, exit_price REAL, entry_at TEXT,
  exit_at TEXT, pnl_usd REAL, pnl_pct REAL, outcome TEXT, hold_days REAL);
CREATE TABLE orders(id INTEGER PRIMARY KEY AUTOINCREMENT, placed_at TEXT, intent_id INT,
  decision_id INT, mode TEXT, client_order_id TEXT, broker_order_id TEXT, status TEXT,
  symbol TEXT, action TEXT, qty REAL, target_price REAL, fill_price REAL, fill_qty REAL,
  error TEXT, response_json TEXT);
CREATE TABLE risk_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, decided_at TEXT, intent_id INT,
  verdict TEXT, reason TEXT, gates_json TEXT, config_json TEXT);
CREATE TABLE trade_intents(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, symbol TEXT,
  action TEXT, confidence REAL, target_pct REAL, stop_loss REAL, take_profit REAL,
  time_in_force TEXT, source TEXT, schema_version INT, raw_json TEXT);
"""

def _NO_PRICES(symbols):
    return {}


def _empty_db(tmp_path) -> str:
    p = tmp_path / "watch_test.db"
    con = sqlite3.connect(p)
    con.executescript(_SCHEMA)
    con.commit()
    con.close()
    return str(p)


def _insert(db, table, **cols):
    con = sqlite3.connect(db)
    keys = ",".join(cols)
    qs = ",".join("?" for _ in cols)
    con.execute(f"INSERT INTO {table}({keys}) VALUES({qs})", list(cols.values()))
    con.commit()
    con.close()


# ── compute_position_metrics (pure function, no IO) ──────────────────────────

def test_metrics_buy_with_price():
    pos = {"action": "BUY", "qty": 10.0, "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 112.0}
    m = watch_view.compute_position_metrics(pos, 110.0)
    assert m["current_price"] == 110.0
    assert m["unrealized_pnl_usd"] == pytest.approx(100.0)            # (110-100)*10
    assert m["unrealized_pnl_pct"] == pytest.approx(0.10)
    assert m["distance_to_stop_pct"] == pytest.approx((110 - 95) / 110 * 100)
    assert m["distance_to_stop_usd"] == pytest.approx((110 - 95) * 10)
    assert m["distance_to_target_pct"] == pytest.approx((112 - 110) / 110 * 100)
    assert m["at_risk"] is False
    assert m["profit_locked"] is False


def test_metrics_buy_profit_locked():
    pos = {"action": "BUY", "qty": 5, "entry_price": 100, "stop_loss": 102, "take_profit": 120}
    m = watch_view.compute_position_metrics(pos, 110)
    assert m["profit_locked"] is True   # stop 102 >= entry 100


def test_metrics_buy_at_risk_near_stop():
    pos = {"action": "BUY", "qty": 5, "entry_price": 100, "stop_loss": 99.5, "take_profit": 120}
    m = watch_view.compute_position_metrics(pos, 100.0)   # dist-to-stop 0.5% < 1.0 → at risk
    assert m["at_risk"] is True


def test_metrics_no_price_degrades():
    pos = {"action": "BUY", "qty": 10, "entry_price": 100, "stop_loss": 95, "take_profit": 112}
    m = watch_view.compute_position_metrics(pos, None)
    assert m["current_price"] is None
    assert m["unrealized_pnl_usd"] is None
    assert m["distance_to_stop_pct"] is None
    assert m["at_risk"] is False
    assert m["profit_locked"] is False   # stop 95 < entry 100, computable without price


def test_metrics_sell_short_pnl():
    pos = {"action": "SELL", "qty": 10, "entry_price": 100, "stop_loss": 105, "take_profit": 88}
    m = watch_view.compute_position_metrics(pos, 90)
    assert m["unrealized_pnl_usd"] == pytest.approx((100 - 90) * 10)   # short gains as price falls


# ── load_watch_data ──────────────────────────────────────────────────────────

def test_load_empty_db(tmp_path):
    d = watch_view.load_watch_data(_empty_db(tmp_path), price_fetcher=_NO_PRICES)
    assert d["schema_version"] == 1
    assert d["mode"] == "paper"
    assert d["open_positions"] == []
    assert d["recent_closes"] == []
    assert d["activity_feed"] == []
    assert d["summary"]["closed_count_total"] == 0
    assert d["summary"]["open_count"] == 0
    assert d["summary"]["sample_provisional"] is True
    assert d["live_prices_available"] is False
    assert d["autonomy"]["closed_n"] == 0


def test_load_missing_tables_never_raises(tmp_path):
    p = tmp_path / "bare.db"
    sqlite3.connect(p).close()                # file exists, zero tables
    d = watch_view.load_watch_data(str(p), price_fetcher=_NO_PRICES)
    assert d["open_positions"] == []
    assert d["summary"]["closed_count_total"] == 0


def test_load_missing_db_file_never_raises(tmp_path):
    d = watch_view.load_watch_data(str(tmp_path / "nope.db"), price_fetcher=_NO_PRICES)
    assert d["open_positions"] == [] and d["summary"]["open_count"] == 0


def test_open_positions_shape_and_order(tmp_path):
    db = _empty_db(tmp_path)
    _insert(db, "paper_positions", order_id=1, intent_id=1, symbol="ORCL", action="BUY", qty=3.0,
            entry_price=220.0, entry_at="2026-06-05T14:00:00+00:00", source="manual",
            stop_loss=209.0, take_profit=246.0, status="OPEN")
    _insert(db, "paper_positions", order_id=2, intent_id=2, symbol="AAPL", action="BUY", qty=2.0,
            entry_price=314.0, entry_at="2026-06-05T15:00:00+00:00", source="manual",
            stop_loss=298.0, take_profit=351.0, status="OPEN")
    _insert(db, "paper_positions", order_id=3, intent_id=3, symbol="OLD", action="BUY", qty=1.0,
            entry_price=10.0, entry_at="2026-01-01T00:00:00+00:00", source="manual",
            stop_loss=9.0, take_profit=12.0, status="CLOSED")   # not OPEN → excluded
    d = watch_view.load_watch_data(db, price_fetcher=lambda s: {"ORCL": 230.0, "AAPL": 320.0})
    syms = [p["symbol"] for p in d["open_positions"]]
    assert syms == ["AAPL", "ORCL"]                # entry_at DESC
    aapl = d["open_positions"][0]
    assert aapl["current_price"] == 320.0
    assert aapl["unrealized_pnl_usd"] == pytest.approx((320 - 314) * 2)
    assert d["summary"]["open_count"] == 2
    assert d["live_prices_available"] is True


def test_recent_closes_window(tmp_path):
    db = _empty_db(tmp_path)
    _insert(db, "paper_outcomes", position_id=1, symbol="NVDA", source="m", action="BUY", qty=1,
            entry_price=100, exit_price=112, entry_at="2026-06-01T00:00:00+00:00",
            exit_at="2026-06-05T00:00:00+00:00", pnl_usd=12, pnl_pct=0.12, outcome="WIN", hold_days=4)
    _insert(db, "paper_outcomes", position_id=2, symbol="TSLA", source="m", action="BUY", qty=1,
            entry_price=100, exit_price=95, entry_at="2026-01-01T00:00:00+00:00",
            exit_at="2026-01-05T00:00:00+00:00", pnl_usd=-5, pnl_pct=-0.05, outcome="LOSS", hold_days=4)
    d = watch_view.load_watch_data(db, lookback_days=7, today="2026-06-06", price_fetcher=_NO_PRICES)
    recent = [r["symbol"] for r in d["recent_closes"]]
    assert recent == ["NVDA"]                       # TSLA close is >7d old
    assert d["summary"]["closed_count_total"] == 2  # lifetime count ignores the window
    assert d["autonomy"]["closed_n"] == 2


def test_activity_feed_merges_and_orders(tmp_path):
    db = _empty_db(tmp_path)
    _insert(db, "trade_intents", created_at="2026-06-05T13:00:00+00:00", symbol="MSFT", action="BUY",
            confidence=0.8, source="consensus", schema_version=1)
    _insert(db, "risk_decisions", decided_at="2026-06-05T13:00:01+00:00", intent_id=1,
            verdict="REJECTED", reason="Daily P&L exceeds max loss")
    _insert(db, "orders", placed_at="2026-06-05T13:05:00+00:00", intent_id=2, mode="paper",
            client_order_id="x1", status="SIMULATED", symbol="AAPL", action="BUY",
            qty=2.0, fill_qty=2.0, fill_price=314.0)
    d = watch_view.load_watch_data(db, today="2026-06-06", price_fetcher=_NO_PRICES)
    feed = d["activity_feed"]
    assert feed[0]["type"] == "FILL" and feed[0]["symbol"] == "AAPL"      # newest first
    rej = [e for e in feed if e["type"] == "GATE_REJECTED"][0]
    assert "max loss" in rej["reason"]


def test_mode_is_always_paper_and_live_orders_excluded(tmp_path):
    db = _empty_db(tmp_path)
    _insert(db, "orders", placed_at="2026-06-05T13:05:00+00:00", intent_id=1, mode="live",
            client_order_id="L1", status="SIMULATED", symbol="AAPL", action="BUY",
            qty=2.0, fill_qty=2.0, fill_price=314.0)
    d = watch_view.load_watch_data(db, today="2026-06-06", price_fetcher=_NO_PRICES)
    assert d["mode"] == "paper"
    assert all(e["type"] != "FILL" for e in d["activity_feed"])   # live fill never surfaced


def test_autonomy_bar_not_ready_without_qualitative_gates(tmp_path):
    db = _empty_db(tmp_path)
    # 30 closed but all losses → count gate passes, win-rate fails → NOT ready.
    for i in range(30):
        _insert(db, "paper_outcomes", position_id=i, symbol="X", source="m", action="BUY", qty=1,
                entry_price=100, exit_price=95, entry_at="2026-06-01T00:00:00+00:00",
                exit_at=f"2026-06-05T00:00:{i:02d}+00:00", pnl_usd=-5, pnl_pct=-0.05,
                outcome="LOSS", hold_days=4)
    d = watch_view.load_watch_data(db, today="2026-06-06", price_fetcher=_NO_PRICES)
    assert d["autonomy"]["closed_n"] == 30
    assert d["autonomy"]["closed_trades"]["pass"] is True
    assert d["autonomy"]["ready"] is False          # bar at 30/30 must NOT imply ready


# ── render_watch_html ────────────────────────────────────────────────────────

def test_render_contains_panels_and_poll(tmp_path):
    d = watch_view.load_watch_data(_empty_db(tmp_path), price_fetcher=_NO_PRICES)
    html = watch_view.render_watch_html(d)
    for needle in ("Open Positions", "Recent Closes", "Activity", "/watch/data",
                   "NOT FINANCIAL ADVICE", "of 30"):
        assert needle in html, needle


def test_render_empty_states(tmp_path):
    d = watch_view.load_watch_data(_empty_db(tmp_path), price_fetcher=_NO_PRICES)
    html = watch_view.render_watch_html(d)
    assert "No open positions" in html
    assert "No closed trades" in html
