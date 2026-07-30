#!/usr/bin/env python3
"""
run_ai2.py — Call Claude deep_analyze_portfolio on fresh engine results.
Appends ai_result back to engine_results.json.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

T0 = time.time()

def _t() -> str:
    """Elapsed-seconds tag for step timing attribution in logs."""
    return f"[t+{time.time() - T0:5.1f}s]"

BASE    = Path(__file__).parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

from run_manifest import (
    RunManifest,
    atomic_write_json,
    manifest_from_snapshot,
    snapshot_degraded_info,
)

try:
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env')
except Exception:
    pass

# ── Load engine results ───────────────────────────────────────────────────────
with open(BASE / 'engine_results.json') as f:
    eng = json.load(f)

# Re-stamp provenance at AI time (age keeps growing after run_engines2) so the
# persisted eng always carries an up-to-date degraded/age/source triple.
_degraded_info = snapshot_degraded_info(eng.get('snapshot', {}))
eng.update(_degraded_info)
try:
    _manifest = RunManifest.from_dict(eng['run_manifest'])
except (KeyError, TypeError, ValueError):
    _manifest = manifest_from_snapshot(eng.get('snapshot', {}))
if _degraded_info['degraded'] and not _manifest.degraded:
    _manifest = RunManifest.create(
        run_id=_manifest.run_id,
        source=_manifest.input_source,
        snapshot_as_of=_manifest.snapshot_as_of,
        now=_manifest.started_at,
        degraded=True,
        quote_as_of=_manifest.quote_as_of,
    )
eng['run_manifest'] = _manifest.to_dict()
atomic_write_json(BASE / 'run_manifest.json', _manifest.to_dict())
if _degraded_info['degraded']:
    print(f"[ai] DEGRADED RUN — snapshot age {_degraded_info['snapshot_age_seconds']}s "
          f"(source={_degraded_info['snapshot_source']}); research-only provenance stamped")

holdings   = eng['holdings']
equity_sum = eng['equity_summary']
consensus  = eng.get('consensus', {})
kelly      = eng.get('kelly', {})
monte      = eng.get('monte_carlo', {})
regime_raw = eng.get('regime', {})

# Build regime summary string from the actual regimes list
regime_lines = []
regimes_list = regime_raw.get('regimes', []) if isinstance(regime_raw, dict) else []
for rd in regimes_list:
    sym  = rd.get('symbol', '?')
    reg  = rd.get('regime', 'UNKNOWN')
    # `or 0` guards None VALUES (key present but null — e.g. failed OHLCV fetch);
    # .get()'s default only covers missing keys. Crashed the chain 2026-07-10 (SPCX).
    adx  = rd.get('adx') or 0
    rsi  = rd.get('rsi') or 0
    bb   = rd.get('bb_width') or 0
    pctb = rd.get('pct_b') or 0
    regime_lines.append(
        f"  {sym}: {reg} | ADX={adx:.1f} RSI={rsi:.1f} BB-Width={bb:.1f}% %B={pctb:.0f}%"
    )

# FRED macro if available
macro_summary = regime_raw.get('macro_summary', '')
if not macro_summary:
    macro_summary = "FRED macro: unavailable (use yfinance proxies)"

regime_summary = (
    "MARKET REGIME CONTEXT (ADX/BB/RSI per symbol):\n"
    + ("\n".join(regime_lines) if regime_lines else "  No per-symbol regime data")
    + f"\n\nMACRO: {macro_summary}"
)

print("[ai] Regime context:\n" + regime_summary)

# ── Signal override rules (apply before AI call) ─────────────────────────────
# Rule 8: cash < $500 → block ALL ADD
cash = equity_sum.get('cash') or 0    # None-safe
total = equity_sum.get('total') or 1  # None-safe
cash_rule_fires = cash < 500.0

# Rule 5: RSI >= 70 → ADD → HOLD for that symbol
rsi_overrides = {}
for rd in regimes_list:
    sym = rd.get('symbol', '?')
    rsi = rd.get('rsi') or 0   # None-safe (failed OHLCV fetch → null rsi)
    if rsi >= 70:
        k_action = kelly.get(sym, {}).get('action', 'HOLD')
        if k_action in ('ADD',):
            rsi_overrides[sym] = f"RSI={rsi:.0f}≥70 → Kelly ADD overridden to HOLD"

override_context = ""
if cash_rule_fires:
    override_context += f"\n⚠️ RULE 8 ACTIVE: Cash ${cash:.2f} < $500 buffer — ALL ADD signals BLOCKED.\n"
if rsi_overrides:
    for sym, msg in rsi_overrides.items():
        override_context += f"⚠️ RULE 5: {sym} — {msg}\n"

# ── Build payload ─────────────────────────────────────────────────────────────
payload = {
    'holdings':      holdings,
    'equity_summary': equity_sum,
    'consensus':     consensus,
    'kelly':         kelly,
    'monte_carlo':   monte,
    'signal_overrides': override_context,
}

# ── Diversification radar (advisory-only) ─────────────────────────────────────
# Scans NEW, non-held names for diversification opportunities and surfaces them in
# the daily report + the /invest tab. Fail-soft: a radar error must NEVER break the
# deep-analysis pipeline. Reuses the holdings regime to avoid a second fetch.
diversification_context = ""
try:
    print(f"{_t()} [ai] Diversification radar scan starting...")
    import invest_radar
    radar_regime = {rd.get('symbol'): rd for rd in regimes_list if rd.get('symbol')}
    radar = invest_radar.scan_candidates(holdings, equity_sum, regime_lookup=radar_regime)
    eng['diversification_candidates'] = radar          # persisted for report + /invest tab
    _radar_txt = invest_radar.summarize_for_prompt(radar)
    if _radar_txt:
        diversification_context = (
            "\n\n" + _radar_txt
            + "\n  → In portfolio_recommendations, you MAY add up to 2-4 ADVISORY new-name ideas "
              "from the radar above (set category='DIVERSIFICATION' and symbol=<ticker>); these are "
              "research suggestions only, never auto-traded, and only when extra cash should be "
              "diversified into NON-held names. If mode=SWAP_ONLY, frame them as swap/trim ideas, not buys."
        )
    print(f"[ai] Radar: mode={radar['mode']} candidates={len(radar['candidates'])} "
          f"swaps={len(radar['swap_suggestions'])} "
          f"book_top_sector={max(radar['existing_sector_weights'], key=radar['existing_sector_weights'].get) if radar['existing_sector_weights'] else '—'}")
except Exception as e:
    print(f"[ai] Radar skipped (non-fatal): {e}")

print(f"\n{_t()} [ai] Calling deep_analyze_portfolio ...")
print(f"[ai] Portfolio: ${total:,.2f}  Cash: ${cash:.2f}  Holdings: {len(holdings)}")

# ── Call AI ───────────────────────────────────────────────────────────────────
try:
    from ai_analyzer import deep_analyze_portfolio
    ai_result = deep_analyze_portfolio(
        payload,
        regime_context=regime_summary + override_context + diversification_context,
    )
    # Canonicalize the fair-value % at the WRITE boundary (Codex #4): the model's own
    # pct_diff uses a non-deterministic convention, and Kelly recomputes the canonical
    # one on read — so without this the report, engine_results.json, and the analysis
    # history would disagree with the sizing decision. Idempotent + fail-safe.
    import fair_value  # noqa: E402 — this repo's copy (the trading sibling has none)
    ai_result = fair_value.canonicalize_analysis(ai_result)
    print(f"{_t()} [ai] Done. Score={ai_result.get('portfolio_score',0)}  Risk={ai_result.get('risk_score',0)}")
    print(f"[ai] Verdict: {ai_result.get('portfolio_verdict','?')}")

    # Save back to engine_results.json
    eng['ai_result'] = ai_result
    eng['ai_result_ran_at'] = __import__('datetime').datetime.now(
        __import__('datetime').timezone.utc).isoformat()
    eng['regime_summary'] = regime_summary
    eng['override_context'] = override_context

    # Atomic write (temp + replace): the :9000 server reads this file while the
    # chain writes it — a bare open('w') exposes a truncated JSON mid-write.
    _out = BASE / 'engine_results.json'
    atomic_write_json(_out, eng)

    # Also dump ai_result standalone for quick inspection
    with open('/tmp/ai_result2.json', 'w') as f:
        json.dump(ai_result, f, indent=2, default=str)

    print("[ai] Saved → engine_results.json + /tmp/ai_result2.json")
    print(f"[ai] Top keys: {list(ai_result.keys())}")

    # ── Persist to the dashboard DB (analysis_runs + equity_snapshots) ───────
    # This was SKILL.md "Step 6": a hand-typed sqlite INSERT every day (which has
    # historically corrupted rows — ticker symbols in the model column). Pinned
    # to BASE/db.py via importlib because a plain `import db` resolves to the
    # gesh-project-trading copy (sys.path shadowing) and would write to the
    # WRONG gesh_financial.db — the :9000 dashboard reads this repo's DB.
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location('gesh_dashboard_db', str(BASE / 'db.py'))
        _gdb = importlib.util.module_from_spec(_spec)
        sys.modules['gesh_dashboard_db'] = _gdb
        _spec.loader.exec_module(_gdb)
        # db.py is a self-replacing shim (gesh-core Phase 3.6): on exec it runs
        # `sys.modules[__name__] = gesh_core.db`, so the real implementation
        # (with log_analysis / log_equity_snapshot) is whatever is now
        # registered under this name. The local _gdb still points at the empty
        # pre-swap shim object, so re-fetch it from sys.modules.
        _gdb = sys.modules['gesh_dashboard_db']
        try:
            from ai_analyzer import MODEL as _MODEL
        except Exception:
            _MODEL = 'claude-sonnet-4-6'
        _run_id = _gdb.log_analysis(f"{_MODEL} (chain)", payload, ai_result)

        # ── Decision Ledger (Phase 4B, non-fatal) ─────────────────────────────
        try:
            from ledger_host import record_daily_analysis_ledger_nonfatal
            record_daily_analysis_ledger_nonfatal(
                engine_results=eng,
                analysis_run_id=_run_id,
                ai_model=_MODEL,
                db_path=_gdb.DB_PATH,
            )
        except Exception as e:
            print(f"[ai] Ledger helper failed to load (non-fatal): {e}")

        _snap_holdings = [
            {**h,
             'current_price': h.get('current_price', h.get('price', 0)),
             'percent_change': h.get('percent_change', h.get('pct_change', 0))}
            for h in holdings
        ]
        _gdb.log_equity_snapshot(_snap_holdings)
        print(f"{_t()} [ai] DB: analysis_runs id={_run_id} + equity snapshot ({len(_snap_holdings)} rows)")
    except Exception as e:
        print(f"[ai] DB save failed (non-fatal): {e}")

except Exception as e:
    print(f"[ai] ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)
