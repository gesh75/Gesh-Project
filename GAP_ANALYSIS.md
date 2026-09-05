# Gap analysis — 2026-09-05

Verified scan of `gesh-project` at `fdc1d33` (`main` + this PR).
Every open P0 below cites a file and the command or snippet that would falsify it.
Prior living audits (`GAPS_AND_IMPROVEMENTS_2026-08-21.md` and earlier) are
**leads**, not facts — several of their P0s are already closed. This file is
the current ranked list for the product repo only (port 9000).

**Method:** grep + file reads + cold-start / docs / validation review agents +
CI workflow inspection. Full pytest was **not** run here: the VM `venv` is
empty (`ensurepip` missing) and `GESH_CORE_PAT` could not clone private
`gesh-core`, so `import server` dies. File-only tests (`--noconftest`) and
ruff syntax gates were used for the three fixes in this PR.

**Out of scope (skipped):** gesh-core / trading-repo changes, dependency
upgrades, product features, live AI/brokerage, production LaunchAgents,
rewriting `server.py`.

---

## P0 — correctness / isolation / CI that is red or structurally unsafe

| ID | Area | Finding | Evidence | Status |
|---|---|---|---|---|
| **P0-1** | Security | `daEsc` omitted `'`. This PR adds `&#39;`, which is correct for **HTML text** (the browser displays `'`). It does **not** close `onclick="fn('${daEsc(x)}')"` breakout: the HTML parser decodes `&#39;` back to `'` before the handler is compiled. Ticker sinks are still fenced by `_SYMBOL_RE` (`server.py:293`). A real handler fix is a separate `daJs` (`\\u0027`) or `data-*` attributes — not done (would touch every onclick concat). | `templates/dashboard.html:860`. Sinks: `:2145` `homeAction('…')` (enum `act.target`), `:2160` / `:2279` / `:2496` (tickers). Prove the residual: `onclick="fn('&#39;);alert(1)//')"` compiles as `fn('');alert(1)//`. | **Partial.** HTML text hardened + source lock. Onclick JS escape left open. |
| **P0-2** | CI / DX | Host `.env` leaks into the suite. `ai_analyzer.py:13` `load_dotenv()` at import. `APP_BASE_URL=https://…` then fails CSRF against TestClient `Origin: http://testserver`. `GESH_AUTH_ENABLED=1` 302s unauthenticated TestClient calls. CI has no `.env`, so this is **red-on-dev, green-on-CI**. | Pre-PR: `rg APP_BASE_URL tests/conftest.py` → no matches. CLAUDE.md (2026-07-30) recorded ~64 failures; `tests/test_watch_endpoint.py:25-34` re-implements a local `delenv`. | **Fixed this PR** — autouse `delenv` of `APP_BASE_URL` / `GESH_AUTH_ENABLED` / `GESH_ALLOWED_ORIGINS` plus a no-op `dotenv.load_dotenv` so a late import cannot re-inject `.env`. |
| **P0-3** | Isolation | `GET /api/trades` and `GET /api/trades/stats` serve the **owner paper/live ledger** with no `Request`, no `_require_owner_view`, no API key. Login wall (when on) only requires *a* session — any customer qualifies. The only caller is `templates/owner_console.html:1072-1074`. Money-masking scales `realized_pnl` (`server.py:735-736`) but still returns owner symbols, outcomes, and scanner sources — private operational data (AGENTS.md §3). | `server.py:7510-7532` — handlers take only query params. Contrast `_require_owner_view` at `server.py:2170-2184` used by `/progress` etc. `tests/test_customer_leak_sweep.py:131-133` documents them as "customer-reachable". | **Open.** Recommended next job: add `_require_owner_view(request)` to `/api/trades`, `/api/trades/stats`, and `/api/paper/summary`, then update the leak-sweep expectations from masked-200 to 403. Not done here — that test rewrite needs a working `gesh-core` install. |

---

## P1 — security, CI, docs that send agents down a dead path

| ID | Area | Finding | Evidence |
|---|---|---|---|
| **P1-1** | Docs / DX | Canonical install `pip install -r requirements.lock` fails without a PAT rewrite. README never mentions `GESH_CORE_PAT`. After a failed install, `import server` dies on `gesh_core`. | `requirements.lock` line `gesh-core @ git+https://github.com/gesh75/gesh-core.git@…`. `README.md` Quick Start. Working pattern is the two-step install in `.github/workflows/tests.yml:109-151`. **Partial fix this PR:** README now states the PAT + CI commands. |
| **P1-2** | Security | `server.py` never calls `load_dotenv()`. Auth DB path / TTL (`auth_users.py:47-48`) are read at import **before** `ai_analyzer.load_dotenv()` (`server.py:496`). The whole security posture depends on an import side effect. | `rg load_dotenv server.py` → no matches. `ai_analyzer.py:7-13` is the loader. Not patched here — moving the load earlier changes which `.env` keys are visible at import and needs the full suite. |
| **P1-3** | Security | `pip-audit` in CI is soft-fail (`exit 0` after warning). Bandit is high/high only. Dependency CVEs cannot block merge. | `.github/workflows/tests.yml:194-199`. |
| **P1-4** | Security | `templates/owner_console.html` is excluded from the XSS wiring guard (52+ `innerHTML` sites). Owner session is the highest-value cookie. | `tests/test_dashboard_xss_wiring.py:30` — `TEMPLATE` is dashboard-only. |
| **P1-5** | Docs | `CLAUDE.md` still names `_check_customer_quota` as the live quota gate. Production spend paths call `_reserve_customer_quota` only. The check helper is count-then-spend (the race the reservation closed) and survives for unit tests. | `server.py:4453` (def) vs `:4530` / `:4724` (reserve). `rg _check_customer_quota server.py` — definition only. CLAUDE.md customer-surface table. |
| **P1-6** | Docs | First-touch runbooks still disagree with the chain and the gate count. | `README.md` Quick Start still said `python server.py` after `source venv/bin/activate`; production launcher `start_gesh_main.sh` forbids activate. Gate tables say 8; `trading/risk_gate.py` also runs `MARKET_REGIME` (9). `analysis_log` vs live table `analysis_runs`. |
| **P1-7** | DX | Cloud `AGENTS.md` claims the VM venv is ready (`pytest` / `ruff` / lock installed). This run: `/tmp/cursor/async-install/install-user.status` = `1` (`ensurepip` missing); `venv/bin` has only a python symlink; `gesh_core` / `uvicorn` absent. | `AGENTS.md` “Cursor Cloud specific instructions”. `/tmp/cursor/async-install/install-user.log`. |
| **P1-8** | Correctness (lead, previously verified) | Several 2026-08-21 fabricated-value items remain **unreverified** here (no `engine_results.json` / no chain run): MCP zero-price holdings (`build_snapshot_from_mcp.py` now documents the trap at `:44-46` — confirm the writer still cannot emit `price: 0.0`), UNKNOWN regime `or 0` consumers, stale paper-gate MERGE keys, Kelly equal-weight-as-optimum (gesh-core). **F-11** (failover integer→0) **is closed** — `llm_failover.py:336-338` returns `None`; `tests/test_llm_failover.py::test_coerce_omitted_numeric_is_none_not_zero`. | Treat `GAPS_AND_IMPROVEMENTS_2026-08-21.md` §P1 as a checklist, not current fact. |

---

## P2 — DX, docs drift, dead code

| ID | Area | Finding |
|---|---|---|
| **P2-1** | Dead code | `_check_customer_quota` has no production callers. Keep until tests that unit-test the 429/503 contract move onto `_reserve_customer_quota`, then delete. |
| **P2-2** | Docs | Three living gap files plus this one. `README.md` pointed at `GAPS_AND_IMPROVEMENTS_2026-08-04.md` as “current” after the 2026-08-21 file superseded it. **Fixed this PR** — pointer now lands here. |
| **P2-3** | CI | `pyproject.toml` `[tool.ruff] include = ["tests/**/*.py"]`. `ruff check server.py` is a silent no-op unless `--isolated`. The CI syntax gate already uses `--isolated` — agents following AGENTS.md “ruff check --fix on touched files” do not. |
| **P2-4** | DX | `ruff format` is told to agents but is not a CI gate; `ruff format --check tests/` wants to rewrite ~129 files. Running it dirties the tree. |
| **P2-5** | DX | No pre-commit/pre-push hook is installed (`.git/hooks` empty). `scripts/install-git-hooks.sh` + `scripts/hooks/pre-push` exist but are unused. |
| **P2-6** | Docs | `HANDOFF_GROK.md:169` still reads as if `CLAUDE.md` pins `grok-build`. The live pin is `grok-4.6`; `CLAUDE.md:213-214` already marks `grok-build` retired. |
| **P2-7** | Docs | `GESH_LLM_FAILOVER` default in CLAUDE.md (`grok,gemini`) ≠ code (`xai,grok,gemini` in `llm_failover.py:14-15`). |
| **P2-8** | Observability | Compatibility scan: no mypy/pyright, no formatter-in-CI, no structured metrics. Score 77/100; Style & Validation 34. Not worth a rewrite. |

---

## Verified healthy — do not re-litigate

- `/track-record` **is** in `auth_users.PUBLIC_PREFIXES` (`auth_users.py:66-68`). The 2026-08-21 “paywalled proof page” finding is closed.
- Billing activation refuse-on-foreign-`billing_sub_id` exists (`billing.py:549-558`). The raw hijack primitive is fenced; do not re-derive it from the function name alone.
- Failover omitted numerics are `None`, not `0` (F-11 closed).
- Required CI jobs `test` / `customer-isolation` run on same-repo PRs (not push-only). `pr-lint` is secret-free. `--isolated` syntax gate is load-bearing and present.
- Customer money-masking, typed tokens, and quota **reservation** (not the leftover checker) remain the live isolation story.

---

## Fixes in this PR (3, all small)

1. `daEsc` now emits `&#39;` for HTML text (displays as `'`). **Not** an onclick JS fix — see P0-1.
2. Autouse isolation of host `APP_BASE_URL` / `GESH_AUTH_ENABLED` / `GESH_ALLOWED_ORIGINS`, and stub `load_dotenv` so a late import cannot refill them.
3. README: `GESH_CORE_PAT` note, Testing commands that match CI, current gaps pointer.

---

## Next recommended agent job

Add `_require_owner_view` to `GET /api/trades`, `GET /api/trades/stats`, and `GET /api/paper/summary`, and flip `tests/test_customer_leak_sweep.py` from “masked 200” to “customer 403” on those three paths.
