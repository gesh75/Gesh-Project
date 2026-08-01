"""Masking-consistency audit for the customer equity-history surfaces.

External audit (Kimi, 2026-07-18) claim-2b said `/api/portfolio/history` and
`/api/ecg` "leak raw, unnormalized totals". VERDICT: refuted — both run through
the customer-view middleware's `_scale_money`, which scales their dollar ARRAYS
(`series`, nested `equity_history`) by the model-book factor while leaving
percentages and counts raw. The numbers the audit reconstructed were the
model-scaled values, not the real book.

These tests LOCK that in: a future refactor that drops `series`/`equity_history`
from `_MONEY_LIST_KEYS`, or marks either route mask-exempt, would leak the owner's
real equity to every customer — and must fail here.
"""
import os

import pytest

os.environ.setdefault("GESH_AUTH_ENABLED", "1")
os.environ.setdefault("GESH_AUTH_SECRET", "test-secret")
os.environ.setdefault("CUSTOMER_MODEL_BOOK_USD", "100000")

import server  # noqa: E402

F = 2.5  # a model/real factor distinct from 1.0 so "unscaled" is detectable


def test_portfolio_history_series_is_scaled():
    """`/api/portfolio/history` → {series: [equity...]} — the array must scale."""
    out = server._scale_money({"status": "ok", "series": [30000.0, 31000.0, 29500.0],
                               "points": 3}, F)
    assert out["series"] == [75000.0, 77500.0, 73750.0]        # each × F, not raw
    assert out["points"] == 3                                  # a count, not dollars → unchanged


def test_ecg_equity_history_is_scaled_and_percentages_are_not():
    """`/api/ecg` → data.states[].equity_history is dollars (scale); drawdown_pct
    and snapshots_available are not (leave raw)."""
    ecg = {"status": "ok", "data": {
        "portfolio_gate": "HEALTHY", "portfolio_message": "ok",
        "states": [{"symbol": "NVDA", "state": "HEALTHY", "drawdown_pct": -12.5,
                    "loss_streak": 0, "snapshots_available": 30,
                    "equity_history": [9000.0, 9300.0, 8800.0], "message": "held"}]}}
    st = server._scale_money(ecg, F)["data"]["states"][0]
    assert st["equity_history"] == [22500.0, 23250.0, 22000.0]  # dollars scaled
    assert st["drawdown_pct"] == -12.5                          # a % — NEVER scaled
    assert st["snapshots_available"] == 30                      # a count — NEVER scaled
    assert st["loss_streak"] == 0


def test_reconstruction_yields_model_book_not_real_total():
    """The privacy contract: a customer reconstructing total from a scaled point
    lands on the MODEL book, never the owner's real total. (factor = model/real,
    so raw_point × factor / (raw_point/real_total) == model_total.)"""
    real_total, model = 34772.0, 100000.0
    factor = model / real_total
    raw_point, weight = 9000.0, 9000.0 / real_total            # this point's real weight
    scaled = server._scale_money({"series": [raw_point]}, factor)["series"][0]
    assert round(scaled / weight, 0) == model                  # → 100000, not 34772


@pytest.mark.parametrize("route", ["/api/portfolio/history", "/api/ecg"])
def test_routes_are_normalized_not_exempt(route):
    """Any customer-reachable equity route must pass through normalization.

    Portfolio history is additionally owner-authenticated at the route itself;
    retaining normalization here is defense in depth if that policy changes.
    """
    assert not any(route == p or route.startswith(p + "/") for p in server._MASK_EXEMPT_PREFIXES)


def test_money_list_keys_cover_the_known_dollar_arrays():
    """Guard: the bare-number dollar arrays these surfaces emit must all be
    enrolled, or the recursive scaler returns them raw (they have no dict key on
    the elements to trigger scalar scaling)."""
    required = {"series", "equity_history", "equity_series", "equity_curve"}
    assert required <= server._MONEY_LIST_KEYS


def test_bare_dollar_array_under_unknown_key_would_leak_is_documented():
    """Documents the failure mode: a bare-number array under a key NOT in
    _MONEY_LIST_KEYS is returned RAW — this asserts the current guarantee so a new
    dollar array added under a fresh key is a conscious decision, not a silent leak."""
    leaked = server._scale_money({"mystery_totals": [50000.0]}, F)
    assert leaked["mystery_totals"] == [50000.0]              # RAW — proves the risk is real
    # …which is exactly why series/equity_history ARE enrolled (test above).
