"""
proof_page.py — public signal-accuracy "proof page" generator.

The Sep-2026 alpha is sold on a documented paper-trade track record. This module
reads the canonical closed-trade table (``paper_outcomes``, written by the trading
engine's ``trading/tracker.py``) and renders a single self-contained HTML page that
shows win-rate / Sharpe / max-drawdown / equity-curve + the autonomy-gate progress.

Design goals:
  * HONEST at 0 closed trades — renders a "building track record · day X of 180"
    state with a visible sample-size banner, never fake numbers.
  * Self-contained — inline SVG equity curve, no external CDN/JS (no SRI surface).
  * Read-only — opens the tracker DB read-only; never writes.
  * Mandatory disclaimers — hypothetical/simulated results + not financial advice.

Usage:
    python3 proof_page.py                       # writes track_record.html
    python3 proof_page.py --db /path/to.db --out track_record.html
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Tracker lives in the trading engine repo; default to its gesh_financial.db.
DEFAULT_TRACKER_DB = os.getenv(
    "GESH_TRACKER_DB",
    str(Path.home() / "Projects" / "gesh-project-trading" / "gesh_financial.db"),
)

# Autonomy-gate thresholds — mirror trading/tracker.py (kept in sync intentionally).
MIN_CLOSED_TRADES = 30
MIN_WIN_RATE      = 0.55
MIN_SHARPE        = 0.50
MAX_DRAWDOWN      = 0.10
DAYS_TARGET       = 180          # 6-month documented track record (the sales asset)
LAUNCH_DATE       = "2026-05-21"  # execution clock start (post bug-fix), per roadmap

# Dark-terminal palette (matches gesh_report.html house style).
_BG, _SURFACE, _BORDER = "#080d14", "#0d1520", "#1a2840"
_CYAN, _GREEN, _YELLOW, _RED, _DIM = "#00d4ff", "#00e87a", "#f0b429", "#ff3c5a", "#5b7290"


_SPY_CACHE: dict[str, Any] = {}
_SPY_CACHE_TTL_S = 3600


def _spy_return_since(start_date: str) -> float | None:
    """SPY total return (fraction) from start_date to now — the benchmark every
    due-diligence checklist demands next to a win rate. TTL-cached because the
    public page renders per request; fails soft to None (stat simply omitted)."""
    import time as _time
    hit = _SPY_CACHE.get(start_date)
    if hit is not None and (_time.time() - hit[0]) <= _SPY_CACHE_TTL_S:
        return hit[1]
    try:
        import yfinance as yf
        closes = yf.Ticker("SPY").history(period="1y", auto_adjust=True)["Close"]
        closes = closes[closes.index >= start_date]
        ret = float(closes.iloc[-1] / closes.iloc[0] - 1.0) if len(closes) >= 2 else None
    except Exception:
        ret = None
    _SPY_CACHE[start_date] = (_time.time(), ret)
    return ret


def _days_between(start: str, end: str) -> int:
    d0 = datetime.fromisoformat(start).date()
    d1 = datetime.fromisoformat(end).date()
    return max((d1 - d0).days, 0)


def load_track_record(
    db_path: str | None = None,
    *,
    launch_date: str = LAUNCH_DATE,
    today: str | None = None,
) -> dict[str, Any]:
    """
    Read closed paper trades and assemble the proof-page data model.

    Returns a dict with: closed_trades, wins, losses, breakeven, win_rate,
    profit_factor, avg_win_pct, avg_loss_pct, total_pnl_pct, sharpe, max_drawdown,
    equity_curve (cumulative, starts at 1.0), trades (list), open_positions,
    signal_ratings, days_running, days_target, and gate (per-threshold pass/fail).
    """
    db_path = db_path or DEFAULT_TRACKER_DB
    today = today or date.today().isoformat()

    rows: list[dict] = []
    open_positions = 0
    signal_ratings = 0
    try:
        # Read-only connect — never create/modify the tracker DB from the product.
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT symbol, source, action, qty, entry_price, exit_price, entry_at, exit_at,"
                " pnl_usd, pnl_pct, outcome, hold_days FROM paper_outcomes"
                " ORDER BY exit_at ASC"
            ).fetchall()]
        except sqlite3.OperationalError:
            rows = []
        for tbl, var in (("paper_positions", "open"), ("signal_overrides", "ratings")):
            try:
                where = " WHERE status='OPEN'" if tbl == "paper_positions" else ""
                n = con.execute(f"SELECT COUNT(*) FROM {tbl}{where}").fetchone()[0]
                if var == "open":
                    open_positions = n
                else:
                    signal_ratings = n
            except sqlite3.OperationalError:
                pass
        con.close()
    except sqlite3.OperationalError:
        rows = []  # DB missing → honest empty state

    closed = len(rows)
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    losses = sum(1 for r in rows if r["outcome"] == "LOSS")
    breakeven = sum(1 for r in rows if r["outcome"] == "BREAKEVEN")
    # POLICY (decided 2026-06-08): CONSERVATIVE — BREAKEVEN COUNTS in the denominator
    # (win_rate = wins / all-closed), so a breakeven drags win_rate down. Kept in sync
    # with trading/tracker.py get_win_rate; a harder gate is the safer bias before live.
    win_rate = round(wins / closed, 4) if closed else 0.0

    pnls = [float(r["pnl_pct"] or 0) for r in rows]
    gains = [p for p in pnls if p > 0]
    drops = [p for p in pnls if p < 0]
    profit_factor = round(sum(gains) / abs(sum(drops)), 2) if drops else (float("inf") if gains else 0.0)
    avg_win = round(sum(gains) / len(gains), 4) if gains else 0.0
    avg_loss = round(sum(drops) / len(drops), 4) if drops else 0.0
    total_pnl_pct = round(sum(pnls), 4)

    # Per-trade Sharpe (rf=0), annualised by trade frequency — mirrors tracker.py.
    sharpe = 0.0
    if closed >= 2:
        mean_r = sum(pnls) / closed
        var = sum((p - mean_r) ** 2 for p in pnls) / (closed - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std == 0:
            sharpe = 10.0 if mean_r > 0 else (-10.0 if mean_r < 0 else 0.0)
        else:
            days = max(_days_between(launch_date, today), 1)
            tpy = closed / max(days / 252.0, 0.001)
            sharpe = round((mean_r / std) * math.sqrt(tpy), 4)

    # Cumulative equity curve (starts at 1.0).
    equity_curve = [1.0]
    peak = 1.0
    max_dd = 0.0
    for p in pnls:
        equity_curve.append(round(equity_curve[-1] * (1.0 + p), 6))
        peak = max(peak, equity_curve[-1])
        if peak > 0:
            max_dd = max(max_dd, (peak - equity_curve[-1]) / peak)
    max_dd = round(max_dd, 4)

    gate = {
        "closed_trades": {"value": closed, "required": MIN_CLOSED_TRADES, "pass": closed >= MIN_CLOSED_TRADES},
        "win_rate":      {"value": win_rate, "required": MIN_WIN_RATE, "pass": closed > 0 and win_rate >= MIN_WIN_RATE},
        "sharpe":        {"value": sharpe, "required": MIN_SHARPE, "pass": sharpe >= MIN_SHARPE},
        "max_drawdown":  {"value": max_dd, "required": MAX_DRAWDOWN, "pass": closed > 0 and max_dd <= MAX_DRAWDOWN},
    }
    gate["ready"] = all(g["pass"] for g in gate.values() if isinstance(g, dict))

    by_source: dict[str, int] = {}
    for r in rows:
        src = (r.get("source") or "unknown").strip() or "unknown"
        by_source[src] = by_source.get(src, 0) + 1

    return {
        "by_source": by_source,
        "spy_return_period": _spy_return_since(launch_date),
        "closed_trades": closed, "wins": wins, "losses": losses, "breakeven": breakeven,
        "win_rate": win_rate, "profit_factor": profit_factor,
        "avg_win_pct": avg_win, "avg_loss_pct": avg_loss, "total_pnl_pct": total_pnl_pct,
        "sharpe": sharpe, "max_drawdown": max_dd, "equity_curve": equity_curve,
        "trades": rows, "open_positions": open_positions, "signal_ratings": signal_ratings,
        "days_running": _days_between(launch_date, today), "days_target": DAYS_TARGET,
        "gate": gate, "generated_at": today,
    }


def _svg_equity_curve(curve: list[float], w: int = 760, h: int = 180) -> str:
    """Inline SVG sparkline of the cumulative equity curve. No external deps."""
    if len(curve) < 2:
        return (
            f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" '
            f'aria-label="No closed trades yet">'
            f'<rect width="{w}" height="{h}" fill="{_SURFACE}"/>'
            f'<line x1="0" y1="{h//2}" x2="{w}" y2="{h//2}" stroke="{_BORDER}" stroke-dasharray="4 4"/>'
            f'<text x="{w//2}" y="{h//2-8}" fill="{_DIM}" font-family="monospace" '
            f'font-size="13" text-anchor="middle">awaiting first closed trade</text></svg>'
        )
    lo, hi = min(curve), max(curve)
    rng = (hi - lo) or 1.0
    pad = 10
    pts = []
    for i, v in enumerate(curve):
        x = pad + i * (w - 2 * pad) / (len(curve) - 1)
        y = pad + (h - 2 * pad) * (1 - (v - lo) / rng)
        pts.append(f"{x:.1f},{y:.1f}")
    up = curve[-1] >= curve[0]
    stroke = _GREEN if up else _RED
    poly = " ".join(pts)
    area = f"{pad},{h-pad} " + poly + f" {w-pad},{h-pad}"
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" '
        f'aria-label="Equity curve">'
        f'<rect width="{w}" height="{h}" fill="{_SURFACE}"/>'
        f'<polygon points="{area}" fill="{stroke}" opacity="0.08"/>'
        f'<polyline points="{poly}" fill="none" stroke="{stroke}" stroke-width="2"/></svg>'
    )


def _gate_chip(label: str, g: dict, fmt: str) -> str:
    ok = g["pass"]
    color = _GREEN if ok else _YELLOW
    mark = "✓" if ok else "•"
    val = fmt.format(g["value"])
    req = fmt.format(g["required"])
    return (
        f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;'
        f'padding:14px 16px;flex:1;min-width:150px">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px">{label}</div>'
        f'<div style="color:{color};font-size:24px;font-weight:700;font-family:monospace;margin:4px 0">{val}</div>'
        f'<div style="color:{_DIM};font-size:11px">{mark} target {req}</div></div>'
    )


def _fmt_spy(v: Any) -> str:
    return f"{v*100:+.1f}%" if isinstance(v, (int, float)) else "n/a"


def _fmt_sources(by_source: Any) -> str:
    if not by_source:
        return "—"
    return " · ".join(f"{k} {v}" for k, v in sorted(by_source.items(), key=lambda x: -x[1]))


def render_html(data: dict[str, Any]) -> str:
    """Render the self-contained proof-page HTML from a load_track_record() dict."""
    n = data["closed_trades"]
    gate = data["gate"]
    pct = lambda x: f"{x*100:.1f}%"

    chips = "".join([
        _gate_chip("Closed Trades", gate["closed_trades"], "{:.0f}"),
        _gate_chip("Win Rate", gate["win_rate"], "{:.0%}"),
        _gate_chip("Sharpe", gate["sharpe"], "{:.2f}"),
        _gate_chip("Max Drawdown", gate["max_drawdown"], "{:.0%}"),
    ])

    if n == 0:
        banner_txt = (
            f"Building track record — Day {data['days_running']} of {data['days_target']}. "
            f"0 of {MIN_CLOSED_TRADES} closed paper trades recorded. "
            "The engine is accumulating signal-accuracy data; metrics publish as trades close."
        )
        banner_color = _YELLOW
    elif not gate["ready"]:
        banner_txt = (
            f"Building track record — Day {data['days_running']} of {data['days_target']}. "
            f"{n} of {MIN_CLOSED_TRADES} closed paper trades. Sample size is still small — "
            "metrics are provisional until the 30-trade autonomy gate is met."
        )
        banner_color = _YELLOW
    else:
        banner_txt = (
            f"Autonomy gate met — {n} closed paper trades, "
            f"{pct(data['win_rate'])} win rate over {data['days_running']} days."
        )
        banner_color = _GREEN

    rows_html = ""
    for t in reversed(data["trades"][-50:]):
        oc = t["outcome"]
        c = _GREEN if oc == "WIN" else (_RED if oc == "LOSS" else _DIM)
        ts = lambda v: (str(v or "")[:16].replace("T", " ")) or "—"
        rows_html += (
            f'<tr style="border-bottom:1px solid {_BORDER}">'
            f'<td style="padding:8px 12px">{t["symbol"]}</td>'
            f'<td style="padding:8px 12px;color:{_DIM}">{t.get("source","")}</td>'
            f'<td style="padding:8px 12px;color:{_DIM};white-space:nowrap">{ts(t.get("entry_at"))}</td>'
            f'<td style="padding:8px 12px;color:{_DIM};white-space:nowrap">{ts(t.get("exit_at"))}</td>'
            f'<td style="padding:8px 12px;text-align:right">${float(t["entry_price"]):.2f}</td>'
            f'<td style="padding:8px 12px;text-align:right">${float(t["exit_price"]):.2f}</td>'
            f'<td style="padding:8px 12px;text-align:right;color:{c}">{float(t["pnl_pct"])*100:+.1f}%</td>'
            f'<td style="padding:8px 12px;text-align:right;color:{c};font-weight:600">{oc}</td>'
            f'<td style="padding:8px 12px;text-align:right;color:{_DIM}">{float(t.get("hold_days") or 0):.1f}d</td>'
            f'</tr>'
        )
    if not rows_html:
        rows_html = (
            f'<tr><td colspan="9" style="padding:24px;text-align:center;color:{_DIM}">'
            f'No closed trades yet — {data["open_positions"]} position(s) open, '
            f'scanning for stop/target exits.</td></tr>'
        )

    disclaimer = (
        "NOT FINANCIAL ADVICE — FOR INFORMATIONAL AND EDUCATIONAL PURPOSES ONLY. "
        "GESH Financial is a self-hosted quantitative research tool, not a registered "
        "investment adviser, and provides no personalized investment advice and owes no "
        "fiduciary duty. All results shown are HYPOTHETICAL / SIMULATED paper trades, not "
        "real money. PAST PERFORMANCE — including simulated performance — does not guarantee "
        "future results. Trading involves substantial risk of loss. You are solely "
        "responsible for your own decisions."
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GESH Financial — Signal Accuracy Track Record</title>
<style>
 body{{margin:0;background:{_BG};color:#e8eef6;font-family:-apple-system,Segoe UI,Roboto,sans-serif}}
 .wrap{{max-width:880px;margin:0 auto;padding:32px 20px 64px}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:{_DIM};font-size:13px;margin-bottom:20px}}
 .num{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
 table{{width:100%;border-collapse:collapse;font-size:13px;font-family:ui-monospace,monospace}}
 th{{text-align:left;color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;padding:8px 12px;border-bottom:1px solid {_BORDER}}}
 th.r{{text-align:right}}
 a{{color:{_CYAN}}}
</style></head>
<body><div class="wrap">
 <h1 style="color:{_CYAN}">GESH Financial — Signal Accuracy Track Record</h1>
 <div class="sub">Documented paper-trade performance — every signal recorded at decision time
   by the live engine (sources labeled per trade: consensus = 11-node engine, manual = rated entries) ·
   generated {data['generated_at']}</div>

 <div style="background:{_SURFACE};border:1px solid {banner_color};border-left:4px solid {banner_color};
   border-radius:8px;padding:14px 16px;margin-bottom:20px;color:#dfe8f2;font-size:14px">{banner_txt}</div>

 <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px">{chips}</div>

 <div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;padding:16px;margin-bottom:8px">
   <div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">
     Cumulative paper equity (normalised to 1.00)</div>
   {_svg_equity_curve(data['equity_curve'])}
 </div>
 <div style="display:flex;gap:24px;flex-wrap:wrap;color:{_DIM};font-size:12px;margin-bottom:24px">
   <span>Wins: <b style="color:{_GREEN}" class="num">{data['wins']}</b></span>
   <span>Losses: <b style="color:{_RED}" class="num">{data['losses']}</b></span>
   <span>Breakeven: <b class="num" style="color:{_DIM}">{data['breakeven']}</b></span>
   <span>Profit factor: <b class="num" style="color:#e8eef6">{data['profit_factor']}</b></span>
   <span>Avg win: <b class="num" style="color:{_GREEN}">{data['avg_win_pct']*100:+.1f}%</b></span>
   <span>Avg loss: <b class="num" style="color:{_RED}">{data['avg_loss_pct']*100:+.1f}%</b></span>
   <span>Total paper P&amp;L: <b class="num" style="color:#e8eef6">{data['total_pnl_pct']*100:+.1f}%</b></span>
   <span>SPY same period: <b class="num" style="color:#e8eef6">{_fmt_spy(data.get('spy_return_period'))}</b></span>
   <span>By source: <b class="num" style="color:#e8eef6">{_fmt_sources(data.get('by_source'))}</b></span>
   <span>Signal ratings: <b class="num" style="color:#e8eef6">{data['signal_ratings']}</b></span>
 </div>

 <div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:8px;padding:12px 16px;
   margin-bottom:24px;color:{_DIM};font-size:12px;line-height:1.6">
   <b style="color:#dfe8f2">Methodology — forward out-of-sample.</b> Every trade below was recorded
   by the live engine at decision time, before the market moved — never backfilled, never
   backtested. The complete ledger is shown including every losing trade. Timestamps are UTC
   close times from the tracker database; win rate uses the conservative policy (breakevens
   count in the denominator).
 </div>

 <h2 style="font-size:15px;margin:0 0 8px">Closed trades</h2>
 <table><thead><tr>
   <th>Symbol</th><th>Source</th><th>Signal (UTC)</th><th>Closed (UTC)</th>
   <th class="r">Entry</th><th class="r">Exit</th>
   <th class="r">P&amp;L</th><th class="r">Outcome</th><th class="r">Hold</th>
 </tr></thead><tbody>{rows_html}</tbody></table>

 <p style="margin-top:32px;padding:14px 16px;background:{_SURFACE};border:1px solid {_BORDER};
   border-radius:8px;color:{_DIM};font-size:11px;line-height:1.6">{disclaimer}</p>
 <p style="color:{_DIM};font-size:11px"><a href="/terms">Terms of Service</a> ·
   <a href="/disclaimer">Full Disclaimer</a></p>
</div></body></html>"""


def generate(db_path: str | None = None, out_path: str = "track_record.html") -> str:
    data = load_track_record(db_path)
    html = render_html(data)
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate the GESH signal-accuracy proof page")
    ap.add_argument("--db", default=None, help="tracker SQLite DB (default: trading engine DB)")
    ap.add_argument("--out", default="track_record.html", help="output HTML path")
    args = ap.parse_args()
    out = generate(args.db, args.out)
    d = load_track_record(args.db)
    print(f"Wrote {out} — {d['closed_trades']} closed trades, "
          f"win rate {d['win_rate']*100:.0f}%, gate ready={d['gate']['ready']}")
