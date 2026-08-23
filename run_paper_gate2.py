#!/usr/bin/env python3
"""
run_paper_gate2.py — Scripted SKILL.md "Step 5b": paper-trade the daily action
plan and run every BUY/TRIM through the 8-gate risk gate, persisting the audit
chain (trade_intents / risk_decisions / paper trades) that had been dead since
the step was left to manual agent snippets.

Causality contract (TASK F): intent → risk decision → (only if APPROVED)
record/fill. Each intent carries the order's REAL confidence (consensus, else
Kelly win_rate; missing → 0.0, fail closed) and REAL post-trade target — never
invented literals. One uuid threads intent → decision → paper record. A
REJECTED intent leaves NO paper-trade record.

Reads engine_results.json (must already contain kelly + regime + snapshot from
run_engines2.py). Writes its outputs back into engine_results.json under
'paper_session' / 'gate_results' / 'trade_stats'. Fail-soft: any sub-step error
is logged and skipped — this script must never break the daily chain.

Degraded runs (TASK G): when the chain ran on a stale snapshot (GESH_DEGRADED
exported by run_daily_analysis.sh, or the degraded provenance stamp
run_engines2/run_ai2 wrote into engine_results.json), the entry guard skips the
whole step — paper records at stale prices would poison the win-rate book.

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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

T0 = time.time()


@dataclass(frozen=True, slots=True)
class PaperGateOutcome:
    """Immutable result contract for a gate preflight or completed batch."""

    actionable: bool
    paper_session: tuple[Mapping[str, object], ...] = ()
    gate_results: tuple[Mapping[str, object], ...] = ()
    reason: str | None = None


def _t() -> str:
    return f"[t+{time.time() - T0:5.1f}s]"


def degraded_skip(eng: dict, environ: Mapping[str, str] | None = None) -> str | None:
    """Entry guard: reason string when this run must not create paper records.

    A degraded (stale-snapshot) chain run is research-only — engines + AI may
    refresh, but paper records at off-hours/day-old prices would poison the
    win-rate book. Trusts either the chain's exported GESH_DEGRADED flag or
    the provenance stamp run_engines2/run_ai2 wrote into engine_results.json.
    Guarded by tests/test_degraded_provenance.py."""
    environ = os.environ if environ is None else environ
    if str(environ.get('GESH_DEGRADED', '')).strip().lower() in ('1', 'true', 'yes'):
        return 'GESH_DEGRADED set by the chain (stale snapshot)'
    manifest = eng.get('run_manifest') or {}
    if manifest.get('degraded'):
        return f"run_manifest {manifest.get('run_id', 'unknown')} is degraded"
    if eng.get('degraded'):
        return (f"engine_results degraded provenance "
                f"(snapshot_age_seconds={eng.get('snapshot_age_seconds')})")
    return None


def preflight_outcome(
    eng: dict, environ: Mapping[str, str] | None = None
) -> PaperGateOutcome | None:
    """Return an audit-only result for degraded inputs, else allow execution."""
    reason = degraded_skip(eng, environ)
    return PaperGateOutcome(actionable=False, reason=reason) if reason else None


def build_confidence_map(consensus: dict, kelly: dict) -> dict[str, float]:
    """Real per-symbol signal confidence in [0, 1].

    Consensus-engine confidence wins; Kelly win_rate is the fallback (both are
    0–100 scales). Symbols absent from both stay absent — callers treat a miss
    as 0.0 (fail closed), never an invented number.
    """
    conf: dict[str, float] = {}
    for sym, k in (kelly or {}).items():
        if isinstance(k, dict) and k.get('win_rate') is not None:
            conf[sym] = min(1.0, max(0.0, float(k['win_rate']) / 100.0))
    for sym, c in (consensus or {}).items():
        if isinstance(c, dict) and c.get('confidence') is not None:
            conf[sym] = min(1.0, max(0.0, float(c['confidence']) / 100.0))
    return conf


def _order_target_pct(order: dict, holdings: list[dict], total_eq: float) -> float:
    """The order's REAL post-trade position as a fraction of equity (0..1)."""
    if total_eq <= 0:
        return 0.0
    sym = (order.get('symbol') or '').upper()
    held = next((float(h.get('equity') or 0) for h in holdings
                 if (h.get('symbol') or '').upper() == sym), 0.0)
    delta = float(order.get('shares') or 0) * float(order.get('current_price') or 0)
    post = held + delta if order.get('action') == 'BUY' else max(0.0, held - delta)
    return max(0.0, post / total_eq)


def gate_and_execute(
    executable: list[dict],
    holdings: list[dict],
    total_eq: float,
    gate_payload: dict,
    confidence_by_sym: dict[str, float],
    *,
    quote_as_of_by_symbol: Mapping[str, str] | None = None,
    intent_builder,
    gate_evaluate,
    recorder,
    scanner_source: str = 'daily-analysis',
    reviewer=None,
    review_blocks: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Causal audit chain per order: intent → risk decision → record if APPROVED.

    The intent gets the order's real confidence/target (see module docstring)
    and its intent_uuid threads through the gate result and the paper record.
    A REJECTED intent records nothing. A recorder failure is per-order
    fail-soft: logged on the gate result, remaining orders still gated.
    Inputs are never mutated — the recorder receives uuid-stamped copies.

    Broker preflight (Stage 2, `reviewer`): an optional callable taking the
    action dict and returning an object with ``.status`` / ``.as_dict()``
    (see rh_order_review.review_action). It runs AFTER the 9 gates approve and
    BEFORE the recorder, so `review_blocks=True` can genuinely veto — a
    post-hoc annotation could not, and a config flag that silently does nothing
    is worse than no flag. It places nothing; a reviewer exception is
    swallowed onto the gate result. Only an explicitly conclusive REJECTED
    verdict vetoes: UNAUTHORIZED/UNAVAILABLE/ERROR mean "not checked" and must
    never be read as either approval or refusal.
    """
    gate_results: list[dict] = []
    created: list[dict] = []
    for a in executable:
        side = 'BUY' if a['action'] == 'BUY' else 'SELL'  # TRIM → SELL for the gate contract
        intent = intent_builder(
            a['symbol'], side,
            confidence=float(confidence_by_sym.get(a['symbol'], 0.0)),
            target_position_pct=_order_target_pct(a, holdings, total_eq),
            rationale=a.get('signal_reason'),
        )
        symbol_payload = dict(gate_payload)
        symbol_payload['as_of'] = (quote_as_of_by_symbol or {}).get(a['symbol'])
        verdict = gate_evaluate(intent, symbol_payload)
        gate_results.append({
            'symbol':      a['symbol'],
            'action':      a['action'],
            'shares':      a['shares'],
            'approved':    verdict.approved,
            'reason':      verdict.reason,
            'gates':       verdict.gates,
            'intent_uuid': intent.intent_uuid,
            'confidence':  intent.confidence,
            'target_position_pct': intent.target_position_pct,
        })
        mark = '✅' if verdict.approved else '⛔'
        print(f"  {mark} {a['symbol']:6s} {a['action']:5s} — {verdict.reason or 'approved'}")
        if not verdict.approved:
            continue

        # ── Broker preflight (read-only; places nothing) ──────────────────────
        if reviewer is not None:
            try:
                rv = reviewer(a)
                gate_results[-1] = {**gate_results[-1], 'broker_review': rv.as_dict()}
                note = rv.status if rv.checked else f"{rv.status} (not checked)"
                extra = f" · {len(rv.alerts)} alert(s)" if getattr(rv, 'alerts', ()) else ''
                print(f"     ↳ broker preflight: {note}{extra}")
                if review_blocks and rv.status == 'REJECTED':
                    gate_results[-1] = {**gate_results[-1],
                                        'approved': False,
                                        'reason': f"BROKER_REVIEW: {rv.message or 'rejected'}"}
                    print(f"     ⛔ {a['symbol']} vetoed by broker preflight — no paper record")
                    continue
            except Exception as e:                      # never break the chain
                gate_results[-1] = {**gate_results[-1], 'broker_review_error': str(e)}
                print(f"     ↳ broker preflight failed (non-fatal): {e}")

        try:
            recs = recorder([{**a, 'intent_uuid': intent.intent_uuid}],
                            holdings, total_eq, scanner_source=scanner_source)
            created.extend({**r, 'intent_uuid': intent.intent_uuid} for r in recs or [])
        except Exception as e:
            gate_results[-1] = {**gate_results[-1], 'record_error': str(e)}
            print(f"{_t()} [gate] paper record failed for {a['symbol']} (non-fatal): {e}")
    return gate_results, created


def main(results_path: str | Path | None = None) -> int:
    results_path = Path(results_path) if results_path else BASE / 'engine_results.json'

    # ── Load engine results ───────────────────────────────────────────────────
    with open(results_path) as f:
        eng = json.load(f)

    # ── Degraded entry guard: research-only run → no paper records at all ─────
    # Checked before chdir/dotenv/DB imports so the skip path has zero side
    # effects (nothing written back, no auto-close, no Telegram signal).
    preflight = preflight_outcome(eng)
    if preflight:
        print(f"{_t()} [gate] DEGRADED RUN — SKIPPING paper session + risk gate: {preflight.reason}")
        print(f"{_t()} [gate] research-only: no paper records, no gate audit, no Telegram signal")
        return 0

    os.chdir(BASE)
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE / '.env')
    except Exception:
        pass

    holdings   = eng.get('holdings', [])
    equity_sum = eng.get('equity_summary', {})
    kelly      = eng.get('kelly', {})
    regime_raw = eng.get('regime', {})
    total_eq   = float(equity_sum.get('total') or equity_sum.get('portfolio_equity') or 0)
    cash       = float(equity_sum.get('cash') or 0)

    regimes_list = regime_raw.get('regimes', []) if isinstance(regime_raw, dict) else []
    rsi_by_sym = {rd.get('symbol'): float(rd.get('rsi') or 0)
                  for rd in regimes_list if isinstance(rd, dict)}

    print(f"{_t()} [gate] holdings={len(holdings)}  total=${total_eq:,.2f}  cash=${cash:.2f}")

    # ── Build the action plan from Kelly + override rules ─────────────────────
    # Rule 8: cash < $500 blocks ALL ADD/BUY. Rule 5: RSI >= 70 turns ADD into HOLD.
    cash_blocked = cash < 500.0
    action_plan: list[dict] = []

    # ── Auto-close FIRST, then build the set of still-open paper positions ────
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

    # ── Risk gate FIRST, paper record only on APPROVED (TRIM → SELL) ──────────
    gate_results: list[dict] = []
    try:
        from paper_trading import run_paper_session
        from trading.contract import manual as manual_intent
        from trading.risk_gate import evaluate as risk_gate_evaluate

        # The DATA_FRESH gate (#8) fails closed without a provider quote timestamp.
        # gate_and_execute injects each symbol's own manifest quote_as_of; the
        # snapshot transport time is deliberately not treated as market freshness.
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
        }
        confidence_by_sym = build_confidence_map(
            eng.get('consensus') if isinstance(eng.get('consensus'), dict) else {},
            kelly if isinstance(kelly, dict) else {},
        )
        # Stage 2 broker preflight — read-only simulation of each approved order.
        # Import is best-effort: a missing/broken module must not cost the chain
        # its paper session, so the reviewer simply stays None.
        _reviewer = None
        _review_blocks = False
        try:
            import rh_order_review
            if rh_order_review.enabled():
                _reviewer = rh_order_review.review_action
                _review_blocks = rh_order_review.blocking()
        except Exception as e:
            print(f"{_t()} [gate] broker preflight unavailable (non-fatal): {e}")

        gate_results, paper_created = gate_and_execute(
            executable, holdings, total_eq, gate_payload, confidence_by_sym,
            quote_as_of_by_symbol=(eng.get('run_manifest') or {}).get('quote_as_of') or {},
            intent_builder=manual_intent,
            gate_evaluate=risk_gate_evaluate,
            recorder=run_paper_session,
            reviewer=_reviewer,
            review_blocks=_review_blocks,
        )
        paper_block['created'] = paper_created
        print(f"{_t()} [gate] paper session: {len(paper_created)} record(s) (approved intents only)")
    except Exception as e:
        paper_block['error'] = str(e)
        print(f"{_t()} [gate] risk gate failed (non-fatal): {e}")
        traceback.print_exc()

    # ── Real-time signal notification (Telegram @gesh755bot) ──────────────────
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
            from datetime import datetime, timezone

            from trading.alerts import _send
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

    # ── 7-day paper vs real stats for the report ──────────────────────────────
    trade_stats: dict = {}
    try:
        from trade_log import trade_summary_for_report
        trade_stats = trade_summary_for_report(days=7)
    except Exception as e:
        print(f"{_t()} [gate] trade stats failed (non-fatal): {e}")

    # ── Persist back into engine_results.json ─────────────────────────────────
    eng['action_plan']   = action_plan
    eng['paper_session'] = paper_block
    eng['gate_results']  = gate_results
    eng['trade_stats']   = trade_stats

    from run_manifest import atomic_write_json
    atomic_write_json(results_path, eng)

    approved = sum(1 for g in gate_results if g['approved'])
    print(f"\n{_t()} [gate] Done — {len(gate_results)} gated ({approved} approved), "
          f"{len(paper_block['created'])} paper records, {len(paper_block['closed'])} closed")

    # Broker-preflight coverage. Reported as checked-vs-total rather than a bare
    # count so an all-UNAUTHORIZED day can never read as an all-clear.
    _rv = [g['broker_review'] for g in gate_results if isinstance(g.get('broker_review'), dict)]
    if _rv:
        _checked = sum(1 for r in _rv if r.get('checked'))
        _statuses = {}
        for r in _rv:
            _statuses[r.get('status', '?')] = _statuses.get(r.get('status', '?'), 0) + 1
        _detail = ' '.join(f"{k}={v}" for k, v in sorted(_statuses.items()))
        print(f"{_t()} [gate] broker preflight: {_checked}/{len(_rv)} conclusively checked · {_detail}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
