"""The track record must lead with the strategy it is selling.

Audit Q-05 / §12 (2026-07-22): the page headlined a blended "+22.2% vs SPY
+0.9%" cumulative equity curve while the autonomy-gated ENGINE cohort — the
strategy a subscriber actually buys, and the only population the gate measures —
was −7.7% with a 0.65 profit factor and a 12% win rate. The blend was carried by
two large manual HOOD trades a subscriber could not have reproduced.

Nothing about the underlying data changes here. What changes is which number a
reader meets first, and that neither number can be read without its scope.
"""

from __future__ import annotations

import re

import pytest

import proof_page


def _cohort(closed, wins, losses, pnl_pct, win_rate):
    return {"closed": closed, "wins": wins, "losses": losses, "breakeven": 0,
            "win_rate": win_rate, "total_pnl_pct": pnl_pct}


@pytest.fixture
def data():
    """The audited shape: a losing engine cohort inside a winning blend."""
    return {
        "generated_at": "2026-07-23T00:00:00Z",
        "engine": _cohort(25, 3, 22, -0.077, 0.12),
        "manual": _cohort(6, 4, 2, 0.299, 0.67),
        "total_pnl_pct": 0.222, "spy_return_period": 0.009,
        "wins": 7, "losses": 24, "breakeven": 0,
        "profit_factor": 1.37, "avg_win_pct": 0.11, "avg_loss_pct": -0.04,
        "win_rate": 0.23, "sharpe": 0.2, "max_drawdown": 0.27,
        "equity_curve": [1.0, 1.1, 1.22], "trades": [], "open_positions": [],
        "by_source": {"consensus": 25, "manual": 6}, "signal_ratings": 6,
        "days_running": 42, "days_target": 180,
        "gate": {"version": proof_page.GATE_DTO_VERSION_EXPECTED, "cohort": "engine",
                 "cohort_sources": ["consensus"], "closes": 25, "required": 100,
                 "passed": False, "reasons": ["insufficient closes"]},
    }


def _num(html: str, key: str) -> float:
    m = re.search(rf'data-k="{key}"[^>]*>([+-]?[\d.]+)%', html)
    assert m, f"missing data-k={key} in rendered page"
    return float(m.group(1))


def test_headline_is_the_engine_cohort_not_the_blend(data):
    html = proof_page.render_html(data)
    assert _num(html, "headline-engine-pnl") == pytest.approx(-7.7)
    assert "gated strategy" in html


def test_engine_headline_appears_before_the_blended_figure(data):
    html = proof_page.render_html(data)
    assert html.index('data-k="headline-engine-pnl"') < html.index("Cumulative paper equity")


def test_blended_figure_is_still_shown_and_scoped(data):
    """Suppressing the blend would be its own dishonesty — it must be present,
    labelled, and explained."""
    html = proof_page.render_html(data)
    assert _num(html, "headline-blended-pnl") == pytest.approx(22.2)
    assert "BLENDED" in html
    assert "never count toward the autonomy gate" in html


def test_equity_curve_is_labelled_as_blended(data):
    html = proof_page.render_html(data)
    curve_label = html[html.index("Cumulative paper equity"):][:220]
    assert "BLENDED" in curve_label or "ALL SOURCES" in curve_label


def test_manual_trade_count_is_disclosed_next_to_the_blend(data):
    html = proof_page.render_html(data)
    assert "6 hand-rated MANUAL trades" in html


def test_sample_size_and_benchmark_accompany_the_headline(data):
    html = proof_page.render_html(data)
    head = html[html.index("Headline"):][:600]
    assert "25 closed paper trades" in head
    assert "win rate 12%" in head
    assert "SPY over the same period" in head


def test_positive_engine_cohort_still_renders(data):
    """The headline must not be hard-wired to a loss."""
    data["engine"] = _cohort(40, 26, 14, 0.183, 0.65)
    html = proof_page.render_html(data)
    assert _num(html, "headline-engine-pnl") == pytest.approx(18.3)


def test_underlying_cohort_numbers_are_untouched(data):
    """Presentation changed; data did not."""
    html = proof_page.render_html(data)
    assert _num(html, "engine-pnl") == pytest.approx(-7.7)
    assert _num(html, "manual-pnl") == pytest.approx(29.9)
