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
    assert "GESH — Diversification Scenario Lab" in html
    assert "NOT FINANCIAL ADVICE" in html


def test_render_never_raises_on_minimal():
    html = iv.render_invest_html(iv.load_invest_data("/nonexistent/path.json"))
    assert "<!doctype html>" in html


def _radar_with_resize_state():
    return {
        "as_of": "2026-06-07T12:00:00+00:00", "mode": "SWAP_ONLY", "deployable_cash": 0.0,
        "open_slots": 3, "new_names_to_add": 0, "excluded_holdings": ["NVDA"],
        "existing_sector_weights": {"Technology": 0.23}, "candidates": [],
        "swap_suggestions": [], "diagnostics": {},
        "resize_state": {
            "survivors": [
                {"symbol": "KO", "company_name": "KO", "sector": "Consumer Defensive",
                 "price": 60.0, "vol": 0.02, "composite_z": 0.6, "factor_scores": {},
                 "timing": {"confluence_fraction": 0.5}, "max_corr_to_holdings": 0.2,
                 "correlation_ok": True, "consensus_verdict": "BUY", "analyst_upside": 15,
                 "num_analysts": 20},
                {"symbol": "XOM", "company_name": "XOM", "sector": "Energy",
                 "price": 110.0, "vol": 0.025, "composite_z": 0.4, "factor_scores": {},
                 "timing": {"confluence_fraction": 0.4}, "max_corr_to_holdings": 0.2,
                 "correlation_ok": True, "consensus_verdict": "HOLD", "analyst_upside": 8,
                 "num_analysts": 12},
            ],
            "sector_value": {"Technology": 8000.0}, "total": 35000.0,
            "open_slots": 3, "max_stock_per_trade": 5000.0,
        },
    }


def test_load_what_if_cash_sizes_and_keeps_state_server_side(tmp_path):
    path = _write(tmp_path, {"diversification_candidates": _radar_with_resize_state()})
    d = iv.load_invest_data(path, what_if_cash=9000.0)
    assert d["mode"] == "BUY"
    assert d["what_if_cash"] == 9000.0
    assert len(d["candidates"]) >= 1 and all(c.get("actionable") for c in d["candidates"])
    blob = json.dumps(d)
    assert "resize_state" not in blob          # server-side sizing state never leaks
    assert "sector_value" not in blob and "withdrawable_amount" not in blob


def test_load_without_what_if_is_live(tmp_path):
    path = _write(tmp_path, {"diversification_candidates": _radar_with_resize_state()})
    d = iv.load_invest_data(path)
    assert d["what_if_cash"] is None
    assert d["mode"] == "SWAP_ONLY"


def test_render_includes_what_if_slider():
    html = iv.render_invest_html(iv.load_invest_data("/nonexistent/path.json"))
    assert 'id="wi-slider"' in html and "what_if_cash=" in html


def test_what_if_cash_sanitizes_bad_input(tmp_path):
    import json as _json
    path = _write(tmp_path, {"diversification_candidates": _radar_with_resize_state()})
    # inf / nan / negative must not produce a what-if result or invalid JSON.
    for bad in (float("inf"), float("nan"), -100.0):
        d = iv.load_invest_data(path, what_if_cash=bad)
        blob = _json.dumps(d)            # must be valid JSON (no Infinity/NaN tokens)
        assert "Infinity" not in blob and "NaN" not in blob
        assert d["what_if_cash"] is None  # rejected → live view
    # a sane value still works
    assert iv.load_invest_data(path, what_if_cash=9000.0)["what_if_cash"] == 9000.0


def test_render_has_prominent_disclaimer_banner():
    html = iv.render_invest_html(iv.load_invest_data("/nonexistent/path.json"))
    assert "NOT FINANCIAL ADVICE" in html
    assert 'id="advisory-banner"' in html   # prominent top banner, not just the footer


def test_render_frames_outputs_as_hypothetical_not_personalized_advice():
    html = iv.render_invest_html(iv.load_invest_data("/nonexistent/path.json"))
    assert "Diversification Scenario Lab" in html
    assert "HYPOTHETICAL" in html
    assert "not suitability-checked" in html
    assert "Model allocation" in html
    assert "Scenario alternatives" in html
    assert "where should NEW cash go?" not in html
    assert ">Suggest<" not in html
    assert "Swap ideas" not in html
