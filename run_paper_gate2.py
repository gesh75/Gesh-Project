#!/usr/bin/env python3
"""
run_paper_gate2.py — Scripted SKILL.md "Step 5b": paper-trade the daily action
plan and run every BUY/TRIM through the 8-gate risk gate, persisting the audit
chain (trade_intents / risk_decisions / paper trades) that had been dead since
the step was left to manual agent snippets.

Reads engine_results.json (must already contain kelly + regime + snapshot from
run_engines2.py). Writes its outputs back into engine_results.json under
'paper_session' / 'gate_results' / 'trade_stats'. Fail-soft: any sub-step error
is logged and skipped — this script must never break the daily chain.

NOTE on imports: this script deliberately does NOT add gesh-project-trading to
sys.path. The gesh-project copies of paper_trading / trade_log / trading.* are
the ones wired to this repo's gesh_financial.db (the :9000 dashboard DB).
"""
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

T0 = time.time()


def _t() -> str:
    return f"[t+{time.time() - T0:5.1f}s]"


try:
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env')
except Exception:
    pass

# ── Load engine results ───────────────────────────────────────────────────────
with open(BASE / 'engine_results.json') as f:
    eng = json.load(f)

holdings   = eng.get('holdings', [])
equity_sum = eng.get('equity_summary', {})
kelly      = eng.get('kelly', {})
regime_raw = eng.get('regime', {})
snap       = eng.get('snapshot', {})
total_eq   = float(equity_sum.get('total') or equity_sum.get('portfolio_equity') or 0)
cash       = float(equity_sum.get('cash') or 0)

regimes_list = regime_raw.get('regimes', []) if isinstance(regime_raw, dict) else []
rsi_by_sym = {rd.get('symbol'): float(rd.get('rsi') or 0)
              for rd in regimes_list if isinstance(rd, dict)}

print(f"{_t()} [gate] holdings={len(holdings)}  total=${total_eq:,.2f}  cash=${cash:.2f}")

# ── Build the action plan from Kelly + override rules ─────────────────────────
# Rule 8: cash < $500 blocks ALL ADD/BUY. Rule 5: RSI >= 70 turns ADD into HOLD.
cash_blocked = cash < 500.0
action_plan: list[dict] = []

# ── Auto-close FIRST, then build the set of still-open paper positions ────────
# Running the close before the plan means a symbol that stops out today is no
# longer "open" and can be legitimately re-entered if Kelly still says ADD.
# The skip-if-open guard (below) then prevents the daily chain from stacking a
# fresh duplicate entry on top of a position it already holds every weekday.
paper_block: dict = {'closed': [], 'created': [], 'error': None}
open_paper_symbols: set[str] = set()
try:
    from paper_trading import check_and_close_paper_positions
    from trade_log import get_trade_history
    paper_block['closed'] = check_and_close_paper_positions()
    print(f"{_t()} [gate] paper auto-close: {len(paper_block['closed'])} position(s) hit stop/target")
    open_paper_symbols = {
        (t.get('symbol') or '').upper()
        for t in get_trade_history(open_only=True, is_paper=True)
        if (t.get('scanner_source') or '') == 'daily-analysis'
    }
    if open_paper_symbols:
        print(f"{_t()} [gate] already-open (skip re-entry): {sorted(open_paper_symbols)}")
except Exception as e:
    print(f"{_t()} [gate] auto-close / open-set failed (non-fatal): {e}")

for h in holdings:
    sym    = h.get('symbol')
    price  = float(h.get('price') or h.get('current_price') or 0)
    equity = float(h.get('equity') or 0)
    k      = kelly.get(sym, {}) if isinstance(kelly, dict) else {}
    k_act  = (k.get('action') or 'HOLD').upper()
    opt    = float(k.get('optimal_pct') or 0)
    if price <= 0 or not sym:
        continue

    if k_act in ('ADD', 'BUY'):
        if sym.upper() in open_paper_symbols:
            action_plan.append({'symbol': sym, 'action': 'HOLD', 'shares': 0,
                                'current_price': price,
                                'signal_reason': 'Skip: already holding an open daily-analysis paper position'})
            continue
        if cash_blocked:
            action_plan.append({'symbol': sym, 'action': 'HOLD', 'shares': 0,
                                'current_price': price,
                                'signal_reason': 'Rule 8: cash < $500 — ADD blocked'})
            continue
        if rsi_by_sym.get(sym, 0) >= 70:
            action_plan.append({'symbol': sym, 'action': 'HOLD', 'shares': 0,
                                'current_price': price,
                                'signal_reason': f'Rule 5: RSI={rsi_by_sym[sym]:.0f}>=70 — ADD overridden'})
            continue
        usd    = max(0.0, opt / 100 * total_eq - equity)
        usd    = min(usd, max(0.0, cash - 500.0))   # keep the $500 buffer
        shares = math.floor(usd / price)
        if shares > 0:
            action_plan.append({'symbol': sym, 'action': 'BUY', 'shares': shares,
                                'current_price': price,
                                'signal_reason': f'Kelly {k_act}: now {equity/total_eq*100:.1f}% vs optimal {opt:.1f}%'})
    elif k_act == 'TRIM':
        usd    = max(0.0, equity - opt / 100 * total_eq)
        shares = math.ceil(usd / price)
        # Clamp to the actual (possibly fractional) position — a whole-share
        # floor would silently drop TRIM signals on fractional holdings.
        shares = min(float(shares), round(float(h.get('quantity') or 0), 4))
        if shares > 0:
            action_plan.append({'symbol': sym, 'action': 'TRIM', 'shares': shares,
                                'current_price': price,
                                'signal_reason': f'Kelly TRIM: now {equity/total_eq*100:.1f}% vs optimal {opt:.1f}%'})

executable = [a for a in action_plan if a['action'] in ('BUY', 'TRIM') and a['shares'] > 0]
print(f"{_t()} [gate] action plan: {len(action_plan)} entries, {len(executable)} executable")
for a in action_plan:
    print(f"  {a['symbol']:6s} {a['action']:5s} {a['shares']:>4} sh @ ${a['current_price']:.2f}  — {a['signal_reason']}")

# ── Paper session: simulate today's executable plan (auto-close already ran) ──
try:
    if executable:
        from paper_trading import run_paper_session
        created = run_paper_session(executable, holdings, total_eq,
                                    scanner_source='daily-analysis')
        paper_block['created'] = created
        print(f"{_t()} [gate] paper session: {len(created)} record(s)")
except Exception as e:
    paper_block['error'] = str(e)
    print(f"{_t()} [gate] paper session failed (non-fatal): {e}")
    traceback.print_exc()

# ── Risk gate every BUY/TRIM (TRIM maps to SELL for the gate contract) ────────
gate_results: list[dict] = []
try:
    from trading.contract import manual as manual_intent
    from trading.risk_gate import evaluate as risk_gate_evaluate

    # The DATA_FRESH gate (#8) fails closed without a quote timestamp — inject
    # the snapshot's fetch time as 'as_of' (snapshot exposes 'fetched_at').
    # Baseline remap: in LIVE mode the DAILY_LOSS gate compares
    # equity_summary['portfolio_equity'] against 'adjusted_equity_previous_close'
    # — both must be on the SAME basis (full account equity); the snapshot's
    # portfolio_equity is positions-only (≈ -5pp cash-fraction bias). In PAPER
    # mode (the default) the gate now reads the paper book's own P&L instead
    # (rules #32/#36 ported into trading/risk_gate.py), but the remap stays so
    # live mode is correct too.
    gate_eqs = dict(equity_sum)
    _full_eq = gate_eqs.get('account_equity') or gate_eqs.get('total')
    if _full_eq:
        gate_eqs['portfolio_equity'] = _full_eq
    gate_payload = {
        'holdings':       holdings,
        'equity_summary': gate_eqs,
        'as_of':          snap.get('fetched_at') or eng.get('fetched_at'),
    }
    for a in executable:
        side = 'BUY' if a['action'] == 'BUY' else 'SELL'
        intent = manual_intent(
            a['symbol'], side,
            confidence=0.85,
            target_position_pct=0.02 if side == 'BUY' else 0.0,
            rationale=a['signal_reason'],
        )
        verdict = risk_gate_evaluate(intent, gate_payload)
        gate_results.append({
            'symbol':   a['symbol'],
            'action':   a['action'],
            'shares':   a['shares'],
            'approved': verdict.approved,
            'reason':   verdict.reason,
            'gates':    verdict.gates,
        })
        mark = '✅' if verdict.approved else '⛔'
        print(f"  {mark} {a['symbol']:6s} {a['action']:5s} — {verdict.reason or 'approved'}")
except Exception as e:
    print(f"{_t()} [gate] risk gate failed (non-fatal): {e}")
    traceback.print_exc()

# ── Real-time signal notification (Telegram @gesh755bot) ──────────────────────
# Two jobs: (a) the daily-touch alert every comparable product ships, and
# (b) a timestamped off-machine record of each signal at decision time — the
# cheap "forward out-of-sample" proof the track-record page references.
try:
    _closed = paper_block.get('closed') or []
    # Send when there's anything worth a phone ping: a position closed (stop/
    # target hit) OR there's an action plan. A pure stop-out day with no new
    # signals must still alert — that's the day the user most wants to know.
    if _closed or action_plan:
        try:
            from dotenv import load_dotenv as _ld
            # TELEGRAM_* live in the trading repo's .env; override=False keeps
            # any locally-set values.
            _ld(Path.home() / 'Projects' / 'gesh-project-trading' / '.env')
        except Exception:
            pass
        from trading.alerts import _send
        from datetime import datetime, timezone
        _lines = [f"🛡️ GESH daily signals — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
        # Lead with auto-closes — the stop/target events are the headline.
        for c in _closed:
            oc = (c.get('outcome') or '').upper()
            ico = '🟢' if oc == 'WIN' else ('🔴' if oc == 'LOSS' else '⚪')
            _lines.append(
                f"{ico} CLOSED {c.get('symbol')} {oc} — exit ${float(c.get('exit_price') or 0):.2f} "
                f"({float(c.get('pnl_pct') or 0):+.1f}%, ${float(c.get('realized_pnl') or 0):+.2f})"
            )
        if _closed:
            _lines.append("— signals —")
        for a in action_plan:
            g = next((x for x in gate_results
                      if x['symbol'] == a['symbol'] and x['action'] == a['action']), None)
            if g is not None:
                mark = '✅' if g['approved'] else '⛔'
                _lines.append(f"{mark} {a['action']} {a['shares']}× {a['symbol']} @ ${a['current_price']:.2f} — "
                              f"{'approved' if g['approved'] else g['reason']}")
            else:
                _lines.append(f"⏸ {a['symbol']} HOLD — {a['signal_reason']}")
        _approved_n = sum(1 for x in gate_results if x['approved'])
        _lines.append(f"Gate: {_approved_n}/{len(gate_results)} approved · "
                      f"{len(_closed)} closed · paper book · not financial advice")
        _send("\n".join(_lines), silent=False)
        print(f"{_t()} [gate] Telegram alert sent ({len(_closed)} closes, {len(action_plan)} signals)")
except Exception as e:
    print(f"{_t()} [gate] Telegram alert failed (non-fatal): {e}")

# ── 7-day paper vs real stats for the report ──────────────────────────────────
trade_stats: dict = {}
try:
    from trade_log import trade_summary_for_report
    trade_stats = trade_summary_for_report(days=7)
except Exception as e:
    print(f"{_t()} [gate] trade stats failed (non-fatal): {e}")

# ── Persist back into engine_results.json ─────────────────────────────────────
eng['action_plan']   = action_plan
eng['paper_session'] = paper_block
eng['gate_results']  = gate_results
eng['trade_stats']   = trade_stats

with open(BASE / 'engine_results.json', 'w') as f:
    json.dump(eng, f, indent=2, default=str)

approved = sum(1 for g in gate_results if g['approved'])
print(f"\n{_t()} [gate] Done — {len(gate_results)} gated ({approved} approved), "
      f"{len(paper_block['created'])} paper records, {len(paper_block['closed'])} closed")
