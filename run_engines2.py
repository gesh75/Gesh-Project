#!/usr/bin/env python3
"""
run_engines2.py — Re-run all GESH analysis engines on fresh live snapshot.
Writes output to engine_results.json.
"""
import os, sys, json, traceback
from pathlib import Path

BASE = Path(__file__).parent
TRADING = Path('/Users/georgigaydarov/Projects/gesh-project-trading')
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

results = {
    'snapshot': snap,
    'holdings': holdings,
    'equity_summary': equity_sum,
}

# ── 1. Regime ────────────────────────────────────────────────────────────────
print("\n[1/8] Running regime detection...")
try:
    sys.path.insert(0, str(TRADING))
    from regime import detect_regimes
    regime_data = detect_regimes(symbols)
    results['regime'] = regime_data
    regime_summary_lines = []
    for sym, rd in regime_data.items():
        r_str = rd.get('regime', 'UNKNOWN') if isinstance(rd, dict) else str(rd)
        adx = rd.get('adx', 0) if isinstance(rd, dict) else 0
        rsi = rd.get('rsi', 0) if isinstance(rd, dict) else 0
        regime_summary_lines.append(f"  {sym}: {r_str}  ADX={adx:.1f}  RSI={rsi:.1f}")
    regime_summary = "MARKET REGIME CONTEXT:\n" + "\n".join(regime_summary_lines)
    print(regime_summary)
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['regime'] = {}
    regime_summary = "MARKET REGIME CONTEXT: unavailable"

# ── 2. Consensus (per holding) ────────────────────────────────────────────────
print("\n[2/8] Running 11-node consensus engine...")
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
except Exception as e:
    print(f"  FATAL: {e}")
    traceback.print_exc()
    results['consensus'] = {}

# ── 3. Kelly ─────────────────────────────────────────────────────────────────
print("\n[3/8] Running Kelly criterion...")
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
except Exception as e:
    print(f"  FATAL: {e}")
    traceback.print_exc()
    results['kelly'] = {}

# ── 4. Monte Carlo ────────────────────────────────────────────────────────────
print("\n[4/8] Running Monte Carlo simulation...")
try:
    from monte_carlo import run_monte_carlo
    mc = run_monte_carlo(holdings, equity_summary=equity_sum)
    results['monte_carlo'] = mc
    var95 = mc.get('var_95_pct', mc.get('var_95', 0))
    print(f"  VaR95={var95:.1f}%  paths={mc.get('n_sims', mc.get('num_paths',1000))}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['monte_carlo'] = {}

# ── 5. Volatility models ──────────────────────────────────────────────────────
print("\n[5/8] Running volatility models...")
try:
    from volatility_models import compare_vol_models
    vol_results = {}
    for sym in symbols:
        try:
            vr = compare_vol_models(sym)
            vol_results[sym] = vr
            print(f"  {sym}: GARCH={vr.get('garch_vol',0):.1%}  EWMA={vr.get('ewma_vol',0):.1%}")
        except Exception as e2:
            print(f"  {sym} vol ERROR: {e2}")
            vol_results[sym] = {}
    results['vol_models'] = vol_results
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['vol_models'] = {}

# ── 6. Alpha benchmarks ───────────────────────────────────────────────────────
print("\n[6/8] Running alpha benchmarks...")
try:
    from benchmarks import batch_alpha
    # batch_alpha takes list of ticker strings
    alpha_table = batch_alpha(symbols)
    results['alpha_table'] = alpha_table
    for a in (alpha_table or [])[:3]:
        print(f"  {a.get('symbol') or a.get('ticker')}: alpha_1m={a.get('vs_market',a.get('alpha_1m',0)):.1f}%")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()
    results['alpha_table'] = []

# ── 7. Tax-loss harvesting ────────────────────────────────────────────────────
print("\n[7/8] Running tax harvester...")
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
print("\n[8/8] Running AI explainability...")
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
print("\n[bonus] Cash yield landscape...")
try:
    from defi_yield import get_yield_landscape
    yield_data = get_yield_landscape()
    results['yield_data'] = yield_data
    print(f"  yield tiers loaded")
except Exception as e:
    print(f"  yield ERROR: {e}")
    results['yield_data'] = {}

# ── Store regime_summary for AI step ─────────────────────────────────────────
results['regime_summary'] = regime_summary

# ── Save ──────────────────────────────────────────────────────────────────────
out = BASE / 'engine_results.json'
with open(out, 'w') as f:
    json.dump(results, f, indent=2, default=str)

print(f"\n[engines] Done — saved to {out}")
print(f"[engines] Keys: {list(results.keys())}")
