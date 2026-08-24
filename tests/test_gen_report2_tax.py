"""Source-level guards for the tax-harvest panel in gen_report2.py.

gen_report2.py executes its pipeline on import, so these tests read source
rather than running the script (same pattern as test_degraded_provenance).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wash_warnings_are_not_sliced_in_the_report():
    """Today's live book had 10 purchase-side warnings; a `[:8]` slice dropped
    KO and BABA — the two 1-day buys with 29d remaining, i.e. the longest wash
    windows. Silent truncation is the same false-clear this panel exists to
    prevent.
    """
    src = (REPO_ROOT / "gen_report2.py").read_text()
    assert "_wash_warn[:8]" not in src
    assert "for _w in _wash_warn:" in src


def test_empty_candidates_do_not_claim_all_positions_profitable():
    src = (REPO_ROOT / "gen_report2.py").read_text()
    assert "all positions profitable" not in src.lower()
    assert "$500 harvest threshold" in src


def test_report_does_not_default_missing_cvar_or_garch_to_zero():
    """Missing CVaR/GARCH used to render as a confident 0.0% (F-01 / F-02)."""
    src = (REPO_ROOT / "gen_report2.py").read_text()
    assert 'monte.get("cvar_95_pct", 0)' not in src
    assert 'monte.get("var_95_pct", 0)' not in src
    assert 'monte.get("prob_profit_pct", 0)' not in src
    assert 'monte.get("median", 0)' not in src
    assert 'garch_annual_pct", 0) or 0' not in src
    assert "format_optional_percent" in src
    assert "format_optional_usd" in src
    assert "cvar_95_pct(monte)" in src
    assert "var_95_status" in src


def test_lot_coverage_caveat_never_reads_as_all_clear():
    """An MCP outage must render as 'unknown, not clear' — never as no wash risk."""
    src = (REPO_ROOT / "gen_report2.py").read_text()
    assert "wash-sale status unknown, not clear" in src
    assert "lot_coverage" in src


def test_engines_lot_enrichment_is_best_effort():
    """A lot-fetch failure must never drop the base harvest report."""
    src = (REPO_ROOT / "run_engines2.py").read_text()
    assert "from tax_lots_enrich import enrich" in src
    assert "lot enrichment skipped (non-fatal)" in src


def test_paper_gate_wires_reviewer_through_enabled_flag():
    """main() must honor GESH_BROKER_REVIEW rather than hard-wiring a no-op."""
    src = (REPO_ROOT / "run_paper_gate2.py").read_text()
    assert "rh_order_review.enabled()" in src
    assert "reviewer=_reviewer" in src
    assert "review_blocks=_review_blocks" in src
