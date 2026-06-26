# Codex Review Checklist — agentic exit-risk work (2 branches)

> Copy/paste this whole file to Codex. Two branches, both unmerged.

**Repos / branches to check out:**
- `~/Projects/gesh-project` → `feat/risk-bands` (head committed)
- `~/Projects/agentic-rh-test` → `feat/phase4-trailing-fulluniverse` (head `58cb540`)

**Context docs to read first:** `agentic-rh-test/REVIEW_HANDOFF_PHASE4.md`, then `REVIEW_HANDOFF.md`.

---

## 🔴 CRITICAL — safety contract (block on any failure)
1. **Trailing stop can only TIGHTEN risk.** In `gesh_signals.trailing_stop()` confirm
   `stop_price = max(trail_price, fixed_stop_price)` — the trail can never sit below the fixed
   vol-adaptive stop, under every tier and any peak. Prove with a case where raw trail < fixed stop.
2. **Caps never widened.** Confirm nothing in `build_exit_plan()` / `exit_bands()` / trailing can
   raise TP/SL past clamps (`BAND_TP_MAX 15`, `BAND_SL_MAX 10`) or loosen the
   $100/trade · $250/day · −$150/day · 8-position caps. EXTREME tier hits the ceiling — confirm
   15%/10% is the intended max (−10% of $100 = −$10).
3. **Trailing vs −$150 day-stop interaction.** Confirm a trailing exit sells only the winning leg
   and can't mask/bypass the account-level daily halt.
4. **Account ring-fence intact.** Confirm NO live-execution / order-placing code landed in
   gesh-project (`risk_bands.py`, `gesh_exit_advice.py` must be pure/advisory — no broker imports).
   MAIN book ••••0533 stays untradeable.

## 🟠 HIGH — correctness
5. **`peaks.json` persistence has no atomic writer** (LLM/playbook-maintained, unlike the run-lock).
   Assess race/corruption risk at the once-per-day cadence; recommend whether a small atomic Python
   writer should own it.
6. **Full-universe coverage is actually total.** Confirm every open position gets a row in
   `build_exit_plan()` — tracked → vol bands, untracked → `fallback_untracked` base bands — with no
   path that drops a holding (blank-symbol skip is intentional; verify nothing else silently drops).
7. **Trigger boundary / float safety.** `trailing_stop` arms at `peak >= entry*(1+tp/100)`. Check
   the FP boundary (we hit `100*1.12 = 112.00000000000001`) — confirm no off-by-epsilon mis-arm in
   real price ranges.
8. **Parity between the two copies.** `risk_bands.py` (canonical) and `gesh_signals.py`'s in-repo
   copy must stay behaviorally identical (`BAND_MULT`, `TRAIL_MULT`, clamps, EXTREME tier). Flag drift.

## 🟡 MEDIUM — quality
9. `tally.py` valid exit-reasons now include `trailing_stop` — confirm analytics/normalization handle
   it and legacy rows aren't reclassified.
10. Run both suites: gesh-project **193** green, agentic **40** green. Re-run `py_compile` +
    `bash -n run_autonomous.sh`. Confirm `pytest` isn't picking up `peaks.json` / runtime state.
11. Playbook (`AUTONOMOUS_PLAYBOOK.md`) + `run_autonomous.sh` step 4 wording matches the code's
    actual trailing/coverage semantics (no instruction the code can't honor).

---

## Deliverable wanted from Codex
Per-finding **severity (CRITICAL / HIGH / MEDIUM / LOW) + file:line + concrete fix**, and an
explicit **merge verdict per branch** (approve / approve-with-fixes / block).
Items 1–4 are merge-blockers.
