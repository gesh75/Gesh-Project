---
name: gesh-daily-report
description: >
  Use when the GESH daily HTML report looks stuck on yesterday, a file:// tab
  still shows an old date, the Action plan shows ADD on almost every holding,
  the user missed an APPROVED cell, asks if leftover-cash lots should be
  bought, or wants gesh_report.html regenerated. Also use for Cmd-Shift-R vs
  re-running the chain, ET masthead vs UTC Generated, Kelly HOLD-band vs
  delta_usd drift, Gate traffic-light colors, and the Action|Symbol|Shares|Now|Kelly|Gate table.
model: grok-4.6
---

# GESH daily report (read it, don't fight it)

`gesh_report.html` is a **generated** artifact (`run_daily_analysis.sh` step 5/5
via `gen_report2.py`). Never hand-edit it. Never treat a screenshot as proof
the chain failed. **REQUIRED BACKGROUND:** daily-portfolio-analysis (run) and
kelly-sizer (HOLD band).

## 1. Screenshot still on yesterday?

Check **disk**, then the tab:

```bash
stat -f '%Sm %z' -t '%Y-%m-%d %H:%M %Z' gesh_report.html
python3 -c "import pathlib,re; t=pathlib.Path('gesh_report.html').read_text();
print(re.search(r'<title>(.*?)</title>',t).group(1));
print('iso', re.search(r'data-generated-iso=\"([^\"]+)\"',t).group(1))"
tail -5 /tmp/gesh_fullchain.log .logs/gesh_fullchain.log 2>/dev/null | tail -5
```

| You see | Meaning | Do |
|---|---|---|
| Masthead is **today ET** (`Weekday, DD Month YYYY · … ET`) but the Chrome tab is yesterday | leftover `file://` cache | **Cmd-Shift-R**. Do not re-run the chain. |
| Title `GESH · YYYY-MM-DD ET` matches disk; banner says file is >20h old | chain has not published today | check log; then run `run_daily_analysis.sh` |
| UTC `Generated` line ≠ calendar day in Sofia/UTC | expected | masthead is America/New_York, not that stamp |
| Weekday masthead is **today ET**, log is `CHAIN DONE fail=0`, Action is HOLD-all / Gated executable: 0 | Kelly band HOLD (`add_list`/`trim_list` empty). Rule 8 also blocks ADD if deployable < $500 | Do not re-run. Narrate HOLD. |

`GET /report` sends `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`.
`file://` does **not**. Prefer `http://localhost:9000/report` over a leftover file tab.
The 10:30 chain is **weekdays only**. Saturday/Sunday have no scheduled run. Do not fire a weekend snapshot unless Georgi asks. A weekday HOLD-all after `CHAIN DONE fail=0` is the band, not a stuck chain.

## 2. Action says ADD on every name?

That is leftover **`delta_usd`**, not Kelly ADD.

Kelly `action` is the 0.6× / 1.3× band (`HOLD` when current is inside).
`delta_usd` still prints the dollar gap to equal-weight. `report_share_plan.py`
paints Action = Kelly band; leftover size is labeled **drift**, not an order.

**Place only Action = ADD or TRIM.** Ignore HOLD + drift, including 4-decimal
fractional lots. Those lots cost 20–30 min and the cash floor.

Rule 8: `equity_summary.deployable` < **$500** blocks **all** ADD. Size from
deployable, never `buying_power`. `cash_recommended_pct` ≈ 0 is not a cash
balance.

Closing leftover drift is **not** a blow-up (direction is toward equal-weight)
but it **is** a miss: dry powder gone, and the one real ADD can overshoot.

## 3. Colors (do not restyle to caption gray)

| Cell | Color |
|---|---|
| Action ADD | `#00e87a` |
| Action HOLD | `#f0b429` amber |
| Action TRIM | `#ff3c5a` |
| Gate APPROVED | `#00e87a` **13px / weight 700** |
| Gate HOLD / not gated | amber |
| Gate REJECTED / NOT EVALUATED | red, weight 700 |

APPROVED in 11px `#888` is a defect. Georgi missed a real APPROVED that way.

Paper TRIM/close in the paper book is **not** a Robinhood fill.

## 4. Columns (do not invent a seventh)

Operator table in `gesh_report.html` is **Action | Symbol | Shares | Now | Kelly | Gate**.
Header counts: TRIM N names / X sh · ADD N names / Y sh · HOLD N names.
Share math (`report_share_plan.share_count`): ADD = floor(|Δ$|/price), TRIM = ceil
clamped to quantity; |Δ$|/price < 1 share → HOLD (or a 4-decimal drift lot).
Missing price/delta → Shares **N/A**, never a fake 0. `optimal_pct` of `0.0` is
AVOID — do not treat it as missing via `or`.

The old “Prioritised Action Plan” is **AI notes** (narrative). Share counts
are only in the Action plan. Gate APPROVED only when that share count actually
cleared the risk gate. A HOLD-all day prints “Gated executable: 0” — that is
Kelly band HOLD, not a failed gate.

## 5. “Make the report again with new data”

Full chain from a live MCP snapshot: `bash ./run_daily_analysis.sh`.
Do not run `gen_report2.py` on a stale `engine_results.json` after cash moved
and call it fresh. After `CHAIN DONE` + `fail=0`, tell them Cmd-Shift-R.

## 6. Red flags — STOP

- Screenshot date ≠ disk mtime / ET masthead → leftover `file://`. **Cmd-Shift-R**. Do not re-run the chain.
- Action = HOLD plus leftover $ → **drift**, not an order. Quote `add_list` / `trim_list`.
- “I’ll buy the leftover 0.00xx lots to close the gap” → burns Rule 8 dry powder.
- APPROVED looks missing → look for **13px / 700 green**, then `gate_results`. Caption-gray is a defect.
- Hand-editing `gesh_report.html` → generated, gitignored. Next chain overwrites it.
- Saturday/Sunday “make the report with new data” → no weekday 10:30. Confirm before running.
- Weekday HOLD-all after `CHAIN DONE fail=0` → Kelly band HOLD, not stuck. Do not re-run.
- Paper TRIM/close → **not** a Robinhood fill.

## 7. Excuses

| Excuse | Reality |
|---|---|
| "Report still says the 25th so the chain is stuck" | Compare disk mtime + ET masthead to the tab. Cached `file://` is the usual cause. |
| "I'll hand-fix the HTML date" | gitignored generated file. Next chain overwrites it. |
| "Every row is a small ADD, I should place them" | HOLD-band drift. Execute `add_list` / `trim_list` only. |
| "I didn't see APPROVED so nothing was approved" | Was 11px gray. Look for bold green, or `gate_results`. |
| "Paper closed NVDA so I sold it" | Paper book ≠ RH. |
| "Gated executable: 0 means the gate crashed" | HOLD-all day. Check TRIM/ADD/HOLD counts. |
| "HOLD-all on a weekday so the chain is stuck" | Check the log. Empty `add_list`/`trim_list` + `fail=0` is the band. |
| "AI notes say trim so I'll sell" | Share counts are in Action plan only. |
