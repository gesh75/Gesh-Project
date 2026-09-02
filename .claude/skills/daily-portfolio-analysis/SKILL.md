---
name: daily-portfolio-analysis
description: >
  Runs the full GESH Financial daily portfolio analysis for Georgi's Robinhood
  account via the deterministic sandboxed chain (run_daily_analysis.sh): live
  snapshot, all engines, Claude deep analysis, paper/risk-gate audit, and the
  HTML report. Use whenever the user says "run analysis", "daily analysis",
  "check my portfolio", "what should I do today", or "run the GESH report".
model: grok-4.6
---

# GESH Daily Portfolio Analysis

ALL computation is scripted. Your job is: **run the chain → verify → narrate.**
Do NOT re-run engines in a REPL, hand-edit `gesh_report.html`, or hand-write SQL
— those manual paths took 30–60 min and produced degraded, divergent output.

## Step 0 — Execution boundary

Run the chain only in the agent's sandbox and only from the checkout that was
reviewed for the current task. Never switch to a host checkout, invoke host
Python, or read host `.env`, session, token, or portfolio database files. This
also applies to scheduled invocations. If sandbox data access is unavailable,
report the limitation and stop rather than weakening this boundary.

**Project path:** the current sandbox checkout (`git rev-parse --show-toplevel`)
**Orchestrator:** `run_daily_analysis.sh` (~3–6 min, logs to `/tmp/gesh_fullchain.log`)
**Also scheduled:** launchd `com.gesh.daily-analysis` runs it 10:30 ET weekdays;
`com.gesh.daily-report` publishes `gesh_report.html` at 10:45 ET (retries the
chain if today's file is missing). Check the log first; today's run may already exist.

## Step 1 — Check / run the chain

```bash
tail -40 /tmp/gesh_fullchain.log   # CHAIN DONE with today's date + fail=0 → skip to Step 2
PROJECT_ROOT="$(git rev-parse --show-toplevel)"
cd "$PROJECT_ROOT"
bash ./run_daily_analysis.sh
echo "exit=$?"; tail -25 /tmp/gesh_fullchain.log
```

The sandboxed chain is self-asserting: snapshot freshness ≤30 min, report
≥60KB, DB row written. `fail=1` / `FATAL` → report the failing step + log
excerpt and STOP; do not improvise an analysis from stale DB data.

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
eng = json.load(open('engine_results.json'))
```

Sanity: `ai_result.portfolio_score` non-zero; `vol_models` not all-identical;
snapshot age in the log ≤30 min. Also read `ai_provider` / `ai_failover_count`:
a failover run (`ai_failover_count > 0`) is **not** a clean Claude run — say
so in the briefing (the report already renders a `MODEL FAILOVER` banner).
A `portfolio_score` of 0 with empty `holdings_analysis` after failover is a
coerced-blank payload, not a real 0-score book — treat it as a failed Step 3.

## Step 3 — Narrate (your only creative work)

Concise markdown briefing, tables over prose:

0. **Signal-vs-gate banner (FIRST LINE, mandatory).** Before anything else,
   cross-check every executable `action_plan` row (BUY/TRIM with shares > 0)
   against `gate_results` by symbol and action. If `paper_session.error` is set,
   or any executable row has no matching result, say **GATE FAILED — NOT
   EVALUATED** and name/count the affected trades; never describe them as
   cleared. If ≥2 BUY/ADD rows are
   rejected by the gate, open the briefing with ONE plain sentence naming it —
   e.g. "⚠ Signals lean ADD on 5 names, but the risk gate blocked all 5
   (size > 2% position cap) — net actionable trades today: 0." Say "All N
   executable actions cleared the gate" only when all N executable rows have a
   matching APPROVED result and there is no gate error. Never present the action
   plan without this reconciliation up top.
1. **Headline** — total, day change, portfolio_score/risk_score,
   `portfolio_verdict`, MC VaR 95% in dollars.
2. **Action plan** — quote the HTML table (and `report_share_plan.share_plan_rows`),
   not AI prose. Columns: **Action | Symbol | Shares | Now | Kelly | Gate**.
   Open with the header counts: TRIM N names / X sh · ADD N names / Y sh · HOLD N names.
   Action follows Kelly’s 0.6× / 1.3× band. HOLD + “drift” is leftover `delta_usd`,
   not an order. **REQUIRED BACKGROUND:** gesh-daily-report.
   An executable row's **Gate** column MUST read APPROVED or REJECTED with the
   gate's own reason (e.g. "REJECTED — size 24% > 2% cap") only when it has a
   matching result **and** matching share count. Missing results MUST read
   "NOT EVALUATED — gate failed" (or the `paper_session.error`). HOLD rows
   MUST read "not gated — pre-gate HOLD" / "not gated — Kelly HOLD band"; they
   are neither approved nor rejected. "Gated executable: 0" on a HOLD-all day
   is band HOLD, not a crash. Never invent share counts; N/A if price/delta missing.
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
- Snapshot is **MCP-sourced** (canonical since 2026-06-25): step 1/5 prefers the
  LLM-free `fetch_snapshot_direct.py`, falling back to `fetch_snapshot_mcp.sh` (a
  `claude -p` courier). Both fetch the RH account named by `GESH_RH_ACCOUNT`
  (env → `.env`; never hardcoded) via the
  `robinhood-trading` MCP → `/tmp/gesh_mcp_raw.json`, then `build_snapshot_from_mcp.py`
  (deterministic money-math). The old `fetch_live_snapshot.py` (robin_stocks pickle login)
  is **fallback only** — it dies on every RH password change. If step 1 fails, check
  `/tmp/gesh_mcp_fetch.log` first; the MCP is OAuth, so a recent password change means the
  token expired → re-auth via `/mcp` → robinhood-trading. Both paths failing → chain aborts
  at 1/5 by design; report, stop.
- Kelly keys: `optimal_pct`, `delta_usd`. **Action follows Kelly's 0.6×/1.3×
  band**, not the sign of `delta_usd`. HOLD rows may still show leftover
  dollars as **drift** — not orders. Quote `add_list` / `trim_list`.
  **REQUIRED BACKGROUND:** kelly-sizer, gesh-daily-report.
- Tax harvest: empty `tax_report.candidates` means no loss cleared `$500`, not
  "the book is profitable". Read `purchase_wash_warnings` and `lot_coverage`
  (`unavailable` is unknown, not clear). Operator contract: `docs/RH_MCP.md`.
- Broker preflight (step 4/5): `[gate] broker preflight: N/M conclusively
  checked`. An all-`UNAUTHORIZED` day is expected on the current OAuth scope —
  not an all-clear, and not a veto (`GESH_REVIEW_BLOCKING` defaults off).
- Step 3/5 can fail over to Grok/Gemini/xAI when Anthropic is down
  (`GESH_LLM_FAILOVER`, default `xai,grok,gemini`). Look for
  `WARNING: ANTHROPIC UNAVAILABLE` in the chain log and
  `ai_provider` / `ai_failover_count` in `engine_results.json`.
  Engine numbers are unaffected; the narrative is not
  grammar-constrained. Empty `GESH_LLM_FAILOVER` disables it.
  See `docs/LLM_OPERATIONS.md` §6. This is **separate** from
  snapshot step 1/5's `claude -p` courier (`direct MCP fetch failed`).
- **Stale screenshot ≠ failed chain.** Compare `gesh_report.html` mtime and
  the ET masthead (`Weekday, DD Month YYYY · … ET`) to the tab. A leftover
  `file://` Chrome tab caches yesterday; `/report` is `Cache-Control: no-store`.
  Cmd-Shift-R before re-running the chain.
- **Gate traffic-light:** APPROVED = bold green 13px/700; HOLD = amber; TRIM /
  REJECTED = red. Caption-gray APPROVED is a defect (missed on 2026-08-27).
- After leftover cash is spent, regenerate with `run_daily_analysis.sh` (fresh
  MCP snapshot). Never hand-edit HTML. Paper TRIM/close ≠ a Robinhood fill.
- The 10:30 launchd job is **weekdays only**. Do not fire a Saturday/Sunday
  snapshot unless Georgi asks. A weekday HOLD-all after `CHAIN DONE fail=0`
  is Kelly band HOLD, not a stuck chain — do not re-run.
  **REQUIRED BACKGROUND:** gesh-daily-report.
