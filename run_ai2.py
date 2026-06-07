#!/usr/bin/env python3
"""
run_ai2.py — Call Claude deep_analyze_portfolio on fresh engine results.
Appends ai_result back to engine_results.json.
"""
import os, sys, json, traceback
from pathlib import Path

BASE    = Path(__file__).parent
TRADING = Path('/Users/georgigaydarov/Projects/gesh-project-trading')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(TRADING))
os.chdir(BASE)

try:
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env')
except Exception:
    pass

# ── Load engine results ───────────────────────────────────────────────────────
with open(BASE / 'engine_results.json') as f:
    eng = json.load(f)

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
    adx  = rd.get('adx', 0)
    rsi  = rd.get('rsi', 0)
    bb   = rd.get('bb_width', 0)
    pctb = rd.get('pct_b', 0)
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
cash = equity_sum.get('cash', 0)
total = equity_sum.get('total', 1)
cash_rule_fires = cash < 500.0

# Rule 5: RSI >= 70 → ADD → HOLD for that symbol
rsi_overrides = {}
for rd in regimes_list:
    sym = rd.get('symbol', '?')
    rsi = rd.get('rsi', 0)
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

print(f"\n[ai] Calling deep_analyze_portfolio ...")
print(f"[ai] Portfolio: ${total:,.2f}  Cash: ${cash:.2f}  Holdings: {len(holdings)}")

# ── Call AI ───────────────────────────────────────────────────────────────────
try:
    from ai_analyzer import deep_analyze_portfolio
    ai_result = deep_analyze_portfolio(
        payload,
        regime_context=regime_summary + override_context + diversification_context,
    )
    print(f"[ai] Done. Score={ai_result.get('portfolio_score',0)}  Risk={ai_result.get('risk_score',0)}")
    print(f"[ai] Verdict: {ai_result.get('overall_verdict','?')}")

    # Save back to engine_results.json
    eng['ai_result'] = ai_result
    eng['regime_summary'] = regime_summary
    eng['override_context'] = override_context

    with open(BASE / 'engine_results.json', 'w') as f:
        json.dump(eng, f, indent=2, default=str)

    # Also dump ai_result standalone for quick inspection
    with open('/tmp/ai_result2.json', 'w') as f:
        json.dump(ai_result, f, indent=2, default=str)

    print("[ai] Saved → engine_results.json + /tmp/ai_result2.json")
    print(f"[ai] Top keys: {list(ai_result.keys())}")

except Exception as e:
    print(f"[ai] ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)
