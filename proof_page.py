"""
proof_page.py — public signal-accuracy "proof page" generator.

The Sep-2026 alpha is sold on a documented paper-trade track record. This module
reads the canonical closed-trade table (``paper_outcomes``, written by the trading
engine's ``trading/tracker.py``) and renders a single self-contained HTML page that
shows the full ledger, engine vs manual cohorts, and the AUTHORITATIVE autonomy-gate
progress (the engine's versioned gate_status_dto — engine cohort, 100-close floor).

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
import html
import math
import os
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any


# Public page rendered OUTSIDE the dashboard XSS guard, under a CSP that carries
# 'unsafe-inline' — so every DB-sourced string (symbol/source/outcome/timestamps)
# MUST be HTML-escaped at the sink or a `<` in the data executes (8th-pass, Fable
# audit). Numbers are float()/int()-coerced elsewhere and are safe.
def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))

# Tracker lives in the trading engine repo; default to its gesh_financial.db.
DEFAULT_TRACKER_DB = os.getenv(
    "GESH_TRACKER_DB",
    str(Path.home() / "Projects" / "gesh-project-trading" / "gesh_financial.db"),
)

# Readiness is NEVER derived here: the page consumes the trading engine's versioned
# gate_status_dto() (trading/tracker.py — engine cohort, 100-close floor plus
# expectancy / profit factor / Monte-Carlo / probabilistic+deflated Sharpe / OOS).
# The retired local 30-trade/win-rate/Sharpe/drawdown re-derivation is gone; the
# constants below only shape the fail-CLOSED fallback DTO when that import fails.
GATE_DTO_VERSION_EXPECTED = 2
GATE_REQUIRED_FALLBACK    = 100
ENGINE_SOURCES_FALLBACK   = ("consensus", "market_scan")
_TRADING_PKG_ALIAS        = "gesh_trading_engine"
DAYS_TARGET       = 180          # 6-month documented track record (the sales asset)
LAUNCH_DATE       = "2026-05-21"  # execution clock start (post bug-fix), per roadmap

# Dark-terminal palette (matches gesh_report.html house style).
_BG, _SURFACE, _BORDER = "#080d14", "#0d1520", "#1a2840"
_CYAN, _GREEN, _YELLOW, _RED, _DIM = "#00d4ff", "#00e87a", "#f0b429", "#ff3c5a", "#5b7290"


_SPY_CACHE: dict[str, Any] = {}
_SPY_CACHE_TTL_S = 3600
_SPY_REQUEST_TIMEOUT_S = 10
_SPY_CACHE_LOCK = threading.Lock()


def _spy_return_since(start_date: str) -> float | None:
    """SPY total return (fraction) from start_date to now — the benchmark every
    due-diligence checklist demands next to a win rate. TTL-cached because the
    public page renders per request; fails soft to None (stat simply omitted)."""
    import time as _time
    hit = _SPY_CACHE.get(start_date)
    if hit is not None and (_time.time() - hit[0]) <= _SPY_CACHE_TTL_S:
        return hit[1]
    # Only one request may refresh the public-page benchmark at a time. Recheck
    # after taking the lock so concurrent cold-cache requests reuse its result.
    with _SPY_CACHE_LOCK:
        hit = _SPY_CACHE.get(start_date)
        if hit is not None and (_time.time() - hit[0]) <= _SPY_CACHE_TTL_S:
            return hit[1]
        try:
            import yfinance as yf
            closes = yf.Ticker("SPY").history(
                period="1y", auto_adjust=True, timeout=_SPY_REQUEST_TIMEOUT_S,
            )["Close"]
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


def _trading_home() -> Path:
    """Trading-engine repo root — same GESH_TRADING_HOME sibling-dir convention as
    run_engines2.py / run_ai2.py."""
    return Path(os.getenv(
        "GESH_TRADING_HOME",
        str(Path(__file__).parent.parent / "gesh-project-trading"),
    ))


def _import_gate_status_fn():
    """Resolve the ENGINE repo's trading.tracker.gate_status_dto.

    The product repo has its OWN `trading` package (risk_gate/state/…) already
    bound in sys.modules inside the server, so a sys.path insert can never reach
    the engine's copy — the engine package is loaded under an alias name via
    importlib instead (the CLAUDE.md "pin via importlib" convention). Separated
    into a seam so tests can stub or fail it deterministically."""
    import importlib
    import importlib.util
    import sys

    pkg_dir = _trading_home() / "trading"
    if _TRADING_PKG_ALIAS not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _TRADING_PKG_ALIAS, pkg_dir / "__init__.py",
            submodule_search_locations=[str(pkg_dir)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"trading engine package not found at {pkg_dir}")
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[_TRADING_PKG_ALIAS] = pkg
        try:
            spec.loader.exec_module(pkg)
        except BaseException:
            sys.modules.pop(_TRADING_PKG_ALIAS, None)  # never cache a half-import
            raise
    tracker = importlib.import_module(f"{_TRADING_PKG_ALIAS}.tracker")
    return tracker.gate_status_dto


def _fallback_gate_dto() -> dict[str, Any]:
    """Fail-CLOSED gate when the engine isn't importable: the public page must never
    claim readiness on a degraded path. `closes` is patched by the caller from the
    locally-read ledger so progress copy stays honest."""
    return {
        "version": GATE_DTO_VERSION_EXPECTED, "cohort": "engine",
        "cohort_sources": list(ENGINE_SOURCES_FALLBACK),
        "closes": 0, "required": GATE_REQUIRED_FALLBACK,
        "criteria": {}, "pass": False,
        "summary": "autonomy gate unavailable — evaluated fail-closed",
        "eta_note": "", "window": "since_launch", "as_of": "",
        "fallback": True,
    }


def _load_gate_dto(db_path: str) -> dict[str, Any]:
    """Authoritative autonomy-gate DTO evaluated over `db_path`. Any import/DB
    error degrades to the fail-closed fallback — never an exception, never a pass."""
    try:
        if not Path(db_path).exists():
            # The engine's _conn() would CREATE a missing DB — this page is read-only.
            raise FileNotFoundError(db_path)
        return _import_gate_status_fn()(db_path=db_path)
    except Exception:
        return _fallback_gate_dto()


def _cohort_stats(rows: list[dict]) -> dict[str, Any]:
    """Per-cohort ledger stats (engine vs manual are computed on DISJOINT row sets —
    manual wins can never leak into engine numbers)."""
    closed = len(rows)
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    pnls = [float(r["pnl_pct"] or 0) for r in rows]
    return {
        "closed": closed,
        "wins": wins,
        "losses": sum(1 for r in rows if r["outcome"] == "LOSS"),
        "breakeven": sum(1 for r in rows if r["outcome"] == "BREAKEVEN"),
        "win_rate": round(wins / closed, 4) if closed else 0.0,
        "total_pnl_pct": round(sum(pnls), 4),
    }


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
    signal_ratings, days_running, days_target — all-source LEDGER stats — plus
    `engine` / `manual` (disjoint cohort stats) and `gate` (the trading engine's
    versioned engine-cohort autonomy-gate DTO; fail-closed fallback on error).
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
            # [CLAUDE 2026-07-18] audit D-05: match trading/metrics.py — a zero-variance
            # sample is an insufficient sample, not an infinite Sharpe.
            sharpe = 0.0
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

    # AUTHORITATIVE gate: the engine's versioned DTO (engine cohort only). The old
    # local 30-trade/win-rate/Sharpe/dd re-derivation over the mixed population is
    # retired — it claimed progress manual closes can never provide.
    gate = _load_gate_dto(db_path)
    sources = tuple(str(s) for s in (gate.get("cohort_sources") or ENGINE_SOURCES_FALLBACK))
    engine_rows = [r for r in rows if (r.get("source") or "") in sources]
    manual_rows = [r for r in rows if (r.get("source") or "") not in sources]
    if gate.get("fallback"):
        gate = {**gate, "closes": len(engine_rows)}  # honest progress, still fail-closed

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
        "engine": _cohort_stats(engine_rows), "manual": _cohort_stats(manual_rows),
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


def _cohort_line(label: str, key: str, s: dict[str, Any], note: str) -> str:
    """One cohort's ledger stats. label/key/note are code literals; every stat is
    int/float-coerced. data-* attributes are the machine-checkable contract that
    engine and manual numbers are never mixed."""
    return (
        f'<div data-cohort="{key}" style="margin:4px 0;color:{_DIM}">'
        f'<b style="color:#dfe8f2">{label}</b> <span>({note})</span> — '
        f'closed <b class="num" style="color:#e8eef6" data-k="{key}-closed">{int(s["closed"])}</b> · '
        f'wins <b class="num" style="color:{_GREEN}" data-k="{key}-wins">{int(s["wins"])}</b> · '
        f'losses <b class="num" style="color:{_RED}" data-k="{key}-losses">{int(s["losses"])}</b> · '
        f'breakeven <b class="num" data-k="{key}-breakeven">{int(s["breakeven"])}</b> · '
        f'win rate <b class="num" style="color:#e8eef6" data-k="{key}-winrate">{float(s["win_rate"])*100:.0f}%</b> · '
        f'total P&amp;L <b class="num" style="color:#e8eef6" data-k="{key}-pnl">{float(s["total_pnl_pct"])*100:+.1f}%</b>'
        f'</div>'
    )


def _fmt_spy(v: Any) -> str:
    return f"{v*100:+.1f}%" if isinstance(v, (int, float)) else "n/a"


def _fmt_sources(by_source: Any) -> str:
    if not by_source:
        return "—"
    return " · ".join(f"{_esc(k)} {int(v)}" for k, v in sorted(by_source.items(), key=lambda x: -x[1]))


def render_html(data: dict[str, Any]) -> str:
    """Render the self-contained proof-page HTML from a load_track_record() dict."""
    gate = data["gate"]
    closes = int(gate.get("closes") or 0)
    required = int(gate.get("required") or GATE_REQUIRED_FALLBACK)
    # Belt-and-braces fail-closed: a pass CLAIM needs both the engine gate's verdict
    # AND the full engine sample — a buggy/spoofed DTO can never render "gate met".
    ready = bool(gate.get("pass")) and closes >= required

    crit = gate.get("criteria") or {}
    chip_defs: list[tuple[str, dict, str]] = [(
        "Engine Closes",
        {"value": closes, "required": required, "pass": closes >= required},
        "{:.0f}",
    )]
    for label, key, fmt in (
        ("Expectancy / Trade", "expectancy", "{:+.4f}"),
        ("Profit Factor", "profit_factor", "{:.2f}"),
        ("Max Drawdown", "max_drawdown", "{:.0%}"),
        ("Prob. Sharpe", "prob_sharpe", "{:.2f}"),
        ("Deflated Sharpe", "deflated_sharpe", "{:.2f}"),
    ):
        g = crit.get(key)
        if isinstance(g, dict):
            chip_defs.append((label, g, fmt))
    chips = "".join(_gate_chip(label, g, fmt) for label, g, fmt in chip_defs)

    if closes == 0:
        banner_txt = (
            f"Building track record — Day {data['days_running']} of {data['days_target']}. "
            f"0 of {required} engine closed trades recorded. Manual trades are tracked "
            "separately below and never count toward the autonomy gate."
        )
        banner_color = _YELLOW
    elif not ready:
        banner_txt = (
            f"Building track record — Day {data['days_running']} of {data['days_target']}. "
            f"{closes} of {required} engine closed trades. Manual trades are shown "
            "separately and never count toward the gate; metrics are provisional until "
            f"the {required}-close engine autonomy gate is met."
        )
        banner_color = _YELLOW
    else:
        banner_txt = (
            f"Autonomy gate met — {closes} engine closed trades, all readiness "
            f"criteria passed over {data['days_running']} days."
        )
        banner_color = _GREEN

    srcs = _esc(" + ".join(str(s) for s in (gate.get("cohort_sources")
                                            or ENGINE_SOURCES_FALLBACK)))
    # HEADLINE = the strategy being evaluated (audit Q-05 / §12). The page led with
    # a blended "+22.2% vs SPY +0.9%" cumulative curve while the gated ENGINE cohort
    # — the thing the autonomy gate measures and the thing a subscriber is buying —
    # was −7.7% with a 0.65 profit factor. Both remain on the page; what changes is
    # which one a reader meets first, and that neither can be read without the other.
    eng, man = data["engine"], data["manual"]
    eng_pnl = float(eng["total_pnl_pct"]) * 100
    blended_pnl = float(data["total_pnl_pct"]) * 100
    headline_html = (
        f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-left:4px solid '
        f'{_GREEN if eng_pnl >= 0 else _RED};border-radius:10px;padding:14px 16px;'
        f'margin-bottom:16px">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;'
        f'margin-bottom:6px">Headline — autonomous engine cohort (the gated strategy)</div>'
        f'<div style="font-size:24px;font-weight:700;color:'
        f'{_GREEN if eng_pnl >= 0 else _RED}" class="num" data-k="headline-engine-pnl">'
        f'{eng_pnl:+.1f}%</div>'
        f'<div style="color:{_DIM};font-size:12px;margin-top:4px">'
        f'{int(eng["closed"])} closed paper trades · win rate '
        f'{float(eng["win_rate"]) * 100:.0f}% · SPY over the same period '
        f'{_fmt_spy(data.get("spy_return_period"))}</div>'
        f'<div style="color:{_DIM};font-size:12px;margin-top:8px;line-height:1.55">'
        f'This is the number to judge the product on. A separate blended figure of '
        f'<b class="num" style="color:#e8eef6" data-k="headline-blended-pnl">{blended_pnl:+.1f}%</b> '
        f'appears further down: it mixes in {int(man["closed"])} hand-rated MANUAL trades, '
        f'which are not produced by the engine, are not reproducible by a subscriber, '
        f'and never count toward the autonomy gate.</div>'
        f'</div>'
    )
    cohorts_html = (
        headline_html
        + f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;'
        f'padding:12px 16px;margin-bottom:20px;font-size:13px">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;'
        f'letter-spacing:.5px;margin-bottom:6px">Cohorts — the autonomy gate measures '
        f'the engine cohort ONLY</div>'
        + _cohort_line("Engine cohort", "engine", data["engine"],
                       f"sources: {srcs} — the only autonomy-gate population")
        + _cohort_line("Manual cohort", "manual", data["manual"],
                       "rated hand trades — informational, never gates")
        + "</div>"
    )

    def ts(v: Any) -> str:
        return _esc((str(v or "")[:16].replace("T", " ")) or "—")

    rows_html = ""
    for t in reversed(data["trades"][-50:]):
        oc = t["outcome"]
        c = _GREEN if oc == "WIN" else (_RED if oc == "LOSS" else _DIM)
        rows_html += (
            f'<tr style="border-bottom:1px solid {_BORDER}">'
            f'<td style="padding:8px 12px">{_esc(t["symbol"])}</td>'
            f'<td style="padding:8px 12px;color:{_DIM}">{_esc(t.get("source",""))}</td>'
            f'<td style="padding:8px 12px;color:{_DIM};white-space:nowrap">{ts(t.get("entry_at"))}</td>'
            f'<td style="padding:8px 12px;color:{_DIM};white-space:nowrap">{ts(t.get("exit_at"))}</td>'
            f'<td style="padding:8px 12px;text-align:right">${float(t["entry_price"]):.2f}</td>'
            f'<td style="padding:8px 12px;text-align:right">${float(t["exit_price"]):.2f}</td>'
            f'<td style="padding:8px 12px;text-align:right;color:{c}">{float(t["pnl_pct"])*100:+.1f}%</td>'
            f'<td style="padding:8px 12px;text-align:right;color:{c};font-weight:600">{_esc(oc)}</td>'
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
   generated {_esc(data['generated_at'])}</div>

 <div style="background:{_SURFACE};border:1px solid {banner_color};border-left:4px solid {banner_color};
   border-radius:8px;padding:14px 16px;margin-bottom:20px;color:#dfe8f2;font-size:14px">{banner_txt}</div>

 <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px">{chips}</div>

 {cohorts_html}

 <div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;padding:16px;margin-bottom:8px">
   <div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">
     Cumulative paper equity — ALL SOURCES BLENDED (engine + manual), not the gated strategy</div>
   {_svg_equity_curve(data['equity_curve'])}
   <div style="color:{_DIM};font-size:11px;margin-top:8px;line-height:1.5">This curve includes
     hand-rated manual trades. The autonomy gate and the headline above are computed on the
     ENGINE cohort alone; the per-cohort figures are listed above.</div>
 </div>
 <div style="display:flex;gap:24px;flex-wrap:wrap;color:{_DIM};font-size:12px;margin-bottom:24px">
   <span><b style="color:#dfe8f2">All sources BLENDED (engine + manual — not the gated strategy):</b></span>
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
   count in the denominator). The autonomy gate is evaluated by the trading engine on the
   ENGINE cohort only — manual trades are reported separately and never count toward it.
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
    print(f"Wrote {out} — {d['closed_trades']} closed trades "
          f"(engine {d['engine']['closed']} / manual {d['manual']['closed']}), "
          f"gate v{d['gate']['version']} pass={d['gate']['pass']} "
          f"({d['gate']['closes']}/{d['gate']['required']} engine closes)")
