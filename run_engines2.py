#!/usr/bin/env python3
"""
run_engines2.py — Re-run all GESH analysis engines on fresh live snapshot.
Writes output to engine_results.json.
"""
import os, sys, json, time, traceback
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
TRADING = Path(os.environ.get('GESH_TRADING_HOME',
                               str(Path(__file__).parent.parent / 'gesh-project-trading')))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(TRADING))
os.chdir(BASE)

try:
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env')
except Exception:
    pass

# ── Load fresh snapshot ───────────────────────────────────────────────────────
snap_path = BASE / 'live_portfolio_snapshot.json'
with open(snap_path) as f:
    snap = json.load(f)

holdings    = snap['holdings']
equity_sum  = snap['equity_summary']
total_port  = equity_sum['total']
cash        = equity_sum['cash']
symbols     = [h['symbol'] for h in holdings]

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
    with open(BASE / 'engine_results.json') as _f:
        results = json.load(_f)
except Exception:
    results = {}

results.update({
    'snapshot': snap,
    'holdings': holdings,
    'equity_summary': equity_sum,
})

# ── 1. Regime ────────────────────────────────────────────────────────────────
print(f"\n{_t()} [1/8] Running regime detection...")
try:
    sys.path.insert(0, str(TRADING))
    from regime import detect_regimes
    regime_data = detect_regimes(symbols)
    results['regime'] = regime_data
    results['regime_ran_at'] = _utcnow()
    # Seed consensus's per-symbol regime cache from this batch result so
    # _regime_weights() doesn't re-run detect_regimes([sym]) once per holding
    # (8 redundant single-symbol downloads per run).
    try:
        import consensus as _consensus_mod
        for _rd in (regime_data.get('regimes') or []):
            if isinstance(_rd, dict) and _rd.get('symbol'):
                _consensus_mod._regime_cache_put(_rd['symbol'], _rd.get('regime', ''))
    except Exception:
        pass
    regime_summary_lines = []
    # detect_regimes returns {'regimes': [...], ...} — iterate the list, not
    # the dict keys (the old .items() loop printed/stored garbage).
    for rd in (regime_data.get('regimes') or []):
        if not isinstance(rd, dict):
            continue
        regime_summary_lines.append(
            f"  {rd.get('symbol','?')}: {rd.get('regime','UNKNOWN')}  "
            f"ADX={rd.get('adx',0):.1f}  RSI={rd.get('rsi',0):.1f}")
    regime_summary = "MARKET REGIME CONTEXT:\n" + "\n".join(regime_summary_lines)
    print(regime_summary)
    # FRED macro layer lives only in gesh-project/regime.py (the trading copy
    # shadows it on sys.path and has no macro code) — load it path-pinned so
    # run_ai2's macro_summary read stops falling back to "FRED macro: unavailable".
    try:
        import importlib.util
        _mspec = importlib.util.spec_from_file_location('gesh_regime_macro', str(BASE / 'regime.py'))
        _gregime = importlib.util.module_from_spec(_mspec)
        _mspec.loader.exec_module(_gregime)
        _macro = _gregime.get_macro_context()
        if isinstance(regime_data, dict) and _macro.get('macro_summary'):
            regime_data['macro_summary'] = _macro['macro_summary']
            results['macro'] = {k: _macro.get(k) for k in
                                ('fed_funds_rate', 'fed_funds_direction',
                                 'yield_curve_spread', 'yield_curve_state',
                                 'cpi_yoy_pct', 'inflation_trend', 'available')}
            print(f"  MACRO: {_macro['macro_summary'][:100]}")
    except Exception as e:
        print(f"  macro skipped (non-fatal): {e}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['regime'] = {}
    regime_summary = "MARKET REGIME CONTEXT: unavailable"

# ── 2. Consensus (per holding) ────────────────────────────────────────────────
print(f"\n{_t()} [2/8] Running 11-node consensus engine...")
try:
    from consensus import run_consensus_holding
    consensus_results = {}
    for h in holdings:
        sym = h['symbol']
        print(f"  consensus {sym}...", end=' ', flush=True)
        try:
            res = run_consensus_holding(
                symbol=sym,
                equity=h['equity'],
                total_portfolio=total_port,
                avg_cost=h['average_buy_price'],
                current_price=h['price'],
            )
            consensus_results[sym] = res
            verdict = res.get('verdict', '?')
            score   = res.get('raw_score', res.get('score', 0))
            nodes   = res.get('active_nodes', res.get('nodes_voted', 0))
            print(f"{verdict}  score={score:.2f}  nodes={nodes}")
        except Exception as e2:
            print(f"ERROR: {e2}")
            consensus_results[sym] = {'verdict': 'INSUFFICIENT DATA', 'raw_score': 0,
                                       'active_nodes': 0, 'error': str(e2)}
    results['consensus'] = consensus_results
    results['consensus_ran_at'] = _utcnow()
except Exception as e:
    print(f"  FATAL: {e}")
    traceback.print_exc()
    results['consensus'] = {}

# ── 3. Kelly ─────────────────────────────────────────────────────────────────
print(f"\n{_t()} [3/8] Running Kelly criterion...")
try:
    from kelly import kelly_size_holding
    kelly_results = {}
    for h in holdings:
        sym = h['symbol']
        try:
            kr = kelly_size_holding(
                symbol=sym,
                current_equity=h['equity'],
                total_portfolio=total_port,
                avg_cost=h['average_buy_price'],
                current_price=h['price'],
            )
            kelly_results[sym] = kr
            action  = kr.get('action', '?')
            opt_pct = kr.get('optimal_pct', kr.get('recommended_pct', 0)) or 0
            print(f"  {sym}: {action}  optimal={opt_pct:.1f}%")
        except Exception as e2:
            print(f"  {sym} Kelly ERROR: {e2}")
            kelly_results[sym] = {'action': 'HOLD', 'optimal_pct': 0, 'error': str(e2)}
    results['kelly'] = kelly_results
    results['kelly_ran_at'] = _utcnow()
except Exception as e:
    print(f"  FATAL: {e}")
    traceback.print_exc()
    results['kelly'] = {}

# ── 4. Monte Carlo ────────────────────────────────────────────────────────────
print(f"\n{_t()} [4/8] Running Monte Carlo simulation...")
try:
    from monte_carlo import run_monte_carlo
    mc = run_monte_carlo(holdings, equity_summary=equity_sum)
    results['monte_carlo'] = mc
    results['monte_carlo_ran_at'] = _utcnow()
    var95 = mc.get('var_95_pct', mc.get('var_95', 0))
    print(f"  VaR95={var95:.1f}%  paths={mc.get('n_sims', mc.get('num_paths',1000))}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['monte_carlo'] = {}

# ── 5. Volatility models ──────────────────────────────────────────────────────
print(f"\n{_t()} [5/8] Running volatility models...")
try:
    from volatility_models import compare_vol_models
    import yfinance as yf
    import numpy as np
    import pandas as pd
    vol_results = {}
    # compare_vol_models expects a RETURNS SERIES, not a ticker string — the old
    # per-ticker call silently hit the short-series guard and emitted constant
    # fake vols for every symbol. One batched 1y download covers all holdings.
    import price_source  # Yahoo primary, Robinhood fallback on 401/crumb storm
    _vraw = price_source.yf_download(symbols, period='1y', interval='1d',
                                     auto_adjust=True, progress=False, threads=False)
    closes = _vraw['Close']
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=symbols[0])
    for sym in symbols:
        try:
            col = closes[sym].dropna()
            rets = np.log(col / col.shift(1)).dropna()
            vr = compare_vol_models(rets, sym)
            vol_results[sym] = vr
            print(f"  {sym}: GARCH={vr.get('garch_annual_pct',0):.1f}%  "
                  f"EWMA={vr.get('ewma_annual_pct',0):.1f}%  preferred={vr.get('preferred','?')}")
        except Exception as e2:
            print(f"  {sym} vol ERROR: {e2}")
            vol_results[sym] = {}
    results['vol_models'] = vol_results
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['vol_models'] = {}

# ── 6. Alpha benchmarks ───────────────────────────────────────────────────────
print(f"\n{_t()} [6/8] Running alpha benchmarks...")
try:
    from benchmarks import batch_alpha
    # batch_alpha takes list of ticker strings
    alpha_table = batch_alpha(symbols)
    results['alpha_table'] = alpha_table
    for a in (alpha_table or [])[:3]:
        print(f"  {a.get('symbol') or a.get('ticker')}: vs_market_1m={a.get('vs_market_pct',0):+.1f}%")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['alpha_table'] = []

# ── 7. Tax-loss harvesting ────────────────────────────────────────────────────
print(f"\n{_t()} [7/8] Running tax harvester...")
try:
    from tax_harvester import get_harvest_summary
    # Expects dict keyed by symbol
    holdings_dict = {h['symbol']: h for h in holdings}
    tax_report = get_harvest_summary(holdings_dict)
    results['tax_report'] = tax_report
    print(f"  harvest candidates: {len(tax_report.get('candidates', []))}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['tax_report'] = {'candidates': [], 'total_harvestable': 0}

# ── 8. Explainability ─────────────────────────────────────────────────────────
print(f"\n{_t()} [8/8] Running AI explainability...")
try:
    from explainability import explain_consensus
    explanations = {}
    for sym in symbols:
        cons = results.get('consensus', {}).get(sym, {})
        if cons and cons.get('verdict') not in ('INSUFFICIENT DATA', None):
            try:
                ex = explain_consensus(sym, cons)
                explanations[sym] = ex
                print(f"  {sym}: {str(ex)[:80]}")
            except Exception as e2:
                print(f"  {sym} explain ERROR: {e2}")
                explanations[sym] = {}
    results['explanations'] = explanations
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['explanations'] = {}

# ── Cash yield (optional) ─────────────────────────────────────────────────────
print(f"\n{_t()} [bonus] Cash yield landscape...")
try:
    from defi_yield import get_yield_landscape
    yield_data = get_yield_landscape()
    results['yield_data'] = yield_data
    print(f"  yield tiers loaded")
except Exception as e:
    print(f"  yield ERROR: {e}")
    results['yield_data'] = {}

# ── Crypto-equity correlation (was an ad-hoc agent "enrich" snippet) ──────────
print(f"\n{_t()} [bonus] Crypto correlation risk...")
try:
    from crypto_correlation import crypto_risk_summary
    crypto_risk = crypto_risk_summary(holdings)
    results['crypto_risk'] = crypto_risk
    print(f"  avg_btc={crypto_risk.get('avg_btc_corr')}  risk={crypto_risk.get('risk_label','?')}")
except Exception as e:
    print(f"  crypto ERROR: {e}")
    results['crypto_risk'] = {}

# ── Store regime_summary for AI step ─────────────────────────────────────────
results['regime_summary'] = regime_summary

# ── Save ──────────────────────────────────────────────────────────────────────
# Atomic write (temp + replace): the :9000 server reads this file while the
# chain writes it — a bare open('w') exposes a truncated JSON mid-write.
out = BASE / 'engine_results.json'
tmp = out.with_name(out.name + '.tmp')
with open(tmp, 'w') as f:
    json.dump(results, f, indent=2, default=str)
    f.flush()
    os.fsync(f.fileno())              # durable before rename — not just atomic
os.replace(tmp, out)

print(f"\n{_t()} [engines] Done — saved to {out}")
print(f"[engines] Keys: {list(results.keys())}")
