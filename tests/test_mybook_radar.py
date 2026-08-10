"""Diversification Radar scoped to a CUSTOMER's own book.

The Radar was owner-only: `invest_radar.scan_candidates` ran on the owner's book
inside the 10:30 chain and rendered at `/invest`, which is in `_OWNER_ONLY_PATHS`
and 403s for customers. The engine itself was never owner-specific — it takes
`holdings` and `equity_summary` as parameters and injects every network
dependency — so the customer version is the same engine pointed at their book.

These tests pin the three things that make that safe and useful:

  * PII discipline — nothing but `cash` and `total` may reach the radar.
  * Slot sizing — the owner's fixed 5-position book would hand any customer
    holding 5+ names an empty radar by construction.
  * Best-effort — the radar must never cost a customer their analysis.

Same offline rules as the rest of the suite: the engine is stubbed, never called
over the network.
"""

from __future__ import annotations


def _holdings(*symbols: str) -> list[dict]:
    n = len(symbols)
    return [
        {
            "symbol": s,
            "shares": 10.0,
            "equity": 1000.0,
            "portfolio_percentage": round(100.0 / n, 4),
        }
        for s in symbols
    ]


def _capture(monkeypatch) -> dict:
    """Replace the radar engine with a recorder. Returns the capture dict."""
    import server

    seen: dict = {}

    def _fake(holdings, equity_summary, **kwargs):
        seen["holdings"] = holdings
        seen["equity_summary"] = equity_summary
        seen["config"] = kwargs.get("config")
        return {"mode": "SCAN", "candidates": [], "excluded_holdings": []}

    monkeypatch.setattr(server.invest_radar, "scan_candidates", _fake)
    return seen


# ─── PII discipline ───────────────────────────────────────────────────────────

def test_radar_receives_only_cash_and_total(monkeypatch):
    """invest_radar documents that only cash + total ever leave equity_summary.
    The customer path must not widen that: a customer's equity_summary carries
    live valuations, and the radar has no business reading any of it."""
    import server

    seen = _capture(monkeypatch)
    server._run_book_radar(_holdings("NVDA", "AAPL"), 2000.0)

    assert set(seen["equity_summary"]) == {"cash", "total"}
    assert seen["equity_summary"]["total"] == 2000.0


def test_radar_sees_the_customers_own_holdings(monkeypatch):
    """Exclusion of already-held names is the radar's core job, so it has to be
    handed the customer's book — not the owner's, and not an empty list."""
    import server

    seen = _capture(monkeypatch)
    server._run_book_radar(_holdings("NVDA", "AAPL", "MSFT"), 3000.0)

    assert [h["symbol"] for h in seen["holdings"]] == ["NVDA", "AAPL", "MSFT"]


# ─── slot sizing ──────────────────────────────────────────────────────────────

def test_large_book_still_gets_room_to_suggest_new_names(monkeypatch):
    """`max_open_positions` defaults to 5 — the owner's book shape. Left alone,
    `open_slots = max(0, 5 - len(holdings))` is ZERO for any customer holding 5
    or more names, and the radar returns nothing for exactly the books that most
    need diversifying."""
    import server

    seen = _capture(monkeypatch)
    book = _holdings(*[f"S{i}" for i in range(30)])
    server._run_book_radar(book, 30_000.0)

    assert seen["config"]["max_open_positions"] == 30 + server._BOOK_RADAR_NEW_NAMES
    assert seen["config"]["max_open_positions"] > len(book)


def test_empty_book_still_gets_slots(monkeypatch):
    import server

    seen = _capture(monkeypatch)
    server._run_book_radar([], 0.0)

    assert seen["config"]["max_open_positions"] == server._BOOK_RADAR_NEW_NAMES


def test_owner_cash_buffer_is_not_imposed_on_a_customer(monkeypatch):
    """The $500 buffer is a live-trading gate concern on the OWNER's account. A
    silent haircut on a customer's own money is the same class of bug that money
    masking already caused once — the radar is advisory, so it stays at zero."""
    import server

    seen = _capture(monkeypatch)
    server._run_book_radar(_holdings("NVDA"), 1000.0)

    assert seen["config"]["min_cash_buffer"] == 0.0


def test_sizing_is_cash_free_so_the_cached_fingerprint_stays_valid(monkeypatch):
    """The analysis is cached per (user, book fingerprint). Folding a cash figure
    into the radar would make that fingerprint wrong the moment the customer
    changed it, so ranking/sector/swaps are computed cash-free and sizing is
    applied afterwards from the persisted resize_state."""
    import server

    seen = _capture(monkeypatch)
    server._run_book_radar(_holdings("NVDA"), 1000.0)

    assert seen["equity_summary"]["cash"] == 0.0


def test_missing_total_equity_does_not_explode(monkeypatch):
    """A book whose valuation failed hands through None; the radar fails closed
    on total <= 0 on its own, but it must receive a float to do that."""
    import server

    seen = _capture(monkeypatch)
    server._run_book_radar(_holdings("NVDA"), None)

    assert seen["equity_summary"]["total"] == 0.0


# ─── best-effort wiring into the engine suite ─────────────────────────────────

def _stub_book_engines(server, monkeypatch):
    monkeypatch.setattr(
        server,
        "deep_analyze_portfolio",
        lambda payload, regime_context=None, **k: {
            "portfolio_score": 71,
            "holdings_analysis": [{"symbol": "NVDA", "verdict": "HOLD"}],
        },
        raising=True,
    )
    monkeypatch.setattr(
        server, "run_monte_carlo", lambda h, eq, **k: {"var_95": 300.0}, raising=True
    )
    monkeypatch.setattr(
        server,
        "run_kelly_analysis",
        lambda h, total, upside, verdicts: {"holdings": []},
        raising=True,
    )
    monkeypatch.setattr(
        server, "detect_regimes", lambda syms: {"regime_summary": "NEUTRAL"}, raising=True
    )
    monkeypatch.setattr(server, "get_analyst_targets", lambda syms: {}, raising=True)


def _payload():
    return {
        "holdings": _holdings("NVDA"),
        "equity_summary": {"portfolio_equity": 1000.0},
        "unpriced": [],
    }


def test_engine_suite_includes_the_radar(monkeypatch):
    import server

    _stub_book_engines(server, monkeypatch)
    monkeypatch.setattr(
        server.invest_radar,
        "scan_candidates",
        lambda h, eq, **k: {"mode": "SCAN", "candidates": [{"symbol": "CAT"}]},
    )

    out = server._run_book_engines(_payload())

    assert out["radar"]["candidates"][0]["symbol"] == "CAT"


def test_radar_failure_never_costs_the_customer_their_analysis(monkeypatch):
    """Monte Carlo and Kelly are already best-effort here — partial results beat
    a 500. The radar joins them: it is the least important engine in the suite
    and the most network-dependent, so it must never be the thing that fails a
    paid analysis the customer already spent quota on."""
    import server

    _stub_book_engines(server, monkeypatch)

    def _boom(h, eq, **k):
        raise RuntimeError("universe fetch exploded")

    monkeypatch.setattr(server.invest_radar, "scan_candidates", _boom)

    out = server._run_book_engines(_payload())

    assert out["analysis"]["portfolio_score"] == 71
    assert out["monte_carlo"]["var_95"] == 300.0
    assert "RuntimeError" in out["radar"]["error"]
