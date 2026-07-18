"""Render the internal, audit-aligned /progress founder cockpit.

The trading engine's ``weekly_snapshots`` table is retained as historical context,
but it contains the retired 30-trade/September launch model and therefore never
determines readiness. Signal readiness comes only from the engine's versioned
gate DTO. Product and operations readiness are explicit audit governance gates.
All database access is read-only.
"""

from __future__ import annotations

import html
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Callable

DEFAULT_DB = os.getenv(
    "GESH_TRACKER_DB",
    str(Path.home() / "Projects" / "gesh-project-trading" / "gesh_financial.db"),
)

_BG, _SURFACE, _BORDER = "#080d14", "#0d1520", "#1a2840"
_CYAN, _GREEN, _YELLOW, _RED, _DIM = (
    "#00d4ff", "#00e87a", "#f0b429", "#ff3c5a", "#5b7290",
)

GateLoader = Callable[[str], dict[str, Any]]


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError, OverflowError):
        return default


def _default_gate_loader(db_path: str) -> dict[str, Any]:
    from proof_page import _load_gate_dto

    return _load_gate_dto(db_path)


def _readiness(gate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    signal_state = "VERIFIED" if gate.get("pass") else "BLOCKED"
    return {
        "product_beta": {
            "label": "Paid-beta readiness",
            "state": "BLOCKED",
            "verified": ("Auth + customer isolation", "Billing webhook + hosted checkout"),
            "blockers": ("Legal + privacy review", "Customer data rights + billing lifecycle"),
        },
        "signal_evidence": {
            "label": "Signal evidence",
            "state": signal_state,
            "verified": ("Versioned fail-closed gate", "Simulated cohort separation"),
            "blockers": () if signal_state == "VERIFIED" else (
                "No validated positive edge", "Gate remains fail-closed",
            ),
        },
        "operations": {
            "label": "Operations",
            "state": "DEGRADED",
            "verified": ("Service + scheduled jobs", "Database integrity checks"),
            "blockers": (
                "Sensitive file permissions + backup/restore",
                "Provider quality + known ECG/alert fixes",
            ),
        },
    }


def load_progress(
    db_path: str | Path | None = None,
    today: str | date | None = None,
    *,
    gate_loader: GateLoader | None = None,
) -> dict[str, Any]:
    """Load legacy weekly context and the authoritative current readiness model."""
    db_path = str(db_path or DEFAULT_DB)
    if isinstance(today, str):
        today = date.fromisoformat(today)
    today = today or date.today()

    rows: list[dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in con.execute(
                "SELECT * FROM weekly_snapshots ORDER BY week_ending DESC LIMIT 12"
            ).fetchall()]
        except sqlite3.OperationalError:
            rows = []
        finally:
            con.close()
    except sqlite3.OperationalError:
        rows = []

    loader = gate_loader or _default_gate_loader
    evaluator_degraded = False
    try:
        raw_gate = loader(db_path)
    except Exception:
        raw_gate = {}
        evaluator_degraded = True
    if not isinstance(raw_gate, dict):
        raw_gate = {}
        evaluator_degraded = True
    required_keys = {"version", "closes", "required", "pass"}
    evaluator_degraded = evaluator_degraded or not required_keys.issubset(raw_gate)
    gate = dict(raw_gate)
    gate.setdefault("version", 2)
    gate.setdefault("cohort", "engine")
    gate.setdefault("closes", 0)
    gate.setdefault("required", 100)
    gate.setdefault("criteria", {})
    gate.setdefault("pass", False)
    gate.setdefault("summary", "gate unavailable — fail-closed")
    gate.setdefault("fallback", evaluator_degraded)
    gate.setdefault("as_of", "")

    return {
        "has_data": bool(rows),
        "latest": rows[0] if rows else None,
        "recent": list(reversed(rows)),
        "today": today.isoformat(),
        "signal_gate": gate,
        "readiness": _readiness(gate),
    }


def _state_color(state: str) -> str:
    return {"VERIFIED": _GREEN, "DEGRADED": _YELLOW, "BLOCKED": _RED}.get(state, _DIM)


def _velocity_svg(recent: list[dict[str, Any]], w: int = 720, h: int = 120) -> str:
    """Plot legacy close-count history without implying a readiness threshold."""
    points = [_nonnegative_int(row.get("closed_n")) for row in recent]
    if len(points) < 2:
        return (
            f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">'
            f'<rect width="{w}" height="{h}" fill="{_SURFACE}"/>'
            f'<text x="{w // 2}" y="{h // 2}" fill="{_DIM}" font-family="monospace" '
            f'font-size="12" text-anchor="middle">historical trend appears from week 2</text></svg>'
        )
    pad = 12
    high = max(max(points), 1)

    def xy(index: int, value: int) -> str:
        x = pad + index * (w - 2 * pad) / (len(points) - 1)
        y = pad + (h - 2 * pad) * (1 - value / high)
        return f"{x:.1f},{y:.1f}"

    line = " ".join(xy(index, value) for index, value in enumerate(points))
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">'
        f'<rect width="{w}" height="{h}" fill="{_SURFACE}"/>'
        f'<polyline points="{line}" fill="none" stroke="{_CYAN}" stroke-width="2"/></svg>'
    )


def _lane(lane: dict[str, Any]) -> str:
    state = str(lane["state"])
    color = _state_color(state)
    verified = "".join(
        f'<li><span style="color:{_GREEN}">✓</span> {_esc(item)}</li>'
        for item in lane.get("verified", ())
    )
    blockers = "".join(
        f'<li><span style="color:{_RED}">●</span> {_esc(item)}</li>'
        for item in lane.get("blockers", ())
    )
    return (
        f'<section class="lane" style="border-top:3px solid {color}">'
        f'<div class="lane-head"><h2>{_esc(lane["label"])}</h2>'
        f'<span class="state" style="color:{color};border-color:{color}">{_esc(state)}</span></div>'
        f'<ul>{verified}{blockers}</ul></section>'
    )


_DISCLAIMER = (
    "NOT FINANCIAL ADVICE — FOR INFORMATIONAL PURPOSES ONLY. Internal readiness and "
    "paper-evidence cockpit. All trade metrics are hypothetical/simulated; a software "
    "readiness state is not evidence of future investment performance."
)


def render_progress_html(data: dict[str, Any]) -> str:
    gate = data["signal_gate"]
    readiness = data["readiness"]
    lanes = "".join(_lane(readiness[key]) for key in (
        "product_beta", "signal_evidence", "operations",
    ))
    closes = _nonnegative_int(gate.get("closes"))
    required = _nonnegative_int(gate.get("required"), 100) or 100
    gate_state = "VERIFIED" if gate.get("pass") else "BLOCKED"
    gate_color = _state_color(gate_state)
    fallback = " · evaluator degraded" if gate.get("fallback") else ""
    gate_card = (
        f'<section class="card gate"><div class="eyebrow">Authoritative engine cohort · gate v{_esc(gate.get("version"))}</div>'
        f'<div class="gate-row"><div><strong class="gate-count">{closes} of {required}</strong>'
        f'<div class="muted">closed simulations required before quality criteria can pass</div></div>'
        f'<span class="state" style="color:{gate_color};border-color:{gate_color}">{gate_state}</span></div>'
        f'<p>{_esc(gate.get("summary"))}{_esc(fallback)}</p></section>'
    )

    legacy = ""
    if data["has_data"]:
        snapshot = data["latest"] or {}
        legacy = (
            '<section class="card"><div class="eyebrow">Legacy weekly snapshot · context only</div>'
            f'<div class="legacy"><strong>{_esc(snapshot.get("status", "—"))}</strong>'
            f'<span>{_nonnegative_int(snapshot.get("closed_n"))} historical closes</span>'
            f'<span>week ending {_esc(snapshot.get("week_ending", "—"))}</span></div>'
            f'{_velocity_svg(data["recent"])}'
            '<p class="muted">This series used the retired deadline model. It does not determine paid-beta or autonomy readiness.</p></section>'
        )
    else:
        legacy = (
            '<section class="card"><div class="eyebrow">Legacy weekly snapshot · context only</div>'
            '<p class="muted">No historical weekly snapshot is available. Current gates remain fail-closed.</p></section>'
        )

    body = (
        '<div class="hero"><div><div class="eyebrow">Audit truth · 2026-07-18</div>'
        '<h1>Paid-beta readiness</h1><p>Release confidence by evidence—not a calendar countdown.</p></div>'
        '<div class="hero-mark">VERIFY<br>BEFORE<br>CLAIM</div></div>'
        f'<div class="lanes">{lanes}</div>{gate_card}{legacy}'
    )
    return _page(body, data["today"])


def _page(body: str, as_of: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GESH — Readiness Progress</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:{_BG};color:#e8eef6;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:1040px;margin:0 auto;padding:26px 20px 56px}} a{{color:{_CYAN}}}
.nav{{display:flex;justify-content:space-between;gap:18px;align-items:center;margin-bottom:28px}} .nav strong{{color:{_CYAN}}}
.nav-links{{font-size:12px;text-align:right}} .hero{{display:flex;justify-content:space-between;gap:24px;align-items:end;padding:22px 0 26px;border-bottom:1px solid {_BORDER}}} h1{{font-size:38px;margin:4px 0 5px;letter-spacing:-.04em}}
.hero p,.muted{{color:{_DIM};font-size:12px;line-height:1.55}} .hero-mark{{font:700 11px/1.45 ui-monospace,monospace;letter-spacing:.18em;color:{_YELLOW};border-left:3px solid {_YELLOW};padding-left:12px}}
.eyebrow{{color:{_DIM};font:600 10px/1.4 ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}}
.lanes{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}} .lane,.card{{background:{_SURFACE};border:1px solid {_BORDER};border-radius:8px;padding:15px 16px}}
.lane-head,.gate-row,.legacy{{display:flex;justify-content:space-between;gap:12px;align-items:center}} .lane h2{{font-size:14px;margin:0}} .state{{font:700 10px ui-monospace,monospace;border:1px solid;border-radius:12px;padding:3px 8px;white-space:nowrap}}
.lane ul{{list-style:none;padding:0;margin:14px 0 0;font-size:12px;line-height:1.75}} .card{{margin:12px 0}} .gate-count{{display:block;color:{_CYAN};font:700 27px ui-monospace,monospace;margin:5px 0}}
.card p{{margin:10px 0 0;font-size:12px;line-height:1.6}} .legacy{{font-size:12px;margin:10px 0;flex-wrap:wrap}} .legacy strong{{color:{_YELLOW}}}
.disclaimer{{margin-top:22px;color:{_DIM};font-size:10px;line-height:1.6;border-top:1px solid {_BORDER};padding-top:14px}}
@media(max-width:760px){{.lanes{{grid-template-columns:1fr}}.hero{{align-items:start}}h1{{font-size:30px}}.nav{{align-items:flex-start}}}}
</style></head>
<body><main class="wrap">
<nav class="nav"><strong>GESH / READINESS</strong><span class="nav-links"><a href="/watch">Paper Evidence Watch</a> · <a href="/invest">Scenario lab</a> · <a href="/">Dashboard</a></span></nav>
{body}
<p class="disclaimer">As of {_esc(as_of)} · {_DISCLAIMER}</p>
</main></body></html>"""
