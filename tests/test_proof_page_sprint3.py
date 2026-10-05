"""Sprint 3 (C-02..C-04, items 12-13): statistical honesty on /track-record."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_proof_page_gate import _make_db, _stub_gate

import proof_page

NOTE = "DSR over K_eff=11.5 effective variants (empirical trial_sr_std=0.040, SR0=0.080, from registry)"

# 10 engine closes: mixed returns, positive mean (exit-100)/100
ENGINE_MIXED = [
    {"exit": e, "outcome": "WIN" if e > 100 else "LOSS", "source": "consensus"}
    for e in (112, 95, 108, 93, 115, 96, 104, 91, 110, 98)
]
MANUAL = [{"exit": 130, "outcome": "WIN", "source": "manual"}] * 4


def _criteria():
    return {
        "prob_sharpe": {"value": 0.81, "required": 0.9, "pass": False, "note": "x"},
        "deflated_sharpe": {"value": 0.54, "required": 0.95, "pass": False, "note": NOTE},
    }


@pytest.fixture
def registry_home(tmp_path, monkeypatch):
    home = tmp_path / "trading_home"
    home.mkdir()
    (home / "strategy_variant_registry.json").write_text(json.dumps({"n_trials_raw": 240}))
    monkeypatch.setattr(proof_page, "_trading_home", lambda: Path(home))
    return home


def _load(tmp_path, monkeypatch, rows=None, *, closes=10, ok=False, today="2026-10-04"):
    monkeypatch.setattr(proof_page, "_import_gate_status_fn",
                        lambda: _stub_gate(closes, ok=ok, criteria=_criteria()))
    db = _make_db(tmp_path, rows if rows is not None else ENGINE_MIXED + MANUAL)
    return proof_page.load_track_record(db, today=today)


# ── C-02: net-of-cost beside gross ───────────────────────────────────────────
def test_engine_stats_carry_stress_net_figures(tmp_path, monkeypatch, registry_home):
    eng = _load(tmp_path, monkeypatch)["engine"]
    assert eng["net_of_20bp_usd"] == pytest.approx(
        eng["realized_pnl_usd"] - eng["gross_entry_notional_usd"] * 0.002, abs=0.01)
    assert eng["net_of_50bp_usd"] == pytest.approx(
        eng["realized_pnl_usd"] - eng["gross_entry_notional_usd"] * 0.005, abs=0.01)


def test_headline_shows_gross_and_stress_net(tmp_path, monkeypatch, registry_home):
    html = proof_page.render_html(_load(tmp_path, monkeypatch))
    m = re.search(r'data-k="headline-net-20bp-usd"[^>]*>([+-])\$([\d,]+\.\d\d)', html)
    assert m, "net-of-20bp figure missing from the headline"
    assert 'data-k="headline-net-50bp-usd"' in html
    assert "headline-realized-pnl-usd" in html  # gross still shown beside it


def test_methodology_no_longer_says_no_net_figure(tmp_path, monkeypatch, registry_home):
    html = proof_page.render_html(_load(tmp_path, monkeypatch))
    assert "No net-of-cost figure is published" not in html
    block = html[html.index('id="methodology-limits"'):]
    block = block[: block.index("</ul>")]
    assert "Gross vs. net:" in block
    assert "stress" in block.lower()


# ── C-03: gate wording is simulated-only ─────────────────────────────────────
def test_gate_met_banner_is_prefixed_simulated_only(tmp_path, monkeypatch, registry_home):
    rows = [{"exit": 110, "outcome": "WIN", "source": "consensus"}] * 100
    monkeypatch.setattr(proof_page, "_import_gate_status_fn",
                        lambda: _stub_gate(100, ok=True, criteria=_criteria()))
    db = _make_db(tmp_path, rows)
    html = proof_page.render_html(proof_page.load_track_record(db, today="2026-10-04"))
    i = html.index("Autonomy gate met")
    assert "Simulated paper only" in html[max(0, i - 160): i]
    assert "not a guarantee" in html[i - 160: i + 400]


def test_gate_note_is_always_present(tmp_path, monkeypatch, registry_home):
    html = proof_page.render_html(_load(tmp_path, monkeypatch))
    assert 'data-k="gate-disclaimer"' in html
    assert "not evidence of future profit" in html  # one unwrapped phrase


# ── C-04: calendar days are labelled ─────────────────────────────────────────
def test_day_counter_is_labelled_calendar_days(tmp_path, monkeypatch, registry_home):
    html = proof_page.render_html(_load(tmp_path, monkeypatch, today="2026-10-04"))
    assert re.search(r"calendar day \d+ of \d+ since launch", html)
    assert not re.search(r"\bDay \d+ of \d+", html)


# ── item 12: minimum-sample panel ────────────────────────────────────────────
def test_min_sample_uses_engine_cohort_only(tmp_path, monkeypatch, registry_home):
    d = _load(tmp_path, monkeypatch)
    assert d["min_sample"]["n"] == len(ENGINE_MIXED)  # 4 manual +30% wins excluded
    assert d["min_sample"]["mean"] < 0.05  # a manual +30% would push it far higher


def test_min_sample_panel_renders_with_caveats(tmp_path, monkeypatch, registry_home):
    html = proof_page.render_html(_load(tmp_path, monkeypatch))
    block = html[html.index('data-k="min-sample-panel"'):]
    block = block[: block.index("</section>")]
    assert "If the observed edge were the true edge" in block
    assert "projection" in block.lower() and "not a forecast" in block.lower()
    assert re.search(r"\d+ closes", block)


def test_min_sample_non_positive_edge_says_so(tmp_path, monkeypatch, registry_home):
    losers = [{"exit": e, "outcome": "LOSS", "source": "consensus"} for e in (90, 95, 99, 92, 97, 94)]
    html = proof_page.render_html(_load(tmp_path, monkeypatch, losers, closes=6))
    block = html[html.index('data-k="min-sample-panel"'):]
    assert "not positive" in block[: block.index("</section>")]


def test_min_sample_with_no_closes_is_insufficient(tmp_path, monkeypatch, registry_home):
    html = proof_page.render_html(_load(tmp_path, monkeypatch, [], closes=0))
    block = html[html.index('data-k="min-sample-panel"'):]
    assert "Not enough closes" in block[: block.index("</section>")]


# ── item 13: PSR/DSR with the number of trials ───────────────────────────────
def test_trial_disclosure_loaded_from_registry(tmp_path, monkeypatch, registry_home):
    assert _load(tmp_path, monkeypatch)["trial_disclosure"]["raw_declared"] == 240


def test_trial_disclosure_renders_trials_psr_dsr(tmp_path, monkeypatch, registry_home):
    html = proof_page.render_html(_load(tmp_path, monkeypatch))
    block = html[html.index('data-k="trial-disclosure"'):]
    block = block[: block.index("</section>")]
    assert "240" in block and "K_eff=11.5" in block
    assert "0.81" in block and "0.54" in block
    assert "undercount" in block.lower()
    assert "using the raw count would deflate the sharpe further" in block.lower()
    assert "independent" in block.lower()


def test_trial_disclosure_without_registry_says_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(proof_page, "_trading_home", lambda: tmp_path / "missing")
    html = proof_page.render_html(_load(tmp_path, monkeypatch))
    block = html[html.index('data-k="trial-disclosure"'):]
    block = block[: block.index("</section>")]
    assert "not available" in block.lower()
    assert "240" not in block


def test_trial_note_is_html_escaped(tmp_path, monkeypatch, registry_home):
    d = _load(tmp_path, monkeypatch)
    d["gate"]["criteria"]["deflated_sharpe"]["note"] = "<script>alert(1)</script>"
    html = proof_page.render_html(d)
    assert "<script>alert(1)</script>" not in html


def test_legacy_data_without_new_keys_still_renders():
    """Callers holding an old dict (no min_sample / trial_disclosure) must not crash."""
    legacy = {
        "closed_trades": 0, "wins": 0, "losses": 0, "breakeven": 0, "win_rate": 0.0,
        "profit_factor": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0, "total_pnl_pct": 0.0,
        "sharpe": 0.0, "max_drawdown": 0.0, "equity_curve": [1.0], "trades": [],
        "open_positions": 0, "signal_ratings": 0, "days_running": 3, "days_target": 180,
        "generated_at": "2026-10-04", "by_source": {},
        "engine": proof_page._cohort_stats([]), "manual": proof_page._cohort_stats([]),
        "gate": {"version": 2, "cohort": "engine", "closes": 0, "required": 100,
                 "criteria": {}, "pass": False},
    }
    assert "<html" in proof_page.render_html(legacy)
