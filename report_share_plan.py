"""Kelly share-count table and paper/gate audit HTML for gen_report2.

ADD = floor(|delta_usd| / price) in whole shares when the gap is ≥1 share,
otherwise a 4-decimal fractional lot (Robinhood). TRIM = ceil, clamped to
quantity. Missing price or delta → shares is None (N/A), never a fabricated
0 that reads as a measured do-nothing.

A sized dollar gap is not a risk-gated order when Kelly's own action is HOLD
(the 0.6× / 1.3× band). The table shows the size; Gate says "not gated".
"""

from __future__ import annotations

import math
from html import escape as _esc
from typing import Any


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _fmt_shares(shares: Any) -> str:
    if shares is None:
        return "N/A"
    try:
        f = float(shares)
    except (TypeError, ValueError):
        return "N/A"
    if f != f:
        return "N/A"
    if f == int(f):
        return str(int(f))
    return f"{f:.4f}".rstrip("0").rstrip(".")


def _whole_or_float(n: float) -> int | float:
    if n == int(n):
        return int(n)
    return n


_SHARE_DECIMALS = 4
_SHARE_SCALE = 10**_SHARE_DECIMALS
_MIN_SHARES = 1 / _SHARE_SCALE


def _floor_lot(raw: float) -> float:
    return math.floor(raw * _SHARE_SCALE) / _SHARE_SCALE


def _ceil_lot(raw: float) -> float:
    return math.ceil(raw * _SHARE_SCALE) / _SHARE_SCALE


def share_count(
    *,
    delta_usd: Any,
    price: Any,
    quantity: Any,
) -> tuple[int | float | None, str]:
    d = _finite_float(delta_usd)
    p = _finite_float(price)
    if d is None or p is None or p <= 0:
        return None, "HOLD"
    if abs(d) < 1e-12:
        return 0, "HOLD"
    raw = abs(d) / p
    if d > 0:
        sh = float(int(math.floor(raw))) if raw >= 1.0 else _floor_lot(raw)
        if sh < _MIN_SHARES:
            return 0, "HOLD"
        return _whole_or_float(sh), "ADD"
    sh = float(int(math.ceil(raw))) if raw >= 1.0 else _ceil_lot(raw)
    qty = _finite_float(quantity)
    if qty is not None and qty >= 0:
        sh = min(float(sh), round(qty, _SHARE_DECIMALS))
    if sh < _MIN_SHARES:
        return 0, "HOLD"
    return _whole_or_float(sh), "TRIM"


def _first_finite(mapping: dict, *keys: str) -> float | None:
    """Keep a measured 0.0. `or` would treat Kelly AVOID (0%) as missing."""
    for key in keys:
        if key in mapping:
            return _finite_float(mapping.get(key))
    return None


def _shares_match(left: Any, right: Any) -> bool:
    a = _finite_float(left)
    b = _finite_float(right)
    if a is None or b is None:
        return False
    return abs(a - b) < 1e-9


def _plan_side_matches(plan_action: Any, size_action: str) -> bool:
    act = str(plan_action or "").upper()
    if act in ("BUY", "ADD"):
        return size_action == "ADD"
    if act == "TRIM":
        return size_action == "TRIM"
    return False


def _executable_plan(plan: Any) -> list[dict]:
    out: list[dict] = []
    for a in plan or []:
        if not isinstance(a, dict):
            continue
        if a.get("action") not in ("BUY", "ADD", "TRIM"):
            continue
        sh = _finite_float(a.get("shares")) or 0.0
        if sh > 0:
            out.append(a)
    return out


def share_plan_rows(eng: dict) -> list[dict]:
    holdings = eng.get("holdings") or []
    kelly = eng.get("kelly") if isinstance(eng.get("kelly"), dict) else {}
    total = _finite_float((eng.get("equity_summary") or {}).get("total")) or 0.0
    rows: list[dict] = []
    for h in holdings:
        if not isinstance(h, dict):
            continue
        sym = h.get("symbol")
        if not sym:
            continue
        k = kelly.get(sym) if isinstance(kelly.get(sym), dict) else {}
        price = h.get("price") or h.get("current_price")
        qty = h.get("quantity")
        equity = _finite_float(h.get("equity"))
        delta = k.get("delta_usd")
        opt = _first_finite(k, "optimal_pct", "recommended_pct")
        now = _first_finite(k, "current_pct")
        if now is None and equity is not None and total > 0:
            now = equity / total * 100.0
        shares, lot_action = share_count(delta_usd=delta, price=price, quantity=qty)
        raw_act = k.get("action")
        kelly_action = str(raw_act).upper() if raw_act else "HOLD"
        # Dollar gap is not an order when Kelly's own band says HOLD/AVOID.
        # Painting those rows as ADD made a 22-name "buy $50–$200 of everything"
        # chore out of in-band drift (2026-08-27).
        if kelly_action in ("HOLD", "AVOID") and lot_action in ("ADD", "TRIM"):
            size_action = "HOLD"
        else:
            size_action = lot_action
        rows.append(
            {
                "symbol": str(sym),
                "size_action": size_action,
                "lot_action": lot_action,
                "shares": shares,
                "delta_usd": _finite_float(delta),
                "now_pct": now,
                "kelly_pct": opt,
                "kelly_action": kelly_action,
                "price": _finite_float(price),
            }
        )
    order = {"TRIM": 0, "ADD": 1, "HOLD": 2}
    rows.sort(
        key=lambda r: (
            order.get(r["size_action"], 9),
            -(abs(r["delta_usd"]) if r["delta_usd"] is not None else -1.0),
            r["symbol"],
        )
    )
    return rows


def share_plan_summary(rows: list[dict]) -> dict:
    trim_names = add_names = hold_names = 0
    trim_shares = add_shares = 0.0
    for r in rows:
        act = r["size_action"]
        sh = r["shares"]
        if act == "TRIM":
            trim_names += 1
            trim_shares += float(sh or 0)
        elif act == "ADD":
            add_names += 1
            add_shares += float(sh or 0)
        else:
            hold_names += 1
    return {
        "trim_names": trim_names,
        "trim_shares": _whole_or_float(trim_shares),
        "add_names": add_names,
        "add_shares": _whole_or_float(add_shares),
        "hold_names": hold_names,
    }


def _gate_status(row: dict, eng: dict) -> str:
    """Dollar-drift ADD/TRIM is not APPROVED unless the gate actually ran."""
    plan = eng.get("action_plan") or []
    gates = eng.get("gate_results") or []
    sym = row["symbol"]
    gate_by = {(g.get("symbol"), g.get("action")): g for g in gates if isinstance(g, dict)}
    for a in plan:
        if not isinstance(a, dict) or a.get("symbol") != sym:
            continue
        act = a.get("action")
        sh = a.get("shares") or 0
        try:
            sh_n = float(sh)
        except (TypeError, ValueError):
            sh_n = 0.0
        if act in ("BUY", "ADD", "TRIM") and sh_n > 0:
            g = gate_by.get((sym, act))
            side_ok = _plan_side_matches(act, row["size_action"])
            size_ok = _shares_match(sh_n, row["shares"])
            if not side_ok or not size_ok:
                return f"gated {_fmt_shares(sh_n)} sh, not this size"
            if g is None:
                return "NOT EVALUATED — gate failed"
            if g.get("approved"):
                return "APPROVED"
            return f"REJECTED — {g.get('reason') or 'rejected'}"
        if act == "HOLD":
            return "not gated — pre-gate HOLD"
    lot = row.get("lot_action") or row["size_action"]
    if lot in ("ADD", "TRIM") and (row["shares"] or 0) not in (None, 0):
        if row["kelly_action"] in ("HOLD", "AVOID") or row["size_action"] == "HOLD":
            return "not gated — Kelly HOLD band"
        return "not gated"
    return "not gated — HOLD"


def share_plan_html(eng: dict) -> str:
    rows = share_plan_rows(eng)
    if not rows:
        return ""
    summary = share_plan_summary(rows)
    colors = {"ADD": "#00e87a", "TRIM": "#ff3c5a", "HOLD": "#888"}
    body = ""
    for r in rows:
        act = r["size_action"]
        col = colors.get(act, "#888")
        sh_cell = "N/A" if r["shares"] is None else f"{_fmt_shares(r['shares'])} sh"
        if (
            r["size_action"] == "HOLD"
            and r.get("lot_action") in ("ADD", "TRIM")
            and r["shares"] not in (None, 0)
        ):
            sh_cell = f"drift {sh_cell}"
        d = r["delta_usd"]
        if d is None:
            d_cell = "N/A"
        else:
            sign = "+" if d > 0 else ("−" if d < 0 else "")
            d_cell = f"{sign}${abs(d):,.0f}"
        now = f"{r['now_pct']:.1f}%" if r["now_pct"] is not None else "N/A"
        kel = f"{r['kelly_pct']:.1f}%" if r["kelly_pct"] is not None else "N/A"
        status = _gate_status(r, eng)
        if r["shares"] is None:
            sh_html = "N/A"
        else:
            sh_html = _esc(sh_cell)
            if d_cell != "N/A":
                sh_html += (
                    f'<div style="font-size:10px;font-weight:400;color:#888">{_esc(d_cell)}</div>'
                )
        body += (
            "<tr>"
            f'<td style="color:{col};font-weight:700">{_esc(act)}</td>'
            f"<td>{_esc(r['symbol'])}</td>"
            f'<td style="text-align:right;font-weight:700">{sh_html}</td>'
            f"<td>{_esc(now)}</td>"
            f"<td>{_esc(kel)}</td>"
            f'<td style="color:#888;font-size:11px">{_esc(status)}</td>'
            "</tr>"
        )
    note = (
        "Action follows Kelly’s 0.6× / 1.3× band, not the leftover dollar gap. "
        "HOLD + “drift” is in-band noise — not an order. ADD/TRIM are the names "
        "Kelly actually wants resized. Gate = APPROVED only when that share "
        "count cleared the risk gate."
    )
    return f"""
  <!-- Action plan -->
  <div class="section-title">⚡ Action plan</div>
  <div class="table-card" style="margin-bottom:16px">
    <div style="padding:8px 10px 4px;font-size:13px;color:#e0e0e0">
      TRIM <b style="color:#ff3c5a">{summary["trim_names"]}</b> names /
      <b style="color:#ff3c5a">{_fmt_shares(summary["trim_shares"])} sh</b>
      &nbsp;·&nbsp;
      ADD <b style="color:#00e87a">{summary["add_names"]}</b> names /
      <b style="color:#00e87a">{_fmt_shares(summary["add_shares"])} sh</b>
      &nbsp;·&nbsp;
      HOLD <b>{summary["hold_names"]}</b> names
    </div>
    <table class="action-table">
      <thead><tr>
        <th>Action</th><th>Symbol</th><th>Shares</th>
        <th>Now</th><th>Kelly</th><th>Gate</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>
    <div style="color:#888;font-size:11px;padding:8px 4px 2px">{_esc(note)}</div>
  </div>"""


def holding_share_caption(holding: dict, kelly: dict, total: float) -> str:
    """One-line 'ADD 13 sh · +$558' for a holding card."""
    sym = holding.get("symbol")
    if not sym:
        return ""
    rows = share_plan_rows(
        {
            "holdings": [holding],
            "equity_summary": {"total": total},
            "kelly": {sym: kelly or {}},
        }
    )
    if not rows:
        return ""
    r = rows[0]
    if r["shares"] is None:
        return "Shares N/A"
    d = r["delta_usd"]
    d_bit = ""
    if d is not None:
        sign = "+" if d > 0 else ("−" if d < 0 else "")
        d_bit = f" · {sign}${abs(d):,.0f}"
    return f"{r['size_action']} {_fmt_shares(r['shares'])} sh{d_bit}"


def gate_audit_html(eng: dict) -> str:
    gates = eng.get("gate_results") or []
    plan = eng.get("action_plan") or []
    paper = eng.get("paper_session") or {}
    stats = eng.get("trade_stats") or {}
    closed = paper.get("closed") or []
    created = paper.get("created") or []

    gate_by_sym = {(g.get("symbol"), g.get("action")): g for g in gates if isinstance(g, dict)}
    rows = ""
    executable = 0
    for a in plan:
        if not isinstance(a, dict):
            continue
        act = a.get("action")
        sh = a.get("shares", 0)
        try:
            sh_n = float(sh or 0)
        except (TypeError, ValueError):
            sh_n = 0.0
        if act in ("BUY", "ADD", "TRIM") and sh_n > 0:
            executable += 1
        g = gate_by_sym.get((a.get("symbol"), act))
        if g is not None:
            verdict = (
                "✅ APPROVED"
                if g.get("approved")
                else f"⛔ {_esc(str(g.get('reason') or 'rejected'))}"
            )
            v_col = "#00e87a" if g.get("approved") else "#ff3c5a"
        elif act == "HOLD":
            verdict, v_col = "not gated — pre-gate HOLD", "#888"
        else:
            verdict, v_col = "—", "#888"
        rows += (
            "<tr>"
            f"<td>{_esc(str(a.get('symbol', '?')))}</td>"
            f"<td>{_esc(str(act or ''))}</td>"
            f'<td style="text-align:right">{_esc(_fmt_shares(sh) if sh is not None else "0")}</td>'
            f'<td style="color:#888;font-size:11px">{_esc(str(a.get("signal_reason", "")))}</td>'
            f'<td style="color:{v_col};font-size:11px">{verdict}</td>'
            "</tr>"
        )

    close_rows = ""
    for c in closed:
        if not isinstance(c, dict):
            continue
        pnl = _finite_float(c.get("realized_pnl"))
        pnl_s = "N/A" if pnl is None else f"${pnl:+,.2f}"
        close_rows += (
            "<tr>"
            f"<td>{_esc(str(c.get('symbol', '?')))}</td>"
            f"<td>{_esc(str(c.get('action') or ''))}</td>"
            f'<td style="text-align:right">{_esc(_fmt_shares(c.get("shares")))}</td>'
            f"<td>{_esc(str(c.get('outcome') or ''))}</td>"
            f"<td>{_esc(pnl_s)}</td>"
            f'<td style="color:#888;font-size:11px">{_esc(str(c.get("notes") or ""))}</td>'
            "</tr>"
        )

    if not rows:
        rows = (
            '<tr><td colspan="5" style="color:#888">No executable Kelly ADD/TRIM today '
            "(band HOLD or already-open skip). Gated executable: 0.</td></tr>"
        )

    if close_rows:
        close_block = f"""
    <div style="color:#e0e0e0;font-size:12px;padding:10px 4px 4px">Closed today</div>
    <table class="action-table">
      <thead><tr><th>Symbol</th><th>Action</th><th>Shares</th><th>Outcome</th><th>P&amp;L</th><th>Notes</th></tr></thead>
      <tbody>{close_rows}</tbody>
    </table>"""
    else:
        close_block = (
            '<div style="color:#888;font-size:11px;padding:8px 4px 2px">'
            "No paper positions auto-closed today.</div>"
        )

    stat_bits = [
        f"{len(created)} paper record(s) today",
        f"{len(closed)} auto-closed",
        f"gated executable: {executable}",
    ]
    paper_stats = stats.get("paper") if isinstance(stats.get("paper"), dict) else {}
    real_stats = stats.get("real") if isinstance(stats.get("real"), dict) else {}
    pw = paper_stats.get("win_rate_pct")
    rw = real_stats.get("win_rate_pct")
    if pw is not None:
        stat_bits.append(f"paper win-rate 7d: {pw}%")
    if rw is not None:
        stat_bits.append(f"real win-rate 7d: {rw}%")

    return f"""
  <!-- Paper & Gate Audit -->
  <div class="section-title">🛡️ Paper &amp; Risk-Gate Audit</div>
  <div class="table-card" style="margin-bottom:16px">
    <table class="action-table">
      <thead><tr><th>Symbol</th><th>Action</th><th>Shares</th><th>Signal</th><th>8-Gate Verdict</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    {close_block}
    <div style="color:#888;font-size:11px;padding:8px 4px 2px">{" · ".join(stat_bits)}</div>
  </div>"""


def signal_gate_banner_html(eng: dict) -> str:
    summary = share_plan_summary(share_plan_rows(eng))
    plan = eng.get("action_plan") or []
    gates = eng.get("gate_results") or []
    closed = (eng.get("paper_session") or {}).get("closed") or []
    gate_by = {(g.get("symbol"), g.get("action")): g for g in gates if isinstance(g, dict)}
    blocked: list[str] = []
    cleared: list[str] = []
    unevaluated: list[str] = []
    reason = ""
    for a in _executable_plan(plan):
        g = gate_by.get((a.get("symbol"), a.get("action")))
        label = str(a.get("symbol", "?"))
        if g is None:
            unevaluated.append(label)
            continue
        if g.get("approved"):
            cleared.append(label)
        else:
            blocked.append(label)
            if not reason and g.get("reason"):
                reason = str(g.get("reason"))

    closed_syms = [str(c.get("symbol")) for c in closed if isinstance(c, dict) and c.get("symbol")]
    close_bit = f" Paper closed: {len(closed)} ({_esc(', '.join(closed_syms))})." if closed else ""
    cap = " (size &gt; 2% position cap)" if "cap" in reason.lower() else ""
    if unevaluated:
        return (
            '<div class="override-banner">⚠️ SIGNAL vs GATE — '
            + f"{len(unevaluated)} executable action(s) NOT EVALUATED "
            + f"({_esc(', '.join(unevaluated))}). Do not treat as cleared.{close_bit}</div>"
        )
    if blocked and not cleared:
        return (
            '<div class="override-banner">⚠️ SIGNAL vs GATE — signals lean ADD/TRIM on '
            + f"{len(blocked)} name(s) ({_esc(', '.join(blocked))}), but the risk gate blocked "
            + f"all of them{cap} — net actionable trades today: 0.{close_bit}</div>"
        )
    if blocked and cleared:
        return (
            '<div class="override-banner" style="background:#f0b42915;border-color:#f0b42944;color:#f0b429">'
            + f"⚠️ SIGNAL vs GATE — {len(cleared)} action(s) cleared the gate "
            + f"({_esc(', '.join(cleared))}); {len(blocked)} blocked ({_esc(', '.join(blocked))}){cap}.{close_bit}</div>"
        )
    if cleared:
        return (
            '<div class="override-banner" style="background:#00e87a15;border-color:#00e87a44;color:#00e87a">'
            + f"✓ SIGNAL vs GATE — all {len(cleared)} planned ADD/TRIM action(s) "
            + f"({_esc(', '.join(cleared))}) cleared the risk gate.{close_bit}</div>"
        )
    share_bit = (
        f"Share plan: TRIM {_fmt_shares(summary['trim_shares'])} sh / "
        f"ADD {_fmt_shares(summary['add_shares'])} sh / "
        f"HOLD {summary['hold_names']} names. Gated executable: 0."
    )
    return (
        '<div class="override-banner" style="background:#f0b42915;border-color:#f0b42944;color:#f0b429">'
        + f"⚠️ {_esc(share_bit)}{close_bit}</div>"
    )
