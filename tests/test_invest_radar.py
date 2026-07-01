"""
test_invest_radar.py — TDD suite for the diversification radar.

The radar is ADVISORY-ONLY, READ-ONLY, and (at runtime) yfinance-based. These
tests pin its behavioural contract using INJECTED synthetic data so they are
deterministic and never touch the network. They encode the fixes that the
adversarial design review surfaced:
  * per-sub-metric z BEFORE averaging (no scale-mixing; pb not double-counted)
  * ewma_vol must receive a Series (low-vol + risk-parity actually vary)
  * hard vetoes fire on the real signals; missing data never manufactures score
  * SWAP_ONLY when slots full / cash below buffer (the live account state)
  * no PII (withdrawable_amount / average_buy_price) ever leaves the radar
  * immutability + fail-soft (never raises into the 10:30 pipeline)
"""
from __future__ import annotations

import json
import math

import invest_radar as ir

# ── fixtures ──────────────────────────────────────────────────────────────────

def mk(symbol: str, **over) -> dict:
    """A screen_market-shaped enriched candidate dict with sane defaults."""
    base = {
        "symbol": symbol,
        "price": 100.0,
        "ret_1w": 2.0,
        "ret_1m": 5.0,
        "vol_ratio": 1.0,
        "pre_score": 3.0,
        "company_name": f"{symbol} Inc.",
        "sector": "Technology",
        "pe_ratio": 25.0,
        "pb_ratio": 5.0,
        "revenue_growth": 0.15,
        "earnings_growth": 0.20,
        "market_cap": 5.0e11,
        "target_mean": 120.0,
        "analyst_upside": 18.0,
        "num_analysts": 20,
        "recommendation": "Buy",
        "reco_key": "buy",
    }
    base.update(over)
    return base


def holding(symbol: str, equity: float = 4000.0, price: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "quantity": equity / price,
        "average_buy_price": price * 0.9,
        "equity": equity,
        "percent_change": 5.0,
        "price": price,
    }


EQUITY_LIVE = {"cash": 448.28, "portfolio_equity": 34971.67, "total": 35419.95,
               "withdrawable_amount": 448.28}
EQUITY_CASHED = {"cash": 10000.0, "portfolio_equity": 25000.0, "total": 35000.0,
                 "withdrawable_amount": 9000.0}


# ── winsorized cross-sectional z ──────────────────────────────────────────────

def test_winsorized_z_basic_zero_mean():
    z = ir.winsorized_z([1.0, 2.0, 3.0, 4.0, 5.0])
    assert abs(sum(z)) < 1e-9               # z-scores sum to ~0
    assert z[0] < 0 < z[-1]                 # monotone

def test_winsorized_z_none_is_neutral_zero():
    z = ir.winsorized_z([1.0, None, 5.0, 3.0])
    assert z[1] == 0.0                       # missing → neutral, never crashes

def test_winsorized_z_too_few_valid_returns_zeros():
    # <3 valid points → factor cannot be scored → all-neutral (caller renormalizes)
    assert ir.winsorized_z([2.0, None, None]) == [0.0, 0.0, 0.0]

def test_winsorized_z_clamps_outlier():
    z = ir.winsorized_z([0.0, 0.0, 0.0, 0.0, 1000.0], limit=3.0)
    assert max(z) <= 3.0 + 1e-9              # outlier winsorized, not unbounded


# ── factor scoring: per-sub-metric z, no pb double-count ──────────────────────

def test_quality_driven_by_growth_not_pb():
    # A has far better growth but worse (higher) pb than B. Quality must rank A>B
    # (growth-driven) — proving pb is NOT what 'quality' keys off.
    cands = [
        mk("AAA", revenue_growth=0.60, earnings_growth=0.80, pb_ratio=40.0),
        mk("BBB", revenue_growth=0.02, earnings_growth=0.01, pb_ratio=1.5),
        mk("CCC", revenue_growth=0.15, earnings_growth=0.10, pb_ratio=8.0),
    ]
    scored = {c["symbol"]: c for c in ir.compute_factor_scores(cands, vols={})}
    assert scored["AAA"]["factor_scores"]["quality"] > scored["BBB"]["factor_scores"]["quality"]

def test_value_uses_pb_low_is_better():
    # Cheaper (lower pe/pb) name scores higher on value.
    cands = [
        mk("CHEAP", pe_ratio=8.0, pb_ratio=1.0),
        mk("RICH", pe_ratio=60.0, pb_ratio=30.0),
        mk("MID", pe_ratio=25.0, pb_ratio=5.0),
    ]
    scored = {c["symbol"]: c for c in ir.compute_factor_scores(cands, vols={})}
    assert scored["CHEAP"]["factor_scores"]["value"] > scored["RICH"]["factor_scores"]["value"]

def test_composite_monotonic_dominator_wins():
    # A dominates on every raw factor input → highest composite_z.
    cands = [
        mk("DOM", ret_1w=10, ret_1m=15, revenue_growth=0.5, earnings_growth=0.6,
           pe_ratio=10, pb_ratio=2, analyst_upside=40),
        mk("MEH", ret_1w=1, ret_1m=1, revenue_growth=0.05, earnings_growth=0.03,
           pe_ratio=40, pb_ratio=20, analyst_upside=3),
        mk("AVG", ret_1w=4, ret_1m=6, revenue_growth=0.15, earnings_growth=0.12,
           pe_ratio=25, pb_ratio=6, analyst_upside=15),
    ]
    vols = {"DOM": 0.015, "MEH": 0.05, "AVG": 0.025}   # DOM also lowest vol
    scored = sorted(ir.compute_factor_scores(cands, vols=vols),
                    key=lambda c: c["composite_z"], reverse=True)
    assert scored[0]["symbol"] == "DOM"

def test_factor_coverage_renormalizes_when_factor_dark():
    # If a whole factor has <3 valid inputs it must not silently zero-dilute the
    # composite — remaining factor weights renormalize to sum ~1.
    cands = [mk("X", analyst_upside=None, num_analysts=0),
             mk("Y", analyst_upside=None, num_analysts=0),
             mk("Z", analyst_upside=None, num_analysts=0)]
    scored = ir.compute_factor_scores(cands, vols={})
    for c in scored:
        assert abs(sum(c["weights_used"].values()) - 1.0) < 1e-6
        assert "analyst_revisions" not in c["weights_used"] or c["weights_used"]["analyst_revisions"] == 0.0


# ── timing gate + hard vetoes ─────────────────────────────────────────────────

def test_timing_veto_negative_month():
    g = ir.timing_gate(mk("D", ret_1m=-3.0), regime_row={"adx": 30, "rsi": 55, "pct_b": 50},
                       fft=None, sector_hot=False)
    assert g["vetoed"] and "month" in g["veto_reason"].lower()

def test_timing_veto_overbought_extended():
    g = ir.timing_gate(mk("D", ret_1m=8.0), regime_row={"adx": 30, "rsi": 78, "pct_b": 95},
                       fft=None, sector_hot=False)
    assert g["vetoed"]

def test_timing_good_confluence_not_vetoed_high_score():
    g = ir.timing_gate(
        mk("D", ret_1m=8.0, ret_1w=3.0),
        regime_row={"regime": "TRENDING", "adx": 30, "rsi": 55, "pct_b": 50},
        fft={"is_rising": True, "cycle_position": 0.30, "signal": "BULLISH"},
        sector_hot=True,
    )
    assert not g["vetoed"]
    assert g["confluence_fraction"] >= 0.6

def test_timing_missing_regime_no_false_credit():
    # No regime data must NOT award the RSI/%B confluence bits by defaulting to mid-band.
    g_missing = ir.timing_gate(mk("D", ret_1m=8.0), regime_row=None, fft=None, sector_hot=False)
    g_good = ir.timing_gate(mk("D", ret_1m=8.0),
                            regime_row={"regime": "TRENDING", "adx": 30, "rsi": 55, "pct_b": 50},
                            fft=None, sector_hot=False)
    assert g_missing["confluence_fraction"] < g_good["confluence_fraction"]


# ── sizing ────────────────────────────────────────────────────────────────────

def test_size_one_respects_7pct_cap():
    cfg = {"max_stock_per_trade": 5000.0, "min_cash_buffer": 500.0}
    out = ir.size_one(price=100.0, vol=0.02, inv_vol_weight=1.0,
                      deployable_remaining=10000.0, total=35000.0, cfg=cfg, kelly_usd=4000.0)
    assert out["recommended_usd"] <= 0.07 * 35000.0 + 1e-6
    assert out["recommended_shares"] == math.floor(out["recommended_usd"] / 100.0)

def test_size_one_zero_when_budget_below_price():
    cfg = {"max_stock_per_trade": 5000.0, "min_cash_buffer": 500.0}
    out = ir.size_one(price=100.0, vol=0.02, inv_vol_weight=1.0,
                      deployable_remaining=50.0, total=35000.0, cfg=cfg, kelly_usd=4000.0)
    assert out["recommended_shares"] == 0      # caller must drop these


# ── orchestrator: SWAP_ONLY / BUY / NO_ACTION ─────────────────────────────────

def _inject(syms, sector_map=None, corr_to_holdings=0.2):
    """Build injectable lookups so scan_candidates never hits the network."""
    sector_map = sector_map or {}
    regime_lookup = {s: {"regime": "TRENDING", "adx": 28, "rsi": 55, "pct_b": 50} for s in syms}
    fft_lookup = {s: {"is_rising": True, "cycle_position": 0.3, "signal": "BULLISH"} for s in syms}
    vols = {s: 0.02 for s in syms}
    return dict(regime_lookup=regime_lookup, fft_lookup=fft_lookup, vols=vols,
                sector_hot=set(syms), sector_of=lambda s: sector_map.get(s, "Technology"),
                corr=None, consensus_fn=lambda s, **k: {"verdict": "BUY", "raw_score": 0.4, "active_nodes": 6},
                # Pin config so tests never depend on the live gesh_config.json.
                config={"max_open_positions": 5, "min_cash_buffer": 500.0, "max_stock_per_trade": 5000.0})

def test_live_state_is_swap_only():
    holdings = [holding(s) for s in ["NVDA", "QQQ", "MU", "AMD", "IONQ", "EEM", "APP", "RKLB"]]
    screened = [mk("DELL", sector="Technology"), mk("KO", sector="Consumer Defensive"),
                mk("XOM", sector="Energy")]
    inj = _inject(["DELL", "KO", "XOM"],
                  sector_map={"KO": "Consumer Defensive", "XOM": "Energy"})
    out = ir.scan_candidates(holdings, EQUITY_LIVE, screened=screened, **inj)
    assert out["mode"] == "SWAP_ONLY"
    assert out["open_slots"] == 0
    assert out["deployable_cash"] == 0.0
    assert out["swap_suggestions"]                       # surfaces a funding-source swap
    # Still surface UNSIZED advisory previews so the user sees WHERE to diversify
    # even with no slots/cash — and never suggest piling into the all-Tech book.
    assert len(out["candidates"]) >= 1
    assert all(c.get("actionable") is False for c in out["candidates"])
    assert "DELL" not in {c["symbol"] for c in out["candidates"]}

def test_buy_mode_when_slots_and_cash():
    holdings = [holding("NVDA"), holding("MSFT")]         # 2 of 5 slots used
    screened = [mk("KO", sector="Consumer Defensive", price=60.0),
                mk("XOM", sector="Energy", price=110.0),
                mk("JPM", sector="Financial Services", price=200.0)]
    inj = _inject(["KO", "XOM", "JPM"],
                  sector_map={"KO": "Consumer Defensive", "XOM": "Energy",
                              "JPM": "Financial Services"})
    out = ir.scan_candidates(holdings, EQUITY_CASHED, screened=screened, **inj)
    assert out["mode"] == "BUY"
    assert len(out["candidates"]) >= 1
    for c in out["candidates"]:
        assert c["recommended_shares"] >= 1
        assert c["symbol"] not in {"NVDA", "MSFT"}
    deployed = sum(c["recommended_usd"] for c in out["candidates"])
    assert deployed <= out["deployable_cash"] + 1e-6      # never breach cash floor
    assert all(c["actionable"] is True for c in out["candidates"])   # BUY picks are sized


def test_swap_only_surfaces_unsized_preview():
    # With no slots/cash the radar must STILL surface the top sector-diversified
    # diversifiers (unsized) so the user can see where new cash should go.
    holdings = [holding(s) for s in ["NVDA", "QQQ", "MU", "AMD", "IONQ", "EEM", "APP", "RKLB"]]
    screened = [mk("KO", sector="Consumer Defensive"), mk("XOM", sector="Energy"),
                mk("JPM", sector="Financial Services")]
    inj = _inject(["KO", "XOM", "JPM"],
                  sector_map={"KO": "Consumer Defensive", "XOM": "Energy",
                              "JPM": "Financial Services"})
    out = ir.scan_candidates(holdings, EQUITY_LIVE, screened=screened, **inj)
    assert out["mode"] == "SWAP_ONLY"
    assert len(out["candidates"]) >= 2
    for c in out["candidates"]:
        assert c["actionable"] is False        # unsized preview
        assert "composite_score" in c and "sector" in c

def test_diversifying_buy_allowed_when_book_already_concentrated():
    # The book is ~68% Technology (two large tech holdings), but open slots + cash
    # exist. A buy into an UNDER-weighted NEW sector (Energy) must be selectable —
    # diversification is the whole point. A further Technology buy must still be
    # blocked by the 30% per-sector cap. (Regression: a hard top-3<=70% gate used to
    # block ALL buys once the existing book already breached it.)
    holdings = [holding("BIGTECH1", equity=12000.0), holding("BIGTECH2", equity=12000.0)]
    eq = {"cash": 10000.0, "portfolio_equity": 24000.0, "total": 35000.0,
          "withdrawable_amount": 9000.0}
    screened = [mk("XOM", sector="Energy", price=110.0),
                mk("MORETECH", sector="Technology", price=300.0)]
    inj = _inject(["XOM", "MORETECH"],
                  sector_map={"XOM": "Energy", "MORETECH": "Technology"})
    out = ir.scan_candidates(holdings, eq, screened=screened, **inj)
    syms = {c["symbol"] for c in out["candidates"]}
    assert out["mode"] == "BUY"
    assert "XOM" in syms                 # diversifier into a new sector → allowed
    assert "MORETECH" not in syms        # piling into ~68% tech → blocked by 30% cap


def test_empty_universe_no_action():
    holdings = [holding("NVDA"), holding("MSFT")]
    out = ir.scan_candidates(holdings, EQUITY_CASHED, screened=[],
                             **_inject([]))
    assert out["mode"] in ("NO_ACTION", "SWAP_ONLY")
    assert out["candidates"] == []

def test_holdings_are_excluded():
    holdings = [holding("NVDA"), holding("MSFT")]
    screened = [mk("NVDA"), mk("MSFT"), mk("KO", sector="Consumer Defensive", price=60.0)]
    out = ir.scan_candidates(holdings, EQUITY_CASHED, screened=screened,
                             **_inject(["NVDA", "MSFT", "KO"],
                                       sector_map={"KO": "Consumer Defensive"}))
    cand_syms = {c["symbol"] for c in out["candidates"]}
    assert "NVDA" not in cand_syms and "MSFT" not in cand_syms
    assert set(out["excluded_holdings"]) == {"NVDA", "MSFT"}


# ── safety invariants ─────────────────────────────────────────────────────────

def test_no_pii_in_output():
    holdings = [holding(s) for s in ["NVDA", "QQQ", "MU"]]
    screened = [mk("KO", sector="Consumer Defensive", price=60.0)]
    out = ir.scan_candidates(holdings, EQUITY_LIVE, screened=screened,
                             **_inject(["KO"], sector_map={"KO": "Consumer Defensive"}))
    blob = json.dumps(out)
    assert "withdrawable_amount" not in blob
    assert "average_buy_price" not in blob

def test_inputs_not_mutated():
    holdings = [holding("NVDA"), holding("MSFT")]
    eq = dict(EQUITY_CASHED)
    holdings_copy = json.loads(json.dumps(holdings))
    screened = [mk("KO", sector="Consumer Defensive", price=60.0)]
    ir.scan_candidates(holdings, eq, screened=screened,
                       **_inject(["KO"], sector_map={"KO": "Consumer Defensive"}))
    assert holdings == holdings_copy
    assert eq == EQUITY_CASHED

def test_scan_never_raises_on_fetch_failure(monkeypatch):
    # If the universe fetch explodes, scan must fail SOFT (never break 10:30 pipeline).
    def boom(*a, **k):
        raise RuntimeError("yfinance 401 storm")
    monkeypatch.setattr(ir, "_fetch_screened", boom)
    out = ir.scan_candidates([holding("NVDA")], EQUITY_CASHED)   # no screened injected → fetch path
    assert out["mode"] == "NO_ACTION"
    assert out["candidates"] == []
    assert out["diagnostics"]["data_warnings"]

def test_module_never_imports_execution_layer():
    # Advisory-only: assert via the import GRAPH (AST), not prose — the radar must
    # never wire in the trade-execution / risk-gate / autopilot layer.
    import ast
    import inspect
    forbidden = {"trading.execution", "trading.risk_gate", "trading.autopilot",
                 "risk_gate", "autopilot", "execution"}
    tree = ast.parse(inspect.getsource(ir))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    leaked = imported & forbidden
    assert not leaked, f"radar must not import the execution layer: {leaked}"

def test_summarize_for_prompt_is_compact_and_safe():
    holdings = [holding(s) for s in ["NVDA", "QQQ", "MU"]]
    screened = [mk("KO", sector="Consumer Defensive", price=60.0)]
    out = ir.scan_candidates(holdings, EQUITY_LIVE, screened=screened,
                             **_inject(["KO"], sector_map={"KO": "Consumer Defensive"}))
    txt = ir.summarize_for_prompt(out)
    assert "DIVERSIFICATION RADAR" in txt
    assert "advisory" in txt.lower()
    assert "withdrawable" not in txt              # PII never reaches the prompt
    assert ir.summarize_for_prompt({}) == ""      # empty radar → empty string


def test_scan_emits_resize_state_for_what_if():
    holdings = [holding("NVDA"), holding("MSFT")]
    screened = [mk("KO", sector="Consumer Defensive", price=60.0),
                mk("XOM", sector="Energy", price=110.0),
                mk("JPM", sector="Financial Services", price=200.0)]
    inj = _inject(["KO", "XOM", "JPM"],
                  sector_map={"KO": "Consumer Defensive", "XOM": "Energy",
                              "JPM": "Financial Services"})
    out = ir.scan_candidates(holdings, EQUITY_CASHED, screened=screened, **inj)
    st = out.get("resize_state")
    assert st and st["survivors"] and st["total"] > 0
    for s in st["survivors"]:
        assert {"symbol", "sector", "price", "vol"} <= set(s)


def test_resize_for_cash_sizes_and_hides_pii():
    holdings = [holding("NVDA"), holding("MSFT")]
    screened = [mk("KO", sector="Consumer Defensive", price=60.0),
                mk("XOM", sector="Energy", price=110.0),
                mk("JPM", sector="Financial Services", price=200.0)]
    inj = _inject(["KO", "XOM", "JPM"],
                  sector_map={"KO": "Consumer Defensive", "XOM": "Energy",
                              "JPM": "Financial Services"})
    # Live state has $0 deployable → SWAP_ONLY, but resize_state lets us test BUY sizing.
    out = ir.scan_candidates(holdings, EQUITY_LIVE, screened=screened, **inj)
    st = out["resize_state"]
    r = ir.resize_for_cash(st, 9000.0)
    assert r["mode"] == "BUY" and r["what_if"] is True
    assert r["deployable_cash"] == 9000.0
    assert len(r["candidates"]) >= 1
    assert all(c["actionable"] for c in r["candidates"])
    assert sum(c["recommended_usd"] for c in r["candidates"]) <= 9000.0 + 1e-6
    blob = json.dumps(r)
    assert "resize_state" not in blob and "sector_value" not in blob and "withdrawable" not in blob
    assert ir.resize_for_cash(st, 0.0)["mode"] in ("SWAP_ONLY", "NO_ACTION")   # no cash → no buys


def test_candidate_decorrelation_fires():
    # Two candidates 0.9-correlated to EACH OTHER (different sectors) must never both
    # be picked — the candidate-vs-candidate de-correlation guard must actually fire.
    holdings = [holding("NVDA"), holding("MSFT")]
    screened = [mk("AAA", sector="Energy", price=50.0),
                mk("BBB", sector="Healthcare", price=50.0),
                mk("CCC", sector="Financial Services", price=50.0)]
    inj = _inject(["AAA", "BBB", "CCC"],
                  sector_map={"AAA": "Energy", "BBB": "Healthcare",
                              "CCC": "Financial Services"})
    cand_corr = {"AAA": {"BBB": 0.9, "CCC": 0.1}, "BBB": {"AAA": 0.9, "CCC": 0.1},
                 "CCC": {"AAA": 0.1, "BBB": 0.1}}
    out = ir.scan_candidates(holdings, EQUITY_CASHED, screened=screened,
                             cand_corr=cand_corr, **inj)
    syms = {c["symbol"] for c in out["candidates"]}
    assert not ("AAA" in syms and "BBB" in syms)   # the 0.9-correlated pair never both picked


def test_weights_used_sum_to_one_when_all_factors_dark():
    # Degenerate cross-section (<3 valid points for every factor) must still leave
    # weights_used summing to 1.0 (equal-weight fallback), never 0.0.
    cands = [mk("X", ret_1w=None, ret_1m=None, revenue_growth=None, earnings_growth=None,
                pe_ratio=None, pb_ratio=None, analyst_upside=None, num_analysts=0)]
    scored = ir.compute_factor_scores(cands, vols={})
    assert abs(sum(scored[0]["weights_used"].values()) - 1.0) < 1e-6


def test_resize_for_cash_handles_corrupted_state():
    for bad in (None, {}, {"survivors": None}, {"survivors": []}, {"survivors": "nope"}):
        r = ir.resize_for_cash(bad, 9000.0)
        assert r["mode"] == "NO_ACTION" and r["candidates"] == []


def test_resize_for_cash_clamps_nonfinite_and_huge():
    state = {"survivors": [{"symbol": "KO", "sector": "Consumer Defensive", "price": 60.0,
                            "vol": 0.02, "composite_z": 0.5}],
             "sector_value": {"Technology": 8000.0}, "total": 35000.0, "open_slots": 3,
             "max_stock_per_trade": 5000.0}
    assert ir.resize_for_cash(state, float("inf"))["deployable_cash"] <= 1_000_000.0
    assert ir.resize_for_cash(state, float("nan"))["mode"] in ("SWAP_ONLY", "NO_ACTION")
    assert ir.resize_for_cash(state, -500.0)["deployable_cash"] == 0.0


def test_resize_for_cash_slider_assumes_room():
    # The cash slider is a pure sizing preview: even if the persisted state had 0 open
    # slots, a positive what-if cash should produce sized BUYs (assume diversification room).
    state = {"survivors": [
                {"symbol": "KO", "sector": "Consumer Defensive", "price": 60.0, "vol": 0.02, "composite_z": 0.6},
                {"symbol": "XOM", "sector": "Energy", "price": 110.0, "vol": 0.025, "composite_z": 0.4}],
             "sector_value": {"Technology": 8000.0}, "total": 35000.0, "open_slots": 0,
             "max_stock_per_trade": 5000.0}
    r = ir.resize_for_cash(state, 9000.0)
    assert r["mode"] == "BUY" and len(r["candidates"]) >= 1


def test_max_corr_to_edge_cases():
    assert ir.max_corr_to("KO", {"NVDA"}, None) is None
    assert ir.max_corr_to("KO", {"NVDA"}, {}) is None
    assert ir.max_corr_to("KO", {"NVDA"}, {"KO": {}}) is None
    assert ir.max_corr_to("KO", {"NVDA"}, {"KO": {"NVDA": float("nan")}}) is None
    assert ir.max_corr_to("KO", {"NVDA", "MU"}, {"KO": {"NVDA": -0.8, "MU": 0.3}}) == 0.8


def test_holdings_sector_value_unknown_fallback():
    holdings = [holding("AAA", equity=5000.0), holding("BBB", equity=3000.0)]
    sv = ir.holdings_sector_value(holdings, lambda s: None if s == "BBB" else "Tech")
    assert sv["Tech"] == 5000.0 and sv["UNKNOWN"] == 3000.0


def test_output_has_required_contract_keys():
    out = ir.scan_candidates([holding("NVDA")], EQUITY_CASHED, screened=[], **_inject([]))
    for key in ("as_of", "mode", "deployable_cash", "open_slots", "new_names_to_add",
                "excluded_holdings", "candidates", "swap_suggestions",
                "existing_sector_weights", "diagnostics"):
        assert key in out
