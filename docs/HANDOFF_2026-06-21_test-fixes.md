# HANDOFF — test-suite fixes (2026-06-21)

**From:** Coordinator session (reviewing the consensus-convergence + breakeven work)
**To:** the agent running the consensus-convergence / hardening track
**Repo:** `gesh-project` (product, :9000) — tests only, no production code touched
**Branch:** applied on `main` working tree (uncommitted) — see "Commit" below

---

## TL;DR
Your work is clean. The product suite was **167 passed / 5 failed**; all 5 failures were
**pre-existing stale/un-isolated tests**, none caused by the convergence (Phase 2–7) or the
equity-breakeven work — both of which are fully green (`test_consensus_phase3`,
`test_consensus_phase7`, `test_equity_breakeven` 11/11). I fixed the 5 stale tests. **Test files
only — zero production changes.** Expected result now: **172 / 172 green.**

## What I changed and why

| File | Change | Root cause it fixes |
|---|---|---|
| `tests/test_ai_analyzer_caching.py` | `_ToolBlock.__init__` now sets `self.type = "tool_use"` | The 06-18 hardening made `ai_analyzer._extract_tool_input()` iterate `message.content` for `block.type == "tool_use"` (instead of blind `content[0]`). The test double never had a `.type`, so the extractor couldn't find it → `ValueError`. Fixed 2 tests. |
| `tests/test_price_source_fallback.py` | added `autouse` fixture clearing `price_source._YF_CACHE` before/after each test | The Phase 2.5 in-process TTL cache (`_YF_CACHE`, period-based frames) leaked a 60-row NVDA frame from one test into `test_yf_download_prefers_yahoo_when_healthy`, which mocks a 10-row frame → shape mismatch. This is **rule #42** ("engine modules carry TTL caches; tests must clear them"). Fixed 1 test. |
| `tests/test_risk_gate.py` | `_base_config()` now pins `paper_mode=False` | After the 06-10 paper-mode port (rules #32/#36/#40, commit 27721a0), CASH_FLOOR uses `cfg.paper_cash_usd` and DAILY_LOSS reads the paper book's realized P&L. `test_gate_cash_floor` / `test_gate_daily_loss` document **live-account** behavior ("$1,000 cash → −$1,000", "down 3% trips 1%") but didn't pin the mode, so the paper guards short-circuited them. Pinning live mode restores their intent. Paper-mode gating is already covered by `test_risk_gate_paper_mode.py`. Fixed 2 tests. |

## Verification
- `test_ai_analyzer_caching.py` → 4 passed; `test_price_source_fallback.py` → 17 passed (run in sandbox).
- `test_risk_gate.py` needs `gesh_core` (host-only) — not run here, but the fix restores the
  pre-port LIVE path (`_post_trade_cash_estimate` snapshot math), which is exactly what those tests
  asserted before the paper-mode port. Please confirm on host:
  ```bash
  cd ~/Projects/gesh-project && source venv/bin/activate && python -m pytest tests/ -q
  ```

## Notes / things to watch
- These are **test-only** fixes. If you intended any of those 5 tests to assert NEW behavior
  (e.g. you want `test_risk_gate.py` to cover paper mode), tell me and I'll re-point them instead.
- **Commit per rule #30** — stage explicit paths, never `git add -A` (concurrent sessions):
  ```bash
  git add tests/test_ai_analyzer_caching.py tests/test_price_source_fallback.py tests/test_risk_gate.py docs/HANDOFF_2026-06-21_test-fixes.md
  git commit -- tests/test_ai_analyzer_caching.py tests/test_price_source_fallback.py tests/test_risk_gate.py docs/HANDOFF_2026-06-21_test-fixes.md
  ```
- Related deeper analysis (stop placement + autonomy-gate critique, with cold-review corrections)
  lives in the engine repo: `gesh-project-trading/docs/STOP_PLACEMENT_AND_GATE_ANALYSIS_2026-06-21.md`.
  Your equity-breakeven ratchet + legacy-stop remediation already closed its first two P1 items —
  nice. The 🔒-locked gate-metric item there must stay locked until n≥30 with HOOD-independent winners.
