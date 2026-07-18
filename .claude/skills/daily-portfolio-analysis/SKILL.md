---
name: daily-portfolio-analysis
description: >
  Runs the full GESH Financial daily portfolio analysis for Georgi's Robinhood
  account via the deterministic host chain (run_daily_analysis.sh): live
  snapshot, all engines, Claude deep analysis, paper/risk-gate audit, and the
  HTML report. Use whenever the user says "run analysis", "daily analysis",
  "check my portfolio", "what should I do today", or "run the GESH report".
model: grok-build
---

# GESH Daily Portfolio Analysis

ALL computation is scripted. Your job is: **run the chain → verify → narrate.**
Do NOT re-run engines in a REPL, hand-edit `gesh_report.html`, or hand-write SQL
— those manual paths took 30–60 min and produced degraded, divergent output.

**Project path:** `/Users/georgigaydarov/Projects/gesh-project`
**Orchestrator:** `run_daily_analysis.sh` (~3–6 min, logs to `/tmp/gesh_fullchain.log`)
**Also scheduled:** launchd `com.gesh.daily-analysis` runs it 10:30 ET weekdays —
check the log first; today's run may already exist.

## Step 1 — Check / run the chain

```bash
tail -40 /tmp/gesh_fullchain.log   # CHAIN DONE with today's date + fail=0 → skip to Step 2
bash /Users/georgigaydarov/Projects/gesh-project/run_daily_analysis.sh
echo "exit=$?"; tail -25 /tmp/gesh_fullchain.log
```

The chain is host-only (sandbox yfinance gets 401 crumb storms — see project
CLAUDE.md) and self-asserting: snapshot freshness ≤30 min, report ≥60KB, DB row
written. `fail=1` / `FATAL` → report the failing step + log excerpt and STOP; do
not improvise an analysis from stale DB data.

What each step produces:

| Step | Script | Output |
|---|---|---|
| 1/5 | **fetch_snapshot_mcp.sh** → build_snapshot_from_mcp.py | `live_portfolio_snapshot.json` (live Robinhood **via MCP** — canonical since 2026-06-25). Legacy `fetch_live_snapshot.py` (robin_stocks pickle) is the fallback only. |
| 2/5 | run_engines2.py | `engine_results.json` — regime, consensus, Kelly, MC, vols, alpha, tax, explainability, yield, crypto |
| 3/5 | run_ai2.py | `ai_result` + radar into engine_results.json, `analysis_runs` + `equity_snapshots` DB rows |
| 4/5 | run_paper_gate2.py | `action_plan`, `gate_results`, `paper_session`, `trade_stats` |
| 5/5 | gen_report2.py | `gesh_report.html` (~75KB) + copy to gesh-project-trading/ |

## Step 2 — Read results

```python
import json
eng = json.load(open('/Users/georgigaydarov/Projects/gesh-project/engine_results.json'))
```

Sanity: `ai_result.portfolio_score` non-zero; `vol_models` not all-identical;
snapshot age in the log ≤30 min.

## Step 3 — Narrate (your only creative work)

Concise markdown briefing, tables over prose:

0. **Signal-vs-gate banner (FIRST LINE, mandatory).** Before anything else,
   cross-check `action_plan` against `gate_results`. If ≥2 BUY/ADD rows are
   rejected by the gate, open the briefing with ONE plain sentence naming it —
   e.g. "⚠ Signals lean ADD on 5 names, but the risk gate blocked all 5
   (size > 2% position cap) — net actionable trades today: 0." If nothing is
   blocked, say so in one line ("All N planned actions cleared the gate."). Never
   present the action plan without this reconciliation up top.
1. **Headline** — total, day change, portfolio_score/risk_score,
   `portfolio_verdict`, MC VaR 95% in dollars.
2. **Action plan** — from `action_plan` + `gate_results`: Priority | Symbol |
   Action | Shares | $ | Rationale | Gate. Use the chain's share counts verbatim.
   Every row's **Gate** column MUST read APPROVED or REJECTED with the gate's own
   reason (e.g. "REJECTED — size 24% > 2% cap"). If an `action_plan` row has a
   blank reason, fill **Rationale** from its consensus verdict + Kelly signal —
   never render an empty cell.
3. **Signal conflicts** — reconcile consensus vs Kelly vs AI (Kelly wins sizing;
   momentum wins short-term; RSI≥70 blocks ADD; cash<$500 blocks all ADD).
4. **Watch items** — regime flips, GARCH≫EWMA stress, alpha grade F, tax-harvest
   candidates, crypto correlation HIGH, radar ideas (advisory only).
5. **Paper/gate status** — approved/rejected counts, closes booked, 7-day stats.

Offer to open `gesh_report.html`. Always include: "NOT FINANCIAL ADVICE — FOR
INFORMATIONAL PURPOSES ONLY".

## Known watchouts

- Cash = `equity_summary.deployable` (settled, leverage-capped). NEVER
  `account["cash"]` or leveraged `buying_power`.
- ECG node ABSTAIN (import error) and QQQ/EEM analyst ABSTAIN (ETFs) — expected.
- Snapshot is **MCP-sourced** (canonical since 2026-06-25): step 1/5 runs
  `fetch_snapshot_mcp.sh` (a `claude -p` that fetches RH acct 557370533 via the
  `robinhood-trading` MCP → `/tmp/gesh_mcp_raw.json`) then `build_snapshot_from_mcp.py`
  (deterministic money-math). The old `fetch_live_snapshot.py` (robin_stocks pickle login)
  is **fallback only** — it dies on every RH password change. If step 1 fails, check
  `/tmp/gesh_mcp_fetch.log` first; the MCP is OAuth, so a recent password change means the
  token expired → re-auth via `/mcp` → robinhood-trading. Both paths failing → chain aborts
  at 1/5 by design; report, stop.
- Kelly keys: `optimal_pct`, `delta_usd`.
