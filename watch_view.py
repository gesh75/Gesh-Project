"""
watch_view.py — internal live paper-trading cockpit (GET /watch on :9000).

A read-only "watch what the engine does" console: OPEN positions (with unrealized
P&L + distance-to-stop/target), RECENT closes (realized P&L, WIN/LOSS), the
gate->fill->exit ACTIVITY feed, and the N/30 autonomy-gate progress. Updates live
via a 12s fetch() poll against GET /watch/data.

Design (mirrors proof_page.py):
  * Read-only — opens the tracker DB `?mode=ro`; never writes. Does NOT import
    trading.tracker (its _conn() is read-WRITE and runs DDL on every call).
  * Honest — never fabricates a trade; empty states are first-class.
  * Paper-only — `mode` is a hard-asserted constant; live orders are never shown.
  * Self-contained — inline CSS + JS, no external CDN/SRI surface, no new deps.
  * Internal — NOT in auth_users.PUBLIC_PREFIXES, so the login wall gates it.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

# Single source of truth for DB path + autonomy thresholds + palette: proof_page.
from proof_page import (
    DEFAULT_TRACKER_DB,
    MIN_CLOSED_TRADES, MIN_WIN_RATE, MIN_SHARPE, MAX_DRAWDOWN,
    LAUNCH_DATE,
    _BG, _SURFACE, _BORDER, _CYAN, _GREEN, _YELLOW, _RED, _DIM,
)

SCHEMA_VERSION = 1
_ORANGE = "#FF8C00"          # risk/at-risk only — never used for a loss
_AT_RISK_PCT = 1.0           # distance-to-stop below this % → row flagged at risk

PriceFetcher = Callable[[list[str]], dict[str, float]]

# Cached engine paper_trading module (loaded by file path to avoid the duplicate
# data_fetcher/paper_trading shadowing that bit the engine — see -trading rule #37).
_engine_pt: Any = None


def _default_price_fetcher(symbols: list[str]) -> dict[str, float]:
    """Best-effort live marks from the trading-engine's yfinance fetch. Any failure
    (import error, yfinance 401, missing symbol) → {} and the view shows '—'."""
    if not symbols:
        return {}
    global _engine_pt
    try:
        if _engine_pt is None:
            import importlib.util
            pt_path = Path(DEFAULT_TRACKER_DB).parent / "paper_trading.py"
            spec = importlib.util.spec_from_file_location("gesh_engine_paper_trading_watch", str(pt_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)   # type: ignore[union-attr]
            _engine_pt = mod
        raw = _engine_pt._fetch_current_prices(list(symbols)) or {}
        return {k: float(v) for k, v in raw.items() if v is not None}
    except Exception:
        return {}


# ── pure metrics ─────────────────────────────────────────────────────────────

def compute_position_metrics(pos: dict[str, Any], price: float | None) -> dict[str, Any]:
    """Pure: derive unrealized P&L + distance-to-stop/target for one open position.

    `profit_locked` (stop has moved past entry) needs no price. Every price-dependent
    field is None when `price` is None so the UI honestly renders '—'.
    """
    action = (pos.get("action") or "BUY").upper()
    qty = float(pos.get("qty") or 0)
    entry = float(pos.get("entry_price") or 0)
    stop = pos.get("stop_loss")
    target = pos.get("take_profit")
    is_buy = action == "BUY"

    profit_locked = False
    if stop is not None and entry:
        profit_locked = (float(stop) >= entry) if is_buy else (float(stop) <= entry)

    out: dict[str, Any] = {
        "current_price": None, "unrealized_pnl_usd": None, "unrealized_pnl_pct": None,
        "distance_to_stop_pct": None, "distance_to_stop_usd": None,
        "distance_to_target_pct": None, "at_risk": False, "profit_locked": profit_locked,
    }
    if price is None or price <= 0 or entry <= 0:
        return out

    out["current_price"] = price
    if is_buy:
        out["unrealized_pnl_usd"] = (price - entry) * qty
        out["unrealized_pnl_pct"] = (price - entry) / entry
        if stop is not None:
            out["distance_to_stop_pct"] = (price - float(stop)) / price * 100
            out["distance_to_stop_usd"] = (price - float(stop)) * qty
        if target is not None:
            out["distance_to_target_pct"] = (float(target) - price) / price * 100
    else:  # SELL / short
        out["unrealized_pnl_usd"] = (entry - price) * qty
        out["unrealized_pnl_pct"] = (entry - price) / entry
        if stop is not None:
            out["distance_to_stop_pct"] = (float(stop) - price) / price * 100
            out["distance_to_stop_usd"] = (float(stop) - price) * qty
        if target is not None:
            out["distance_to_target_pct"] = (price - float(target)) / price * 100

    dsp = out["distance_to_stop_pct"]
    out["at_risk"] = dsp is not None and dsp < _AT_RISK_PCT and not profit_locked
    return out


def _sharpe(pnls: list[float], launch_date: str, today: str) -> float:
    """Per-trade Sharpe (rf=0), annualised by trade frequency — mirrors proof_page."""
    n = len(pnls)
    if n < 2:
        return 0.0
    mean_r = sum(pnls) / n
    var = sum((p - mean_r) ** 2 for p in pnls) / (n - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 10.0 if mean_r > 0 else (-10.0 if mean_r < 0 else 0.0)
    d0 = datetime.fromisoformat(launch_date).date()
    d1 = datetime.fromisoformat(today).date()
    days = max((d1 - d0).days, 1)
    tpy = n / max(days / 252.0, 0.001)
    return round((mean_r / std) * math.sqrt(tpy), 4)


def _max_drawdown(pnls: list[float]) -> float:
    """Max drawdown of the cumulative equity curve (starts at 1.0) — mirrors proof_page."""
    eq, peak, mdd = 1.0, 1.0, 0.0
    for p in pnls:
        eq *= (1.0 + p)
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return round(mdd, 4)


def _streak(outcomes: list[str]) -> str:
    """Current consecutive-outcome streak from newest→older, e.g. '3W' / '2L'."""
    if not outcomes:
        return "—"
    first = outcomes[0]
    if first not in ("WIN", "LOSS"):
        return "—"
    k = 0
    for o in outcomes:
        if o == first:
            k += 1
        else:
            break
    return f"{k}{'W' if first == 'WIN' else 'L'}"


# ── data layer (read-only) ────────────────────────────────────────────────────

def _ro_query(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []   # missing table/column → honest empty, never raise


def load_watch_data(
    db_path: str | None = None,
    *,
    lookback_days: int = 7,
    activity_limit: int = 50,
    today: str | None = None,
    price_fetcher: PriceFetcher | None = None,
) -> dict[str, Any]:
    """Assemble the /watch contract from gesh_financial.db, read-only. Never raises
    on a missing/empty/locked DB — returns the honest empty contract instead."""
    db_path = db_path or DEFAULT_TRACKER_DB
    today = today or date.today().isoformat()
    fetch = price_fetcher or _default_price_fetcher
    cutoff = (date.fromisoformat(today) - timedelta(days=max(lookback_days, 0))).isoformat()

    open_rows: list[dict] = []
    all_closed: list[dict] = []
    fills: list[dict] = []
    gates: list[dict] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            open_rows = _ro_query(con,
                "SELECT symbol, action, qty, entry_price, entry_at, source, stop_loss, take_profit"
                " FROM paper_positions WHERE status='OPEN' ORDER BY entry_at DESC")
            all_closed = _ro_query(con,
                "SELECT symbol, action, entry_price, exit_price, pnl_usd, pnl_pct, outcome,"
                " hold_days, exit_at FROM paper_outcomes ORDER BY exit_at ASC")
            fills = _ro_query(con,
                "SELECT placed_at, symbol, action, fill_qty, fill_price FROM orders"
                " WHERE mode='paper' AND status='SIMULATED' AND fill_qty IS NOT NULL"
                " AND datetime(placed_at) >= datetime(?) ORDER BY placed_at DESC LIMIT ?",
                (cutoff, activity_limit))
            gates = _ro_query(con,
                "SELECT rd.decided_at, rd.verdict, rd.reason, ti.symbol, ti.action"
                " FROM risk_decisions rd LEFT JOIN trade_intents ti ON rd.intent_id = ti.id"
                " WHERE datetime(rd.decided_at) >= datetime(?) ORDER BY rd.decided_at DESC LIMIT ?",
                (cutoff, activity_limit))
        finally:
            con.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        pass   # missing/locked/corrupt DB → empty contract

    # Live prices (best-effort) for the open symbols only.
    symbols = [r["symbol"] for r in open_rows if r.get("symbol")]
    try:
        prices = fetch(symbols) if symbols else {}
    except Exception:
        prices = {}
    if not symbols:
        price_source = "none"
    elif prices:
        price_source = "yfinance"
    else:
        price_source = "unavailable"

    # Panel 1 — open positions + metrics.
    open_positions = []
    total_unreal = 0.0
    have_unreal = False
    total_at_risk = 0.0
    have_at_risk = False
    for r in open_rows:
        m = compute_position_metrics(r, prices.get(r["symbol"]))
        if m["unrealized_pnl_usd"] is not None:
            total_unreal += m["unrealized_pnl_usd"]; have_unreal = True
        if m["distance_to_stop_usd"] is not None:
            total_at_risk += max(m["distance_to_stop_usd"], 0.0); have_at_risk = True
        open_positions.append({
            "symbol": r["symbol"], "action": r.get("action"), "qty": r.get("qty"),
            "entry_price": r.get("entry_price"), "stop_loss": r.get("stop_loss"),
            "take_profit": r.get("take_profit"), "source": r.get("source"),
            "hold_time": _hold_time(r.get("entry_at")), **m,
        })

    # Panel 2 — recent closes (window) + lifetime stats for autonomy.
    recent_closes = [r for r in reversed(all_closed) if (r.get("exit_at") or "") >= cutoff]
    pnls = [float(r.get("pnl_pct") or 0) for r in all_closed]
    closed_n = len(all_closed)
    wins = sum(1 for r in all_closed if r.get("outcome") == "WIN")
    win_rate = round(wins / closed_n, 4) if closed_n else 0.0
    sharpe = _sharpe(pnls, LAUNCH_DATE, today)
    max_dd = _max_drawdown(pnls)
    realized_7d = round(sum(float(r.get("pnl_usd") or 0) for r in recent_closes), 2)
    closed_today = sum(1 for r in all_closed if (r.get("exit_at") or "")[:10] == today)
    streak = _streak([r.get("outcome") for r in reversed(all_closed)])

    # Panel 3a — merged activity feed (fills + gate decisions + exits), newest first.
    feed: list[dict] = []
    for f in fills:
        q = float(f.get("fill_qty") or 0); px = float(f.get("fill_price") or 0)
        feed.append({"event_time": f.get("placed_at"), "type": "FILL",
                     "symbol": f.get("symbol"), "action": f.get("action"), "reason": None,
                     "summary": f"FILLED {f.get('action')} {f.get('symbol')} {q:g} @ ${px:.2f}"})
    for g in gates:
        rejected = (g.get("verdict") == "REJECTED")
        feed.append({"event_time": g.get("decided_at"),
                     "type": "GATE_REJECTED" if rejected else "GATE_APPROVED",
                     "symbol": g.get("symbol"), "action": g.get("action"),
                     "reason": g.get("reason") if rejected else None,
                     "summary": (f"REJECTED {g.get('symbol') or ''} {g.get('action') or ''} — {g.get('reason') or ''}"
                                 if rejected else f"APPROVED {g.get('symbol') or ''} {g.get('action') or ''}")})
    for r in recent_closes:
        feed.append({"event_time": r.get("exit_at"), "type": "EXIT",
                     "symbol": r.get("symbol"), "action": r.get("action"), "reason": None,
                     "summary": f"EXIT {r.get('symbol')} {r.get('outcome')} {float(r.get('pnl_pct') or 0)*100:+.1f}%"})
    feed.sort(key=lambda e: e.get("event_time") or "", reverse=True)
    feed = feed[:activity_limit]

    autonomy = {
        "closed_n": closed_n,
        "required": MIN_CLOSED_TRADES,
        "closed_trades": {"value": closed_n, "required": MIN_CLOSED_TRADES, "pass": closed_n >= MIN_CLOSED_TRADES},
        "win_rate": {"value": win_rate, "required": MIN_WIN_RATE, "pass": closed_n > 0 and win_rate >= MIN_WIN_RATE},
        "sharpe": {"value": sharpe, "required": MIN_SHARPE, "pass": sharpe >= MIN_SHARPE},
        "max_drawdown": {"value": max_dd, "required": MAX_DRAWDOWN, "pass": closed_n > 0 and max_dd <= MAX_DRAWDOWN},
    }
    autonomy["ready"] = all(
        autonomy[k]["pass"] for k in ("closed_trades", "win_rate", "sharpe", "max_drawdown")
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "paper",                          # hard constant — never "live"
        "price_source": price_source,
        "live_prices_available": bool(prices),
        "summary": {
            "open_count": len(open_positions),
            "total_unrealized_pnl_usd": round(total_unreal, 2) if have_unreal else None,
            "total_at_risk_usd": round(total_at_risk, 2) if have_at_risk else None,
            "closed_count_total": closed_n,
            "closed_today": closed_today,
            "realized_pnl_7d_usd": realized_7d,
            "win_rate_total": win_rate,
            "streak": streak,
            "sample_provisional": closed_n < MIN_CLOSED_TRADES,
        },
        "open_positions": open_positions,
        "recent_closes": recent_closes,
        "activity_feed": feed,
        "autonomy": autonomy,
    }


def _hold_time(entry_at: str | None) -> str:
    if not entry_at:
        return "—"
    try:
        t0 = datetime.fromisoformat(str(entry_at).replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - t0
        d, rem = divmod(int(delta.total_seconds()), 86400)
        h = rem // 3600
        return f"{d}d {h}h" if d else f"{h}h"
    except Exception:
        return "—"


# ── renderer (self-contained HTML + inline poll JS) ──────────────────────────

_DISCLAIMER = (
    "Paper mode only — NOT FINANCIAL ADVICE. Internal research cockpit. All positions and "
    "results shown are HYPOTHETICAL / SIMULATED paper trades, not real money."
)


def render_watch_html(data: dict[str, Any]) -> str:
    """Self-contained dark-theme /watch page: server-rendered initial paint + a 12s
    fetch() poll that rebuilds the three panels and stat strip client-side."""
    import json
    payload = json.dumps(data)
    _auto = data.get("autonomy", {})
    auto_label = f"{_auto.get('closed_n', 0)} of {_auto.get('required', MIN_CLOSED_TRADES)}" + (
        " · READY" if _auto.get("ready") else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GESH — Live Trading Watch (paper)</title>
<style>
 body{{margin:0;background:{_BG};color:#e8eef6;font-family:-apple-system,Segoe UI,Roboto,sans-serif}}
 .wrap{{max-width:1080px;margin:0 auto;padding:24px 18px 64px}}
 h1{{font-size:20px;margin:0}} .sub{{color:{_DIM};font-size:12px;margin:2px 0 16px}}
 .num{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}}
 .strip{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}}
 .stat{{background:{_SURFACE};border:1px solid {_BORDER};border-radius:9px;padding:10px 14px;min-width:120px}}
 .stat .k{{color:{_DIM};font-size:10px;text-transform:uppercase;letter-spacing:.5px}}
 .stat .v{{font-size:19px;font-weight:700;margin-top:3px}}
 .card{{background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;padding:14px 16px;margin-bottom:14px}}
 h2{{font-size:14px;margin:0 0 10px;color:#cfe0f2}}
 table{{width:100%;border-collapse:collapse;font-size:12.5px}}
 th{{text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;letter-spacing:.5px;padding:6px 8px;border-bottom:1px solid {_BORDER}}}
 td{{padding:6px 8px;border-bottom:1px solid {_BORDER}}} .r{{text-align:right}}
 .pill{{padding:1px 7px;border-radius:10px;font-size:10px;font-weight:700}}
 .bar{{height:8px;background:{_BORDER};border-radius:5px;overflow:hidden}} .bar>i{{display:block;height:100%}}
 .empty{{color:{_DIM};text-align:center;padding:20px;font-size:12.5px}}
 .feed div{{padding:5px 8px;border-left:3px solid {_BORDER};margin-bottom:3px;font-size:12px}}
 a{{color:{_CYAN}}} #status{{float:right;color:{_DIM};font-size:11px}}
</style></head>
<body><div class="wrap">
 <div><span id="status">connecting…</span>
   <h1 style="color:{_CYAN}">GESH — Live Trading Watch <span class="pill" style="background:{_YELLOW};color:#1a1200">PAPER</span></h1></div>
 <div class="sub">Internal cockpit · what the engine is doing right now · updates every 12s</div>

 <div class="strip" id="strip"></div>

 <div class="card"><h2>Autonomy progress — <span id="auto-label" class="num">{auto_label}</span></h2>
   <div class="bar"><i id="auto-bar"></i></div>
   <div id="auto-checks" style="margin-top:8px;color:{_DIM};font-size:11px"></div></div>

 <div class="card"><h2>Open Positions</h2>
   <table><thead><tr><th>Symbol</th><th></th><th class="r">Qty</th><th class="r">Entry</th>
     <th class="r">Now</th><th class="r">Unreal P&amp;L</th><th class="r">→ Stop</th>
     <th class="r">→ Target</th><th class="r">$ at risk</th><th class="r">Held</th></tr></thead>
     <tbody id="open-body"></tbody></table></div>

 <div class="card"><h2>Recent Closes <span id="closes-kpi" style="color:{_DIM};font-size:11px;font-weight:400"></span></h2>
   <table><thead><tr><th>Symbol</th><th></th><th class="r">Entry</th><th class="r">Exit</th>
     <th class="r">P&amp;L $</th><th class="r">P&amp;L %</th><th class="r">Outcome</th>
     <th class="r">Hold</th><th class="r">Exit at</th></tr></thead>
     <tbody id="closes-body"></tbody></table></div>

 <div class="card"><h2>Activity feed <span style="color:{_DIM};font-size:11px;font-weight:400">gate → fill → exit</span></h2>
   <div class="feed" id="feed"></div></div>

 <p class="card" style="color:{_DIM};font-size:11px;line-height:1.6">{_DISCLAIMER}
   &nbsp;·&nbsp;<a href="/track-record">Track record</a> · <a href="/progress">Progress</a> · <a href="/invest">💎 Invest</a></p>
</div>
<script>
const C={{green:"{_GREEN}",red:"{_RED}",dim:"{_DIM}",yellow:"{_YELLOW}",orange:"{_ORANGE}",border:"{_BORDER}"}};
const fmtUsd=v=>v==null?"—":(v>=0?"+":"-")+"$"+Math.abs(v).toFixed(2);
const fmtPct=v=>v==null?"—":(v>=0?"+":"")+(v*100).toFixed(1)+"%";
const pnlColor=v=>v==null?C.dim:(v>=0?C.green:C.red);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
function badge(a){{const b=(a||"").toUpperCase();const c=b==="BUY"?C.green:C.red;
  return a?`<span class="pill" style="background:${{c}}22;color:${{c}}">${{esc(a)}}</span>`:"";}}
function renderWatch(d){{
  const s=d.summary;
  document.getElementById("strip").innerHTML=[
    ["Open",s.open_count],
    ["Unrealized P&L",fmtUsd(s.total_unrealized_pnl_usd),pnlColor(s.total_unrealized_pnl_usd)],
    ["$ at risk",s.total_at_risk_usd==null?"—":"$"+s.total_at_risk_usd.toFixed(2),C.orange],
    ["Closed today",s.closed_today],
    ["Realized 7d",fmtUsd(s.realized_pnl_7d_usd),pnlColor(s.realized_pnl_7d_usd)],
    ["Win rate",(s.win_rate_total*100).toFixed(0)+"%"+(s.sample_provisional?" *":""),C.yellow],
    ["Streak",s.streak||"—"],
  ].map(([k,v,col])=>`<div class="stat"><div class="k">${{k}}</div><div class="v num" style="color:${{col||'#e8eef6'}}">${{v}}</div></div>`).join("");

  const a=d.autonomy, pct=Math.min(a.closed_n/a.required*100,100);
  document.getElementById("auto-label").textContent=a.closed_n+" of "+a.required+(a.ready?" · READY":"");
  const bar=document.getElementById("auto-bar"); bar.style.width=pct+"%";
  bar.style.background=a.ready?C.green:(a.closed_trades.pass?C.orange:C.yellow);
  const ck=g=>`<span style="color:${{g.pass?C.green:C.dim}}">${{g.pass?'☑':'☐'}}</span>`;
  document.getElementById("auto-checks").innerHTML=
    `${{ck(a.win_rate)}} win≥${{(a.win_rate.required*100)|0}}% &nbsp; ${{ck(a.sharpe)}} sharpe≥${{a.sharpe.required}} &nbsp; ${{ck(a.max_drawdown)}} maxDD≤${{(a.max_drawdown.required*100)|0}}%`
    + (a.closed_trades.pass&&!a.ready?` &nbsp; <span style="color:${{C.orange}}">— count met, quality not yet</span>`:"");

  const ob=document.getElementById("open-body");
  ob.innerHTML=d.open_positions.length?d.open_positions.map(p=>{{
    const tint=p.at_risk?`background:${{C.orange}}14;`:"";
    const ds=p.distance_to_stop_pct;
    return `<tr style="${{tint}}"><td><b>${{esc(p.symbol)}}</b>${{p.profit_locked?` <span style="color:${{C.green}}">+lock</span>`:""}}</td>
      <td>${{badge(p.action)}}</td><td class="r num">${{(p.qty||0).toFixed(4)}}</td>
      <td class="r num">$${{(p.entry_price||0).toFixed(2)}}</td>
      <td class="r num">${{p.current_price==null?"—":"$"+p.current_price.toFixed(2)}}</td>
      <td class="r num" style="color:${{pnlColor(p.unrealized_pnl_usd)}}">${{fmtUsd(p.unrealized_pnl_usd)}} <span style="color:${{C.dim}}">${{fmtPct(p.unrealized_pnl_pct)}}</span></td>
      <td class="r num">${{ds==null?"—":ds.toFixed(1)+"%"}}</td>
      <td class="r num">${{p.distance_to_target_pct==null?"—":p.distance_to_target_pct.toFixed(1)+"%"}}</td>
      <td class="r num" style="color:${{C.orange}}">${{p.distance_to_stop_usd==null?"—":"$"+p.distance_to_stop_usd.toFixed(0)}}</td>
      <td class="r num" style="color:${{C.dim}}">${{esc(p.hold_time)}}</td></tr>`;}}).join("")
    :`<tr><td colspan="10" class="empty">No open positions — ready for entry.</td></tr>`;

  const cb=document.getElementById("closes-body");
  cb.innerHTML=d.recent_closes.length?d.recent_closes.map(r=>{{
    const c=r.outcome==="WIN"?C.green:(r.outcome==="LOSS"?C.red:C.dim);
    return `<tr><td><b>${{esc(r.symbol)}}</b></td><td>${{badge(r.action)}}</td>
      <td class="r num">$${{(+r.entry_price).toFixed(2)}}</td><td class="r num">$${{(+r.exit_price).toFixed(2)}}</td>
      <td class="r num" style="color:${{pnlColor(r.pnl_usd)}}">${{fmtUsd(r.pnl_usd)}}</td>
      <td class="r num" style="color:${{pnlColor(r.pnl_pct)}}">${{fmtPct(r.pnl_pct)}}</td>
      <td class="r"><span class="pill" style="background:${{c}}22;color:${{c}}">${{esc(r.outcome)}}</span></td>
      <td class="r num" style="color:${{C.dim}}">${{(+r.hold_days||0).toFixed(1)}}d</td>
      <td class="r num" style="color:${{C.dim}}">${{(r.exit_at||"").slice(0,16).replace("T"," ")}}</td></tr>`;}}).join("")
    :`<tr><td colspan="9" class="empty">No closed trades yet — building track record. ${{d.autonomy.closed_n}} of ${{d.autonomy.required}} closed.</td></tr>`;
  document.getElementById("closes-kpi").textContent=
    `· win ${{(s.win_rate_total*100).toFixed(0)}}%${{s.sample_provisional?" (provisional)":""}} · streak ${{s.streak||"—"}} · realized 7d ${{fmtUsd(s.realized_pnl_7d_usd)}}`;

  const fc={{FILL:C.green,GATE_REJECTED:C.red,GATE_APPROVED:C.dim,EXIT:C.yellow}};
  const fe=document.getElementById("feed");
  fe.innerHTML=d.activity_feed.length?d.activity_feed.map(e=>
    `<div style="border-left-color:${{fc[e.type]||C.border}}"><span class="num" style="color:${{C.dim}}">${{esc((e.event_time||"").slice(11,19))}}</span> ${{esc(e.summary)}}</div>`
  ).join(""):`<div class="empty">No activity yet.</div>`;
}}
function setStatus(t){{document.getElementById("status").textContent=t;}}
async function refresh(){{
  try{{const r=await fetch('/watch/data',{{cache:'no-store'}});if(!r.ok){{setStatus('offline — last good view');return;}}
    const d=await r.json();renderWatch(d);setStatus('live · '+(d.as_of||'').slice(11,19));}}
  catch(_){{setStatus('offline — last good view');}}
}}
renderWatch({payload});                       // initial paint from server-rendered data
refresh(); setInterval(refresh,12000);
document.addEventListener('visibilitychange',()=>{{if(!document.hidden)refresh();}});
</script>
</body></html>"""
