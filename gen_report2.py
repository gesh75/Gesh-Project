#!/usr/bin/env python3
"""gen_report2.py — Build gesh_report.html from engine_results.json"""

import json
import os
from datetime import datetime, timezone
from html import escape as _esc  # the report body f-string is bound to `html`, so alias
from pathlib import Path

import report_share_plan
from alpha_reporting import alpha_value_color, format_alpha_percent
from run_manifest import report_output_path


def _html(value):
    """Escape untrusted engine/LLM values before inserting them into HTML."""
    return _esc(str(value), quote=True)


def _optional_pct(value) -> str:
    """Format a percent-unit metric. Missing/None renders N/A, never a fake 0.0%."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _finite_float(value):
    """None if the value is missing or not a real number. 0.0 is kept (measured)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _optional_usd(value) -> str:
    n = _finite_float(value)
    return "N/A" if n is None else f"${n:,.0f}"


def _score_display(value) -> str:
    """AI score card. Missing/unparseable renders N/A, never a fake 0/100."""
    n = _finite_float(value)
    return "N/A" if n is None else f"{n:g}/100"


def _cvar_95_display(monte: dict, fallback_equity: float) -> str:
    """CVaR as a percent of equity. Derive it; never default a missing key to 0.0%."""
    raw = monte.get("cvar_95_pct") if isinstance(monte, dict) else None
    if isinstance(raw, (int, float)):
        return f"{float(raw):.1f}%"
    if not isinstance(monte, dict):
        return "N/A"
    cvar = monte.get("cvar_95")
    equity = monte.get("current_equity") or fallback_equity
    try:
        cvar_f = float(cvar)
        eq_f = float(equity)
    except (TypeError, ValueError):
        return "N/A"
    if eq_f <= 0:
        return "N/A"
    return f"{(cvar_f / eq_f) * 100:.1f}%"


def _json_script(value):
    """Serialize data for an inline script without permitting ``</script>``."""
    return (
        json.dumps(value)
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("&", r"\u0026")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )


BASE = Path(__file__).parent
engine_results_path = Path(os.environ.get("GESH_ENGINE_RESULTS") or BASE / "engine_results.json")
with open(engine_results_path) as f:
    eng = json.load(f)

holdings = eng["holdings"]
equity_sum = eng["equity_summary"]
consensus = eng.get("consensus", {})
kelly_res = eng.get("kelly", {})
monte = eng.get("monte_carlo", {})
regime_raw = eng.get("regime", {})
vol_models = eng.get("vol_models", {})
alpha_table = eng.get("alpha_table", [])
tax_report = eng.get("tax_report", {})
yield_data = eng.get("yield_data", {})
explanations = eng.get("explanations", {})
ai = eng.get("ai_result", {})

total = equity_sum.get("total", 0)
cash = equity_sum.get("cash", 0)
portfolio_equity = equity_sum.get("portfolio_equity", 0)

# Regime list
regimes_list = regime_raw.get("regimes", []) if isinstance(regime_raw, dict) else []
regime_by_sym = {rd["symbol"]: rd for rd in regimes_list}

# Signal overrides
cash_blocked = cash < 500.0
rsi_overrides = {}
for rd in regimes_list:
    sym = rd.get("symbol", "")
    rsi = rd.get("rsi") or 0  # regime may emit rsi=None on a short/missing series
    k_act = kelly_res.get(sym, {}).get("action", "HOLD")
    if rsi >= 70 and k_act == "ADD":
        rsi_overrides[sym] = rsi


# Action plan (after overrides)
def final_action(sym):
    k_act = kelly_res.get(sym, {}).get("action", "HOLD")
    rsi = regime_by_sym.get(sym, {}).get("rsi") or 0
    if cash_blocked and k_act == "ADD":
        return "HOLD", f"Cash ${cash:.0f}<$500 (Rule 8)"
    if rsi >= 70 and k_act == "ADD":
        return "HOLD", f"RSI={rsi:.0f}≥70 (Rule 5)"
    return k_act, ""


# Day change — computed from the snapshot's previous-close equity (mirrors
# server.py's /api/portfolio calculation). Falls back to 0 when the snapshot
# predates the adjusted_equity_previous_close export.
snap = eng.get("snapshot", {})
_eqs_hdr = eng.get("equity_summary", {}) or snap.get("equity_summary", {}) or {}
_prev_eq = float(_eqs_hdr.get("adjusted_equity_previous_close") or 0)
# account_equity is the app-headline figure — the SAME basis as the prev-close
# baseline. 'total' (positions + settled cash) excludes pending deposits and
# would bias the day-change on deposit days.
_cur_eq = float(
    _eqs_hdr.get("account_equity") or _eqs_hdr.get("total") or _eqs_hdr.get("portfolio_equity") or 0
)
if _prev_eq > 0 and _cur_eq > 0:
    day_gain_usd = _cur_eq - _prev_eq
    day_gain_pct = day_gain_usd / _prev_eq * 100
else:
    day_gain_usd = 0.0
    day_gain_pct = 0.0
day_gain_col = "#00e87a" if day_gain_usd >= 0 else "#ff3c5a"


# Score colour helpers
def score_col(s):
    n = _finite_float(s)
    if n is None:
        return "#888"
    if n >= 75:
        return "#00e87a"
    if n >= 50:
        return "#f0b429"
    return "#ff3c5a"


def verdict_icon(v):
    v = (v or "").upper()
    if "STRONG BUY" in v:
        return "🚀"
    if "BUY" in v:
        return "🟢"
    if "HOLD" in v:
        return "🟡"
    if "SELL" in v:
        return "🔴"
    return "⚪"


def regime_badge(r):
    r = (r or "").upper()
    col = {
        "TRENDING": "#00d4ff",
        "MOMENTUM-HIGH": "#f0b429",
        "BREAKOUT-READY": "#00e87a",
        "RANGING": "#aaa",
        "MOMENTUM-LOW": "#ff7c3a",
    }.get(r, "#aaa")
    return f'<span class="badge" style="background:{col}22;color:{col};border:1px solid {col}44">{_html(r)}</span>'


def pct_bar(val, max_val=100):
    pct = min(abs(float(val or 0)) / max_val * 100, 100)
    col = "#00e87a" if float(val or 0) >= 0 else "#ff3c5a"
    return f'<div class="bar-bg"><div class="bar-fill" style="width:{pct:.0f}%;background:{col}"></div></div>'


def snowflake_bar(val, label, max_val=100):
    pct = min(float(val or 0) / max_val * 100, 100)
    col = "#00d4ff"
    return f"""<div class="sf-row"><span class="sf-label">{label}</span>
    <div class="sf-bar-bg"><div class="sf-bar-fill" style="width:{pct:.0f}%;background:{col}"></div></div>
    <span class="sf-val">{int(val or 0)}</span></div>"""


now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
fetch_at = snap.get("fetched_at", "")[:19].replace("T", " ")

# Degraded-run provenance banner — surfaced when the chain ran on a stale
# snapshot (run_engines2/run_ai2 stamp degraded/age/source): research-only,
# no paper records were created for this run.
notes_banner_html = ""
_snap_notes = snap.get("notes") if isinstance(snap, dict) else None
if _snap_notes:
    notes_banner_html = (
        '<div class="override-banner" style="background:#f0b42915;border-color:#f0b42944;color:#f0b429">'
        f"{_html(str(_snap_notes))}</div>"
    )

degraded_banner_html = ""
if eng.get("degraded"):
    _age_s = eng.get("snapshot_age_seconds")
    _age_txt = f"{_age_s / 3600:.1f}h old" if isinstance(_age_s, (int, float)) else "age unknown"
    degraded_banner_html = (
        '<div style="background:#ff3c5a22;border:1px solid #ff3c5a;color:#ff3c5a;'
        'padding:10px 14px;border-radius:8px;margin:0 0 14px;font-weight:bold">'
        f"⚠️ DEGRADED RUN — snapshot {_age_txt} "
        f"(source: {_esc(str(eng.get('snapshot_source', 'UNKNOWN')))}). "
        "Research-only: prices/cash may be stale; no paper records were created for this run."
        "</div>"
    )

# ─── Ticker tape ─────────────────────────────────────────────────────────────
tape_items = []
for h in holdings:
    sym = h["symbol"]
    px = h["price"]
    pch = h["percent_change"]
    col = "#00e87a" if pch >= 0 else "#ff3c5a"
    arrow = "▲" if pch >= 0 else "▼"
    tape_items.append(
        f'<span class="tape-item"><b>{_html(sym)}</b> <span style="color:{col}">{arrow} ${px:,.2f} ({pch:+.2f}%)</span></span>'
    )
tape_html = " &nbsp;|&nbsp; ".join(tape_items * 3)


# ─── Holdings cards ───────────────────────────────────────────────────────────
def holding_card(h):
    sym = h["symbol"]
    px = h["price"]
    qty = h["quantity"]
    eq = h["equity"]
    avg = h["average_buy_price"]
    pch = h["percent_change"]
    wt = eq / total * 100

    cons = consensus.get(sym, {})
    verdict = cons.get("verdict", "N/A")
    score = cons.get("raw_score", cons.get("score", 0))
    nodes = cons.get("active_nodes", cons.get("nodes_voted", 0))
    conf = cons.get("confidence", 0)

    kly = kelly_res.get(sym, {})
    opt_pct = kly.get("optimal_pct", kly.get("recommended_pct", 0)) or 0
    curr_pct = eq / total * 100

    reg = regime_by_sym.get(sym, {})
    reg_name = reg.get("regime", "?")
    rsi_val = reg.get("rsi") or 0
    adx_val = reg.get("adx") or 0

    act, reason = final_action(sym)

    ai_h = {}
    for ha in ai.get("holdings_analysis") or []:
        if ha.get("symbol") == sym:
            ai_h = ha
            break

    scores = ai_h.get("scores", {})
    fv = ai_h.get("fair_value", {})
    fv_est = fv.get("estimate", 0)
    fv_diff = fv.get("pct_diff", 0)
    fv_status = fv.get("status", "")
    fv_col = "#ff3c5a" if "Over" in fv_status else "#00e87a" if "Under" in fv_status else "#f0b429"
    pred = ai_h.get("prediction", {})

    risks_raw = ai_h.get("risks", [])
    rewards_raw = ai_h.get("rewards", [])

    px_col = "#00e87a" if pch >= 0 else "#ff3c5a"
    act_col = {"ADD": "#00e87a", "TRIM": "#f0b429", "HOLD": "#aaa", "AVOID": "#ff3c5a"}.get(
        act, "#aaa"
    )

    sf_html = ""
    for lbl, key in [
        ("VAL", "valuation"),
        ("GRO", "growth"),
        ("HLT", "health"),
        ("MOM", "momentum"),
        ("DIV", "dividend"),
    ]:
        sf_html += snowflake_bar(scores.get(key, 0), lbl)

    risks_html = "".join(
        f'<li class="risk-item">{_html(x.get("text", "") if isinstance(x, dict) else x)}</li>'
        for x in risks_raw[:2]
    )
    rewards_html = "".join(
        f'<li class="reward-item">{_html(x.get("text", "") if isinstance(x, dict) else x)}</li>'
        for x in rewards_raw[:2]
    )

    pred_3m = pred.get("3m", "—") if isinstance(pred, dict) else "—"
    pred_12m = pred.get("12m", "—") if isinstance(pred, dict) else "—"
    verdict_ai = ai_h.get("verdict", "—")

    vol_sym = vol_models.get(sym, {})
    # compare_vol_models emits *_annual_pct in percent units, or None when GARCH
    # was not estimated (`arch` missing). Never coerce None to 0.0%.
    garch_display = _optional_pct(vol_sym.get("garch_annual_pct"))
    ewma_display = _optional_pct(vol_sym.get("ewma_annual_pct"))

    exp_sym = explanations.get(sym, {})
    headline = exp_sym.get("headline", "") if isinstance(exp_sym, dict) else ""

    return f"""
<div class="holding-card">
  <div class="hc-header">
    <div>
      <span class="hc-sym">{_html(sym)}</span>
      {regime_badge(reg_name)}
      <span class="hc-verdict-badge" style="color:{act_col};border:1px solid {act_col}44;background:{act_col}15;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">{act}{(" ✋" if reason else "")}</span>
    </div>
    <div style="text-align:right">
      <div class="hc-price" style="color:{px_col}">${px:,.2f} ({pch:+.2f}%)</div>
      <div class="hc-equity">${eq:,.0f} &nbsp;|&nbsp; {wt:.1f}% of portfolio</div>
    </div>
  </div>
  <div class="hc-meta">
    {qty:.3f} sh &nbsp;·&nbsp; avg ${avg:.2f} &nbsp;·&nbsp; gain {((px / avg - 1) * 100):+.1f}%
    &nbsp;·&nbsp; RSI {rsi_val:.0f} &nbsp;·&nbsp; ADX {adx_val:.0f}
    {f'<span style="color:#ff7c3a;font-size:10px;margin-left:8px">⚠️ {reason}</span>' if reason else ""}
  </div>
  <div class="hc-body">
    <div class="hc-col">
      <div class="hc-section-title">Consensus <span style="font-size:10px;color:#888">({nodes} nodes, conf {conf:.0f}%)</span></div>
      <div class="consensus-row">
        <span class="cons-icon">{verdict_icon(verdict)}</span>
        <span class="cons-verdict">{_html(verdict)}</span>
        <span class="cons-score" style="color:{score_col((score + 1) / 2 * 100)}">score {score:+.2f}</span>
      </div>
      {f'<div class="explain-headline">{_html(headline)}</div>' if headline else ""}
      <div class="hc-section-title" style="margin-top:10px">Kelly Sizing</div>
      <div class="kelly-row">
        <span>Now {curr_pct:.1f}%</span>
        <span style="color:#00d4ff">→ Optimal {opt_pct:.1f}%</span>
      </div>
      <div style="font-size:11px;color:#ccc;margin:2px 0 4px">{_html(report_share_plan.holding_share_caption(h, kly, total))}</div>
      {pct_bar(curr_pct, 30)}
      <div class="hc-section-title" style="margin-top:10px">Volatility</div>
      <div style="font-size:11px;color:#aaa">GARCH {garch_display} &nbsp;|&nbsp; EWMA {ewma_display} annualised</div>
    </div>
    <div class="hc-col">
      <div class="hc-section-title">Snowflake Scores</div>
      {sf_html}
      <div class="hc-section-title" style="margin-top:10px">Fair Value</div>
      <div>
        <span style="font-size:13px;color:{fv_col}">{_html(fv_status)}</span>
        <span style="font-size:11px;color:#888;margin-left:6px">est ${fv_est:,.0f} ({fv_diff:+.1f}%)</span>
      </div>
    </div>
    <div class="hc-col">
      <div class="hc-section-title">Risks</div>
      <ul class="risk-list">{risks_html}</ul>
      <div class="hc-section-title" style="margin-top:8px">Rewards</div>
      <ul class="reward-list">{rewards_html}</ul>
    </div>
    <div class="hc-col">
      <div class="hc-section-title">Predictions</div>
      <div class="pred-block">
        <div class="pred-label">3M</div>
        <div class="pred-text">{_html(pred_3m)}</div>
      </div>
      <div class="pred-block" style="margin-top:8px">
        <div class="pred-label">12M</div>
        <div class="pred-text">{_html(pred_12m)}</div>
      </div>
      <div class="hc-section-title" style="margin-top:10px">AI Verdict</div>
      <div style="font-size:11px;color:#ccc;line-height:1.5">{_html(verdict_ai)}</div>
    </div>
  </div>
</div>"""


holdings_html = "\n".join(holding_card(h) for h in holdings)

# ─── Action table ─────────────────────────────────────────────────────────────
recs = ai.get("portfolio_recommendations", [])
action_rows = ""
pri_col = {"URGENT": "#ff3c5a", "High": "#f0b429", "Medium": "#00d4ff", "Low": "#888"}
for rec in recs:
    act_text = rec.get("action", "") if isinstance(rec, dict) else str(rec)
    pri = rec.get("priority", "") if isinstance(rec, dict) else ""
    rat = rec.get("rationale", "") if isinstance(rec, dict) else ""
    col = pri_col.get(pri, "#888")
    action_rows += f"""<tr>
      <td><span style="color:{col};font-weight:700;font-size:11px">{_html(pri)}</span></td>
      <td style="color:#e0e0e0">{_html(act_text)}</td>
      <td style="color:#888;font-size:11px">{_html(rat)}</td>
    </tr>"""


# ─── Share plan + paper/gate audit (always visible, even on all-HOLD days) ──
share_plan_section_html = report_share_plan.share_plan_html(eng)
gate_audit_html = report_share_plan.gate_audit_html(eng)
signal_gate_banner_html = report_share_plan.signal_gate_banner_html(eng)


def _provider_banner(eng: dict) -> str:
    """Disclose when a non-Anthropic model authored this analysis.

    The daily chain can fail over to Grok/Gemini when Claude is unavailable. That
    output is not grammar-constrained and omitted keys are filled with blanks, so
    the reader must be able to see which vendor wrote the page — an undisclosed
    swap would let a degraded run read as a normal one.
    """
    provider = str(eng.get("ai_provider") or "anthropic").lower()
    if not eng.get("ai_failover_count") or provider in ("", "anthropic"):
        return ""
    return (
        '<div class="override-banner" style="background:#f0b42915;border-color:#f0b42944;color:#f0b429">'
        + f"⚠️ MODEL FAILOVER — Anthropic was unavailable; this analysis was produced by "
        + f"<b>{_esc(provider)}</b>. Output was not grammar-constrained and any field the "
        + "model omitted is blank rather than estimated. Engine numbers (regime, Kelly, "
        + "Monte Carlo, gates) are unaffected — they are computed, not generated.</div>"
    )


provider_banner_html = _provider_banner(eng)


# ─── Diversification radar section (advisory-only, new non-held names) ────────
# Every string cell is HTML-escaped (company/sector come from yfinance, thesis/swap
# rationale may originate downstream) — never trust free text in an f-string table.
def _radar_section(radar: dict) -> str:
    if not radar:
        return ""
    mode = radar.get("mode", "NO_ACTION")
    weights = radar.get("existing_sector_weights") or {}
    cands = radar.get("candidates") or []
    swaps = radar.get("swap_suggestions") or []
    mode_col = {"BUY": "#00e87a", "SWAP_ONLY": "#f0b429", "NO_ACTION": "#888"}.get(mode, "#888")
    sw = sorted(weights.items(), key=lambda x: -x[1])[:4]
    sw_txt = " · ".join(f"{_esc(str(k))} {v * 100:.0f}%" for k, v in sw) if sw else "—"
    head = (
        f'<div class="section-title">💎 Diversification Radar '
        f'<span style="font-size:11px;color:{mode_col};border:1px solid {mode_col};'
        f'border-radius:8px;padding:1px 7px;vertical-align:middle">{_esc(mode)}</span></div>'
        f'<div style="color:#888;font-size:12px;margin:-6px 0 10px">New, non-held opportunities · '
        f"deployable ${radar.get('deployable_cash', 0):,.0f} · {radar.get('open_slots', 0)} open slot(s) · "
        f"book: {sw_txt}</div>"
    )
    body = ""
    if cands:
        rows = ""
        for c in cands:
            up = c.get("analyst_upside")
            up_txt = f"{up:+.0f}%" if isinstance(up, (int, float)) else "—"
            corr = c.get("max_corr_to_holdings")
            corr_txt = f"{corr:.2f}" if isinstance(corr, (int, float)) else "—"
            if c.get("actionable") is False:
                suggest = '<span style="color:#f0b429">preview · fund via swap</span>'
            else:
                suggest = f"{c.get('recommended_shares', 0)} ≈${c.get('recommended_usd', 0):,.0f}"
            rows += (
                f'<tr><td><b style="color:#00d4ff">{_esc(str(c.get("symbol", "")))}</b></td>'
                f"<td>{_esc(str(c.get('sector', '')))}</td>"
                f'<td style="text-align:right">{c.get("composite_score", "—")}</td>'
                f"<td>{_esc(str(c.get('consensus_verdict', '—')))}</td>"
                f'<td style="text-align:right;color:#00e87a">{up_txt}</td>'
                f'<td style="text-align:right">{corr_txt}</td>'
                f'<td style="text-align:right">{suggest}</td>'
                f'<td style="color:#888;font-size:11px">{_esc(str(c.get("rationale", "")))}</td></tr>'
            )
        body += (
            '<div class="table-card" style="margin-bottom:12px"><table class="action-table">'
            "<thead><tr><th>Symbol</th><th>Sector</th><th>Score</th><th>Consensus</th>"
            "<th>Analyst</th><th>Corr→Book</th><th>Suggest</th><th>Thesis</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )
    if swaps and mode != "BUY":
        srows = ""
        for s in swaps:
            srows += (
                f'<tr><td style="color:#ff3c5a"><b>{_esc(str(s.get("trim_symbol", "")))}</b></td>'
                f'<td style="color:#00e87a"><b>{_esc(str(s.get("fund_new_symbol", "")))}</b></td>'
                f'<td style="color:#888;font-size:11px">{_esc(str(s.get("rationale", "")))}</td></tr>'
            )
        body += (
            '<div class="table-card" style="margin-bottom:12px"><table class="action-table">'
            "<thead><tr><th>Trim</th><th>Fund New</th><th>Why</th></tr></thead>"
            f"<tbody>{srows}</tbody></table></div>"
        )
    if not cands and not swaps:
        body += '<div style="color:#888;font-size:12px;margin-bottom:12px">No new-name candidates today.</div>'
    disc = (
        '<div style="color:#666;font-size:10px;margin:-4px 0 16px">Advisory research only — NOT '
        "financial advice. New-name suggestions are never auto-traded; PAPER_MODE unaffected.</div>"
    )
    return head + body + disc


div_radar_html = _radar_section(eng.get("diversification_candidates") or {})

# ─── MC histogram data ────────────────────────────────────────────────────────
buckets = monte.get("buckets", [])
mc_labels = [b.get("label", "") for b in buckets]
mc_data = [b.get("pct", 0) for b in buckets]
mc_colors = []
var95 = _finite_float(monte.get("var_95_pct") if isinstance(monte, dict) else None)
curr_eq = monte.get("current_equity", total) if isinstance(monte, dict) else total
for b in buckets:
    center = b.get("center", 0)
    if var95 is None:
        mc_colors.append("'#00d4ff'")
    elif center < curr_eq * (1 - var95 / 100):
        mc_colors.append("'#ff3c5a'")
    elif center < curr_eq:
        mc_colors.append("'#f0b429'")
    else:
        mc_colors.append("'#00d4ff'")

# ─── Kelly chart data ─────────────────────────────────────────────────────────
kelly_syms = [h["symbol"] for h in holdings]
kelly_curr = [round(h["equity"] / total * 100, 1) for h in holdings]
kelly_opts = []
for sym in kelly_syms:
    k = kelly_res.get(sym, {})
    opt = k.get("optimal_pct", k.get("recommended_pct", 0)) or 0
    kelly_opts.append(round(float(opt), 1))

# ─── Portfolio risks ──────────────────────────────────────────────────────────
port_risks = ai.get("portfolio_risks", [])
risks_html_list = ""
sev_col = {"High": "#ff3c5a", "Medium": "#f0b429", "Low": "#00d4ff"}
for rk in port_risks:
    txt = rk.get("text", "") if isinstance(rk, dict) else str(rk)
    sev = rk.get("severity", "") if isinstance(rk, dict) else ""
    col = sev_col.get(sev, "#888")
    risks_html_list += f'<div class="port-risk"><span class="sev-pill" style="background:{col}22;color:{col}">{_html(sev)}</span> {_html(txt)}</div>\n'

# ─── Regime cards ─────────────────────────────────────────────────────────────
regime_cards_html = ""
for rd in regimes_list:
    sym = rd.get("symbol", "")
    reg = rd.get("regime", "")
    adx = rd.get("adx") or 0
    rsi = rd.get("rsi") or 0
    bb = rd.get("bb_width") or 0
    pctb = rd.get("pct_b") or 0
    desc = rd.get("description", "")
    col = {
        "TRENDING": "#00d4ff",
        "MOMENTUM-HIGH": "#f0b429",
        "BREAKOUT-READY": "#00e87a",
        "RANGING": "#aaa",
    }.get(reg, "#aaa")
    regime_cards_html += f"""<div class="regime-card" style="border-left:3px solid {col}">
  <div class="rc-sym">{_html(sym)}</div>
  <div class="rc-regime" style="color:{col}">{_html(reg)}</div>
  <div class="rc-stats">ADX {adx:.0f} &nbsp;|&nbsp; RSI {rsi:.0f} &nbsp;|&nbsp; BB-W {bb:.0f}% &nbsp;|&nbsp; %B {pctb:.0f}%</div>
  <div class="rc-desc">{_html(desc)}</div>
</div>"""

# ─── Vol model table ──────────────────────────────────────────────────────────
vol_rows = ""
for sym in kelly_syms:
    vd = vol_models.get(sym, {})
    gv = vd.get("garch_annual_pct")
    ev = vd.get("ewma_annual_pct")
    diff_str = ""
    if isinstance(gv, (int, float)) and isinstance(ev, (int, float)) and ev:
        diff = (gv - ev) / ev * 100
        diff_str = f"{'↑' if diff > 0 else '↓'} {abs(diff):.0f}%"
    vol_rows += f"""<tr>
      <td>{_html(sym)}</td>
      <td style="color:#00d4ff">{_optional_pct(gv)}</td>
      <td style="color:#f0b429">{_optional_pct(ev)}</td>
      <td style="color:#888;font-size:11px">{diff_str}</td>
    </tr>"""

# ─── Alpha table ──────────────────────────────────────────────────────────────
alpha_rows = ""
for a in alpha_table or []:
    sym = a.get("symbol") or a.get("ticker", "?")
    vs_mkt = a.get("vs_market_pct")
    vs_sec = a.get("vs_sector_pct")
    ret_1m = a.get("stock_return_pct")
    col_mkt = alpha_value_color(vs_mkt)
    col_sec = alpha_value_color(vs_sec)
    alpha_rows += f"""<tr>
      <td>{_html(sym)}</td>
      <td style="color:{col_mkt}">{format_alpha_percent(vs_mkt)}</td>
      <td style="color:{col_sec}">{format_alpha_percent(vs_sec)}</td>
      <td style="color:#aaa">{format_alpha_percent(ret_1m)}</td>
    </tr>"""

# ─── Tax harvest ──────────────────────────────────────────────────────────────
tax_cands = tax_report.get("candidates", [])
tax_html = ""
if tax_cands:
    for tc in tax_cands:
        sym = tc.get("symbol", "") if isinstance(tc, dict) else str(tc)
        loss = tc.get("unrealized_loss", 0) if isinstance(tc, dict) else 0
        tax_html += f'<div class="tax-item"><span>{_html(sym)}</span><span style="color:#ff7c3a">${loss:,.0f} harvestable</span></div>'
else:
    # NOT a claim that the book is in profit — that was a fabricated claim.
    # An empty candidate list means no loss cleared MIN_LOSS_USD ($500), which
    # is a very different statement from "nothing is underwater" (rule #60).
    # Report the threshold, and the count of losing positions when the lot
    # layer knows it.
    _lot_views = tax_report.get("lots_by_symbol") or {}
    _losing = len(_lot_views)
    _detail = f" {_losing} position(s) are at a loss but below the threshold." if _losing else ""
    tax_html = (
        '<div style="color:#666;font-style:italic">No position\'s unrealized '
        f"loss reaches the $500 harvest threshold.{_html(_detail)}</div>"
    )

# Purchase-side wash-sale warnings (lot layer). The base scan only tracks prior
# harvest SALES, so these are invisible to it — surface them explicitly.
_wash_warn = tax_report.get("purchase_wash_warnings") or []
if _wash_warn:
    tax_html += (
        '<div style="margin-top:10px;padding:8px;border-left:3px solid #f0b429;'
        'background:#1a1508"><div style="color:#f0b429;font-weight:600;'
        'margin-bottom:4px">⚠ Wash-sale exposure from recent purchases</div>'
    )
    for _w in _wash_warn:
        tax_html += (
            f'<div class="tax-item"><span>{_html(_w["ticker"])}</span>'
            f'<span style="color:#f0b429">bought {int(_w["bought_days_ago"])}d ago '
            f"· safe in {int(_w['days_until_safe'])}d</span></div>"
        )
    tax_html += "</div>"

_lot_cov = tax_report.get("lot_coverage") or {}
if _lot_cov.get("unavailable"):
    tax_html += (
        f'<div style="margin-top:6px;color:#888;font-size:11px">Lot data '
        f"unavailable for {int(_lot_cov['unavailable'])} of "
        f"{int(_lot_cov.get('checked', 0))} symbol(s) — wash-sale status "
        f"unknown, not clear.</div>"
    )

# ─── Yield tiers ──────────────────────────────────────────────────────────────
tiers = yield_data.get("tiers", {}) if isinstance(yield_data, dict) else {}
yield_html = ""
if tiers:
    tier_labels = {
        "tier1": "Conservative (< 6% APY)",
        "tier2": "Moderate (6–15% APY)",
        "tier3": "Aggressive (> 15% APY)",
    }
    tier_cols = {"tier1": "#00e87a", "tier2": "#f0b429", "tier3": "#ff7c3a"}
    for tk, tl in tier_labels.items():
        items = tiers.get(tk, []) if isinstance(tiers, dict) else []
        if not items:
            continue
        col = tier_cols.get(tk, "#aaa")
        yield_html += f'<div class="yield-tier"><div class="yield-tier-label" style="color:{col}">{_html(tl)}</div>'
        for item in items[:3]:
            name = item.get("name", "") if isinstance(item, dict) else str(item)
            apy = item.get("apy", 0) if isinstance(item, dict) else 0
            risk = item.get("risk", "") if isinstance(item, dict) else ""
            yield_html += f'<div class="yield-item"><span class="yi-name">{_html(name)}</span><span class="yi-apy" style="color:{col}">{apy:.1f}% APY</span><span class="yi-risk">{_html(risk)}</span></div>'
        yield_html += "</div>"
else:
    yield_html = '<div style="color:#666;font-style:italic">Yield data unavailable.</div>'

# ─── Scores box ──────────────────────────────────────────────────────────────
port_score = _finite_float(ai.get("portfolio_score") if isinstance(ai, dict) else None)
risk_score = _finite_float(ai.get("risk_score") if isinstance(ai, dict) else None)
div_score = _finite_float(ai.get("diversification_score") if isinstance(ai, dict) else None)
port_type = ai.get("portfolio_type", "")
port_verdict = ai.get("portfolio_verdict", "")
mkt_ctx = ai.get("market_context", "")

var95_pct = _finite_float(monte.get("var_95_pct") if isinstance(monte, dict) else None)
cvar95_display = _cvar_95_display(monte, monte.get("current_equity") or total or portfolio_equity)
prob_n = _finite_float(monte.get("prob_profit_pct") if isinstance(monte, dict) else None)
prob_display = "N/A" if prob_n is None else f"{prob_n:.0f}%"
mc_median_display = _optional_usd(monte.get("median") if isinstance(monte, dict) else None)

if var95_pct is None:
    var_col, var_badge = "#888", "n/a"
elif var95_pct > 15:
    var_col, var_badge = "#ff3c5a", "⚠️ RED >15%"
elif var95_pct > 10:
    var_col, var_badge = "#f0b429", "✓ OK"
else:
    var_col, var_badge = "#00e87a", "✓ OK"
cash_col = "#ff3c5a" if cash < 500 else "#00e87a"

# ─── Full HTML ────────────────────────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GESH Financial — Daily Report {now_str[:10]}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#080d14;color:#e0e0e0;font-family:'JetBrains Mono','Courier New',monospace;font-size:13px;min-height:100vh}}
  a{{color:#00d4ff;text-decoration:none}}

  /* Layout */
  .container{{max-width:1400px;margin:0 auto;padding:16px}}
  .header{{display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid #1a2840;margin-bottom:16px}}
  .header h1{{font-size:20px;color:#00d4ff;letter-spacing:2px}}
  .header .ts{{color:#555;font-size:11px}}

  /* Ticker tape */
  .tape{{background:#0a1628;border:1px solid #1a2840;border-radius:6px;padding:10px 16px;margin-bottom:16px;overflow:hidden;white-space:nowrap;font-size:12px}}
  .tape-item{{margin-right:4px}}

  /* Stats grid */
  .stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}}
  .stat-card{{background:#0d1520;border:1px solid #1a2840;border-radius:8px;padding:14px;text-align:center}}
  .stat-card .sv{{font-size:26px;font-weight:700;margin:4px 0}}
  .stat-card .sl{{font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px}}

  /* Verdict / AI card */
  .ai-card{{background:#0d1520;border:1px solid #1a2840;border-radius:8px;padding:16px;margin-bottom:16px}}
  .ai-card h3{{color:#00d4ff;font-size:13px;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}}
  .ai-text{{color:#bbb;font-size:12px;line-height:1.7}}

  /* Override banner */
  .override-banner{{background:#ff3c5a15;border:1px solid #ff3c5a44;border-radius:6px;padding:10px 14px;margin-bottom:16px;font-size:12px;color:#ff7c9a}}

  /* Holdings cards */
  .section-title{{font-size:12px;color:#555;text-transform:uppercase;letter-spacing:2px;margin:20px 0 10px}}
  .holding-card{{background:#0d1520;border:1px solid #1a2840;border-radius:10px;padding:16px;margin-bottom:12px}}
  .hc-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
  .hc-sym{{font-size:18px;font-weight:700;color:#fff;margin-right:10px}}
  .hc-price{{font-size:15px;font-weight:600}}
  .hc-equity{{font-size:11px;color:#555}}
  .hc-meta{{font-size:11px;color:#666;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid #1a2840}}
  .hc-body{{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px}}
  @media(max-width:900px){{.hc-body{{grid-template-columns:1fr 1fr}}}}
  .hc-section-title{{font-size:10px;color:#444;text-transform:uppercase;letter-spacing:1px;margin-bottom:5px}}
  .badge{{font-size:9px;font-weight:700;padding:2px 7px;border-radius:3px;margin-left:4px;letter-spacing:1px}}
  .consensus-row{{display:flex;align-items:center;gap:8px;margin:4px 0}}
  .cons-icon{{font-size:16px}}
  .cons-verdict{{font-size:13px;font-weight:700;color:#e0e0e0}}
  .cons-score{{font-size:11px}}
  .kelly-row{{display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px}}
  .bar-bg{{background:#1a2840;border-radius:3px;height:4px;width:100%;margin-bottom:4px}}
  .bar-fill{{height:4px;border-radius:3px;transition:width 0.3s}}
  .sf-row{{display:flex;align-items:center;gap:6px;margin-bottom:3px}}
  .sf-label{{font-size:9px;color:#555;width:25px;flex-shrink:0;text-transform:uppercase}}
  .sf-bar-bg{{flex:1;background:#1a2840;border-radius:2px;height:6px}}
  .sf-bar-fill{{height:6px;border-radius:2px}}
  .sf-val{{font-size:9px;color:#888;width:20px;text-align:right}}
  .explain-headline{{font-size:10px;color:#555;font-style:italic;margin-top:4px}}
  .risk-list,.reward-list{{list-style:none;padding:0}}
  .risk-item{{font-size:10px;color:#ff7c9a;line-height:1.5;margin-bottom:3px;padding-left:10px;border-left:2px solid #ff3c5a33}}
  .risk-item.sev-high{{color:#ff3c5a}}
  .reward-item{{font-size:10px;color:#88d4a0;line-height:1.5;margin-bottom:3px;padding-left:10px;border-left:2px solid #00e87a33}}
  .pred-block{{background:#0a1628;border-radius:4px;padding:6px 8px}}
  .pred-label{{font-size:9px;color:#00d4ff;font-weight:700;margin-bottom:2px}}
  .pred-text{{font-size:10px;color:#bbb;line-height:1.5}}

  /* Charts */
  .charts-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
  @media(max-width:700px){{.charts-grid{{grid-template-columns:1fr}}}}
  .chart-card{{background:#0d1520;border:1px solid #1a2840;border-radius:8px;padding:16px}}
  .chart-title{{font-size:11px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}}

  /* Regime grid */
  .regime-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-bottom:16px}}
  .regime-card{{background:#0d1520;border-radius:6px;padding:12px}}
  .rc-sym{{font-size:15px;font-weight:700;color:#fff;margin-bottom:2px}}
  .rc-regime{{font-size:11px;font-weight:700;letter-spacing:1px;margin-bottom:4px}}
  .rc-stats{{font-size:10px;color:#555;margin-bottom:4px}}
  .rc-desc{{font-size:10px;color:#888;line-height:1.4}}

  /* Action table */
  .action-table{{width:100%;border-collapse:collapse;margin-bottom:16px}}
  .action-table th{{font-size:10px;color:#555;text-align:left;padding:6px 10px;border-bottom:1px solid #1a2840;text-transform:uppercase;letter-spacing:1px}}
  .action-table td{{padding:8px 10px;border-bottom:1px solid #0d1f33;font-size:12px;vertical-align:top}}
  .action-table tr:hover td{{background:#0a1628}}

  /* Vol + alpha tables */
  .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
  @media(max-width:700px){{.two-col{{grid-template-columns:1fr}}}}
  .table-card{{background:#0d1520;border:1px solid #1a2840;border-radius:8px;padding:14px}}
  .mini-table{{width:100%;border-collapse:collapse}}
  .mini-table th{{font-size:9px;color:#444;text-align:left;padding:4px 8px;border-bottom:1px solid #1a2840;text-transform:uppercase}}
  .mini-table td{{padding:5px 8px;border-bottom:1px solid #0d1f33;font-size:11px}}

  /* Portfolio risks */
  .port-risk{{background:#0d1520;border:1px solid #1a2840;border-radius:6px;padding:10px 14px;margin-bottom:8px;font-size:12px;line-height:1.6}}
  .sev-pill{{font-size:9px;font-weight:700;padding:2px 6px;border-radius:3px;margin-right:8px}}

  /* Tax + Yield */
  .tax-item{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1a2840;font-size:12px}}
  .yield-tier{{margin-bottom:12px}}
  .yield-tier-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}}
  .yield-item{{display:grid;grid-template-columns:1fr 100px 80px;gap:8px;padding:5px 0;border-bottom:1px solid #0d1f33;font-size:11px;align-items:center}}
  .yi-name{{color:#e0e0e0}}
  .yi-apy{{font-weight:700}}
  .yi-risk{{color:#555;font-size:10px}}

  /* Footer */
  .footer{{text-align:center;color:#333;font-size:10px;padding:24px 0;border-top:1px solid #1a2840;margin-top:24px}}
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <div>
      <h1>⚡ GESH FINANCIAL</h1>
      <div style="font-size:11px;color:#00d4ff;margin-top:2px">AI-Powered Portfolio Observability &nbsp;|&nbsp; {_html(port_type)}</div>
    </div>
    <div class="ts" style="text-align:right">
      <div style="font-size:12px;color:{day_gain_col}">${day_gain_usd:+,.2f} ({day_gain_pct:+.2f}%) today</div>
      <div>Generated {now_str}</div>
      <div>Data: {fetch_at} UTC</div>
    </div>
  </div>

  {degraded_banner_html}
  {notes_banner_html}

  <!-- Ticker tape -->
  <div class="tape">{tape_html}</div>

  <!-- Stats grid -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="sl">Portfolio Total</div>
      <div class="sv" style="color:#00e87a">${total:,.0f}</div>
    </div>
    <div class="stat-card">
      <div class="sl">AI Score</div>
      <div class="sv" style="color:{score_col(port_score)}">{_score_display(port_score)}</div>
      <div style="font-size:9px;color:#7a8a99">model-generated, not a calculation</div>
    </div>
    <div class="stat-card">
      <div class="sl">AI Risk Opinion</div>
      <div class="sv" style="color:{score_col(None if risk_score is None else 100 - risk_score)}">{_score_display(risk_score)}</div>
      <div style="font-size:9px;color:#7a8a99">model-generated, not a calculation</div>
    </div>
    <div class="stat-card">
      <div class="sl">VaR 95% (21d)</div>
      <div class="sv" style="color:{var_col}">{_optional_pct(var95_pct)}</div>
      <div style="font-size:9px;color:{var_col}">{var_badge}</div>
    </div>
    <div class="stat-card">
      <div class="sl">CVaR 95%</div>
      <div class="sv" style="color:#f0b429">{cvar95_display}</div>
    </div>
    <div class="stat-card">
      <div class="sl">Prob Profit</div>
      <div class="sv" style="color:#00d4ff">{prob_display}</div>
    </div>
    <div class="stat-card">
      <div class="sl">MC Median (21d)</div>
      <div class="sv" style="color:#00d4ff">{mc_median_display}</div>
    </div>
    <div class="stat-card">
      <div class="sl">Cash / Buying Power</div>
      <div class="sv" style="color:{cash_col}">${cash:,.0f}</div>
      <div style="font-size:9px;color:{cash_col}">{"⚠️ BELOW $500 BUFFER" if cash < 500 else "✓ OK"}</div>
    </div>
    <div class="stat-card">
      <div class="sl">Diversification</div>
      <div class="sv" style="color:{score_col(div_score)}">{_score_display(div_score)}</div>
    </div>
  </div>

  <!-- Override banner -->
  {'<div class="override-banner">⚠️ RULE 8 ACTIVE — Cash $' + f"{cash:.2f} < $500 minimum buffer. ALL ADD signals blocked. Priority action: generate $" + f"{500 - cash:.0f}+ cash to restore tactical flexibility.</div>" if cash_blocked else ""}
  {('<div class="override-banner" style="background:#f0b42915;border-color:#f0b42944;color:#f0b429">⚠️ RULE 5 ACTIVE — RSI≥70 overrides Kelly ADD → HOLD for: ' + ", ".join(f"{s} (RSI={v:.0f})" for s, v in rsi_overrides.items()) + "</div>") if rsi_overrides else ""}

  {provider_banner_html}
  {signal_gate_banner_html}

  {share_plan_section_html}

  <!-- AI Verdict -->
  <div class="ai-card">
    <h3>📊 AI Portfolio Verdict</h3>
    <div class="ai-text">{_html(port_verdict)}</div>
  </div>

  <!-- Market Context -->
  <div class="ai-card">
    <h3>🌍 Market Context</h3>
    <div class="ai-text">{_html(mkt_ctx)}</div>
  </div>

  <!-- Holdings -->
  <div class="section-title">📈 Holdings Analysis ({len(holdings)} positions)</div>
  {holdings_html}

  <!-- Charts -->
  <div class="charts-grid">
    <div class="chart-card">
      <div class="chart-title">Monte Carlo Distribution — 21-Day Portfolio Value (1,000 paths)</div>
      <canvas id="mcChart" height="180"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-title">Kelly Sizing — Current vs Optimal Allocation %</div>
      <canvas id="kellyChart" height="180"></canvas>
    </div>
  </div>

  <!-- Regime -->
  <div class="section-title">🌡️ Market Regime</div>
  <div class="regime-grid">{regime_cards_html}</div>

  <!-- AI notes — prose, not share counts -->
  <div class="section-title">📝 AI notes</div>
  <div style="color:#888;font-size:11px;margin:-6px 0 8px">Narrative only — share counts are in the Action plan at the top. Not orders.</div>
  <div class="table-card" style="margin-bottom:16px">
    <table class="action-table">
      <thead><tr><th>Priority</th><th>Note</th><th>Rationale</th></tr></thead>
      <tbody>{action_rows}</tbody>
    </table>
  </div>

  {gate_audit_html}

  <!-- Diversification Radar (advisory-only, new non-held names) -->
  {div_radar_html}

  <!-- Portfolio Risks -->
  <div class="section-title">⚠️ Portfolio Risk Factors</div>
  {risks_html_list}

  <!-- Vol + Alpha tables -->
  <div class="two-col">
    <div class="table-card">
      <div class="chart-title">Volatility Models (GARCH vs EWMA — annualised)</div>
      <table class="mini-table">
        <thead><tr><th>Symbol</th><th>GARCH</th><th>EWMA</th><th>Diff</th></tr></thead>
        <tbody>{vol_rows}</tbody>
      </table>
      <div style="color:#888;font-size:11px;padding:8px 4px 2px">GARCH N/A means the (1,1) fit did not run (short sample, or <code>arch</code> missing). EWMA is not relabelled as GARCH.</div>
    </div>
    <div class="table-card">
      <div class="chart-title">Alpha vs Benchmarks (1M)</div>
      <table class="mini-table">
        <thead><tr><th>Symbol</th><th>vs SPY</th><th>vs Sector</th><th>Return</th></tr></thead>
        <tbody>{alpha_rows if alpha_rows else '<tr><td colspan=4 style="color:#444;font-style:italic">No alpha data</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  <!-- Tax + Yield -->
  <div class="two-col">
    <div class="table-card">
      <div class="chart-title">Tax-Loss Harvest Scan</div>
      {tax_html}
    </div>
    <div class="table-card">
      <div class="chart-title">Cash Yield Landscape (${cash:,.0f} available)</div>
      {yield_html}
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    NOT FINANCIAL ADVICE — FOR INFORMATIONAL PURPOSES ONLY<br>
    GESH Financial Observability v2.1 &nbsp;|&nbsp; Generated {now_str} &nbsp;|&nbsp; Data via Robinhood API + yfinance + Claude AI<br>
    PAPER_MODE=true by default &nbsp;|&nbsp; All trading decisions remain the user's responsibility
  </div>

</div><!-- /container -->

<script>
// Monte Carlo chart
new Chart(document.getElementById('mcChart').getContext('2d'), {{
  type: 'bar',
  data: {{
    labels: {_json_script(mc_labels)},
    datasets: [{{
      label: '% of simulations',
      data: {_json_script(mc_data)},
      backgroundColor: [{",".join(mc_colors)}],
      borderRadius: 3,
    }}]
  }},
  options: {{
    responsive:true, maintainAspectRatio:true,
    plugins:{{ legend:{{ display:false }}, tooltip:{{ callbacks:{{
      title: ctx => ctx[0].label,
      label: ctx => ctx.raw.toFixed(1) + '% of paths'
    }}}} }},
    scales:{{
      x:{{ ticks:{{ color:'#444', font:{{size:9}}, maxRotation:45 }}, grid:{{ color:'#111' }} }},
      y:{{ ticks:{{ color:'#444', font:{{size:9}} }}, grid:{{ color:'#1a2840' }} }}
    }}
  }}
}});

// Kelly chart
new Chart(document.getElementById('kellyChart').getContext('2d'), {{
  type: 'bar',
  data: {{
    labels: {_json_script(kelly_syms)},
    datasets: [
      {{ label:'Current %', data:{_json_script(kelly_curr)}, backgroundColor:'#00d4ff55', borderColor:'#00d4ff', borderWidth:1, borderRadius:3 }},
      {{ label:'Kelly Optimal %', data:{_json_script(kelly_opts)}, backgroundColor:'#f0b42955', borderColor:'#f0b429', borderWidth:1, borderRadius:3 }}
    ]
  }},
  options:{{
    responsive:true, maintainAspectRatio:true,
    plugins:{{ legend:{{ labels:{{ color:'#888', font:{{size:10}} }} }} }},
    scales:{{
      x:{{ ticks:{{ color:'#666', font:{{size:10}} }}, grid:{{ color:'#111' }} }},
      y:{{ ticks:{{ color:'#666', font:{{size:9}}, callback: v => v+'%' }}, grid:{{ color:'#1a2840' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

requested = Path(os.environ.get("GESH_REPORT_OUTPUT") or BASE / "gesh_report.html")
out = report_output_path(BASE, eng, requested=requested)
out.write_text(html)
print(f"[report] Written → {out}  ({len(html):,} chars)")
