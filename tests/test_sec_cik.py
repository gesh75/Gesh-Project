"""SEC CIK resolution must work with the 2025+ EDGAR atom feed shape."""
from __future__ import annotations

import sec_agent as sa


def test_get_cik_parses_atom_cik_element(monkeypatch):
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <cik>0001045810</cik>
    </feed>"""

    class Resp:
        status_code = 200
        text = xml

    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: Resp())
    assert sa._get_cik("NVDA") == "0001045810"


def test_get_cik_skips_etfs():
    assert sa._get_cik("QQQ") is None
    assert sa._get_cik("EEM") is None


def test_parse_edgar_dt_handles_offset():
    dt = sa._parse_edgar_dt("2026-07-02T09:23:16-04:00")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 7


def test_finalize_fundamental_fallback():
    import fundamental_agent as fa
    valid = [{
        "symbol": "NVDA", "name": "NVIDIA", "sector": "Tech",
        "revenue_growth": 40, "earnings_growth": 30, "analyst_upside": 12,
        "insider_signal": "BUYING", "price": 100, "ret_1m": 5,
    }]
    out = fa._finalize_fundamental_result({"top_opportunities": []}, valid)
    assert len(out["top_opportunities"]) >= 1
    assert out["top_opportunities"][0]["symbol"] == "NVDA"