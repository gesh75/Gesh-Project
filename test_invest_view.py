"""
test_invest_view.py — the /invest panel data + render contract.

Mirrors test_watch_view.py: read-only, honest empty states, PII-safe, XSS-safe.
"""
from __future__ import annotations

import json

import invest_view as iv


def _write(tmp_path, eng: dict) -> str:
    p = tmp_path / "engine_results.json"
    p.write_text(json.dumps(eng))
    return str(p)


def test_load_missing_file_is_empty_contract(tmp_path):
    d = iv.load_invest_data(str(tmp_path / "nope.json"))
    assert d["mode"] == "NO_ACTION"
    assert d["candidates"] == []
    assert d["stale"] is True


def test_load_no_radar_key_is_honest_empty(tmp_path):
    path = _write(tmp_path, {"holdings": [], "equity_summary": {}})
    d = iv.load_invest_data(path)
    assert d["mode"] == "NO_ACTION"
    assert "10:30" in d["note"]
    assert d["candidates"] == []


def test_load_surfaces_radar(tmp_path):
    radar = {
        "as_of": "2026-06-06T12:00:00+00:00", "mode": "SWAP_ONLY", "deployable_cash": 0.0,
        "open_slots": 0, "new_names_to_add": 0, "excluded_holdings": ["NVDA", "MU"],
        "existing_sector_weights": {"Technology": 0.71, "Energy": 0.05},
        "candidates": [], "swap_suggestions": [{"trim_symbol": "IONQ", "fund_new_symbol": "KO",
                                                "rationale": "trim tech"}],
        "diagnostics": {"data_warnings": []},
    }
    path = _write(tmp_path, {"diversification_candidates": radar})
    d = iv.load_invest_data(path)
    assert d["mode"] == "SWAP_ONLY"
    assert d["existing_sector_weights"]["Technology"] == 0.71
    assert d["swap_suggestions"][0]["trim_symbol"] == "IONQ"


def test_load_never_leaks_pii(tmp_path):
    # Even if a candidate dict carried PII, the whitelist must drop it.
    radar = {
        "as_of": "2026-06-06T12:00:00+00:00", "mode": "BUY", "deployable_cash": 9500.0,
        "open_slots": 3, "new_names_to_add": 3, "excluded_holdings": ["NVDA"],
        "existing_sector_weights": {"Technology": 0.4},
        "candidates": [{"symbol": "KO", "sector": "Consumer", "composite_score": 80,
                        "recommended_usd": 2400, "recommended_shares": 40,
                        "withdrawable_amount": 12345.67, "average_buy_price": 58.1}],
        "swap_suggestions": [], "diagnostics": {},
    }
    path = _write(tmp_path, {"diversification_candidates": radar,
                             "equity_summary": {"withdrawable_amount": 12345.67}})
    blob = json.dumps(iv.load_invest_data(path))
    assert "withdrawable_amount" not in blob
    assert "average_buy_price" not in blob


def test_render_escapes_xss_and_breaks_script():
    data = {
        "as_of": "2026-06-06T12:00:00+00:00", "mode": "BUY", "deployable_cash": 9500.0,
        "open_slots": 3, "new_names_to_add": 3, "excluded_holdings": [],
        "existing_sector_weights": {"Tech</script>": 0.7},
        "candidates": [{"symbol": "<img src=x onerror=alert(1)>", "sector": "X",
                        "composite_score": 80, "recommended_usd": 2400,
                        "recommended_shares": 40, "rationale": "</script><script>evil()</script>"}],
        "swap_suggestions": [], "diagnostics": {},
    }
    html = iv.render_invest_html(data)
    # The real security property of this render pattern: an attacker-controlled
    # "</script>" in the embedded JSON payload CANNOT terminate the <script> block —
    # it is neutralised to "<\\/script>". (An inert "<img ...>" inside the JS string
    # is HTML-escaped again at runtime by esc() before innerHTML.)
    assert "evil()</script>" not in html          # raw break-out neutralised...
    assert "<\\/script>" in html                   # ...into the escaped form
    assert "const esc=" in html                    # runtime escaper is wired in
    assert "GESH — Diversification Radar" in html
    assert "NOT FINANCIAL ADVICE" in html


def test_render_never_raises_on_minimal():
    html = iv.render_invest_html(iv.load_invest_data("/nonexistent/path.json"))
    assert "<!doctype html>" in html
