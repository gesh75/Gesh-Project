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


def _cohort(closed, wins, losses, pnl_pct, win_rate, *, gross_notional=1000.0):
    realized = pnl_pct * gross_notional
    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "breakeven": 0,
        "win_rate": win_rate,
        "total_pnl_pct": pnl_pct,
        "sum_trade_returns": pnl_pct,
        "realized_pnl_usd": realized,
        "gross_entry_notional_usd": gross_notional,
        "pnl_to_gross_entry_notional": pnl_pct,
        "return_on_deployed_capital": pnl_pct,
        # Presentation fixture: interval arithmetic itself is covered in
        # test_proof_page.py against the real cohort calculator.
        "win_rate_ci_95": (0.04, 0.30),
        "cost_sensitivity_20bp_usd": gross_notional * 0.002,
        "cost_sensitivity_50bp_usd": gross_notional * 0.005,
        "profit_factor_usd": 1.0,
    }


@pytest.fixture
def data():
    """The audited shape: a losing engine cohort inside a winning blend."""
    return {
        "generated_at": "2026-07-23T00:00:00Z",
        "engine": _cohort(25, 3, 22, -0.077, 0.12),
        "manual": _cohort(6, 4, 2, 0.299, 0.67),
        "total_pnl_pct": 0.222,
        "spy_return_period": 0.009,
        "all_sources": _cohort(31, 7, 24, 0.111, 0.23, gross_notional=2000.0),
        "wins": 7,
        "losses": 24,
        "breakeven": 0,
        "profit_factor": 1.37,
        "avg_win_pct": 0.11,
        "avg_loss_pct": -0.04,
        "win_rate": 0.23,
        "sharpe": 0.2,
        "max_drawdown": 0.27,
        "equity_curve": [1.0, 1.1, 1.22],
        "trades": [],
        "open_positions": [],
        "by_source": {"consensus": 25, "manual": 6},
        "signal_ratings": 6,
        "days_running": 42,
        "days_target": 180,
        "gate": {
            "version": proof_page.GATE_DTO_VERSION_EXPECTED,
            "cohort": "engine",
            "cohort_sources": ["consensus"],
            "closes": 25,
            "required": 100,
            "passed": False,
            "reasons": ["insufficient closes"],
        },
    }


def _num(html: str, key: str) -> float:
    m = re.search(rf'data-k="{key}"[^>]*>([+-]?[\d.]+)%', html)
    assert m, f"missing data-k={key} in rendered page"
    return float(m.group(1))


def _money(html: str, key: str) -> float:
    m = re.search(rf'data-k="{key}"[^>]*>([+-]?)\$([\d,]+\.\d\d)', html)
    assert m, f"missing data-k={key} in rendered page"
    value = float(m.group(2).replace(",", ""))
    return -value if m.group(1) == "-" else value


def test_headline_is_defensible_engine_accounting_not_trade_return_sum(data):
    html = proof_page.render_html(data)
    assert _money(html, "headline-realized-pnl-usd") == pytest.approx(-77.0)
    assert _num(html, "headline-pnl-to-gross-entry-notional") == pytest.approx(-7.7)
    assert "gated strategy" in html


def test_engine_headline_appears_before_the_illustrative_index(data):
    html = proof_page.render_html(data)
    assert html.index('data-k="headline-realized-pnl-usd"') < html.index(
        "Illustrative sequential trade-return index"
    )


def test_all_source_trade_return_sum_is_still_shown_and_scoped(data):
    """The historical raw sum remains inspectable but cannot masquerade as return."""
    html = proof_page.render_html(data)
    assert _num(html, "all-sum-trade-returns") == pytest.approx(11.1)
    assert "BLENDED" in html
    assert "not a portfolio return" in html
    assert "never count toward the autonomy gate" in html


def test_trade_return_index_is_labelled_as_blended_and_illustrative(data):
    html = proof_page.render_html(data)
    curve_label = html[html.index("Illustrative sequential trade-return index") :][:320]
    assert "BLENDED" in curve_label or "ALL SOURCES" in curve_label
    assert "not portfolio equity" in html


def test_manual_trade_count_is_disclosed_next_to_the_blend(data):
    html = proof_page.render_html(data)
    assert "6 hand-rated MANUAL trades" in html


def test_sample_size_and_benchmark_accompany_the_headline(data):
    html = proof_page.render_html(data)
    head = html[html.index("Headline") :][:1400]
    assert "25 closed paper trades" in head
    assert "win rate 12%" in head
    assert "Approx. Wilson 95% interval" in head
    assert "not adjusted for overlapping/correlated trades" in head
    assert "SPY calendar context +0.9% (not exposure-matched)" in head


def test_positive_engine_cohort_still_renders(data):
    """The headline must not be hard-wired to a loss."""
    data["engine"] = _cohort(40, 26, 14, 0.183, 0.65)
    html = proof_page.render_html(data)
    assert _money(html, "headline-realized-pnl-usd") == pytest.approx(183.0)
    assert _num(html, "headline-pnl-to-gross-entry-notional") == pytest.approx(18.3)


def test_legacy_percent_profit_factor_is_not_relabelled_as_gross_dollars(data):
    data.pop("all_sources")
    html = proof_page.render_html(data)
    all_sources = html[html.index("All sources BLENDED") : html.index("Tracker methodology")]

    assert "Profit factor (gross $):" in all_sources
    assert "n/a (legacy input)" in all_sources
    assert ">1.37<" not in all_sources


def test_underlying_cohort_numbers_are_untouched(data):
    """Presentation changed; data did not."""
    html = proof_page.render_html(data)
    assert _num(html, "engine-pnl") == pytest.approx(-7.7)
    assert _num(html, "manual-pnl") == pytest.approx(29.9)
