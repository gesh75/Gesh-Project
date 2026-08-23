# GESH — Full-Project Gap Audit, 2026-08-21

> **Scope:** both repos (`gesh-project` :9000 product, `gesh-project-trading` :9003 engine), audited
> by four independent read-only passes on the last day of the Anthropic Max subscription, ahead of
> the one-month Grok handoff.
>
> **Every finding below was verified by running the command that would falsify it** (rule #60).
> Items I could not verify are marked **UNVERIFIED** and must be treated as leads, not facts.
>
> **Authority order: live code > `CLAUDE.md` > this file > every other handoff doc.**
> This file supersedes `GAPS_AND_IMPROVEMENTS_2026-08-04.md`, which carries at least five stale
> entries (listed in §6).

---

## 0. The one-paragraph version

The system is in better shape than the number of findings suggests. The chain is green
(`2026-08-21 10:46:23 EDT, fail=0 degraded=0`), backups are correct and current, secrets are not in
git, the money-masking and per-user isolation layers are unusually well built, and the launcher
hardening from rules #55–#58 is real. **The defects that matter are almost all of one shape: a
number that is wrong but looks right.** CVaR renders as 0.0% every day. Every holding card claims
"GARCH 0.0%". Kelly publishes equal weight as an optimum. That is the failure mode this project
already named as its most expensive one, and it is still the dominant category.

---

## P0 — Do before Grok starts

| # | Item | Status |
|---|---|---|
| **P0-1** | **Rotate the Robinhood password.** During this audit a verification command (`bash -c 'set -a; . ./.env'`, run to confirm rule #58) caused bash to echo the live `ROBINHOOD_PASS` value into an agent transcript. The credential must be considered exposed. Rotate, then update `.env` in **both** repos. To prove unsourceability without executing, use `grep -qE '^ROBINHOOD_PASS=.*[()#]' .env && echo unsourceable`. | ⬜ **owner action** |
| **P0-2** | **Grok failover could not fire from the scheduled chain.** `scripts/daily_analysis_et.sh:28` *overwrote* PATH without `$HOME/.local/bin`, where the Grok CLI lives. `llm_failover.grok_available()` uses a bare `shutil.which("grok")` → returned `False` in every scheduled run, so the chain silently skipped its primary fallback and went to Gemini. Every prior verification was done interactively, where grok IS on PATH — which is why it survived. Rule #55, one level deeper. | ✅ **fixed + verified this session** (`grok_available: True` under the scheduled PATH) |
| **P0-3** | **Commit the staged `llm_gateway.py` fix.** `LLMProviderUnconfiguredError` is defined (`:53`), raised (`:167`) and added to `_FAILOVER_TRIGGERS` (`:647`) — but sits **staged, not committed**, alongside `tests/test_llm_failover.py` and `CLAUDE.md`. Commit path-limited (rule #30); a bare `git commit` sweeps whatever else scheduled jobs have dropped into the index. | ⬜ |
| **P0-4** | **`gesh-project-trading` runs ZERO tests on pull requests.** `tests.yml` triggers on `push:[main]` only; the required `pr-test` check runs ruff + bandit + advisory mypy and **never invokes pytest** — against **981 test functions in 107 files**. Grok will be committing into this repo for a month with no test gate. This is the same defect class the product repo diagnosed and fixed on 2026-08-02 (PR #133 merged a `SyntaxError` under green CI). Port `gesh-project/.github/workflows/tests.yml:102-151` — the credential-scoped two-step install — into `pr-checks.yml`. | ⬜ **highest-leverage fix in this document** |
| **P0-5** | **Gemini failover leg is dead.** `llm_failover.py:373` pins `gemini-2.5-pro`; the API returns `404 … no longer available to new users`. On 2026-08-20 both legs failed and the chain lost step 3/5 (`FATAL: run_ai2 failed`). With P0-2 fixed, redundancy is restored to 2 legs only if this is also fixed. Make the id env-overridable (`GESH_GEMINI_FAILOVER_MODEL`) with the same retry-on-404 degrade `call_grok` already has. | ⬜ |

---

## P1 — Fabricated values (the project's named core hazard)

These all render as confident numbers a reader would trust. Ordered by how visible the lie is.

| ID | Sev | Where | The lie | Fix |
|---|---|---|---|---|
| **F-01** | 🔴 CRIT | `gen_report2.py:712,878` | **CVaR 95% shows `0.0%` every single day.** The report reads `cvar_95_pct`; nothing in the repo writes that key. True value today: `cvar_95 = $1,608.63 / $34,799.63` = **4.62%**. The headline risk card claims tail risk is zero. | Derive from `cvar_95 / current_equity`; render `N/A` when absent, never `0.0%`. |
| **F-02** | 🔴 CRIT | `gen_report2.py:257,297` | **Every holding card reads "GARCH 0.0%".** `volatility_models.py` correctly emits `garch_annual_pct: None` when `arch` isn't installed (it isn't — 24/25 names show `estimator: "ewma:no_arch"`). The consumer undoes it: `(vol_sym.get("garch_annual_pct", 0) or 0)`. A reported volatility of zero is worse than a missing one. | Call the existing `format_optional_metric()` — `run_engines2.py:337` already uses it correctly. |
| **F-03** | 🟠 HIGH | gesh-core `kelly.py:417-423` | **Kelly publishes equal weight as an optimum.** 24 of 25 names get an identical `optimal_pct = 4.3%` (= 1/23 of the book): every name hits the 25% per-name cap → gross 625% → `gross_factor 0.173` → all land on the same number. `_rescale_result` then overwrites `half_kelly` with the final value, destroying the only field carrying per-name magnitude. Every TRIM/ADD in the action plan is literally "distance from equal weight", published as *"Kelly TRIM: now 6.4% vs optimal 4.3%"*. Also: `corr_haircut == 1.0` for all 25 names on a book holding NVDA/MU/AMD. | Preserve `half_kelly`, add `reconciled_pct`, and emit `kelly_degenerate: true` when >N% of names pin at the cap so the report can say "Kelly is uninformative on this book". Body is in gesh-core (rule #46). |
| **F-04** | 🟠 HIGH | `run_paper_gate2.py:86-100` | **The CONFIDENCE_FLOOR gate filters nothing.** It uses `consensus[sym]["confidence"]`, which `consensus.py:652-654` documents verbatim as *"DATA COVERAGE … not the strength of the call"*. Every intent today carried `0.89` regardless of verdict (NVDA: `verdict=HOLD raw=0.237 confidence=89.0 conviction=43.0`). `MIN_CONFIDENCE=0.75` is cleared by having 8/9 nodes online. | Feed `conviction/100`; record both on the intent. |
| **F-05** | 🟠 HIGH | `build_snapshot_from_mcp.py:100-113` | **A missing MCP quote prices a holding at `$0`.** `_f()` defaults to `0.0` unflagged → `equity = 0`, `percent_change = -100`. The only guard fires when *all* holdings are lost. A partial quote response understates portfolio equity and skews every Kelly/MC weight. | Collect `zero_priced[]`; exit 1 (three fallback tiers exist) or stamp it and surface it. |
| **F-06** | 🟠 HIGH | `run_ai2.py:76-84`, `gen_report2.py:203-205`, `run_paper_gate2.py:236` | **An UNKNOWN regime reaches Claude as `ADX=0.0 RSI=0.0`.** `detect_regimes` correctly returns `None`; three consumers do `or 0`. RSI=0 means "maximally oversold", not "unknown" — and Rule 5's RSI≥70 override silently never fires for such names. | `None`-aware formatter → `ADX=n/a RSI=n/a`. |
| **F-07** | 🟠 HIGH | `gen_report2.py:349-397` | **Yesterday's gate verdicts render as "N paper record(s) today".** The degraded early-return at `run_paper_gate2.py:219` doesn't clear `action_plan`/`gate_results`/`paper_session`, and `run_engines2.py:71-76` MERGE-loads the previous file. `_gate_audit_section` has no timestamp check and hardcodes "today". | Stamp `paper_gate_ran_at`, compare against `run_manifest.run_id`, `pop()` the four keys on the degraded path. |
| **S-01** | 🟠 HIGH | all four chain scripts | **No `logging.basicConfig` anywhere, so every `logger.info` is dropped.** `grep -c price_source .logs/gesh_fullchain.log` → **0**. The repo cannot currently tell you it ran a whole day on the Robinhood fallback tier. | One `basicConfig(level=INFO)` at the top of `run_engines2.py` / `run_ai2.py` / `run_paper_gate2.py`. |
| **F-08** | 🟡 MED | `paper_trading.py:158-160` | `"0 positions hit stop/target"` cannot distinguish *checked* from *not checked* — an unpriced symbol `continue`s with no counter. Under a VPN outage this reads as a clean no-trigger day, in the code path that feeds the win-rate book. | Return `(closed, unpriced)`; log `checked N/M` as a ratio, like the new broker-preflight line. |
| **F-09** | 🟡 MED | `monte_carlo.py:132-146` | A total price-fetch failure produces a complete VaR table from `FALLBACK_DAILY_VOL = 0.018`. `calibration: "degraded"` IS emitted — but `grep -n calibration gen_report2.py` → **no matches**. The flag is never shown. | Render a degraded banner when `calibration != "ok"`. |
| **F-10** | 🟡 MED | `gen_report2.py:428` | Banner asserts a **"2% position cap"**; `.env:65` is `MAX_POSITION_PCT=0.18`. | Parse the figure from the gate's reason string. |
| **F-11** | 🟡 MED | `llm_failover.py:231-237` | CLAUDE.md claims "a blank renders as blank — it never invents a number". **False for numerics:** `_ZERO_BY_TYPE` maps `integer→0`, so an omitted `portfolio_score` becomes the headline gauge reading **0**. Today's run *was* a failover run. | Coerce numerics to `None`; render `—`. |
| **D-01** | 🟡 MED | `engine_results.json` | `dod_delta` and `cash_unlock` are **fossils frozen at 2026-07-06**, carried 46 days by the MERGE-write, with no writer and no reader. Anything treating `dod_delta` as "what changed today" gets July 6 numbers. | `pop()` them beside the existing `kelly_overconviction` pop. |
| **C-01** | 🟡 MED | `run_paper_gate2.py` lane | **The ORDER_RATE gate is structurally inert.** `count_orders_today()` queries `orders`; the chain's recorder writes `paper_trades`. 7 intents today all passed `ORDER_RATE` against a cap of 3 — it compared `0 < 3` seven times. | Log to `orders` (restores rule #20) or give the counter a source mode. |

---

## P2 — Security & commercial

| ID | Sev | Where | Finding |
|---|---|---|---|
| **F1** | 🟠 HIGH | `billing.py:340-364`, `auth_users.py:1354-1366` | **Billing account-hijack primitive.** `provision_magic_link_user` overwrites `billing_sub_id`/`tier` on whatever row the *provider-supplied, unverified* email resolves to. The `same_sub` fence exists only on the `subscription_payment_success` no-variant path; `subscription_created` goes straight to `provision()`. Attack: buy one month using a victim's email → your sub_id replaces theirs → cancel → `revoke()` matches → victim deactivated, their own renewals then refused as `unknown_variant`. Owner accounts are fenced on revoke but **not on provision**. The repo already documents the hazard in `tests/test_billing.py:655`. **Fix:** refuse in `_dispatch` when `user["billing_sub_id"]` is set and differs. |
| **F3** | 🟠 HIGH | `server.py` (no `load_dotenv`) | **The entire security posture depends on an import side effect.** `server.py` never loads `.env`; it arrives only because `ai_analyzer.py:13` calls `load_dotenv()` at import. And `auth_users.py:47-48` reads `GESH_AUTH_DB`/`GESH_AUTH_TTL` at module scope **before** that import runs. If `GESH_AUTH_ENABLED` is ever absent, `_session_email()` returns the owner email for **every anonymous request** (`server.py:1489-1494`) and `_is_owner_request` returns `True`. Fail-open, unasserted. **Fix:** `load_dotenv()` as the first statement in `server.py` + a startup assertion refusing to boot with `https://` base URL and auth disabled. |
| **F2** | 🟠 HIGH | `server.py:646` vs `:1802` | **The "#1 sales asset" is owner-only but linked as public.** `/track-record` is in `_OWNER_ONLY_PATHS` and absent from `PUBLIC_PREFIXES` → anonymous gets 302, customers get 403 — yet it is linked from the **public pricing page**. `proof_page.py:2` still calls itself public and CLAUDE.md says "never paywall it". Decide one way. |
| **F7** | 🟡 MED | `templates/dashboard.html:879` | `daEsc` does not escape `'`, and four sinks interpolate into single-quoted JS inside `onclick=`. Currently unreachable **only** because `_SYMBOL_RE` guards the API boundary — the store layer does no validation. One regex is the whole defence. **Fix:** add `.replace(/'/g,'&#39;')`. |
| **F4** | 🟡 MED | `server.py:7147,7162,7251` | `/api/trades`, `/api/trades/stats`, `/api/paper/summary` serve the owner's trade ledger to any authenticated customer with no role check. Symbols, entries, exits, stops pass the money mask unscaled. Inconsistent with F2. |
| **F8** | 🟡 MED | `templates/owner_console.html` | 52 `innerHTML` sites, **excluded from the XSS guard test** (`tests/test_dashboard_xss_wiring.py:31` scans only `dashboard.html`). Owner-only, but the owner session is the highest-value credential in the system. **Fix:** parametrise the guard over both templates. |
| **F6** | 🟡 MED | `server.py:1659-1663` | Logout deletes the cookie only; the 7-day signed token stays valid server-side. `issue_token` already mints a `jti` — the plumbing is half there. |
| **F5** | 🟡 MED | `billing.py:93-114` | Adding a second Lemon Squeezy variant without its env var → `unknown_variant` → **HTTP 200** → LS stops retrying → dedup key marked `completed` permanently. Customers pay, get no account, no automatic recovery. **Fix:** `_alert_dead_letter()` on a refused activation. |

**Dead code found:** `_check_customer_quota` (`server.py:4424`) has **zero call sites** — superseded by the atomic `_reserve_customer_quota`, but still documented in CLAUDE.md as the enforcement point. It is a count-then-spend design, i.e. exactly the race the reservation path closed. Delete it.

---

## P3 — Ops & code health

| ID | Sev | Finding |
|---|---|---|
| **OPS-03** | 🟠 HIGH | `start_gesh_main.sh:9` is `source venv/bin/activate` — a **rule #57 violation in the launcher for the only service customers pay for**. Every other launcher in both repos was hardened and carries the incident writeup inline; this one was missed. Copy the guard block from `start_gesh_trading.sh:21-62`. |
| **OPS-04** | 🟠 HIGH | **The product's risk gate is missing 4 safety gates** the engine has: `CORRELATION_HEAT`, `SECTOR_CAP`, `EARNINGS_PROXIMITY`, `LOSS_STREAK` (grep count 0 vs 3 each). Rule #53 exists *because* a correlated cohort caused a 21.4% drawdown — and the daily chain's gate lane has no correlation protection at all, while CLAUDE.md describes it as running "all 9 gates". |
| **OPS-05** | 🟠 HIGH | **gesh-core: neither pinned commit is what runs.** Product pins `4931239` (v0.8.1), engine pins stale `33af748`, and **both venvs run an editable install of `~/Projects/gesh-core`**. The engine's CI therefore tests a different gesh-core than production executes. Bump the engine pin; decide explicitly whether editable is intended and document it. |
| **OPS-07** | 🟡 MED | The documented recovery command **silently reverts a risk parameter**. The installed autopilot plist carries `MIN_CONFIDENCE=0.60` (in no git-tracked file); `fix_and_reload_services.sh:55` copies repo→installed, restoring 0.75 with no warning. Commit the intended value or drop it. |
| **OPS-09** | 🟡 MED | **No log rotation anywhere.** `/tmp/gesh_main.log` is **7.3 MB / 78,074 lines**; all 17 `/tmp/gesh_*.log` are mode **0644** (world-readable) and contain portfolio positions and proposed trades. Add `/etc/newsyslog.d/gesh.conf` (`/tmp/gesh_*.log 600 5 5000 * J`) — fixes size *and* mode. |
| **OPS-12** | 🟡 MED | `et_gate_require` writes the day's stamp **before** the caller does any work, so a run that fails mid-chain cannot be retried by a later tick. Confirmed: 3 `FATAL: run_ai2 failed` entries, each with no same-day recovery. Promote a `.running` marker to the dated stamp only on exit 0. |
| **OPS-13** | 🟡 MED | The documented AI-failure diagnostic points at a retired file. `CLAUDE.md:878` says check `llm_usage.jsonl`; nothing has written it since 2026-08-05 — usage is now in `llm_usage.db`. An operator following the runbook sees a stale file **every day** and concludes the AI call failed. Textbook rule #60. |
| **OPS-14** | 🟡 MED | **~64 tests fail on this host by default**, from the real `.env`'s `APP_BASE_URL` leaking into the CSRF trusted-origin check at collection time. Diagnosed in CLAUDE.md on 2026-07-30; no fix landed. A suite that is red by default trains everyone to ignore red. **Fix:** `monkeypatch.delenv("APP_BASE_URL")` in an autouse conftest fixture. |
| **OPS-02** | 🟡 MED | The engine repo's CI **never parses its root-level modules** — ruff is scoped to `trading/`, so `consensus.py`, `market_scanner.py`, all connectors and the whole crypto stack are never compiled. Add `ruff check --isolated --select E9,F63,F7,F82 .` (`--isolated` is load-bearing — without it the gate silently scans nothing). |
| **OPS-08** | 🟡 MED | `com.gesh.mcp-preflight.plist` is the **last ET-claiming wall-clock job** (latent rule #56). It is correct today only by accident: the host moved `Europe/Sofia → America/New_York` between 08-05 and 08-06. When it moves back, preflight fires ~8h from its target. Convert to `StartInterval` + `et_gate_require`. |
| **OPS-11** | 🟡 MED | Two production LaunchAgents exist **only on the host, in no repo**: `com.gesh.onboarding-sweep` (documented in CLAUDE.md but its plist is untracked, so the documented reload command cannot be satisfied on a rebuilt machine) and `com.gesh.billing-reconcile` (a money path, undocumented and untracked entirely). |
| **OPS-15** | 🟡 MED | The engine repo has **no lockfile** — CI resolves fresh every run. Combined with OPS-05, its CI environment is doubly unpinned. |
| **OPS-10 / 16 / 17 / 18** | 🟢 LOW | Installed `engine-gate-report` plist leaks stdout to world-readable `/tmp` (repo copy correctly uses `/dev/null`) · hardcoded `/Users/georgigaydarov/…` in 4 engine scripts, contradicting the stated path convention · dead/disabled plists, a 0-byte `webhook` file, ~50 `.fuse_hidden*` leftovers · `fix_and_reload_services.sh` covers 6 of ~17 GESH labels while CLAUDE.md calls it "all 4 services". |

---

## 4. Handoff-specific hazards (read before the first Grok session)

1. **Local `main` is 182 commits behind `origin/main`** (`git rev-list --left-right --count origin/main...main` → `182  3`). An agent told "don't work on main" who then checks out `main` for a clean base lands on a tree ~6 months stale. **Always branch from `origin/main`.**
2. **There are four handoff documents and they contradict each other** on the status of C1/C2/C3/C5: `gesh-project/HANDOFF_GROK.md`, `HANDOFF_GROK_SCHEDULER.md` (untracked), `gesh-project-trading/HANDOFF_GROK.md`, `~/Projects/HANDOFF_GROK_INDEX.md`. The scheduler doc is newest and most accurate; the one named first in the reading order is the most stale. **Consolidate to one before Grok starts, or Grok will act on the wrong one.**
3. **Grok does not auto-discover `.claude/skills/`** (`~/.grok/skills/` is empty), yet `AGENTS.md §1/§8` and `.grok/agents/gesh.md:18` both instruct it to "read the matching skill". Its `permission_mode` is `always-approve`, so `AGENTS.md §4`'s protected-systems list is the de-facto deny list for an unattended run. Say this explicitly.
4. **Multiple concurrent writers share these trees.** During this audit HEAD moved twice and the dirty count went 34 → 25 → 17. Any "current state" table is stale on arrival — tell the agent to re-derive it, never trust it.
5. **The model pin is now correct everywhere** — `grok models` returns `grok-4.6` (default) and `grok-4.5`; `grok-build` is retired and appears in no live file. Verify with `grok models` before trusting any doc, including this one.
6. **A stale `venv.py314.pre-fcdc095/` tree sits in the product repo root.** `.python-version` says 3.12 and `venv` symlinks to `~/Projects/gesh-venvs/fcdc095-py312`. The 3.14 directory is dead weight that matches repo-wide greps and will mislead searches.

---

## 5. Verified healthy — do not re-litigate

Preserved here so a month of Grok sessions don't waste effort re-auditing solid work:

- **Typed tokens genuinely prevent magic→session replay** (`auth_users.py:154-166, 1567, 1601`), magic tokens are single-use via atomic `INSERT OR IGNORE` with `rowcount==0` rejection, and schema-version pinning fails legacy tokens closed.
- **The billing webhook HMAC is correct and fails closed**, and idempotency is a real outbox state machine — `pending` claim with a 60s lease, owner-fenced CAS reclaim, `in_progress` → 503 so LS keeps retrying.
- **Money masking is unusually well built** — `_requires_scaling_factor` is defined by *differential evaluation of the scaler itself* rather than a parallel key list that would rot; an unusable factor returns a typed 503 instead of a fabricated `$0.00`.
- **Customer isolation is done at the boundary**, not after: `_context_payload` routes customers through `model_portfolio.build_customer_model_context` so ten owner-fed engines never see the owner payload. `/api/mybook/consensus` even forces the ECG node to ABSTAIN and discloses the reduced coverage.
- **Quota reservation is atomic and pre-spend**, fails closed to 503, and `/api/analyze-ticker` is 404-before-spend.
- **CSRF handling and proxy-header trust are both correct** (trusted origins from config not the Host header; first-untrusted-host-from-the-right).
- **Backups are correct**: SQLite online-backup API, `PRAGMA integrity_check` on every copy, `0600` dest created before writing, 14-day prune, non-zero exit on failure. 45 files per repo, newest today.
- **No secrets in git** in either repo; `.gitignore` is thoughtful, including an honest note explaining why an already-tracked attorney brief was flagged rather than silently ignored.
- **Rules #55–#58 hardening is real** — 8 of 9 launchers invoke `venv/bin/python3` absolutely and assert `sys.prefix`; `scripts/et_gate.sh` is byte-identical across repos and TZ-independent by construction.
- **The chain's failure alerting is wired** — `tg_alert()` on every FATAL path, PID-liveness lock with stale reclaim, `caffeinate -i` self re-exec.
- **Zero TODO/FIXME/HACK** across all tracked Python in the engine repo.
- The **tax-harvest fabrication is genuinely fixed** (this session), and the **0-byte tracked files** flagged as an urgent deadline item were already restored — the only remaining 0-byte tracked files are legitimate package `__init__.py` markers.

---

## 6. Corrections to `GAPS_AND_IMPROVEMENTS_2026-08-04.md`

That file is now stale on five points:

| Its claim | Reality |
|---|---|
| P0-2: "host `gesh_tracker.db` observed empty — confirm write path" | The write path **works**. `gesh_tracker.db` is vestigial; the live DB has `paper_positions` 58, `paper_outcomes` 37, `orders` 1510. |
| P0-2 targets: "≥30 closed, WR≥55%, Sharpe≥0.5, DD<10%" | **Retired spec.** The 2026-06-24 re-spec requires ≥100 *engine* closes, PF ≥1.5, expectancy >0, PSR/DSR ≥0.95, and win_rate is descriptive-only and never gates. Engine closes today: **23**. |
| P2: "Owner JS out of shared bundle" · "Cookie consent copy" — open | Both **done** (`tests/test_owner_js_extraction.py` exists; `server.py:1442` renders the consent copy). |
| P1: "Split `server.py` (~6.5k lines)" | Now **7,635** lines — regressed ~1.1k. |
| Doc map: "`docs/GESH_FINANCE_DEFECT_REGISTER.md` — status column added" | True for the `docs/` copy; the **root shadow** has no Status column and reads as 38 open defects. **Eight** root docs shadow `docs/` copies, not four. |

---

## 7. Method note for whoever works here next

The single practice that produced every finding above: **before asserting that something is
published, consumed, empty, gated, or escaped — run the command that would prove it false, and
paste the output.** Two claims in this audit were disproved that way and had to be rewritten:
a lot-level tax-harvest optimisation that measured *worse* than the blended baseline, and a
"C1 deadline" that had already been resolved one minute before the doc claiming it was written.

A wrong test fails loudly. A wrong sentence gets believed for a month.
