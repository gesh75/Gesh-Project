"""Guards for the DCF modules now that they live in the PUBLISHER repo.

Why they moved (2026-08-07). `dcf_inputs.py` / `dcf_fair_value.py` were written in
`gesh-project-trading`, but `engine_results.json` — the contract agentic-rh reads via
`config.py:18` — is published from **this** repo (`server.py`, `run_engines2.py`,
`snapshot_state.py`). `gesh-project-trading` has no `engine_results.json` at all, so
computing a DCF there could never reach the consumer. The integration brief's diagram
named the wrong repo; this move is the correction.

These tests are deliberately about the CONTRACT, not the arithmetic:

  1. The modules import and resolve `vendor.*` from the flat layout — the relocation
     rewrote `trading.dcf_inputs` -> `dcf_inputs` and `trading.vendor.dcf_model` ->
     `vendor.dcf_model`, and a missed rewrite would only surface at runtime, inside a
     lazy import, on the day a DCF is actually requested.
  2. `available` stays a REFUSAL, never a fabrication. Banks (BAC/SCHW) have no
     meaningful EBITDA/capex/NWC; the fetcher must report what is missing rather than
     invent a figure. A DCF built on an invented capex reads as a real valuation
     forever after — rules #45/#51/#59.
  3. A refusal must never be readable as a valuation of zero. "No opinion" and
     "worthless" are opposite claims, and the consumer will apply a Kelly penalty to
     one of them.

Offline: nothing here fetches. `fetch_dcf_inputs` is never called with a live symbol.
"""
from __future__ import annotations

import dcf_fair_value
import dcf_inputs
from dcf_inputs import MIN_PERIODS, DCFInputs

# ── 1. the relocation itself ─────────────────────────────────────────────────

def test_modules_import_from_the_flat_layout():
    assert dcf_inputs.__name__ == "dcf_inputs"
    assert dcf_fair_value.__name__ == "dcf_fair_value"


def test_no_stale_trading_package_references_survive_the_move():
    # A leftover `from trading.…` would raise only inside a lazy import, on the day a
    # DCF is first requested — i.e. in production, not here.
    import inspect
    for mod in (dcf_inputs, dcf_fair_value):
        src = inspect.getsource(mod)
        assert "from trading." not in src, f"{mod.__name__} still imports from trading.*"
        assert "import trading." not in src, f"{mod.__name__} still imports trading.*"


def test_vendored_cookbook_resolves_from_this_repo():
    # dcf_fair_value imports these lazily, so a broken path hides until runtime.
    from vendor.dcf_model import DCFModel
    from vendor.sensitivity_analysis import SensitivityAnalyzer
    assert DCFModel is not None and SensitivityAnalyzer is not None


def test_vendored_code_keeps_its_licence_and_notice():
    # MIT permits reuse *with attribution* — the licence must travel with the copy.
    from pathlib import Path
    vendor = Path(dcf_fair_value.__file__).parent / "vendor"
    assert (vendor / "LICENSE-cookbooks").is_file()
    assert (vendor / "NOTICE.md").is_file()


# ── 2. the refusal contract ──────────────────────────────────────────────────

def test_empty_inputs_are_not_available():
    assert DCFInputs(symbol="BAC").available is False


def test_an_error_makes_it_unavailable_even_if_figures_are_present():
    full = dict(
        years=(2023, 2024, 2025),
        revenue=(100.0, 110.0, 120.0),
        ebitda=(20.0, 22.0, 24.0),
        capex=(5.0, 5.0, 5.0),
        nwc=(1.0, 1.0, 1.0),
        tax_rate=0.21,
        shares_outstanding=100.0,
        net_debt=0.0,
    )
    assert DCFInputs(symbol="X", **full).available is True
    assert DCFInputs(symbol="X", error="SNI-blocked", **full).available is False


def test_a_missing_list_makes_it_unavailable():
    # the bank case: EBITDA/capex/NWC have no meaning for a lender
    d = DCFInputs(
        symbol="BAC",
        years=(2023, 2024, 2025),
        revenue=(100.0, 110.0, 120.0),
        missing=("ebitda", "capex", "nwc"),
    )
    assert d.available is False
    assert "ebitda" in d.missing


def test_too_few_periods_is_a_refusal_not_an_extrapolation():
    d = DCFInputs(
        symbol="X",
        years=(2025,),
        revenue=(120.0,),
        ebitda=(24.0,),
        capex=(5.0,),
        nwc=(1.0,),
        tax_rate=0.21,
        shares_outstanding=100.0,
        net_debt=0.0,
    )
    assert len(d.revenue) < MIN_PERIODS
    assert d.available is False


def test_a_refusal_never_presents_itself_as_a_zero_valuation():
    # "no opinion" and "worth nothing" are opposite claims. A consumer that reads a
    # refusal as fair_value=0 would treat every bank as infinitely overvalued and
    # apply the severe-overvaluation Kelly penalty to the whole sector at once.
    d = DCFInputs(symbol="BAC", missing=("ebitda",))
    assert d.available is False
    assert d.tax_rate is None
    assert d.shares_outstanding is None
    assert d.net_debt is None
    assert d.revenue == ()
