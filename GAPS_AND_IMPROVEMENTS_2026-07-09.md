# GESH Financial — Deep Analysis, Gaps & Improvements
**Date:** 2026-07-09 (Grok deep review — smarter than prior Opus passes)  
**Scope:** All gesh* local repos + git history + remote branches (via local fetch) + key code/docs/audits/CI/tests.  
**Goal:** Make this the **best-in-class** personal + commercial AI trading intelligence product.

## Executive Summary
GESH is a sophisticated, production-leaning AI financial observability + paper-trading system (FastAPI + SQLite + multi-LLM + 11-node consensus + 8-gate risk + autopilot + billing tiers). Significant hardening has occurred since May 2026 audits (customer isolation, spend gates, billing, tracker, shims to gesh-core).

**Current Strengths**
- Excellent risk gate + persistence of decisions/intents.
- Strong recent security/product surface (leak sweeps, XSS, owner/customer auth, quotas).
- Paper-first defaults + conservative config.
- Multi-source signals (regime, macro FRED, news, SEC, fundamentals, FFT, MC, Kelly, crypto engine).
- Active test suite (many offline consensus/risk/billing tests) + CI with isolation gate.
- gesh-core initiative to kill duplication (46 modules previously).

**Overall Maturity:** ~70% toward "best product" for serious personal use + early commercial. Gaps are mostly around completeness, reliability, ops, and scaling the architecture/product.

**Top 5 Highest-Leverage Moves (ranked)**
1. Complete gesh-core extraction + make dependency reliable (monorepo or robust private package).
2. Raise real coverage to 70-80%+ with property-based + Monte Carlo harness tests on core math.
3. Data source abstraction + resilience layer (multi-provider, circuit breakers, strong freshness).
4. Production-grade deployment story (Docker, health, observability) + clear self-host / hosted path.
5. Bulletproof autonomy proof (tracker accuracy) + polished customer surfaces (Android, reports, onboarding).

## Repositories Overview (Checked)
- **gesh-project** (https://github.com/gesh75/Gesh-Project.git, port 9000): The product. Dashboard, daily chain, analysis, billing, customer features. Many local uncommitted skill updates + minor py changes.
- **gesh-project-trading** (https://github.com/gesh75/gesh-project-trading.git, port 9003): Internal engine, scanner, autopilot, paper execution, crypto, win-rate tracker. Modified docs.
- **gesh-core** (https://github.com/gesh75/gesh-core.git): Shared extraction. Phase 3 in progress.
- **agentic-rh-test** (agentic-rh-gesh): RH-specific agentic testing + recent audits.
- **gesh-ai-network-tool**: Older/archived network tooling.

**Git State (local + fetched):**
- Main active. Several claude/opus-* and feat branches (spend-gate-hardening, analyze-*, etc.) merged recently.
- Local dirty: primarily .claude/skills/* (6+ files), some core py, workflows, CLAUDE.md, gesh_config.json. Untracked .grok / .cursor / AGENTS.md / scripts.
- No major .env committed (good). Recent PRs focused on customer surfaces, billing tiers, spend hardening, autonomy funnel.

## Architecture & Duplication
- Good move to gesh-core for shims (allocation_config, some trading.*).
- Still incomplete: consensus, kelly, many engines, risk primitives partially extracted.
- Two large overlapping codebases + separate DBs historically (improving).
- Risks: drift, install friction (PAT for private core in CI), inconsistent shims.

**Recommendation:** Finish extraction (Tier 2/3), then decide monorepo vs versioned package. Add parity tests.

## Gaps by Category (Evidence-Based)

### 1. Testing & Correctness (High)
- Coverage floor = 55 (bumped in this pass; was 45). Still far from 80%.
- Strong phase/consensus/risk/billing tests, but many app modules (server, agents, data_fetcher paths, report gens) under-exercised.
- No property-based testing on consensus votes / Kelly formulas / gate math.
- Monte Carlo paths not deeply validated against known distributions.
- CI: good offline + isolation gate, but 15min timeout, no security lint (bandit), no full integration, brittle gesh-core PAT.
- Past audit (May): zero real tests → now much better, but incomplete.

**Improvements:**
- Raise target stepwise (60→70→80).
- Add `hypothesis` tests for kelly, consensus scoring, risk gates.
- Add coverage badge + local `make coverage`.
- Expand CI: ruff on more, pip-audit, mypy on core, scheduled backtest smoke.

### 2. Architecture & Maintainability (High)
- Duplication partially solved.
- Large files, some procedural style in engines.
- Async/await mixed with blocking yfinance/robin_stocks (event loop stalls).
- Overlapping run_*.py / report generators.
- DB still has some legacy fragmentation risk.

**Improvements:**
- Finish core extraction.
- Introduce DataProvider protocol + adapters (yfinance, future Alpaca/Polygon).
- Make heavy I/O explicit (to_thread or async libs).
- Consolidate orchestrators and report gens.
- Add architecture decision records (ADRs) in docs/.

### 3. Data Reliability & External Dependencies (Critical for Best Product)
- robin_stocks: pickle auth, device approval landmines, undocumented.
- yfinance: crumb fragility, rate limits, incomplete fields.
- Partial fallbacks (some agents have).
- No systematic freshness + anomaly beyond api_verifier.
- Macro/news/SEC good but cache strategies vary.

**Improvements:**
- Abstract broker/market data.
- Add retry + circuit breaker (tenacity or custom) + age stamps everywhere.
- Multi-source quotes (primary + fallback).
- Stronger validation + synthetic data for tests.
- Consider paid reliable sources for production signals (Polygon, Tiingo, etc.).

### 4. Security & Secrets (High — Recent Progress Excellent)
- Good: hmac compare, owner/customer separation, require_api_key fails closed, NaN guard, rate limits, customer leak/XSS tests, spend gates.
- Risks: local secret storage and bootstrap need a documented, provider-neutral
  secure-storage policy; service credentials should not depend on one principal.
- No static security scanning in CI.

**Improvements (do before any live):**
- Document + script secret bootstrap (1password / macOS keychain helpers).
- Add GESH_API_KEY generation to setup.
- Add bandit / pip-audit / semgrep to CI.
- Rotate exposed keys regularly.
- Harden launchd plists (no world-readable logs if possible).

### 5. Ops, Deployment & Observability (High)
- Pure launchd + /tmp logs. Fragile for others.
- No Docker, compose, health endpoints standardized, metrics.
- Limited structured logging.
- No automated restart / watchdog beyond launchd.
- Single-machine assumption.

**Improvements:**
- Add docker-compose for (9000 + 9003 + redis optional + db).
- Standard /health /metrics endpoints (Prometheus).
- Structured logs (structlog or logging + JSON).
- Systemd units + docs for Linux self-host.
- Basic alerting (Telegram already, add PagerDuty/webhook option).
- Versioned releases + changelog enforcement.

### 6. Product & Commercial Readiness
- Billing tiers, usage, proof page, watchlist, mybook, spend gates — strong recent work.
- Android: multiple duplicate dirs + zip + "android 2/3" cruft (audit flagged 91% bloat). Status unclear — one real app?
- Onboarding sweep exists but manual elements remain.
- No clear hosted SaaS path or packaging for self-host customers.
- Win-rate proof (tracker) is gold — must be 100% trustworthy.
- LLM costs can spike without tight gates + caching. **Addressed (2026-08):** gateway reservations + `LLM_DAILY_BUDGET_USD`, prompt `cache_control`, Haiku routing, owner `llm_provider` dashboard — see [`docs/LLM_OPERATIONS.md`](docs/LLM_OPERATIONS.md). Remaining open gap: financial-outcome evaluation / eval harness (verdict quality vs realized returns).

**Improvements:**
- Clean Android: keep single clean /android/ (source only), ignore builds, integrate or archive.
- Full end-to-end autonomy gate validation + public (sanitized) scorecard.
- ~~Prompt caching + Haiku routing + cost dashboard.~~ **Addressed (2026-08)** — [`docs/LLM_OPERATIONS.md`](docs/LLM_OPERATIONS.md).
- Self-serve onboarding + magic links fully wired + tested.
- Commercial docs: pricing page, feature matrix, "why trust the signals" one-pager.
- Consider web PWA first before heavy native.

### 7. LLM / AI Surface
- Multi-model good (Sonnet deep, Haiku batch, Gemini, Ollama).
- **Addressed (2026-08):** prompt caching (`cache_control`), canonical model routing (`llm_gateway.resolve_model`), strict tool/semantic contracts (`llm_contracts`), provider telemetry (`llm_calls` + owner `/usage`), adversarial fixture regression gate — see [`docs/LLM_OPERATIONS.md`](docs/LLM_OPERATIONS.md).
- **Still open:** no systematic **financial-outcome evaluation / eval harness** for verdict quality vs realized returns; debate engine cost remains a cost-ops concern.
- Consensus node learning exists but maturity?

**Improvements:**
- ~~Implement cache_control on large prompts.~~ **Addressed (2026-08).**
- Add eval harness (backtest alignment of consensus vs realized). **Still open.**
- ~~Budget per-customer + owner dashboards.~~ **Addressed (2026-08)** — customer quotas + owner `llm_provider` summary.
- A/B or shadow mode for new nodes/weights.

### 8. Code Quality & Tooling
- Ruff only on tests (intentional to avoid retro-lint pain).
- No pre-commit hook standardized.
- Lots of __pycache__ and venv noise in workspace.
- Some bare excepts still (improving per audits).

**Improvements:**
- Pre-commit config (ruff, pyright/mypy on new code, secret scan).
- .gitignore polish (pyc, android builds, reports, .DS_Store).
- Run ruff on selected production modules gradually.
- Add mypy/pyright to core shims.

### 9. Other / Polish
- Scattered docs (good content, hard to navigate). Single "start here" + architecture index.
- No SBOM or license audit.
- Forex/other scanner modes exist but less battle-tested.
- Memory-bank / .claude in repo (agent harness) — keep but document.

## Prioritized Roadmap (for "Best Product")

**Phase 0 (Now — 1-2 weeks): Hygiene + Safety**
- [x] Bump coverage floor + document target (this pass).
- [x] Complete .env.example (GESH_API_KEY + trading params + reddit + core PAT).
- Create/run full local verification script (syntax, import smoke, offline tests, risk gate unit).
- Clean Android cruft (git rm cached duplicates + .gitignore).
- Add GAPS doc (this file) + link from README/CLAUDE.

**Phase 1 (High Value): Architecture & Tests**
- Finish gesh-core extraction for consensus + kelly + risk.
- Add 20-30 new high-signal unit/property tests.
- DataProvider abstraction + 2 fallbacks.
- Raise CI coverage gate + add security job.

**Phase 2: Reliability + Ops**
- Docker + health + metrics.
- Retry/circuit everywhere critical paths.
- Structured logging + centralized log tailing.
- ~~Prompt caching + cost telemetry.~~ **Addressed (2026-08)** — [`docs/LLM_OPERATIONS.md`](docs/LLM_OPERATIONS.md).

**Phase 3: Product Polish + Scale**
- Android cleanup + minimal viable app or PWA.
- Full tracker + public proof page automation.
- Self-host packaging + docs.
- Optional: Alpaca live path behind extra gates + human-in-loop.

**Success Metrics for "Best"**
- 80%+ coverage on money logic.
- <5min full daily chain end-to-end with zero silent fails.
- Zero customer data leaks in tests.
- Documented "how to self-host in 10 min".
- Win-rate proof page with >50 closed paper trades, sharpe/drawdown stats.
- LLM spend per analysis < $0.02 average.

## Ecosystem Review (2026-07-09)

The review covered the product repositories, tests, CI, documentation, and public
product surface. Machine-specific repository inventories, filesystem paths,
automation topology, and runtime observations are intentionally omitted; they are
operational reconnaissance and belong in an access-controlled runbook.

The biggest remaining productization gaps are:
- Public product visibility and documentation.
- Generated-artifact and archived-project hygiene.
- A simpler, clearly owned orchestration model.
- A unified, authenticated operational view that does not expose portfolio or
  security-control state.

Internal engineering is advanced; product documentation, polish, operational
clarity, and repository hygiene remain the focus.

## Evidence Sources Used in This Review
- Local: git status/log/branch/remote (targeted + limited finds), file trees, pyproject, CI workflow, server.py, risk_gate.py, .env.example, gesh_config, tests/ (50+), README/CLAUDE/AGENTS, multiple audits, full enumeration of gesh75 remotes and hub content.
- Prior audits: May 2026 gesh-financial (many items addressed: tests added, isolation, spend gates), agentic-rh 07-2026 audits.
- Code patterns: auth hardening, gate persistence, NaN guard, FD bump.
- Gaps confirmed by grep for patterns, test collection, coverage config, duplicate android, shim imports, public README inspection.

## Immediate Next Commands (Run These)
```bash
# 1. Verify current state
cd ~/Projects/gesh-project
python -m py_compile server.py consensus.py trading/risk_gate.py  # or use the skill
bash run_daily_analysis.sh --dry  # if supported, or manual steps
pytest tests/ -q --cov=. --cov-report=term-missing | tail -20

# 2. Hygiene
git status
git diff --stat
# Review uncommitted skills changes before commit

# 3. After fixes
pytest tests/test_risk_gate*.py tests/test_consensus*.py -q --cov=trading --cov=consensus
```

This analysis is actionable. Prioritize Phase 0 items immediately. The foundation is already very strong — closing these gaps will make GESH undeniably the best personal AI trading intelligence system available.

**Final confirmation (last background task):** ~/Projects/Git is empty (only .DS_Store). No .git directories under paths containing /Git/ add new active gesh work. Gesh mentions in *.md files under Projects are the expected core docs + attribution in other gesh75 projects. All broad "check all the gits + all the local" scans the user surfaced are now fully processed and consistent.

*Analysis performed by Grok (xAI) — focused on product excellence, safety, and engineering rigor.*

---

# 2026-07-14 — State Update: "Where we are now" (post-customer-surface + audit-hardening)

This section brings the living roadmap current. It records what shipped **since the
2026-07-09 review**, where the long-term features stand, and what's still open. It is
the answer to "no new doc on the long terms / where are we now."

## What shipped since 2026-07-09

### Customer surface — the self-serve product (the *Analyze* tab)
The dashboard is no longer just "view the owner's research." A per-user, money-masked
customer surface now lets any logged-in user research any ticker and analyze their own book:

| Capability | Route(s) | LLM? |
|---|---|---|
| Company-name → ticker search (RH primary, curated fallback; megacaps win name ties) | `/api/symbol-search` | no |
| 12-node consensus card (LT_Macro is the 12th node) | `/api/consensus/{sym}` | no |
| Momentum signal — RSI + MACD + Volume → BUY/SELL/NEUTRAL | `/api/momentum/{sym}` | no |
| Trade setup — support/resistance (pivots + fractal swings) → entry/stop/target/RR | `/api/levels/{sym}` | no |
| Earnings — EPS beat/miss + surprise % (Finnhub primary, yfinance fallback) | `/api/earnings/{sym}` | no |
| Regime / cycle / fundamentals / analyst / news cards | per-ticker GETs | no |
| AI research card — price-grounded, one Claude call/symbol/15 min | `/api/analyze-ticker` | **yes** |
| Per-user watchlist + portfolio book; full engine run on the customer's own book | `/api/watchlist`, `/api/mybook`, `/api/mybook/analyze` | mixed |

All customer dollar amounts are normalized to a model book (`_MODEL_BOOK_USD`,
`CUSTOMER_MODEL_BOOK_USD`) so a customer never sees the owner's real account size —
percentages, prices, and signals are real; only absolute sizing is illustrative. Full
runbook: `docs/CUSTOMER_SURFACE.md`.

### Engine
- **LT_Macro node** added to the consensus engine (12th surfaced node; 1–3y market-wide
  bias from VIX / Fear&Greed / margin debt). Weighted directional tally is 10 nodes;
  Kelly (sizing) and FFT_Cycle (informational) are surfaced but out of the vote.

### Data-quality & resilience hardening (from Nikolai's live audit, 2026-07-14)
- **Canonical fair-value %** — recomputed deterministically from estimate+current so the
  same stock can't read +88% on one surface and −47% on another.
- **Fundamentals guards** — negative trailing P/E renders "N/M (trailing loss)";
  implausible 52-wk ranges (split-mismatched upstream) are suppressed, not shown.
- **Price bad-tick guard** — a single outlier close (observed NVDA $17.16 flash) is
  rejected against the recent-20 median before it can poison book valuation / Kelly.
- **AI card honest degrade** — an empty/degenerate model response shows "temporarily
  unavailable" instead of a misleading all-zeros research card.

### UX / honesty
- Offline snapshots now say so ("offline — ↻ shows the latest snapshot, not live prices");
  Refresh no longer implies a live pull it can't deliver.
- Tab switches reset scroll to top (fixed the Risk tab blank-viewport quirk).
- Header badges are customer-facing: "✨ Powered by Claude" (model in tooltip) and
  "📊 Illustrative sizing · signals are real" (masking base in tooltip).
- Tier admin CLI: `python3 auth_users.py tier <email> <observer|trader|desk>` to comp a
  tester to higher LLM quotas (1× / 2× / 5×).

## Long-term features — status

| Feature | Status |
|---|---|
| FRED macro layer in regime prompts | ✅ live |
| Polymarket macro sentiment blend | ✅ live |
| Gemini multi-LLM second opinions | ✅ live |
| LT_Macro long-term (1–3y) consensus node | ✅ live (2026-07) |
| Customer self-serve research + book analysis | ✅ live (2026-07) |
| Billing (Lemon Squeezy) + tiers + quotas | ✅ live |
| Autonomy gate → live Alpaca trading | ⏳ accumulating paper proof (0 closed vs. ≥30 needed) |
| gesh-core extraction (kill 46-module duplication) | 🚧 phase 3 in progress |
| Data-source resilience layer (multi-provider, circuit breakers) | 🚧 partial (bad-tick guard added; full abstraction open) |
| Production deploy story (Docker, health, observability) | ❌ open |

## Still open (carried from the 2026-07-09 top-5, unchanged priority)
1. Complete gesh-core extraction + make the private dependency reliable.
2. Raise real test coverage to 70–80% with property-based + Monte-Carlo harness tests.
3. Data-source abstraction + resilience (multi-provider, circuit breakers, strong freshness).
4. Production-grade deployment (Docker, health, observability) + clear self-host/hosted path.
5. Bulletproof autonomy proof (tracker accuracy) + polished customer surfaces + onboarding.

## Cross-repo note
The `gesh-project-trading` repo (port 9003 engine, `GESH_PROJECT_DOCS.html`,
`GESH_MONETIZATION_REPORT.html`) and the local `gesh_financial_learning_path.html` are
**separate from this repo** and are NOT updated by this section — they need their own pass.
