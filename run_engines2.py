#!/usr/bin/env python3
"""
run_engines2.py — Re-run all GESH analysis engines on fresh live snapshot.
Writes output to engine_results.json.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

T0 = time.time()

# FD safety net: yfinance/OpenBB/Reddit retry storms under Yahoo throttling can
# exhaust the 256-FD default of launchd-spawned processes ([Errno 24],
# 2026-06-12). Mirror server.py's in-process bump.
try:
    import resource

    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(_soft, 10240), max(_hard, 10240)))
except Exception:
    pass


def _t() -> str:
    """Elapsed-seconds tag for step timing attribution in logs."""
    return f"[t+{time.time() - T0:5.1f}s]"


BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

from run_manifest import atomic_write_json, manifest_from_snapshot, snapshot_degraded_info

try:
    from dotenv import load_dotenv

    load_dotenv(BASE / ".env")
except Exception:
    pass

# ── Load fresh snapshot ───────────────────────────────────────────────────────
snap_path = BASE / "live_portfolio_snapshot.json"
with open(snap_path) as f:
    snap = json.load(f)

holdings = snap["holdings"]
equity_sum = snap["equity_summary"]
total_port = equity_sum["total"]
cash = equity_sum["cash"]
symbols = [h["symbol"] for h in holdings]

print(f"[engines] {len(holdings)} holdings  total=${total_port:,.2f}  cash=${cash:.2f}")
print(f"[engines] symbols: {symbols}")


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# MERGE over the existing file instead of clobbering it: a full rewrite wipes
# the *_ran_at freshness stamps and the dashboard's POST-run caches (screener
# etc.) every morning, leaving every sidebar badge stale and the Radar tab
# empty. Keys this run produces are overwritten below; everything else survives.
results = {}
try:
    with open(BASE / "engine_results.json") as _f:
        results = json.load(_f)
except Exception:
    results = {}

results.update(
    {
        "snapshot": snap,
        "holdings": holdings,
        "equity_summary": equity_sum,
    }
)

# Provenance: stamp degraded/age/source so a stale-snapshot run can never
# masquerade as fresh output (run_paper_gate2 skips on it; the report labels it).
_degraded_info = snapshot_degraded_info(snap)
results.update(_degraded_info)
_manifest = manifest_from_snapshot(snap)
results["run_manifest"] = _manifest.to_dict()
# [CLAUDE V6 2026-07-19] run_manifest.json is now published TOGETHER with
# engine_results.json at the end of the run (see the Save block), so the standalone
# file and the embedded manifest can never disagree during the multi-minute engine
# window — a crash mid-engines leaves BOTH at the previous run, never a split pair.
if _degraded_info["degraded"]:
    print(
        f"[engines] DEGRADED RUN — snapshot age {_degraded_info['snapshot_age_seconds']}s "
        f"(source={_degraded_info['snapshot_source']}); research-only provenance stamped"
    )

# ── 1. Regime ────────────────────────────────────────────────────────────────
print(f"\n{_t()} [1/8] Running regime detection...")
try:
    from regime import detect_regimes

    regime_data = detect_regimes(symbols)
    results["regime"] = regime_data
    results["regime_ran_at"] = _utcnow()
    # Seed consensus's per-symbol regime cache from this batch result so
    # _regime_weights() doesn't re-run detect_regimes([sym]) once per holding
    # (8 redundant single-symbol downloads per run).
    try:
        import consensus as _consensus_mod

        for _rd in regime_data.get("regimes") or []:
            if isinstance(_rd, dict) and _rd.get("symbol"):
                _consensus_mod._regime_cache_put(_rd["symbol"], _rd.get("regime", ""))
    except Exception:
        pass
    regime_summary_lines = []
    # detect_regimes returns {'regimes': [...], ...} — iterate the list, not
    # the dict keys (the old .items() loop printed/stored garbage).
    for rd in regime_data.get("regimes") or []:
        if not isinstance(rd, dict):
            continue
        _adx = float(rd.get("adx") or 0)
        _rsi = float(rd.get("rsi") or 0)
        regime_summary_lines.append(
            f"  {rd.get('symbol', '?')}: {rd.get('regime', 'UNKNOWN')}  "
            f"ADX={_adx:.1f}  RSI={_rsi:.1f}"
        )
    regime_summary = "MARKET REGIME CONTEXT:\n" + "\n".join(regime_summary_lines)
    print(regime_summary)
    # FRED macro layer lives only in gesh-project/regime.py (the trading copy
    # shadows it on sys.path and has no macro code) — load it path-pinned so
    # run_ai2's macro_summary read stops falling back to "FRED macro: unavailable".
    try:
        import importlib.util

        _mspec = importlib.util.spec_from_file_location(
            "gesh_regime_macro", str(BASE / "regime.py")
        )
        _gregime = importlib.util.module_from_spec(_mspec)
        _mspec.loader.exec_module(_gregime)
        _macro = _gregime.get_macro_context()
        if isinstance(regime_data, dict) and _macro.get("macro_summary"):
            regime_data["macro_summary"] = _macro["macro_summary"]
            results["macro"] = {
                k: _macro.get(k)
                for k in (
                    "fed_funds_rate",
                    "fed_funds_direction",
                    "yield_curve_spread",
                    "yield_curve_state",
                    "cpi_yoy_pct",
                    "inflation_trend",
                    "available",
                )
            }
            print(f"  MACRO: {_macro['macro_summary'][:100]}")
    except Exception as e:
        print(f"  macro skipped (non-fatal): {e}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results["regime"] = {}
    results.pop("regime_ran_at", None)  # don't leave a stale freshness stamp from MERGE
    results.pop("macro", None)
    regime_summary = "MARKET REGIME CONTEXT: unavailable"

# ── 2. Consensus (per holding) ────────────────────────────────────────────────
print(f"\n{_t()} [2/8] Running 11-node consensus engine...")
try:
    from consensus import run_consensus_holding

    consensus_results = {}
    for h in holdings:
        sym = h["symbol"]
        print(f"  consensus {sym}...", end=" ", flush=True)
        try:
            res = run_consensus_holding(
                symbol=sym,
                equity=h["equity"],
                total_portfolio=total_port,
                avg_cost=h["average_buy_price"],
                current_price=h["price"],
            )
            consensus_results[sym] = res
            verdict = res.get("verdict", "?")
            score = res.get("raw_score", res.get("score", 0))
            nodes = res.get("active_nodes", res.get("nodes_voted", 0))
            print(f"{verdict}  score={score:.2f}  nodes={nodes}")
        except Exception as e2:
            print(f"ERROR: {e2}")
            consensus_results[sym] = {
                "verdict": "INSUFFICIENT DATA",
                "raw_score": 0,
                "active_nodes": 0,
                "error": str(e2),
            }
    results["consensus"] = consensus_results
    results["consensus_ran_at"] = _utcnow()
except Exception as e:
    print(f"  FATAL: {e}")
    traceback.print_exc()
    results["consensus"] = {}

# ── 3. Kelly ─────────────────────────────────────────────────────────────────
print(f"\n{_t()} [3/8] Running Kelly criterion...")
try:
    # PORTFOLIO-RECONCILED Kelly (2026-07-27). This loop previously called
    # kelly_size_holding() per name, which is the STANDALONE path: each name is
    # sized independently against a 25% per-position hard cap, with no knowledge
    # of the rest of the book. On a 10-name book of high-momentum names that
    # pinned 7 names at the 25% ceiling and summed to ~225% of the portfolio —
    # a textbook overbet, and the reason every Kelly ADD was rejected by the
    # risk gate's POSITION_CAP (0.18) for weeks: Kelly proposed 24-25% targets
    # that the gate could never approve, so the daily gate lane was a permanent
    # rejection log.
    #
    # run_kelly_analysis() is the portfolio-level entry point that already
    # exists in gesh-core for exactly this ("summing independent per-name
    # Kellys systematically OVERBETS once positions are correlated"). It adds
    # two overlays on top of the same per-name math:
    #   1. a correlation haircut (1/sqrt(N) inside a >=0.70 correlated cluster);
    #   2. a gross-exposure cap (KELLY_MAX_GROSS_PCT, default 100%) scaling the
    #      whole book proportionally if it still sums above the cap.
    # Measured on the 2026-07-27 book: gross factor 0.5187, max single target
    # 25.0% -> 13.0%, all 9 sized names clear the 0.18 gate. It also improves
    # cross-engine coherence (IONQ flips ADD->TRIM, agreeing with consensus SELL).
    #
    # NOTE this deliberately does NOT change any risk limit: the gate cap stays
    # 0.18 and the per-name Kelly ceiling stays 0.25 for the standalone
    # /api/kelly path. Only the daily chain's book-level sizing is reconciled.
    from kelly import kelly_size_holding, run_kelly_analysis

    kelly_results = {}
    kelly_portfolio = {}
    try:
        # run_kelly_analysis reads h["current_price"]; the snapshot uses "price".
        _norm = [
            {**h, "current_price": h.get("current_price", h.get("price", 0))} for h in holdings
        ]
        _res = run_kelly_analysis(_norm, total_port)
        # Downstream (run_paper_gate2, gen_report2, /api/kelly/cached) reads a
        # per-symbol dict — keep that shape, don't leak the list form.
        for r in _res.get("holdings", []):
            if r.get("symbol"):
                kelly_results[r["symbol"]] = r
        kelly_portfolio = {k: v for k, v in _res.items() if k != "holdings"}
        print(
            f"  reconciled: gross_scale_factor={_res.get('gross_scale_factor')} "
            f"rescaled={_res.get('rescaled')} "
            f"allocated={_res.get('total_optimal_allocated_pct')}%"
        )
    except Exception as e_recon:
        # Degrade to the legacy per-name path rather than losing Kelly entirely.
        print(f"  reconciliation failed ({e_recon}) — falling back to per-name sizing")
        traceback.print_exc()
        kelly_results = {}
        for h in holdings:
            sym = h["symbol"]
            try:
                kelly_results[sym] = kelly_size_holding(
                    symbol=sym,
                    current_equity=h["equity"],
                    total_portfolio=total_port,
                    avg_cost=h["average_buy_price"],
                    current_price=h["price"],
                )
            except Exception as e2:
                print(f"  {sym} Kelly ERROR: {e2}")
                kelly_results[sym] = {"action": "HOLD", "optimal_pct": 0, "error": str(e2)}
        kelly_portfolio = {"reconciled": False, "error": str(e_recon)}

    for sym, kr in kelly_results.items():
        action = kr.get("action", "?")
        opt_pct = kr.get("optimal_pct", kr.get("recommended_pct", 0)) or 0
        opt_pct = opt_pct if isinstance(opt_pct, (int, float)) else 0
        print(f"  {sym}: {action}  optimal={opt_pct:.1f}%")

    results["kelly"] = kelly_results
    results["kelly_portfolio"] = kelly_portfolio
    results["kelly_ran_at"] = _utcnow()
    # Fossil key from a pre-reconciliation version: nothing produces it any more,
    # but the merge-write carried a stale "raw_sum_pct: 225%" over-conviction
    # figure forward indefinitely. Reconciliation is what that flag was warning
    # about, so drop it rather than leave a permanently-wrong number in the file.
    results.pop("kelly_overconviction", None)
except Exception as e:
    print(f"  FATAL: {e}")
    traceback.print_exc()
    results["kelly"] = {}

# ── 4. Monte Carlo ────────────────────────────────────────────────────────────
print(f"\n{_t()} [4/8] Running Monte Carlo simulation...")
try:
    from monte_carlo import run_monte_carlo

    mc = run_monte_carlo(holdings, equity_summary=equity_sum)
    results["monte_carlo"] = mc
    results["monte_carlo_ran_at"] = _utcnow()
    var95 = mc.get("var_95_pct", mc.get("var_95", 0))
    print(f"  VaR95={var95:.1f}%  paths={mc.get('n_sims', mc.get('num_paths', 1000))}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results["monte_carlo"] = {}

# ── 5. Volatility models ──────────────────────────────────────────────────────
print(f"\n{_t()} [5/8] Running volatility models...")
try:
    import numpy as np
    import pandas as pd

    from volatility_models import compare_vol_models, format_optional_metric

    vol_results = {}
    # compare_vol_models expects a RETURNS SERIES, not a ticker string — the old
    # per-ticker call silently hit the short-series guard and emitted constant
    # fake vols for every symbol. One batched 1y download covers all holdings.
    import price_source  # Yahoo primary, Robinhood fallback on 401/crumb storm

    _vraw = price_source.yf_download(
        symbols, period="1y", interval="1d", auto_adjust=True, progress=False, threads=False
    )
    closes = _vraw["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=symbols[0])
    for sym in symbols:
        try:
            col = closes[sym].dropna()
            rets = np.log(col / col.shift(1)).dropna()
            vr = compare_vol_models(rets, sym)
            vol_results[sym] = vr
            garch_display = format_optional_metric(
                vr.get("garch_annual_pct"), format_spec=".1f", suffix="%"
            )
            print(
                f"  {sym}: GARCH={garch_display}  "
                f"EWMA={vr.get('ewma_annual_pct', 0):.1f}%  preferred={vr.get('preferred', '?')}"
            )
        except Exception as e2:
            print(f"  {sym} vol ERROR: {e2}")
            vol_results[sym] = {}
    results["vol_models"] = vol_results
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results["vol_models"] = {}

# ── 6. Alpha benchmarks ───────────────────────────────────────────────────────
print(f"\n{_t()} [6/8] Running alpha benchmarks...")
try:
    from alpha_reporting import format_alpha_percent
    from benchmarks import batch_alpha

    # batch_alpha takes list of ticker strings
    alpha_table = batch_alpha(symbols)
    results["alpha_table"] = alpha_table
    for a in (alpha_table or [])[:3]:
        print(
            f"  {a.get('symbol') or a.get('ticker')}: "
            f"vs_market_1m={format_alpha_percent(a.get('vs_market_pct'))}"
        )
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results["alpha_table"] = []

# ── 7. Tax-loss harvesting ────────────────────────────────────────────────────
print(f"\n{_t()} [7/8] Running tax harvester...")
try:
    from tax_harvester import get_harvest_summary

    # Expects dict keyed by symbol
    holdings_dict = {h["symbol"]: h for h in holdings}
    tax_report = get_harvest_summary(holdings_dict)
    results["tax_report"] = tax_report
    print(f"  harvest candidates: {len(tax_report.get('candidates', []))}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results["tax_report"] = {"candidates": [], "total_harvestable": 0}

# ── 8. Explainability ─────────────────────────────────────────────────────────
print(f"\n{_t()} [8/8] Running AI explainability...")
try:
    from explainability import explain_consensus

    explanations = {}
    for sym in symbols:
        cons = results.get("consensus", {}).get(sym, {})
        if cons and cons.get("verdict") not in ("INSUFFICIENT DATA", None):
            try:
                ex = explain_consensus(sym, cons)
                explanations[sym] = ex
                print(f"  {sym}: {str(ex)[:80]}")
            except Exception as e2:
                print(f"  {sym} explain ERROR: {e2}")
                explanations[sym] = {}
    results["explanations"] = explanations
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results["explanations"] = {}

# ── Cash yield (optional) ─────────────────────────────────────────────────────
print(f"\n{_t()} [bonus] Cash yield landscape...")
try:
    from defi_yield import get_yield_landscape

    yield_data = get_yield_landscape()
    results["yield_data"] = yield_data
    print("  yield tiers loaded")
except Exception as e:
    print(f"  yield ERROR: {e}")
    results["yield_data"] = {}

# ── Crypto-equity correlation (was an ad-hoc agent "enrich" snippet) ──────────
print(f"\n{_t()} [bonus] Crypto correlation risk...")
try:
    from crypto_correlation import crypto_risk_summary

    crypto_risk = crypto_risk_summary(holdings)
    results["crypto_risk"] = crypto_risk
    print(f"  avg_btc={crypto_risk.get('avg_btc_corr')}  risk={crypto_risk.get('risk_label', '?')}")
except Exception as e:
    print(f"  crypto ERROR: {e}")
    results["crypto_risk"] = {}

# ── Long-term macro overlay (B1/B2 for the agentic-rh bridge) ────────────────
# VIX regime + CNN Fear&Greed + FINRA margin debt → a normalized block the bridge
# turns into a CONTRARIAN regime-tier exposure ladder. Fail-soft: a data outage
# writes an empty block, and the bridge degrades to today's (binary) behaviour.
print(f"\n{_t()} [bonus] Long-term macro sentiment...")
try:
    import long_term_macro

    results["long_term_macro"] = long_term_macro.engine_block()
    results["long_term_macro_ran_at"] = _utcnow()
    _lt = results["long_term_macro"]
    print(
        f"  bias={_lt.get('bias')}  score={_lt.get('combined_score')}  "
        f"F&G={(_lt.get('fear_greed') or {}).get('label')}"
    )
except Exception as e:
    print(f"  long-term macro ERROR: {e}")
    results["long_term_macro"] = {}

# ── Per-name FFT cycle phase (B4) ────────────────────────────────────────────
# Dominant-cycle phase per held/tracked name so the bridge can prefer TROUGH/
# EARLY-UP entries and avoid PEAK for multi-day holds. Slimmed to the fields the
# bridge reads (phase_label / cycle_position / is_rising / primary period).
print(f"\n{_t()} [bonus] FFT cycle phase per name...")
try:
    from fft_cycles import batch_fft_cycles

    _fft_raw = batch_fft_cycles(symbols).get("cycles", {})
    _fft: dict = {}
    for _sym, _rec in _fft_raw.items():
        if not isinstance(_rec, dict) or _rec.get("error"):
            continue
        _primary = _rec.get("primary_cycle") if isinstance(_rec.get("primary_cycle"), dict) else {}
        _fft[_sym] = {
            "phase_label": _rec.get("phase_label"),
            "cycle_position": _rec.get("cycle_position"),
            "is_rising": _rec.get("is_rising"),
            "primary_cycle": {"period_days": _primary.get("period_days")},
        }
    results["fft"] = _fft
    results["fft_ran_at"] = _utcnow()
    print(f"  fft phases for {len(_fft)}/{len(symbols)} names")
except Exception as e:
    print(f"  fft ERROR: {e}")
    results["fft"] = {}

# ── Per-name consolidation zones (B7) ────────────────────────────────────────
# Tight-range breakout setups (entry/stop/target/R-R) as a {SYM: zone} map so the
# bridge can use structural stops instead of a flat band %. Intraday 5m scan;
# fail-soft (network/illiquid names simply produce no zone).
print(f"\n{_t()} [bonus] Consolidation zones per name...")
try:
    from consolidation_zone import scan_consolidations

    _scan = scan_consolidations(symbols, interval="5m", period="1d", min_rr=1.5)
    _zones: dict = {}
    for _r in _scan.get("found") or []:
        _sym = _r.get("symbol")
        if not _sym:
            continue
        _zones[str(_sym).upper()] = {
            "entry": _r.get("entry"),
            "stop": _r.get("stop"),
            "target": _r.get("target"),
            "rr": _r.get("rr_ratio"),
            "zone_low": _r.get("zone_low"),
            "zone_high": _r.get("zone_high"),
        }
    results["consolidation_zone"] = _zones
    results["consolidation_zone_ran_at"] = _utcnow()
    print(f"  consolidation setups for {len(_zones)}/{len(symbols)} names")
except Exception as e:
    print(f"  consolidation ERROR: {e}")
    results["consolidation_zone"] = {}

# ── Store regime_summary for AI step ─────────────────────────────────────────
results["regime_summary"] = regime_summary

# ── Save ──────────────────────────────────────────────────────────────────────
# Atomic write (temp + replace): the :9000 server reads this file while the
# chain writes it — a bare open('w') exposes a truncated JSON mid-write.
out = BASE / "engine_results.json"
atomic_write_json(out, results)
# [CLAUDE V6] publish the standalone manifest from the SAME final object so the pair
# (run_manifest.json + engine_results.json['run_manifest']) is only ever written together.
atomic_write_json(BASE / "run_manifest.json", results["run_manifest"])

print(f"\n{_t()} [engines] Done — saved to {out}")
print(f"[engines] Keys: {list(results.keys())}")
