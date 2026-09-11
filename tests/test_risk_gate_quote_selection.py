"""Astra R5: DATA_FRESH must age the selected symbol's quote, not the snapshot.

A fresh container/snapshot as_of cannot launder a stale, missing, malformed,
or excessively future-dated per-symbol quote. Clock skew is a code constant
(60s), not a deployment setting.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from trading import risk_gate as rg
from trading.config import RiskConfig
from trading.contract import TradeIntent

pytestmark = pytest.mark.unit

# Wednesday 2026-06-03 16:00 UTC == 12:00 EDT, inside market hours.
FROZEN_NOW = _dt.datetime(2026, 6, 3, 16, 0, 0, tzinfo=_dt.timezone.utc)
# Reviewer-agreed future-quote bound; not an env knob.
EXPECTED_FUTURE_SKEW_S = 60


class _FrozenDateTime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.astimezone(tz) if tz else FROZEN_NOW.replace(tzinfo=None)


def _cfg(**overrides) -> RiskConfig:
    defaults = dict(
        kill_switch_path="/tmp/__gesh_kill_does_not_exist__",
        market_open_hhmm="09:30",
        market_close_hhmm="16:00",
        market_timezone="America/New_York",
        min_confidence=0.5,
        max_position_pct=0.05,
        max_orders_per_day=10,
        min_cash_buffer_usd=100.0,
        max_daily_loss_pct=0.05,
        max_price_age_seconds=300,
        max_price_age_seconds_paper=1800,
        paper_mode=False,
        market_regime_gate=False,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


def _equity() -> dict:
    return {
        "cash": 50_000.0,
        "portfolio_equity": 100_000.0,
        "adjusted_equity_previous_close": 100_000.0,
    }


def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat()


def _snap(
    *,
    as_of: str,
    holdings=None,
    watchlist=None,
    quote_provenance=None,
    quotes=None,
    quote_as_of=None,
    fetched_at=None,
    source=None,
) -> dict:
    payload = {"as_of": as_of, "equity_summary": _equity()}
    if holdings is not None:
        payload["holdings"] = holdings
    if watchlist is not None:
        payload["watchlist"] = watchlist
    if quote_provenance is not None:
        payload["quote_provenance"] = quote_provenance
    if quotes is not None:
        payload["quotes"] = quotes
    if quote_as_of is not None:
        payload["quote_as_of"] = quote_as_of
    if fetched_at is not None:
        payload["fetched_at"] = fetched_at
    if source is not None:
        payload["source"] = source
    return payload


def _buy(symbol: str = "MSTR") -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        action="BUY",
        confidence=0.9,
        source="consensus",
        target_position_pct=0.02,
    )


@pytest.fixture(autouse=True)
def _patch_seams(monkeypatch):
    monkeypatch.setattr(rg, "datetime", _FrozenDateTime)
    monkeypatch.setattr(rg, "log_intent", lambda intent: 101)
    monkeypatch.setattr(rg, "log_decision", lambda *a, **k: 202)
    monkeypatch.setattr(rg, "count_orders_today", lambda *a, **k: 0)


def _fresh_container() -> str:
    return _iso(FROZEN_NOW)


def _stale(seconds: int = 3600) -> str:
    return _iso(FROZEN_NOW - _dt.timedelta(seconds=seconds))


def _future(seconds: int) -> str:
    return _iso(FROZEN_NOW + _dt.timedelta(seconds=seconds))


# ── laundering ────────────────────────────────────────────────────────────────


def test_fresh_snapshot_as_of_cannot_launder_stale_holding_quote():
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": _stale(3600)}],
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is False
    assert verdict.gates["DATA_FRESH"] is False
    assert "exceeds max" in verdict.reason


def test_fresh_snapshot_as_of_cannot_launder_stale_quote_provenance():
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[{"symbol": "MSTR", "price": 100.0}],
        quote_provenance={
            "MSTR": {
                "quote_as_of": _stale(3600),
                "provider": "robinhood_mcp",
                "timestamp_basis": "venue_last_trade_time",
                "session": "sess-test",
            }
        },
        fetched_at=_fresh_container(),
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is False
    assert verdict.gates["DATA_FRESH"] is False
    assert "exceeds max" in verdict.reason


def test_missing_per_symbol_timestamp_on_holding_fails_closed():
    """Holding exists but carries no quote time — container as_of is not a substitute."""
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[{"symbol": "MSTR", "price": 100.0, "equity": 1000.0}],
        watchlist=[{"symbol": "SPY", "updated_at": _fresh_container()}],
        source="robinhood_live",
    )
    verdict = rg.evaluate(_buy("MSTR"), snap, _cfg())
    assert verdict.approved is False
    assert verdict.gates["DATA_FRESH"] is False
    assert "Quote age unknown" in verdict.reason


def test_malformed_per_symbol_quote_fails_closed():
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": "N/A"}],
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is False
    assert verdict.gates["DATA_FRESH"] is False
    assert "Quote age unknown" in verdict.reason


# ── future / skew ─────────────────────────────────────────────────────────────


def test_excessively_future_quote_rejected():
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[
            {"symbol": "MSTR", "price": 100.0, "quote_as_of": _future(EXPECTED_FUTURE_SKEW_S + 1)}
        ],
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is False
    assert verdict.gates["DATA_FRESH"] is False
    assert "future" in verdict.reason.lower()


def test_future_quote_within_skew_accepted():
    snap = _snap(
        as_of=_stale(3600),
        holdings=[
            {"symbol": "MSTR", "price": 100.0, "quote_as_of": _future(EXPECTED_FUTURE_SKEW_S)}
        ],
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is True
    assert verdict.gates["DATA_FRESH"] is True


# ── mixed freshness ───────────────────────────────────────────────────────────


def test_mixed_freshness_stale_symbol_rejected_fresh_sibling_approved():
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[
            {"symbol": "NVDA", "price": 100.0},
            {"symbol": "MU", "price": 50.0},
        ],
        quote_provenance={
            "NVDA": {
                "quote_as_of": _stale(3600),
                "provider": "robinhood_mcp",
                "timestamp_basis": "updated_at",
                "session": "s1",
            },
            "MU": {
                "quote_as_of": _iso(FROZEN_NOW),
                "provider": "robinhood_mcp",
                "timestamp_basis": "updated_at",
                "session": "s1",
            },
        },
        fetched_at=_fresh_container(),
        source="robinhood_mcp",
    )
    stale = rg.evaluate(_buy("NVDA"), snap, _cfg())
    fresh = rg.evaluate(_buy("MU"), snap, _cfg())
    assert stale.approved is False
    assert stale.gates["DATA_FRESH"] is False
    assert fresh.approved is True
    assert fresh.gates["DATA_FRESH"] is True


# ── boundaries ────────────────────────────────────────────────────────────────


def test_age_at_max_boundary_passes():
    snap = _snap(
        as_of=_stale(3600),
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": _stale(300)}],
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(_buy(), snap, _cfg(max_price_age_seconds=300))
    assert verdict.approved is True
    assert verdict.gates["DATA_FRESH"] is True


def test_age_one_second_over_max_rejected():
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": _stale(301)}],
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(_buy(), snap, _cfg(max_price_age_seconds=300))
    assert verdict.approved is False
    assert verdict.gates["DATA_FRESH"] is False
    assert "exceeds max" in verdict.reason


# ── timezone / DST ────────────────────────────────────────────────────────────


def test_timezone_offset_equivalent_to_utc():
    """12:00 EDT == 16:00 UTC at frozen now — age 0, not a TZ parse miss.

    Transport as_of is stale on purpose so the gate must use the holding stamp.
    """
    snap = _snap(
        as_of=_stale(3600),
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": "2026-06-03T12:00:00-04:00"}],
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is True
    assert verdict.gates["DATA_FRESH"] is True


def test_dst_spring_forward_offsets_that_name_the_same_instant():
    """Monday after 2026-03-08 spring-forward: -05:00 and -04:00 naming 16:00Z.

    The transition itself is a Sunday (market closed). A weekday instant that
    can still be labelled EST or EDT must not be rejected as a parse miss.
    """
    frozen = _dt.datetime(2026, 3, 9, 16, 0, 0, tzinfo=_dt.timezone.utc)

    class _DstClock(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    rg.datetime = _DstClock
    transport = "2026-03-09T12:00:00+00:00"  # 4h stale vs frozen now
    snap = _snap(
        as_of=transport,
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": "2026-03-09T11:00:00-05:00"}],
        source="robinhood_mcp",
    )
    est = rg.evaluate(_buy(), snap, _cfg())
    snap_edt = _snap(
        as_of=transport,
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": "2026-03-09T12:00:00-04:00"}],
        source="robinhood_mcp",
    )
    edt = rg.evaluate(_buy(), snap_edt, _cfg())
    assert est.approved is True and est.gates["DATA_FRESH"] is True
    assert edt.approved is True and edt.gates["DATA_FRESH"] is True


# ── retry after refresh ───────────────────────────────────────────────────────


def test_retry_after_refresh_passes():
    stale_snap = _snap(
        as_of=_fresh_container(),
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": _stale(3600)}],
        source="robinhood_mcp",
    )
    first = rg.evaluate(_buy(), stale_snap, _cfg())
    assert first.approved is False
    assert first.gates["DATA_FRESH"] is False

    refreshed = _snap(
        as_of=_fresh_container(),
        holdings=[{"symbol": "MSTR", "price": 101.0, "quote_as_of": _iso(FROZEN_NOW)}],
        source="robinhood_mcp",
    )
    second = rg.evaluate(_buy(), refreshed, _cfg())
    assert second.approved is True
    assert second.gates["DATA_FRESH"] is True


# ── paper vs live age policy ──────────────────────────────────────────────────


def test_live_mode_rejects_quote_older_than_300s():
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": _stale(400)}],
        source="robinhood_mcp",
    )
    # Container is fresh; the selected MSTR quote is 400s old. Live cap is 300s.
    verdict = rg.evaluate(_buy(), snap, _cfg(paper_mode=False, max_price_age_seconds=300))
    assert verdict.approved is False
    assert verdict.gates["DATA_FRESH"] is False


def test_paper_mode_accepts_quote_within_1800s(monkeypatch):
    monkeypatch.setattr(rg, "paper_realized_pnl_usd_today", lambda: 0.0)
    snap = _snap(
        as_of=_stale(7200),
        holdings=[{"symbol": "MSTR", "price": 100.0, "quote_as_of": _stale(400)}],
        source="robinhood_mcp",
    )
    verdict = rg.evaluate(
        _buy(),
        snap,
        _cfg(paper_mode=True, max_price_age_seconds=300, max_price_age_seconds_paper=1800),
    )
    assert verdict.approved is True
    assert verdict.gates["DATA_FRESH"] is True


# ── watchlist same-symbol stamp ───────────────────────────────────────────────


def test_watchlist_updated_at_is_the_selected_quote_for_that_symbol():
    snap = _snap(
        as_of=_fresh_container(),
        holdings=[],
        watchlist=[{"symbol": "MSTR", "last_trade_price": 100.0, "updated_at": _stale(3600)}],
        source="robinhood_live",
    )
    verdict = rg.evaluate(_buy("MSTR"), snap, _cfg())
    assert verdict.approved is False
    assert verdict.gates["DATA_FRESH"] is False
    assert "exceeds max" in verdict.reason


# ── no per-symbol row: container as_of remains the selected quote ─────────────


def test_container_as_of_still_used_when_no_per_symbol_row():
    """Daily-chain substitution and existing unit snapshots with only as_of."""
    snap = _snap(as_of=_iso(FROZEN_NOW))
    verdict = rg.evaluate(_buy(), snap, _cfg())
    assert verdict.approved is True
    assert verdict.gates["DATA_FRESH"] is True
