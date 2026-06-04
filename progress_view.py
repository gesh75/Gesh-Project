"""
progress_view.py — render the internal /progress founder cockpit on :9000.

Reader side of the weekly progress tracker. The writer (gesh-project-trading
trading/tracker_weekly.py, Friday 09:00 job) persists a full status row to the
weekly_snapshots table; this module reads the latest row READ-ONLY and renders a
single self-contained HTML cockpit, recomputing only the live days-to-alpha
countdown so it stays current between Friday writes. Honest "pending first
review" state when no snapshot exists yet. No external CDN (inline SVG trend).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_DB = os.getenv(
    "GESH_TRACKER_DB",
    str(Path.home() / "Projects" / "gesh-project-trading" / "gesh_financial.db"),
)
ALPHA_DATE = date(2026, 9, 1)
GATE_DEADLINE = date(2026, 8, 31)
GATE_TARGET = 30
MILESTONES = ("auth", "proof", "tos", "payment", "routes", "entry_stream")

_BG, _SURFACE, _BORDER = "#080d14", "#0d1520", "#1a2840"
_CYAN, _GREEN, _YELLOW, _ORANGE, _RED, _DIM = "#00d4ff", "#00e87a", "#f0b429", "#ff8c3a", "#ff3c5a", "#5b7290"


def load_progress(db_path: str | Path | None = None, today: str | date | None = None) -> dict[str, Any]:
    db_path = str(db_path or DEFAULT_DB)
    if isinstance(today, str):
        today = date.fromisoformat(today)
    today = today or date.today()

    rows: list[dict] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM weekly_snapshots ORDER BY week_ending DESC LIMIT 12"
            ).fetchall()]
        except sqlite3.OperationalError:
            rows = []
        con.close()
    except sqlite3.OperationalError:
        rows = []

    return {
        "has_data": bool(rows),
        "latest": rows[0] if rows else None,
        "recent": list(reversed(rows)),                  # oldest → newest
        "days_to_sep1": (ALPHA_DATE - today).days,        # LIVE countdown
        "days_to_gate": (GATE_DEADLINE - today).days,
        "today": today.isoformat(),
    }


def _countdown_color(days: int) -> str:
    if days > 14:
        return _GREEN
    if days >= 7:
        return _YELLOW
    if days >= 2:
        return _ORANGE
    return _RED


def _status_color(status: str) -> str:
    return {"ON TRACK": _GREEN, "GATE MET": _GREEN, "PRIMING": _CYAN,
            "OFF TRACK": _RED, "REPLAN": _RED}.get(status, _DIM)


def _card(label: str, value: str, color: str, sub: str = "") -> str:
    return (
        f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;'
        f'padding:14px 16px;flex:1;min-width:130px">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px">{label}</div>'
        f'<div style="color:{color};font-size:24px;font-weight:700;font-family:ui-monospace,monospace;margin:4px 0">{value}</div>'
        f'<div style="color:{_DIM};font-size:11px">{sub}</div></div>'
    )


def _velocity_svg(recent: list[dict], w: int = 720, h: int = 150) -> str:
    """Inline SVG of closed_n over weeks with the 30-gate line. No external deps."""
    pts = [(r.get("closed_n") or 0) for r in recent]
    if len(pts) < 2:
        return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}"><rect width="{w}" height="{h}" '
                f'fill="{_SURFACE}"/><text x="{w//2}" y="{h//2}" fill="{_DIM}" font-family="monospace" '
                f'font-size="13" text-anchor="middle">velocity trend appears from week 2</text></svg>')
    pad = 12
    hi = max(GATE_TARGET, max(pts))
    def xy(i, v):
        x = pad + i * (w - 2 * pad) / (len(pts) - 1)
        y = pad + (h - 2 * pad) * (1 - v / hi)
        return f"{x:.1f},{y:.1f}"
    line = " ".join(xy(i, v) for i, v in enumerate(pts))
    gate_y = pad + (h - 2 * pad) * (1 - GATE_TARGET / hi)
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">'
        f'<rect width="{w}" height="{h}" fill="{_SURFACE}"/>'
        f'<line x1="{pad}" y1="{gate_y:.1f}" x2="{w-pad}" y2="{gate_y:.1f}" stroke="{_YELLOW}" '
        f'stroke-dasharray="4 4" opacity="0.7"/>'
        f'<text x="{w-pad}" y="{gate_y-4:.1f}" fill="{_YELLOW}" font-family="monospace" font-size="10" '
        f'text-anchor="end">30 gate</text>'
        f'<polyline points="{line}" fill="none" stroke="{_GREEN}" stroke-width="2"/></svg>'
    )


_DISCLAIMER = ("NOT FINANCIAL ADVICE — FOR INFORMATIONAL PURPOSES ONLY. Internal launch-tracking "
               "cockpit. Metrics are hypothetical/simulated paper results; past performance does "
               "not guarantee future results.")


def render_progress_html(data: dict[str, Any]) -> str:
    d2s = data["days_to_sep1"]
    d2g = data["days_to_gate"]
    cc = _countdown_color(d2s)
    hero = (
        f'<div style="text-align:center;padding:28px 0 8px">'
        f'<div style="color:{cc};font-size:64px;font-weight:800;font-family:ui-monospace,monospace;line-height:1">{d2s}</div>'
        f'<div style="color:{_DIM};font-size:13px;letter-spacing:2px">DAYS TO SEP-1 ALPHA</div>'
        f'<div style="color:{_DIM};font-size:12px;margin-top:4px">30-trade gate must clear by Aug-31 · {d2g}d</div></div>'
    )

    if not data["has_data"]:
        body = (hero +
                f'<div style="background:{_SURFACE};border:1px solid {_YELLOW};border-left:4px solid {_YELLOW};'
                f'border-radius:8px;padding:16px;margin:16px 0;color:#dfe8f2">First weekly review runs '
                f'<b>Friday 09:00 ET</b> — no snapshot yet. It writes the baseline row (status PRIMING, '
                f'velocity 0); week-over-week deltas become meaningful from week 2.</div>')
        return _page(body)

    s = data["latest"]
    try:
        ms = json.loads(s.get("milestones_json") or "{}")
    except Exception:
        ms = {}
    status = s.get("status", "—")
    sc = _status_color(status)
    closed_n = s.get("closed_n") or 0
    cd = s.get("closed_delta") or 0
    wr = s.get("win_rate")
    sharpe = s.get("sharpe") or 0.0
    mdd = s.get("max_drawdown") or 0.0

    wr_val = "n/a" if not closed_n else f"{(wr or 0)*100:.0f}%"
    wr_sub = "directional only" if 0 < closed_n < 20 else ""
    cards = "".join([
        _card("Closed / 30", f"{closed_n}/30", _GREEN if closed_n >= 30 else _CYAN,
              f"day {s.get('days_running','?')}/180"),
        _card("Velocity (wk)", (f"+{cd}" if cd > 0 else str(cd)), _GREEN if cd > 0 else _RED, "closes this week"),
        _card("Win rate", wr_val, _GREEN if (wr or 0) >= 0.55 and closed_n else _DIM, wr_sub or "≥55% target"),
        _card("Sharpe", f"{sharpe:.2f}", _GREEN if sharpe >= 0.5 else _DIM, "≥0.50 target"),
        _card("Max DD", f"{mdd*100:.0f}%", _GREEN if mdd <= 0.10 else _RED, "≤10% target"),
    ])

    burndown = (
        f'<div style="display:flex;align-items:center;gap:12px;background:{_SURFACE};border:1px solid {_BORDER};'
        f'border-radius:10px;padding:14px 16px;margin:14px 0">'
        f'<div style="flex:1;color:#dfe8f2;font-size:14px">Need <b style="font-family:ui-monospace,monospace">'
        f'{(s.get("required_pace") or 0):.1f}/wk</b> to hit 30 by Aug-31 — running '
        f'<b style="font-family:ui-monospace,monospace">{(s.get("actual_pace") or 0):.1f}/wk</b> (4-wk)</div>'
        f'<div style="background:{sc};color:#03121a;font-weight:800;border-radius:20px;padding:6px 16px;'
        f'font-size:13px">{status}</div></div>'
    )

    checklist = ""
    for m in MILESTONES:
        ok = bool(ms.get(m))
        checklist += (f'<span style="color:{_GREEN if ok else _DIM};font-size:13px;margin-right:14px">'
                      f'{"✓" if ok else "○"} {m}</span>')
    milestones_block = (
        f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;padding:14px 16px;margin:14px 0">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">'
        f'Commercial readiness — {(s.get("milestone_pct") or 0):.0f}%</div>{checklist}</div>'
    )

    trend = (
        f'<div style="background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;padding:14px 16px;margin:14px 0">'
        f'<div style="color:{_DIM};font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">'
        f'Closed-trade velocity trend</div>{_velocity_svg(data["recent"])}</div>'
    )

    blocker = (
        f'<div style="background:{_SURFACE};border:1px solid {_RED if not s.get("on_track") else _BORDER};'
        f'border-left:4px solid {sc};border-radius:8px;padding:14px 16px;margin:14px 0;color:#dfe8f2">'
        f'<b style="color:{_DIM};font-size:11px;text-transform:uppercase">Top blocker</b><br>{s.get("top_blocker","—")}</div>'
    )

    return _page(hero + f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin:12px 0">{cards}</div>'
                 + burndown + milestones_block + trend + blocker +
                 f'<p style="color:{_DIM};font-size:12px">Week ending {s.get("week_ending","—")} · '
                 f'<a href="/track-record" style="color:{_CYAN}">public proof page</a></p>')


def _page(body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GESH — Launch Progress</title>
<style>body{{margin:0;background:{_BG};color:#e8eef6;font-family:-apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:820px;margin:0 auto;padding:24px 20px 56px}} a{{color:{_CYAN}}}</style></head>
<body><div class="wrap">
<div style="display:flex;justify-content:space-between;align-items:baseline">
 <h1 style="color:{_CYAN};font-size:20px;margin:0">GESH — Fall-Launch Progress</h1>
 <a href="/" style="color:{_DIM};font-size:13px">← Dashboard</a></div>
{body}
<p style="margin-top:24px;padding:12px 14px;background:{_SURFACE};border:1px solid {_BORDER};border-radius:8px;
 color:{_DIM};font-size:11px;line-height:1.6">{_DISCLAIMER}</p>
</div></body></html>"""
