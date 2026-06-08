"""
invest_view.py — internal Diversification Radar panel (GET /invest on :9000).

A read-only "where should new cash go?" cockpit. Surfaces the advisory radar that
the daily 10:30 pipeline persists into engine_results.json: the book's real sector
concentration, ranked NEW (non-held) candidates with multi-factor scores + suggested
sizing, and — when slots/cash are full — swap ideas. Updates via a 12s fetch() poll
against GET /invest/data.

Design (mirrors watch_view.py / proof_page.py):
  * Read-only — reads engine_results.json; writes nothing. The 12s poll just re-reads
    the cached radar so it is fast and never recomputes/hits the network per poll.
  * PII-safe — only whitelisted public fields are surfaced; withdrawable_amount,
    average_buy_price, quantities never leave this module.
  * XSS-safe — every interpolated string is esc()'d; the embedded JSON payload has
    '</' neutralised so it cannot break out of the <script> tag.
  * Honest — empty/zero states are first-class; never fabricates a candidate.
  * Advisory-only — new-name suggestions are research, never auto-traded.
  * Internal — NOT in auth_users.PUBLIC_PREFIXES, so the login wall gates it (the
    dollar figures are account-identifying).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from proof_page import (
    _BG, _SURFACE, _BORDER, _CYAN, _GREEN, _YELLOW, _RED, _DIM,
)

SCHEMA_VERSION = 1
_ORANGE = "#FF8C00"
DEFAULT_RESULTS = str(Path(__file__).parent / "engine_results.json")

# Fields safe to surface (no PII). Mirrors invest_radar._PUBLIC_FIELDS plus the
# derived display fields the radar adds in _public().
_CARD_FIELDS = (
    "symbol", "company_name", "sector", "price", "composite_score", "composite_z",
    "consensus_verdict", "analyst_upside", "num_analysts", "max_corr_to_holdings",
    "correlation_ok", "sector_weight_after", "recommended_usd", "recommended_shares",
    "sizing_binding_constraint", "factor_scores", "timing", "horizon_view", "rationale",
    "actionable",
)
_MODE_NOTE = {
    "BUY": "Deployable cash + open slots — ranked new-name candidates below.",
    "SWAP_ONLY": "No open slots or cash below buffer — diversify via the swap ideas below "
                 "(or raise max_open_positions).",
    "NO_ACTION": "No new-name candidates surfaced in the latest scan.",
}


def _empty(note: str, *, generated_at: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_at": generated_at,
        "mode": "NO_ACTION",
        "note": note,
        "deployable_cash": 0.0,
        "open_slots": 0,
        "new_names_to_add": 0,
        "excluded_holdings": [],
        "existing_sector_weights": {},
        "candidates": [],
        "swap_suggestions": [],
        "diagnostics": {},
        "stale": True,
    }


def _age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    except Exception:
        return None


def _whitelist(cands: list) -> list:
    return [{k: c.get(k) for k in _CARD_FIELDS if k in c} for c in (cands or [])]


def load_invest_data(results_path: str | None = None,
                     what_if_cash: float | None = None) -> dict[str, Any]:
    """Read the persisted radar from engine_results.json, read-only. Never raises —
    returns an honest empty contract on any missing/unreadable/absent-radar case.

    When `what_if_cash` is given, re-sizes the candidates for that HYPOTHETICAL deployable
    amount server-side (pure, no network) — the portfolio total / sector $ used for sizing
    stay server-side; only the sized public candidates reach the client."""
    path = results_path or DEFAULT_RESULTS
    try:
        with open(path) as f:
            eng = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty("Daily results not available yet.")

    radar = eng.get("diversification_candidates")
    if not isinstance(radar, dict) or not radar:
        return _empty("The diversification radar runs with the daily 10:30 analysis — "
                      "no scan recorded yet.")

    generated_at = radar.get("as_of") or eng.get("ai_result_ran_at")
    age = _age_days(generated_at)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_at": generated_at,
        "mode": radar.get("mode", "NO_ACTION"),
        "note": _MODE_NOTE.get(radar.get("mode", "NO_ACTION"), ""),
        "deployable_cash": radar.get("deployable_cash", 0.0),
        "open_slots": radar.get("open_slots", 0),
        "new_names_to_add": radar.get("new_names_to_add", 0),
        "excluded_holdings": radar.get("excluded_holdings", []),
        "existing_sector_weights": radar.get("existing_sector_weights", {}),
        "candidates": _whitelist(radar.get("candidates")),
        "swap_suggestions": radar.get("swap_suggestions", []),
        "diagnostics": radar.get("diagnostics", {}),
        "stale": (age is not None and age > 1.5),
        "age_days": round(age, 2) if age is not None else None,
        "what_if_cash": None,
    }

    # Harden the slider input: reject non-finite / negative, clamp huge — a hostile or
    # buggy ?what_if_cash=inf|nan|-1|1e308 must never reach sizing or emit Infinity/NaN JSON.
    if what_if_cash is not None:
        try:
            wic = float(what_if_cash)
            what_if_cash = min(wic, 1_000_000.0) if (math.isfinite(wic) and wic >= 0) else None
        except (TypeError, ValueError):
            what_if_cash = None

    # What-if cash slider — re-size server-side using the captured survivors (no network).
    state = radar.get("resize_state")
    if what_if_cash is not None and isinstance(state, dict) and state.get("survivors"):
        try:
            import invest_radar
            wi = invest_radar.resize_for_cash(state, float(what_if_cash))
            payload["mode"] = wi["mode"]
            payload["deployable_cash"] = wi["deployable_cash"]
            payload["open_slots"] = wi["open_slots"]
            payload["new_names_to_add"] = wi["new_names_to_add"]
            payload["candidates"] = _whitelist(wi["candidates"])
            payload["swap_suggestions"] = []      # what-if focuses on the sized buys
            payload["what_if_cash"] = round(float(what_if_cash), 2)
            payload["note"] = (f"What-if: sized picks for ${float(what_if_cash):,.0f} deployable "
                               f"(hypothetical — your real config and account are unchanged).")
        except Exception:
            pass   # fall back to the live payload on any what-if error
    return payload


# ── renderer (self-contained HTML + inline poll JS) ──────────────────────────

_DISCLAIMER = (
    "Advisory research only — NOT FINANCIAL ADVICE. New-name candidates are HYPOTHETICAL "
    "diversification ideas, never auto-traded; PAPER_MODE and the autonomy gate are unaffected."
)


def render_invest_html(data: dict[str, Any]) -> str:
    """Self-contained dark page: server-rendered initial paint + a 12s fetch() poll
    that rebuilds the panels. The JSON payload has '</' neutralised so a candidate
    string can never break out of the <script> tag."""
    payload = json.dumps(data).replace("</", "<\\/")
    mode = data.get("mode", "NO_ACTION")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GESH — Diversification Radar</title>
<style>
 body{{margin:0;background:{_BG};color:#e8eef6;font-family:-apple-system,Segoe UI,Roboto,sans-serif}}
 .wrap{{max-width:1080px;margin:0 auto;padding:24px 18px 64px}}
 h1{{font-size:20px;margin:0}} .sub{{color:{_DIM};font-size:12px;margin:2px 0 16px}}
 .num{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}}
 .strip{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}}
 .stat{{background:{_SURFACE};border:1px solid {_BORDER};border-radius:9px;padding:10px 14px;min-width:120px}}
 .stat .k{{color:{_DIM};font-size:10px;text-transform:uppercase;letter-spacing:.5px}}
 .stat .v{{font-size:19px;font-weight:700;margin-top:3px}}
 .card{{background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;padding:14px 16px;margin-bottom:14px}}
 h2{{font-size:14px;margin:0 0 10px;color:#cfe0f2}}
 table{{width:100%;border-collapse:collapse;font-size:12.5px}}
 th{{text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;letter-spacing:.5px;padding:6px 8px;border-bottom:1px solid {_BORDER}}}
 td{{padding:6px 8px;border-bottom:1px solid {_BORDER};vertical-align:top}} .r{{text-align:right}}
 .pill{{padding:1px 7px;border-radius:10px;font-size:10px;font-weight:700}}
 .bar{{height:9px;background:{_BORDER};border-radius:5px;overflow:hidden}} .bar>i{{display:block;height:100%}}
 .secrow{{display:grid;grid-template-columns:130px 1fr 48px;gap:8px;align-items:center;margin-bottom:5px;font-size:12px}}
 .empty{{color:{_DIM};text-align:center;padding:18px;font-size:12.5px}}
 a{{color:{_CYAN}}} #status{{float:right;color:{_DIM};font-size:11px}}
 .chip{{font-size:10px;padding:1px 6px;border-radius:8px;border:1px solid {_BORDER};color:{_DIM};margin-left:6px}}
</style></head>
<body><div class="wrap">
 <div><span id="status">connecting…</span>
   <h1 style="color:{_CYAN}">💎 GESH — Diversification Radar
     <span class="pill" id="mode-pill" style="background:{_YELLOW};color:#1a1200">{mode}</span></h1></div>
 <div class="sub">Internal · where should NEW cash go? · advisory-only · updates every 12s
   &nbsp;·&nbsp;<a href="/watch">📡 Live watch</a> · <a href="/progress">Progress</a> · <a href="/track-record">Track record</a></div>

 <div id="advisory-banner" style="background:{_RED}22;border-left:3px solid {_RED};border-radius:7px;padding:10px 14px;margin:0 0 14px;font-size:12px;color:#e8eef6">
   ⚠️ <b>Advisory research only — NOT FINANCIAL ADVICE.</b> These are diversification ideas for research; they are never auto-traded, and PAPER_MODE + the autonomy gate are unaffected.</div>

 <div class="strip" id="strip"></div>

 <div class="card"><h2>Book concentration <span style="color:{_DIM};font-size:11px;font-weight:400">existing holdings by sector</span></h2>
   <div id="sectors"></div></div>

 <div class="card">
   <h2>What-if · cash to deploy
     <span id="wi-val" class="num" style="color:{_CYAN};font-weight:400">live</span>
     <button id="wi-reset" style="float:right;background:{_BORDER};color:#e8eef6;border:0;border-radius:6px;padding:3px 10px;font-size:11px;cursor:pointer">reset to live</button></h2>
   <input type="range" id="wi-slider" min="0" max="20000" step="250" value="0" style="width:100%;accent-color:{_CYAN};cursor:pointer">
   <div style="display:flex;justify-content:space-between;color:{_DIM};font-size:10px"><span>$0</span><span>$5k</span><span>$10k</span><span>$15k</span><span>$20k</span></div>
   <div style="color:{_DIM};font-size:11px;margin-top:5px">Drag to preview how the radar would size new-name buys at a hypothetical deployable cash — your real config and account stay unchanged.</div>
 </div>

 <div class="card"><h2>New-name candidates <span class="chip" id="cand-note"></span></h2>
   <table><thead><tr><th>Symbol</th><th>Sector</th><th class="r">Score</th><th>Consensus</th>
     <th class="r">Analyst</th><th class="r">Corr→Book</th><th class="r">Suggest</th><th>Thesis</th></tr></thead>
     <tbody id="cand-body"></tbody></table></div>

 <div class="card" id="swap-card" style="display:none"><h2>Swap ideas <span style="color:{_DIM};font-size:11px;font-weight:400">fund a diversifier by trimming concentration</span></h2>
   <table><thead><tr><th>Trim</th><th>Fund new</th><th>Why</th></tr></thead>
     <tbody id="swap-body"></tbody></table></div>

 <p class="card" style="color:{_DIM};font-size:11px;line-height:1.6">{_DISCLAIMER}</p>
</div>
<script>
const C={{green:"{_GREEN}",red:"{_RED}",dim:"{_DIM}",yellow:"{_YELLOW}",orange:"{_ORANGE}",cyan:"{_CYAN}",border:"{_BORDER}"}};
const modeCol={{BUY:C.green,SWAP_ONLY:C.yellow,NO_ACTION:C.dim}};
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
const usd=v=>v==null?"—":"$"+Number(v).toLocaleString(undefined,{{maximumFractionDigits:0}});
function renderInvest(d){{
  const mp=document.getElementById("mode-pill");
  mp.textContent=d.mode; mp.style.background=modeCol[d.mode]||C.dim; mp.style.color="#0b0f14";
  document.getElementById("strip").innerHTML=[
    ["Mode",d.mode,modeCol[d.mode]],
    ["Deployable cash",usd(d.deployable_cash),C.cyan],
    ["Open slots",d.open_slots],
    ["New names",d.new_names_to_add],
    ["Excluded held",(d.excluded_holdings||[]).length],
    ["Scan age",d.age_days==null?"—":(d.age_days.toFixed(1)+"d")+(d.stale?" *":""),d.stale?C.orange:null],
  ].map(([k,v,col])=>`<div class="stat"><div class="k">${{esc(k)}}</div><div class="v num" style="color:${{col||'#e8eef6'}}">${{esc(v)}}</div></div>`).join("");

  const sw=Object.entries(d.existing_sector_weights||{{}}).sort((a,b)=>b[1]-a[1]);
  const maxw=sw.length?sw[0][1]:1;
  document.getElementById("sectors").innerHTML=sw.length?sw.map(([s,w])=>{{
    const over=w>0.30; const col=over?C.orange:C.cyan;
    return `<div class="secrow"><span>${{esc(s)}}</span>
      <span class="bar"><i style="width:${{Math.min(w/maxw*100,100)}}%;background:${{col}}"></i></span>
      <span class="num r" style="color:${{col}}">${{(w*100).toFixed(0)}}%</span></div>`;}}).join("")
    :`<div class="empty">No sector data.</div>`;

  document.getElementById("cand-note").textContent=esc(d.note||"");
  const cb=document.getElementById("cand-body");
  cb.innerHTML=(d.candidates&&d.candidates.length)?d.candidates.map(c=>{{
    const up=c.analyst_upside==null?"—":(c.analyst_upside>=0?"+":"")+Number(c.analyst_upside).toFixed(0)+"%";
    const corr=c.max_corr_to_holdings==null?"—":Number(c.max_corr_to_holdings).toFixed(2);
    return `<tr><td><b style="color:${{C.cyan}}">${{esc(c.symbol)}}</b></td>
      <td>${{esc(c.sector)}}</td>
      <td class="r num">${{c.composite_score==null?"—":c.composite_score}}</td>
      <td>${{esc(c.consensus_verdict||"—")}}</td>
      <td class="r num" style="color:${{C.green}}">${{up}}</td>
      <td class="r num">${{corr}}</td>
      <td class="r num">${{c.actionable===false?`<span style="color:${{C.yellow}}">fund via swap</span>`:(c.recommended_shares||0)+` <span style="color:${{C.dim}}">≈`+usd(c.recommended_usd)+`</span>`}}</td>
      <td style="color:${{C.dim}};font-size:11px">${{esc(c.rationale||"")}}</td></tr>`;}}).join("")
    :`<tr><td colspan="8" class="empty">${{esc(d.note||"No new-name candidates.")}}</td></tr>`;

  const sc=document.getElementById("swap-card"), sb=document.getElementById("swap-body");
  if(d.swap_suggestions&&d.swap_suggestions.length){{
    sc.style.display="block";
    sb.innerHTML=d.swap_suggestions.map(s=>
      `<tr><td style="color:${{C.red}}"><b>${{esc(s.trim_symbol)}}</b></td>
        <td style="color:${{C.green}}"><b>${{esc(s.fund_new_symbol)}}</b></td>
        <td style="color:${{C.dim}};font-size:11px">${{esc(s.rationale)}}</td></tr>`).join("");
  }} else {{ sc.style.display="none"; }}
}}
function setStatus(t){{document.getElementById("status").textContent=t;}}
let whatIf=null;   // null = live; a number = hypothetical deployable cash
async function refresh(){{
  try{{
    const url=whatIf?('/invest/data?what_if_cash='+whatIf):'/invest/data';
    const r=await fetch(url,{{cache:'no-store'}});if(!r.ok){{setStatus('offline — last good view');return;}}
    const d=await r.json();renderInvest(d);
    setStatus((d.what_if_cash?('what-if $'+d.what_if_cash.toLocaleString()+' · '):'live · ')+(d.as_of||'').slice(11,19));
  }}catch(_){{setStatus('offline — last good view');}}
}}
const wiSlider=document.getElementById('wi-slider');
const wiVal=document.getElementById('wi-val');
let wiTimer=null;
wiSlider.addEventListener('input',()=>{{
  const v=+wiSlider.value;
  wiVal.textContent=v>0?('what-if $'+v.toLocaleString()):'live';
  wiVal.style.color=v>0?C.yellow:C.cyan;
  whatIf=v>0?v:null;
  setStatus(v>0?('what-if $'+v.toLocaleString()+' · sizing…'):'live · …');   // immediate feedback
  clearTimeout(wiTimer); wiTimer=setTimeout(refresh,200);   // debounce drags
}});
document.getElementById('wi-reset').addEventListener('click',()=>{{
  whatIf=null; wiSlider.value=0; wiVal.textContent='live'; wiVal.style.color=C.cyan; refresh();
}});
renderInvest({payload});
refresh(); setInterval(refresh,12000);
document.addEventListener('visibilitychange',()=>{{if(!document.hidden)refresh();}});
</script>
</body></html>"""
