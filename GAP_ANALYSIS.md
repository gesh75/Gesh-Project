# GAP_ANALYSIS.md — verifiable rescan (2026-09-05)

Scope: `gesh75/Gesh-Project` at `fdc1d33` (`main`) plus this PR.
Method: every open item below was checked against **current** source (or a
command that would falsify it). Prior gap docs
(`GAPS_AND_IMPROVEMENTS_2026-08-21.md` and earlier) are **leads**, not facts —
several of their P0/P1 rows are already closed.

Out of scope (skipped on purpose): large rewrites, dependency upgrades, new
product features, production/ops actions, live AI/brokerage calls, gesh-core
body changes (rule #46), GitHub Pro / ruleset application.

---

## Closed since the 2026-08-21 audit (do not re-open)

| Prior ID | Claim | Current evidence |
|---|---|---|
| F-01 / F-02 | CVaR 0.0% / GARCH 0.0% | `gen_report2.py` uses `cvar_95_pct()` / `_optional_pct()` / `format_optional_percent`; guards in `tests/test_gen_report2_tax.py` |
| F-05 | Missing MCP quote prices a holding at `$0` | `build_snapshot_from_mcp.py:123-126` uses `_positive_price` and skips into `unpriced[]` |
| F-11 | Failover numerics coerced to `0` | `llm_failover.py:332-337` returns `None` for omitted integer/number |
| F1 (billing) | Account-hijack on provision | `billing.py` refuses foreign `billing_sub_id` rebind; `tests/test_billing.py` |
| F2 | `/track-record` owner-only but linked public | Route is in `auth_users.PUBLIC_PREFIXES`; **not** in `server.py:666` `_OWNER_ONLY_PATHS` |
| P0-CI-pytest-on-PRs | Required jobs skip on PRs | `.github/workflows/tests.yml` runs `test` + `customer-isolation` on same-repo PRs |
| Tracked secrets | Live keys in git | `git ls-files` has no `.env` / pickle / `.rh_mcp_oauth.json`; `.env.example` is placeholders |

---

## P0 — correctness / isolation that still fabricates or leaks

Each item has a file path and a command that would falsify it.

### P0-1. Daily report hides Monte Carlo `calibration: degraded` (F-09)

- **Where:** `gen_report2.py` (no `calibration` reader before this PR).
- **Evidence:** `rg -n calibration gen_report2.py` was empty. Producer
  `monte_carlo.py:315-316` always emits `calibration` +
  `calibration_degraded_symbols`. `tests/test_monte_carlo_calibration.py`
  proves a total fetch failure still returns a complete VaR table from
  `FALLBACK_DAILY_VOL`. The report then rendered that table with
  `var_95_status` → `✓ OK` and never mentioned degradation.
- **Fix in this PR:** `_calibration_banner()` + VaR card badge `degraded`
  (not `✓ OK`) when calibration is not `ok`. Do not loosen the engine fallback.

### P0-2. Stale paper/gate rows still render as “today” (F-07)

- **Where:** `gen_report2.py:508` (`"{n} paper record(s) today"`);
  `run_engines2.py:80-89` MERGE-loads yesterday’s `engine_results.json`;
  `run_paper_gate2.py:353-360` degraded skip returns 0 **without writing**.
- **Evidence:** `rg paper_gate_ran_at` → **zero** hits outside the 2026-08-21
  gaps doc. A skip day therefore keeps yesterday’s `action_plan` /
  `gate_results` / `paper_session` via MERGE, and `_gate_audit_section`
  labels them “today”.
- **Not fixed here:** needs a `paper_gate_ran_at` stamp + pop-on-skip +
  timestamp check. Larger than this scan’s 3-fix budget.

---

## P1 — security / isolation / DX

| ID | Area | Evidence | Small fix? |
|---|---|---|---|
| P1-XSS | `templates/dashboard.html:860` `daEsc` omitted `'` | HTML text/`value=` sinks now get `&#39;`. **Not a full onclick close:** the HTML parser decodes `&#39;` before event-handler JS runs, so `');alert(1)//` still breaks `homeAction('${daEsc(…)}')` (e.g. `:2145`). `_SYMBOL_RE` (`server.py:293`) remains the load-bearing fence | Partial HTML-text harden **in this PR**; JS-in-attribute still open |
| P1-DX-env | `tests/conftest.py` did not `delenv("APP_BASE_URL")` | `auth.py` / `ai_analyzer.py` `load_dotenv()` at import; `_trusted_origins` (`server.py:1141`) then mismatches `http://testserver`. CLAUDE.md 2026-07-30 (~64 host-local fails). CI has no `.env` | **Fixed here** |
| P1-RSI0 | `run_ai2.py:94-97,125` `rd.get("rsi") or 0` | `None` RSI becomes `RSI=0.0` in the Claude prompt (maximally oversold), and Rule 5 (`RSI>=70`) silently never fires | Yes (n/a formatter) — **not in this PR** |
| P1-trades | `server.py:7510-7532,7614-7621` GET `/api/trades`, `/stats`, `/api/paper/summary` | No owner role check. In `tests/test_customer_leak_sweep.py:246-248` as customer-reachable. Dollar P&L is in `_MONEY_KEYS`; `entry_price` / `stop_loss` / `target_price` are not | Yes (owner gate) — **not in this PR** (touches isolation contract; needs its own test pass) |
| P1-cap | `gen_report2.py:555` hardcodes “2% position cap” | Gate reason substring `"cap"` appends that copy; live daily-chain cap is `MAX_POSITION_PCT=0.18` (CLAUDE.md) | Yes (parse the reason) |
| P1-hook | `scripts/hooks/pre-push` | Runs `ruff check tests/` + 3 isolation files; **no** `ruff check --isolated --select E9,F63,F7,F82 .` (the PR #133 class) | Yes |
| P1-dotenv | `server.py` has **zero** `load_dotenv()` | `.env` arrives only because `ai_analyzer.py:13` imports after `auth_users` (`server.py:485` vs `:496`). `auth_users.py:47-48` reads `GESH_AUTH_DB` / `GESH_AUTH_TTL` at module scope first. Wall-off → `_session_email` / `_is_owner_request` treat every anonymous caller as owner (`server.py:1495-1496`, `:2987-2988`). **By design for solo mode**; hazard is HTTPS deploy with wall still off | Partial (`load_dotenv()` first). Do **not** flip the wall on from this scan |

---

## P2 — CI, docs, dead code

| ID | Area | Evidence |
|---|---|---|
| P2-quota-dead | `server.py:4453` `_check_customer_quota` | Definition only; production uses `_reserve_customer_quota` (`:4482`, call at `:4724`). Tests still monkeypatch the dead helper (`tests/test_spend_gate_hardening.py`, `tests/test_mybook_radar_cash.py`) |
| P2-pickle | `.gitignore` | Has `.rh_session/` / `*.session` / `.rh_mcp_oauth.json`; **no** `*.pickle` / `*.pkl` |
| P2-attorney | `docs/GESH_ATTORNEY_ENGAGEMENT_BRIEF.md` | Tracked. `.gitignore:96-101` documents this on purpose (needs `git rm --cached` + history decision) |
| P2-docs-drift | Root vs `docs/` + three `GAPS_AND_IMPROVEMENTS_*.md` | 2026-08-21 file still lists F-01/F-05/F-11/F1/F2 as if open. `HANDOFF_GROK.md:98` still cites gesh-core `4931239`; lock is `56f14d4` (`requirements.lock:35`) |
| P2-pip-audit | `.github/workflows/tests.yml:196-198` | Soft-fail (`exit 0` after `::warning::`) — intentional until triage |
| P2-ruleset | `docs/BRANCH_PROTECTION.md` | **DRAFT / deferred** (no GitHub Pro). Live ruleset `protect-main` is deletion + non-fast-forward only — `gh api repos/gesh75/Gesh-Project/rulesets/17091782` |
| P2-owner-xss | `templates/owner_console.html` |  XSS wiring test scans only `dashboard.html` (`tests/test_dashboard_xss_wiring.py:30`) |
| P2-server-size | `server.py` | 7,996 lines. Health routes already extracted (`routers.health`); further splits are a feature, not a scan fix |
| P2-android | `android/` | Tracked Kotlin/Gradle tree; `android 2` / `android 3` gitignored. No dedicated CI job (Python suite no-ops on Android-only diffs) |
| P2-ruff-include | `pyproject.toml:16` | `include = ["tests/**/*.py"]` — `ruff check .` without `--isolated` scans nothing outside tests. CI already uses `--isolated` |

---

## Fixes in this PR (3, all small)

1. **`daEsc` apostrophe escape (HTML text only)** — `templates/dashboard.html` + XSS / customer-view tests. Does **not** close JS-in-`onclick` by itself.
2. **Autouse `delenv(APP_BASE_URL, GESH_ALLOWED_ORIGINS)`** — `tests/conftest.py` + isolation test.
3. **Monte Carlo calibration banner** — `gen_report2.py` `_calibration_banner` + report tests.

No drive-by refactors. No risk-gate widening. No dependency bumps.

---

## What this scan proved vs skipped

**Proved:** CI workflow on this repo already runs pytest + customer-isolation on
same-repo PRs; coverage floor 55 matches `pyproject.toml`; gesh-core pin is
`56f14d4` in lock + `tests/test_dependency_sanity.py`; several 2026-08-21
“fabricated number” items are closed in code; `/track-record` is public;
billing foreign-sub rebind is fenced; no live secrets in git.

**Skipped:** applying GitHub Pro required checks; deleting `_check_customer_quota`
(tests still call it); owner-gating trade GETs; `run_ai2.py` `or 0` RSI;
`paper_gate_ran_at` + MERGE pop; installing/running the full pytest suite in
this VM (`python3-venv` missing, `gesh_core` not importable without a working
venv install — `GESH_CORE_PAT` is present but the environment bootstrap
`ensurepip` failed). Focused tests below use `--noconftest` where they do not
need `db`.

---

## Next recommended agent job

Stamp `paper_gate_ran_at`, pop stale `action_plan`/`gate_results`/`paper_session`
on the `run_paper_gate2` skip path, and refuse to render “today” unless the
stamp matches `run_manifest.run_id` (closes P0-2).
