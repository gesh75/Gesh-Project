# Codex Review Handoff — CI/test-coverage plumbing fix (:9000)

**Author:** Georgi · **Date:** 2026-07-01 · **Repo:** `gesh-project` (main, pushed)
**Reviewer role:** Adversarially verify a CI/test-infrastructure change. Lower blast radius
than the payment work (no runtime/entitlement logic touched), but CI is the safety net for
*everything else* — a hole here silently un-protects the payment code this review already
approved. Hunt for: tests that still don't run, a trigger/coverage scope that's wrong in a new
way, or a moved file that broke a hidden assumption.

This is a follow-up to `docs/CODEX_REVIEW_2026-06-30.md` (APPROVED at `6311c2c`). **Only the
change below is new** — the 3 other commits since (`2042e67`, `c418ee8`, `b0bd767`) are
docs-only (payment/CI-doc bookkeeping), no code changed, not in scope.

---

## 0. Scope — what to review

Commit: **`ace50d5`** (single commit, range `e0fe4cc..ace50d5` for the full diff including the
docs-only commits if you want full context, but only `ace50d5` has behavior changes).

```bash
git show ace50d5 --stat
```

## 1. What changed and why

An independent test-coverage audit (agent-run, not me) found CI wasn't the "few gaps" story it
looked like — it was **plumbing hiding both real tests and real code changes from ever
running**:

1. **5 real test files were orphaned at repo root.** `pyproject.toml` has always set
   `testpaths = ["tests"]`, but `test_invest_radar.py`, `test_invest_view.py`,
   `test_risk_gate_paper_mode.py`, `test_watch_endpoint.py`, `test_watch_view.py` lived at
   root and were **never collected by CI**. Moved into `tests/` via `git mv` (history
   preserved).

2. **2 files named `test_*.py` were live-network debug scripts, not tests** — no assertions,
   no pytest fixtures, just scripts that call `yfinance`/Anthropic on import. Renamed off the
   `test_` prefix into `scripts/manual_screen_market_smoketest.py` /
   `scripts/manual_screener_smoketest.py` so pytest can never collect and execute them.

3. **CI trigger was a 6-path allowlist** (`kelly.py`/`consensus.py`/`fft_cycles.py`/
   `trading/**`/`tests/**`/`pyproject.toml`) — a change to `server.py`, `billing.py`, or any
   other app module ran **zero tests**. Switched `on.push`/`on.pull_request` from `paths:` to
   `paths-ignore:` (skip only `**/*.md`, `docs/**`, `.claude/**`, `memory-bank/**`,
   `_archive/**`, `career-ops/**`, `android*/**`) so any code change triggers CI by default.

4. **CI coverage was scoped to 4 of ~90 modules** (`--cov=kelly --cov=consensus
   --cov=fft_cycles --cov=trading/risk_gate`). Added `[tool.coverage.run]`/
   `[tool.coverage.report]` to `pyproject.toml` (`source="."`, `omit` for venv/tests/scripts/
   docs-adjacent dirs) and switched the CI step to `--cov=. --cov-report=term-missing`. No
   `--cov-fail-under` yet — deliberately establishing the real baseline (36%, 2904/7969
   statements) before gating on a number.

5. **Moving the tests immediately surfaced a real, previously-invisible bug**:
   `test_watch_endpoint.py` assumed the login wall is off by default, but `auth.py` (imported
   transitively via `server.py`) calls `load_dotenv()` at import time with no path argument, so
   `.env`'s `GESH_AUTH_ENABLED=1` (added for the paid product's login wall, in an earlier
   payment-integration round) leaked into the test process and every `/watch` route 302'd to
   `/login` instead of returning 200. Fixed with the same guard `test_billing.py` already uses
   (`monkeypatch.delenv("GESH_AUTH_ENABLED", raising=False)`), added to the autouse fixture.

6. Fixed 3 ruff findings (import order, unused import, `lambda`-assignment) in the newly
   in-scope test files — they were never linted at root either (ruff is scoped to `tests/**`).

---

## 2. 🔴 Adversarial questions

1. **Is the auth-leak fix actually complete?** I checked that none of the other 4 relocated
   test files (`test_invest_radar.py`, `test_invest_view.py`, `test_risk_gate_paper_mode.py`,
   `test_watch_view.py`) import `server`/`auth` directly or transitively via `invest_view.py`/
   `watch_view.py` — so only `test_watch_endpoint.py` needed the guard. **Verify this
   independently** — is there a subtler transitive-import path I missed (e.g. via `db.py`,
   `trading/*`, or a module that itself imports `server` deep in its call graph) that could
   leak `GESH_AUTH_ENABLED` or another `.env` var into a different relocated test?
2. **`paths-ignore` correctness.** Is `.claude/**` genuinely safe to exclude from CI triggers?
   It holds `PRPs/`, `settings.local.json`, `skills/` — none of those execute in CI today, but
   confirm nothing there could ever affect test behavior (e.g. a hook script) such that
   excluding it from triggering CI would hide a real regression.
3. **`--cov=.` scope leakage.** Does whole-repo coverage collection import/execute anything
   with side effects merely by being *imported* during coverage instrumentation (not just
   during actual test runs) — e.g. a module-level `load_dotenv()` + network call, or a
   module-level file write? Coverage.py only instruments files actually imported by the test
   run, so this should be a non-issue, but worth Codex confirming no module-level code fires
   unexpectedly when `--cov=.` is added vs. the old `--cov=kelly` narrow scope.
4. **Renamed debug scripts — fully inert?** Confirm `scripts/manual_screen_market_smoketest.py`
   and `scripts/manual_screener_smoketest.py` cannot be collected by pytest under any
   discovery mode (e.g. `pytest .` from repo root, or an IDE's auto-discovery) — I checked
   `pytest --collect-only scripts/` returns 0 collected, but Codex should verify no other
   pytest config (a stray `conftest.py`, a CI matrix step) points broader than `testpaths`.
5. **File-move correctness.** `git mv` was used for all 7 relocations — confirm `git log
   --follow` on at least one moved file still resolves its pre-move history (proves it's a
   true rename, not a delete+recreate that would look identical in a diff but lose blame).
6. **No `--cov-fail-under` — accepted gap or should it block approval?** This is deliberate
   (establish baseline before gating), not an oversight — but flag if you disagree with
   deferring it.

---

## 3. Verify

```bash
# full suite (expect 330 pass, matching pre-move total — none dropped, none newly broken)
./venv/bin/python -m pytest -q

# the 5 relocated files specifically (expect all pass, including the auth-leak regression test)
./venv/bin/python -m pytest tests/test_invest_radar.py tests/test_invest_view.py \
  tests/test_risk_gate_paper_mode.py tests/test_watch_endpoint.py tests/test_watch_view.py -q

# ruff (CI gate scope — tests/ only, per pyproject.toml)
./venv/bin/ruff check tests/

# whole-repo coverage baseline (matches the CI step)
./venv/bin/pip install pytest-cov -q
./venv/bin/python -m pytest tests/ -q --cov=. --cov-report=term-missing

# YAML sanity
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml')); print('OK')"

# confirm git history survived the moves (pick one)
git log --oneline --follow -- tests/test_watch_endpoint.py | head -5
```

Local result at authoring time: 330 passed, ruff clean, coverage baseline 36%, YAML valid.

---

## 4. Output requested

Severity (CRITICAL/HIGH/MEDIUM/LOW) per finding, file:line, concrete scenario, suggested fix.
Decision: **APPROVE / APPROVE-WITH-COMMENTS / REQUEST-CHANGES / BLOCK**. This change doesn't
touch money-moving logic, so the bar is "does CI now actually protect what it claims to," not
"is there an entitlement bug" — focus fire on **§2.1 (auth-leak completeness)** and **§2.2
(paths-ignore correctness)**, since those are the two places a gap would silently readmit the
exact "code change, zero tests" failure mode this fix exists to close.

---

## 5. Round 2 — Resolutions (commit `c37a485`)

> **STATUS: Round 1 REQUEST-CHANGES (2 MEDIUM, 1 LOW) → all 3 fixed in `c37a485`.**

| # | Finding | Fix | Verified |
|---|---------|-----|----------|
| **MEDIUM-1** | 4 real tests in `trading/tests/test_autopilot_open_window.py` never ran in CI (`testpaths=["tests"]` only); `trading/autopilot.py` showed 0% despite these tests existing | Moved into `tests/` via `git mv` (history preserved); fixed `REPO_ROOT` depth (`parents[2]`→`parents[1]`) | `trading/autopilot.py` now 27% (was 0%); `pytest .` and `pytest tests/` collect identically (334) |
| **MEDIUM-2** | `android*/**` in `paths-ignore` excluded tracked Kotlin/Gradle **source**, not just gitignored build artifacts — contradicting "any code change triggers CI" | Removed the pattern entirely; an Android-only change now runs the Python suite too (harmless no-op) until a dedicated Android workflow exists | Confirmed via `git show` on the pushed commit — only the explanatory comment mentions "android," no active ignore entry remains |
| **LOW-3** | `security/*` omitted from `[tool.coverage.run]`, hiding `api_verifier.py` (powers `GET /api/security/health`, real production code) from the "whole-repo" report | Removed the omission | `security/api_verifier.py` now shows 0% (honest gap) in the coverage report |

**Bonus finding while fixing MEDIUM-1:** moving the file into `tests/` immediately surfaced a
**real, previously-invisible test-isolation bug** — `_install_stub_modules()` installed a fake
`auth` module (missing `logout`) into `sys.modules` at collection time and never cleaned it
up. Once this file ran in the same pytest session as `test_watch_endpoint.py` (which does
`from auth import login, logout`), collection failed with `ImportError`. Confirmed the stubs
are provably unneeded past the `from trading import autopilot` import line (autopilot's
loaders are plain functions never invoked at import time, and every test in the file
monkeypatches them directly) — `_install_stub_modules()` now returns only the names it
actually inserted, and the test file deletes them from `sys.modules` immediately after the
import completes. This is the second time in this review chain that relocating an orphaned
test surfaced a real global-state leak the isolation had been hiding (the first was the
`GESH_AUTH_ENABLED` leak in round 1) — worth treating as a pattern: **orphaned tests aren't
just untested, they're unvalidated for session-safety too.**

**Process note:** the first attempt to commit these fixes silently failed to stage the real
edits (a `git add` with one stale path failed atomically, and the commit that followed had
0 content diff despite a full commit message describing the fixes). Caught by independently
verifying the pushed commit's *object content* (`git show <sha>:<path>`) rather than trusting
the local working tree or the commit message — fixed forward with a new commit (`c37a485`),
no force-push, no history rewrite.

Verified: 334 tests pass; ruff clean; YAML valid.
