# HANDOFF → Grok: gesh-project (GESH Financial — THE PRODUCT, port 9000)

> **Regenerated 2026-08-21 (evening), replacing the morning draft**, which was already wrong on
> five points — including claiming a React/Next.js frontend that does not exist. Every fact below
> was verified by running the command.
> **Authority order: live code > `CLAUDE.md` > `docs/` > this file.**

---

## 0. Read these first, in order

1. **`../gesh-project-trading/GESH_CROSS_REPO_AUDIT_2026-08-21.md`** — 5 CRITICAL items spanning
   both repos, verified. **C1 has a deadline: two tracked files are currently 0 bytes.**
2. `GAPS_AND_IMPROVEMENTS_2026-08-04.md` — the living roadmap. The audit corrects two entries.
3. `AGENTS.md`, then `CLAUDE.md`.

---

## 1. What this is

**The paid product.** An AI-powered financial observability dashboard on port 9000 — eight tabs
covering signals, risk, regime, Kelly sizing, Monte Carlo, SEC alerts, fundamentals, and daily
analysis. The **only** layer that faces paying users ($49 / $149 / $399 per month).

Stack: FastAPI + SQLite + yfinance + Anthropic (with Grok/Gemini failover).

> ❌ **There is no React and no Next.js.** The morning draft of this file said otherwise and it
> seeded a myth already logged as GFD-037. The UI is a **single server-rendered
> `templates/dashboard.html`, 4,095 lines**, with Chart.js from a CDN. `README.md:103` calls it
> "React-style", which is where the confusion starts. Do not go looking for a JS build.

| Repo | Role |
|---|---|
| **this repo** (9000) | THE PRODUCT — the only customer-facing surface |
| `../gesh-project-trading` (9003) | Internal engine; its paper win-rate is this product's public proof |
| `../gesh-core` | Private shared package — **pin by 40-char SHA, never by tag** |
| `../agentic-rh-test` | Real-money executor downstream of the engine |

> ⚠️ `../gesh-project-audit-hardening/` and `copilot-worktrees/gesh-project` are git **worktrees**
> of this repo, not copies. Run `git worktree list` before editing an unfamiliar directory.

---

## 2. Current state — verified 2026-08-21

| | |
|---|---|
| Branch | **`feat/rh-mcp-data-layer`** — not `main`. **2 ahead of `origin/main`, 0 behind.** |
| HEAD | `d63b352` — *feat: read-only Robinhood MCP data layer + broker preflight*, today |
| Working tree | **dirty, 17 entries** — see §3, and note the 0-byte damage |
| Tests | **180** `tests/test_*.py` |
| CI | `tests.yml` — PR triggers correctly in place, `ruff --isolated` whole-repo gate live |
| gesh-core pin | `49312393…` (v0.8.1) — **the engine is on v0.8.0. Mismatch: audit C4.** |
| Python | 3.12 |
| `server.py` | **7,635 lines** (the roadmap's split task assumed ~6.5k — it regressed) |

---

## 3. The dirty working tree — and the one thing that will destroy work

### ⚠️ Do this before anything else

```bash
git checkout -- Gesh_Architecture_Diagram.html gesh_financial_learning_path.html
```

Both are **tracked files currently truncated to 0 bytes** (628 and 259 lines deleted). Status is
` M`, not ` D` — accidental. The content is safe in HEAD **until someone commits over it**, and
`CLAUDE.md` documents `git add -u` as the approved checkpoint command, which stages exactly these.

### Also at risk

- **3 commits exist only on local `main`** — unpushed, unmerged, on no remote and not on this
  branch. `40e7195` is a real reliability fix to `_create_streaming` (mid-stream transport stalls),
  `bfd23f0` a report fix. Cherry-pick them onto the branch or push `main` before anyone resets it.
- **`stash@{0}` on main** — *"Scanner owner-only + margin panel"*. Security-relevant, still unapplied.
- Two **prunable worktrees**, one holding work on `fix/audit-p0-p1-pr` that is on no branch you're on.

### Everything else untracked

10 generated `*.html` reports from one batch run (identical timestamps), `webhook` (a 0-byte stray
file, not a directory), `.claude/settings.local.json`. None are source. None contain secrets.

**Never `git add -A`.** `.rh_mcp_oauth.json` is gitignored — and note it contains an MFA code and a
backup code in plaintext, so treat it as a recovery-code file, not merely a token.

---

## 4. Model independence — what changed today, and the live defect

Georgi's Claude subscription ends today. `llm_failover.py` (added today) routes gateway calls to
the **Grok CLI** using the OAuth session in `~/.grok/auth.json`, with Gemini as second fallback.

**Verified working:** Grok CLI 1.0.5, logged in, default model **`grok-4.6`**. A forced-failover run
today produced 23/23 holdings cards with zero key drift vs the Claude result, 153KB report.

### 🔴 But it does NOT fire when the API key is simply absent — audit C2

`llm_gateway.py:147` raises `LLMConfigurationError` when `ANTHROPIC_API_KEY` is unset, and
`:618-624` **deliberately excludes** that class from `_FAILOVER_TRIGGERS`. So `unset
ANTHROPIC_API_KEY` → no failover → **step 3/5 of the daily chain exits 1.**

The exclusion is correct reasoning for refusals and truncations, and wrong for an absent key —
which from tomorrow is the *normal* state, not a misconfiguration. **Do not just add
`LLMConfigurationError` to the tuple**; that would also swallow genuine config errors. Split it
into a `LLMProviderUnconfiguredError` subclass raised only at `:149`.

**Stopgap needing no code change:** leave a syntactically valid but dead key in `.env`. The SDK then
raises `AuthenticationError` — a subclass of `APIStatusError`, which *is* in the tuple and *does*
fail over.

### Model ids

Use **`grok-4.6`**. `grok-build` is **retired** and the CLI rejects it — yet `CLAUDE.md` §"Agent
harness model" and `AGENTS.md` §2 both still name it, in the sections read first. `CLAUDE.md`
contradicts itself about this ~850 lines later. Fix the early ones.

### The other Claude dependency is step 1/5, and it is separate

`fetch_snapshot_direct.py` (tier 1, free, own OAuth, no LLM) is **healthy** — token valid to
**2026-08-28**, self-refreshing. Tier 2 is a `claude -p` courier that will now fail rather than cost
money. `run_daily_analysis.sh:171` is still a bare `echo`, so tier-1 failure is silent (roadmap
P0-3). Also `refresh_dashboard_snapshot.sh:42` calls tier 2 **directly**, skipping tier 1 — so the
30-minute dashboard snapshot job breaks outright (audit H4).

---

## 5. Get it running

```bash
cd ~/Projects/gesh-project
venv/bin/python3 server.py                        # :9000
venv/bin/python3 -m pytest tests/ -q              # 180 files
./venv/bin/python3 smoke_customer_surface.py      # live customer-surface smoke

launchctl list | grep com.gesh
tail -f /tmp/gesh_main.log /tmp/gesh_fullchain.log
```

⚠️ `start_gesh_main.sh:9-10` still does `source venv/bin/activate` — a rule #57 violation in the
launcher of the thing customers pay for, made worse because `venv` here is a **symlink to a shared
venv directory outside the repo** (audit H7).

---

## 6. Non-negotiables

- **Preserve customer/owner isolation.** Never expose owner holdings, cash, account identifiers or
  recommendations to customers. The mask-exempt allowlist (`server.py:652-673`) is short and each
  entry carries a written no-owner-data proof — keep it that way.
- **Never weaken auth, CSRF, Origin, cookie, session, or tenant checks** to make a test pass.
- **Never fabricate a financial value when source data is missing.** Prefer a typed failure over a
  plausible default. This is the most expensive failure class in the whole project.
- **`PAPER_MODE=true`.** Never flip it.
- **Never print, log, or commit secrets.**
- Two docs under `docs/` (`GESH_ATTORNEY_ENGAGEMENT_BRIEF.md`, `GESH_LEGAL_COMPLIANCE_PLAYBOOK.md`)
  are **tracked and already in git history**. If privileged, `git rm --cached` does not undo that —
  it needs a decision from Georgi, not an agent.

---

## 7. Open threads

| Thread | Status |
|---|---|
| Audit C1 (0-byte files), C2 (failover), C3 (dead plists), C4 (pin), C5 (ledger) | Open — do C1 today |
| `/track-record` 403s paying customers — the "#1 sales asset" | Open, audit H2 |
| `com.gesh.signal-alerts` + `weekly-digest` have **never sent a message** | Fix is in the repo, never bootstrapped — audit C3 |
| Attorney review of ToS/Privacy (both still DRAFT) | Roadmap P0-1, open |
| Error tracking (no Sentry anywhere) | Roadmap P0-4, open |
| Four stale root doc duplicates shadow `docs/` copies | Root defect register has **no status column** → reads as 38 open defects |
| 55 of 124 routes have no test — incl. `/billing/success` | Open |

---

*NOT FINANCIAL ADVICE. `PAPER_MODE=true` by default. Never flip to live execution.*
