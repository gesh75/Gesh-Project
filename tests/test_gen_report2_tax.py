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
