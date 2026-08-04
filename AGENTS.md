# AGENTS.md

Repository guide for coding agents working in `gesh75/Gesh-Project`.

## 1. Scope

- **Port 9000 (`server.py`) is the customer product.**
- `gesh-project-trading` / port 9003 is the internal proof and execution sidecar.
- Read `CLAUDE.md` before substantive work.
- For portfolio, screener, risk, regime, Kelly, news, SEC, or paper-trading analysis, read the matching skill under `.claude/skills/` first.

## 2. Model routing

- Use **Grok 4.5** for coding-agent analysis and implementation in this repo.
- Preferred model ids: `grok-build` or `grok-4.5`.
- Do **not** use `grok-composer-2.5-fast` for substantive work here.

## 3. Safety and product boundaries

- Preserve customer/owner isolation at all times.
- Never expose owner holdings, cash, account identifiers, recommendations, or private operational data to customers.
- Never fabricate financial values when source data is missing.
- Prefer explicit typed failures over silent fallback or invented defaults.
- Never weaken authentication, authorization, CSRF, Origin, cookie, session, tenant, or request-boundary checks.
- Never weaken fail-closed risk gates just to make tests pass.
- Never print, log, summarize, or commit secrets.

## 4. Protected systems: do not modify or operate

- Production databases
- `.env` files or credentials
- Billing or Lemon Squeezy state
- Brokerage integrations or live orders
- Customer records
- LaunchAgents / deployment state / production services
- Live portfolio snapshots used as production state
- Daily analysis chain side effects in production

Also do **not** run AI calls, brokerage actions, billing actions, or production restart/deploy commands from the sandbox.

## 5. Architecture quick map

- `server.py` — FastAPI app and customer-facing routes
- `data_fetcher.py` — live portfolio, holdings, quotes, snapshot loading
- `ai_analyzer.py` — Claude prompt orchestration and structured outputs
- `regime.py`, `kelly.py`, `monte_carlo.py`, `consensus.py`, `ecg.py` — core portfolio engines
- `news_agent.py`, `sec_agent.py`, `fundamental_agent.py`, `gemini_agent.py`, `ollama_agent.py` — supporting intelligence agents
- `trade_log.py`, `paper_trading.py`, `trading/` — paper/live execution pipeline and gates
- `templates/dashboard.html` — main UI
- `tests/` — pytest suite

## 6. Implementation rules

- Prefer the smallest behavior-preserving change.
- Add a regression test for every real defect.
- Run focused tests before broader suites.
- Do not edit unrelated code or relax security behavior to satisfy a test.
- Keep generated report artifacts and privileged/legal docs out of commits.
- In tracked code, avoid hardcoded `/Users/...` paths; use repo-relative or configured paths.

## 7. Financial-engine rules

- Position sizing and risk bands are canonical in `gesh-core`; import them instead of re-implementing.
- Size from settled/deployable cash: `account["portfolio_cash"]` or `equity_summary["deployable"]`.
- Do **not** size from `withdrawable_amount`.
- Treat `account["buying_power"]` on Gold accounts as leveraged margin, not deployable cash.
- Preserve provider fallback behavior and per-symbol `price_source` tagging.
- Do not widen a risk gate to make a test pass.
- Do not set `auto_trade=True` or `PAPER_MODE=false` without explicit authorization and extensive paper validation.

## 8. Analysis-specific workflow

Before analysis work:

1. Read the relevant skill in `.claude/skills/`.
2. Inject regime context into deep portfolio analysis.
3. Check news sentiment against the technical regime.
4. Cross-check Kelly sizing with the fundamental verdict.
5. For scanner flows, run `sector_filter` → `consolidation_zone` → full engines.

Output expectations:

- Use specific dollar amounts, percentages, and dates.
- Tie predictions to fundamentals, not only technicals.
- Prefer tables over long prose.
- Never fabricate sentiment when `article_count == 0`.
- Never call a portfolio “safe” without honoring Monte Carlo / risk evidence.

## 9. Daily analysis chain

- The chain is deterministic: `run_daily_analysis.sh` orchestrates snapshot → engines → AI narrative → paper gate → report.
- Do **not** hand-run engines to fabricate output, hand-edit `gesh_report.html`, or hand-write chain SQL.
- The chain is intended to run on the host environment, not improvised from a sandbox REPL.
- `run_engines2.py` / `run_ai2.py` intentionally shadow some imports from the trading repo; verify which copy you are editing before changing engine behavior.

## 10. Build and test

Runtime and CI:

- Python version is declared in `.python-version` (CI runs 3.12).
- Canonical install:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install pytest pytest-cov ruff
```

Validation expectations:

- Run `ruff check --fix` and `ruff format` on touched Python files.
- Run focused `pytest` coverage for the area you changed; expand only as needed.
- Never hit the live network in tests; mock at the client boundary.

Dependency notes:

- `requirements.txt` references private `gesh-core`; CI rewrites access with `GESH_CORE_PAT`.
- `openbb` is intentionally excluded from CI and lives in `requirements-openbb.txt`.

## 11. Testing conventions

- Use `pytest`, not `unittest.TestCase`, for new tests.
- Name tests `test_<unit>_<scenario>_<expected>`.
- Prefer fixtures and parametrization over shared mutable setup.
- Cover unhappy paths and boundary cases.
- Use obviously fake secrets in fixtures.

## 12. When in doubt

- Check `CLAUDE.md` for the full project operating manual.
- Check `.github/copilot-instructions.md` for repository-wide safety and workflow rules.
- If a task risks changing financial correctness, customer isolation, or production behavior, stop and ask for clarification before proceeding.

## Cursor Cloud specific instructions

Notes for cloud agents running in this VM (dependencies are refreshed by the startup update script — `venv/` + `requirements.lock` + `pytest pytest-cov ruff` + the NLTK vader lexicon).

- **`gesh-core` is a hard boot blocker and needs the `GESH_CORE_PAT` secret.** `db.py`, `kelly.py`, `consensus.py`, `price_source.py`, `trading/*`, etc. are thin shims that `sys.modules`-alias to `gesh_core.*` at import time, and `tests/conftest.py` imports `db`. Without `gesh_core` installed, `import server` raises `ModuleNotFoundError` and the *entire* pytest suite errors at collection. `gesh-core` is a private repo (`gesh75/gesh-core`) that the default VM GitHub token cannot clone — a `GESH_CORE_PAT` (fine-grained PAT with read access to that repo) must be set as a secret so the update script can install the pinned commit from `requirements.lock`. The rest of the dependency tree installs fine without it.
- **Interpreter / venv:** Python 3.12 (declared in `.python-version`); the venv lives at `./venv` and is gitignored. Run everything through `./venv/bin/python` (e.g. `./venv/bin/python server.py`, `./venv/bin/python -m pytest ...`). `setup.sh` is the human one-time installer but it interactively prompts for `.env` and hard-requires Robinhood + Anthropic keys — do not run it unattended; the cloud update script installs the same locked tree non-interactively.
- **Running the app:** `./venv/bin/python server.py` starts uvicorn on `127.0.0.1:9000` (override with `PORT`). It intentionally binds loopback only. The server boots **without** any API keys or Robinhood login — startup RH auth failure is non-fatal (falls back to cached data). `GET /healthz` and `GET /` (dashboard) work with zero secrets.
- **Portfolio + AI endpoints need data/keys.** `/api/portfolio` needs a live Robinhood session **or** a `live_portfolio_snapshot.json` file in the repo root; with neither it returns `503` (`offline: true`). AI routes (`/api/deep-analysis`, `/api/analyze-ticker`, screener, etc.) need `ANTHROPIC_API_KEY`; when the auth wall is OFF, customer LLM routes also need `GESH_API_KEY` (fail-closed `503`). Do not make live AI/brokerage/billing calls from the sandbox (see §4).
- **Lint / test commands** (see `.github/workflows/tests.yml` for the canonical gates): `./venv/bin/ruff check tests/`, whole-repo syntax gate `./venv/bin/ruff check --isolated --select E9,F63,F7,F82 .`, and `./venv/bin/python -m pytest tests/ -q`. `pyproject.toml` sets default `addopts = "-ra"` and scopes ruff config to `tests/` only (hence the `--isolated` flag on the repo-wide gate).
