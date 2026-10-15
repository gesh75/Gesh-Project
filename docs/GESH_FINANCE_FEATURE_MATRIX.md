# GESH Financial — Feature Status Matrix

> **Canonical.** A second, independently written audit of the same 2026-07-18 baseline (`dc969264`) formerly shadowed this file at the repo root; it now lives at [`docs/audits/second-pass-2026-07-18/GESH_FINANCE_FEATURE_MATRIX.md`](audits/second-pass-2026-07-18/GESH_FINANCE_FEATURE_MATRIX.md) (moved 2026-10-15). It was never reconciled after 2026-07-19; its IDs do not map to the ones here.

**Date:** 2026-07-18 | **Test Results:** 1,214 passed, 0 failed (74s)

## Status Legend

| Status | Meaning |
|---|---|
| ✅ Complete and verified | Feature works correctly with test coverage |
| ✓ Complete but not verified | Code exists, appears correct, but lacks full test coverage |
| ⚠ Partially implemented | Core functionality exists but gaps remain |
| 🎨 UI only | Frontend elements exist but backend not connected |
| 🔧 Mocked or hardcoded | Placeholder or demo data |
| ❌ Broken | Feature exists but does not work correctly |
| ⬜ Missing | Feature does not exist |
| ❓ Not verified | Cannot determine status without live testing |

---

## Authentication & Account Management

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| Registration | ✅ Complete | [auth_users.py](file:///Users/georgigaydarov/Projects/gesh-project/auth_users.py) `create_user()`, tested in test_auth_users.py | Essential | Essential | None |
| Login (password) | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L803-834, brute-force throttled | Essential | Essential | None |
| Logout | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L837-841 | Essential | Low | None |
| Magic link login | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L844-868, single-use, purpose-typed | Essential | Essential | None |
| Password reset | ⬜ Missing | No /auth/reset-password route | Medium | Medium | Add password reset flow |
| Email verification | ⬜ Missing | Accounts created by billing webhook, no separate verification | Low | Low | Consider adding for direct registration |
| Session expiration | ✅ Complete | TOKEN_TTL_SECONDS = 604800 (7 days) | Essential | Essential | None |
| Remember-me | ✓ Complete | 7-day cookie effectively implements this | Low | Low | None |
| Account deletion | ⬜ Missing | No delete account endpoint or UI | **Critical** | **Critical** | GDPR/CCPA requirement — must add |
| Profile management | ⬜ Missing | No profile edit page/endpoint | Medium | Medium | Add basic profile management |
| Subscription status | ✅ Complete | [billing.py](file:///Users/georgigaydarov/Projects/gesh-project/billing.py) billing_status field | Essential | Essential | None |
| Owner vs Customer access | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L414-593, model-book masking, leak-sweep tested | **Critical** | **Critical** | None |
| Brute-force protection | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L268-305, 8 failures / 5 min window | Essential | Essential | None |
| Rate limiting | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L219-265, per-IP, heavy vs normal | Essential | Essential | None |

---

## Billing & Payment

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| Subscription plans | ✅ Complete | Single "GESH Access" plan at $49/mo via Lemon Squeezy | Essential | Essential | None (single tier is honest for alpha) |
| Checkout flow | ✅ Complete | Hosted LS checkout; [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L895-933 | Essential | Essential | None |
| Webhook handling | ✅ Complete | [billing.py](file:///Users/georgigaydarov/Projects/gesh-project/billing.py) — HMAC verified, idempotent, claim/reclaim state machine | **Critical** | **Critical** | None |
| Magic-link provisioning | ✅ Complete | Webhook → create user → magic link email → auto-login | Essential | Essential | None |
| Payment failure handling | ✅ Complete | `subscription_payment_failed` → past_due (no revoke); revoke only on expired | Essential | Essential | None |
| Cancellation | ✅ Complete | `subscription_cancelled` → marks status, access continues to period end | Essential | Essential | None |
| Subscription revocation | ✅ Complete | Only `subscription_expired` revokes (disables account) | Essential | Essential | None |
| Post-checkout landing | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L936-947 | Medium | Low | None |
| Pricing page | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L895-933 | Essential | Essential | None |
| Refund policy | ⬜ Missing | No refund policy on pricing page or ToS | Medium | **High** | Add refund policy |
| Invoice access | ⬜ Missing | No customer portal for past invoices | Medium | Medium | Link to LS customer portal |
| Upgrade/downgrade | ⬜ Missing | Single tier — N/A for now | Low | Low | Add when multi-tier launches |
| Coupon codes | ⬜ Missing | Not implemented | Low | Medium | Consider for launch marketing |
| Premium route enforcement | ✅ Complete | Backend auth_gate_middleware, not just frontend hiding | **Critical** | **Critical** | None |
| Billing tests | ✅ Complete | [test_billing.py](file:///Users/georgigaydarov/Projects/gesh-project/tests/test_billing.py) (32KB), test_billing_race.py, test_billing_reconcile.py | **Critical** | **Critical** | None |

---

## Portfolio & Market Data

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| Live portfolio (owner) | ✅ Complete | [data_fetcher.py](file:///Users/georgigaydarov/Projects/gesh-project/data_fetcher.py) via robin_stocks | Essential | Essential | None |
| Offline fallback (snapshot) | ✅ Complete | load_snapshot_payload() with age validation | Essential | Medium | None |
| Market value | ✅ Complete | Computed from qty × current_price | Essential | Essential | None |
| Day change | ✅ Complete | curr - prev close, with percentage | Essential | Low | None |
| Holdings table | ✅ Complete | Dashboard renders full portfolio with allocation % | Essential | Essential | None |
| Benchmark comparison | ✅ Complete | [benchmarks.py](file:///Users/georgigaydarov/Projects/gesh-project/benchmarks.py) — alpha vs SPY + sector ETFs | Medium | Medium | None |
| Historical equity curve | ✅ Complete | get_equity_history() in db.py | Medium | Medium | None |
| Customer book ("My Book") | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L2626-2724, per-user positions, tested in test_mybook.py (52KB) | **Critical** | **Critical** | None |
| Bulk book import | ✅ Complete | _parse_bulk_book() with symbol/shares/cost parsing | Medium | Medium | None |
| Equal-weight mode | ✅ Complete | Privacy-first path with 1/N weighting | Medium | Medium | None |
| Cost basis tracking | ⚠ Partially | Customer book stores avg_cost; owner portfolio gets it from RH | Medium | Medium | Add transaction-level cost basis |
| Realized/unrealized gains | ⚠ Partially | percent_change computed for customer book; trade_log has realized P&L | Medium | Medium | Enhance for customer view |
| Dividends | ⬜ Missing | Not tracked anywhere | Low | Low | Future enhancement |
| Currency conversion | ⬜ Missing | USD-only assumption throughout | Low | Low | Future enhancement |
| Split-adjusted prices | ✓ Complete | yfinance auto_adjust=True handles splits | Low | Low | None |
| Stale price detection | ✅ Complete | snapshot_age_seconds, stale_snapshot flag, MAX_PRICE_AGE_SECONDS | Essential | Medium | None |

---

## AI Analysis

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| Deep portfolio analysis | ✅ Complete | [ai_analyzer.py](file:///Users/georgigaydarov/Projects/gesh-project/ai_analyzer.py) deep_analyze_portfolio() | Essential | Essential | None |
| Single-ticker research card | ✅ Complete | [server.py](file:///Users/georgigaydarov/Projects/gesh-project/server.py) L2477-2559, analyze_ticker_endpoint() | Essential | Essential | None |
| Gemini second opinion | ✅ Complete | [gemini_agent.py](file:///Users/georgigaydarov/Projects/gesh-project/gemini_agent.py) | Medium | Medium | None |
| LLM usage tracking | ✅ Complete | `llm_gateway` SQLite `llm_calls` + owner `/usage` / `/api/usage/summary` → `llm_provider` (not jsonl/`_record_usage`) — see [LLM_OPERATIONS.md](LLM_OPERATIONS.md) | Essential | Essential | None |
| Daily budget cap | ✅ Complete | Gateway reservations before each attempt; `LLM_DAILY_BUDGET_USD` (unknown-cost retained on failed streams) | Essential | Essential | None |
| Customer usage quotas | ✅ Complete | Per-customer rate limiting + usage logging | Essential | Essential | None |
| Hallucination safeguards | ✅ Complete | Strict tools + semantic validators in `llm_contracts.py` (ticker price grounding, sentiment/SEC/debate ranges); adversarial fixtures under `tests/fixtures/llm/` | **High** | **High** | None |
| Unavailable evidence semantics | ✅ Complete | News/SEC/mood return `availability: UNAVAILABLE` — never fabricate NEUTRAL/0; prompt-injection source boundary via `untrusted_source_prompt` | **High** | **High** | None |
| AI provenance stamping | ✅ Complete | generated_at, model, price_source, suspect flag | Medium | Medium | None |
| Screener analysis | ✅ Complete | analyze_screener() with Haiku (cost-routed) | Medium | Medium | None |
| Bull/Bear debate | ✅ Complete | [bull_bear_debate.py](file:///Users/georgigaydarov/Projects/gesh-project/bull_bear_debate.py) — Haiku ×2 + Sonnet ×1 | Medium | Medium | None |

---

## Consensus & Risk

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| 11-node consensus engine | ✅ Complete | [consensus.py](file:///Users/georgigaydarov/Projects/gesh-project/consensus.py) — momentum, trend, rel. strength, fundamentals, analyst, news, Kelly, ECG, volume, FFT, macro | Essential | Essential | None |
| Regime-conditional weights | ✅ Complete | _BASE_WEIGHTS with regime multipliers | Medium | Medium | None |
| 8-gate risk gate | ✅ Complete | [trading/risk_gate.py](file:///Users/georgigaydarov/Projects/gesh-project/trading/risk_gate.py) | **Critical** | **Critical** | None |
| Kill switch | ✅ Complete | File-based kill switch at KILL_SWITCH_PATH | **Critical** | **Critical** | None |
| Monte Carlo stress test | ✅ Complete | [monte_carlo.py](file:///Users/georgigaydarov/Projects/gesh-project/monte_carlo.py) — 1000 GBM paths, EWMA vol | Essential | Essential | None |
| Kelly Criterion sizing | ✅ Complete | [kelly.py](file:///Users/georgigaydarov/Projects/gesh-project/kelly.py) via gesh-core | Essential | Essential | None |
| Market regime detection | ✅ Complete | [regime.py](file:///Users/georgigaydarov/Projects/gesh-project/regime.py) — ADX + Bollinger + RSI + FRED macro | Essential | Essential | None |
| Equity Curve Gate (ECG) | ✅ Complete | [ecg.py](file:///Users/georgigaydarov/Projects/gesh-project/ecg.py) | Medium | Medium | None |
| Volatility models | ✅ Complete | [volatility_models.py](file:///Users/georgigaydarov/Projects/gesh-project/volatility_models.py) — EWMA + GARCH(1,1) | Medium | Medium | None |

---

## Paper Trading

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| Paper session execution | ✅ Complete | [paper_trading.py](file:///Users/georgigaydarov/Projects/gesh-project/paper_trading.py) | Essential | Essential | None |
| Auto-close on stop/target | ✅ Complete | check_and_close_paper_positions() | Essential | Essential | None |
| Paper summary / stats | ✅ Complete | get_paper_summary() | Essential | Essential | None |
| Paper trade logging | ✅ Complete | trade_log.py with paper=True flag | Essential | Essential | None |
| Separation from real portfolio | ✅ Complete | Separate paper flag in all trade operations | **Critical** | **Critical** | None |
| Track record / proof page | ✅ Complete | [proof_page.py](file:///Users/georgigaydarov/Projects/gesh-project/proof_page.py) — public signal-accuracy page | Essential | Essential | None |
| Customer paper trading | ⬜ Missing | Customers cannot paper trade (owner-only) | Medium | **High** | Consider for customer engagement |

---

## Email & Notifications

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| Onboarding magic link email | ✅ Complete | [email_sender.py](file:///Users/georgigaydarov/Projects/gesh-project/email_sender.py) via Resend API | Essential | Essential | None |
| Signal change alerts | ✅ Complete | run_signal_alerts.py + email opt-out | Medium | Medium | None |
| Weekly digest | ✅ Complete | run_weekly_digest.py + email opt-out | Medium | Medium | None |
| Telegram alerts | ✅ Complete | [trading/alerts.py](file:///Users/georgigaydarov/Projects/gesh-project/trading/alerts.py) | Medium | Low | None |
| Email opt-out | ✅ Complete | /alerts/unsubscribe, /digest/unsubscribe with typed HMAC tokens | Medium | Medium | None |

---

## Compliance & Legal

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| Terms of Service page | ⚠ Partially | [TERMS_OF_SERVICE.md](file:///Users/georgigaydarov/Projects/gesh-project/TERMS_OF_SERVICE.md) — labeled DRAFT | **Critical** | **Critical** | **Finalize with attorney** |
| Disclaimer page | ✅ Complete | Same content as /terms; combined page | Essential | Essential | None |
| Privacy Policy | ⬜ Missing | No /privacy route | **Critical** | **Critical** | **Must add** |
| Cookie disclosure | ⬜ Missing | Session cookie set without disclosure | **High** | **High** | **Must add** |
| Account deletion | ⬜ Missing | No self-service data deletion | **Critical** | **Critical** | **Must add for GDPR/CCPA** |
| Data export | ⬜ Missing | No data portability mechanism | **High** | **High** | **Should add** |

---

## Infrastructure & Operations

| Feature | Status | Evidence | User Impact | Business Impact | Required Action |
|---|---|---|---|---|---|
| Health check endpoint | ✅ Complete | GET /healthz returns {status: ok, auth: bool} | Essential | Essential | Expand with dependency checks |
| Database backup | ⬜ Missing | No automated backup process | **Critical** | **Critical** | **Implement immediately** |
| CI/CD pipeline | ✅ Complete | [tests.yml](file:///Users/georgigaydarov/Projects/gesh-project/.github/workflows/tests.yml) — 2 CI jobs | Essential | Essential | None |
| Production monitoring | ⬜ Missing | No APM, no error tracking (Sentry etc.) | **High** | **High** | Add structured logging + monitoring |
| Docker deployment | ⬜ Missing | macOS-only self-hosted | Medium | **High** | Add Dockerfile for portability |
| Staging environment | ⬜ Missing | Single production instance | Medium | Medium | Add pre-production environment |
| Log aggregation | ⬜ Missing | Logs to stdout/file only | Medium | Medium | Add structured JSON logging |
| Security scanning | ⬜ Missing | No bandit, no pip-audit in CI | Medium | Medium | Add to CI pipeline |
