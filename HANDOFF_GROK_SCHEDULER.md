# HANDOFF → Grok: the scheduler layer

> Written 2026-08-21, 20:30 EDT — the last Claude day. Every fact below was verified by
> running the command on this Mac, not copied from a doc.
>
> The two existing handoffs cover the **repos**:
> `HANDOFF_GROK.md` (product, :9000) and `../gesh-project-trading/HANDOFF_GROK.md` (engine, :9003).
> **This file covers the clock** — the four scheduled tasks, and how Grok replaces them.
>
> Authority order: live code > `CLAUDE.md` > the two repo handoffs > this file.

---

## 0. The short version

Four Cowork scheduled tasks run as **Claude agent sessions bound to this Mac**. They stop
when the subscription lapses. **launchd does not.**

Everything that actually trades, scans, prices, and writes to the databases is launchd +
Python and contains no model call — it keeps running tomorrow with nothing changed. What
you lose is the *narration and verification* layer sitting on top, and only two of the four
tasks genuinely need a model to do their job. The other two are a file reader and a health
check wearing an LLM costume; they should become plain launchd jobs and never touch a model
again.

The Grok failover-on-absent-key fix **is committed** (`263472f` on `feat/rh-mcp-data-layer`).
The scheduled PATH hole that hid the Grok CLI is also committed (`8da2dca`). Gemini's
retired `gemini-2.5-pro` pin was replaced 2026-08-22 (`GESH_GEMINI_FAILOVER_MODEL`, default
Flash, 404→lite). What can still break Monday: **unrotated `ROBINHOOD_PASS`** (exposed in
the 2026-08-21 audit transcript) and a `:9000` process that predates the rule-#57 launcher
fix — restart `com.gesh.main` after the launcher commit lands.

---

## 1. Verified host state at handover

Local time at audit: **Fri 2026-08-21 20:14 EDT**.

| Component | State |
|---|---|
| `com.gesh.trading.autopilot` | **healthy** — last job 18:02:25, "executed successfully", next 18:00 Fri |
| `com.gesh.crypto.scheduler` | **healthy** — 4h cycle completed 17:36:02 in 29.3s, **Errors: 0**, next 21:35 |
| `com.gesh.daily-analysis` | **healthy** — `StartInterval 600` + ET gate, log written 20:04 |
| Grok CLI | **1.0.5** at `~/.local/bin/grok`, logged in to grok.com, default **`grok-4.6`** |
| Grok failover, key absent | ✅ **verified live** — `env -u ANTHROPIC_API_KEY … call_grok()` returned `FAILOVER_OK` |
| `tests/test_llm_failover.py` | ✅ **25 passed** in 9.52s |
| `ruff --isolated` on gateway+failover | ✅ clean |
| Audit C1 (0-byte tracked files) | ✅ **resolved** — both restored 23:59 (44,296 and 17,603 bytes) |
| `gesh-project` git | `feat/rh-mcp-data-layer`, **2 ahead / 0 behind** `origin/main`, **25 dirty entries** |

**29 `com.gesh.*` LaunchAgents are loaded.** The three above are the ones the scheduled
tasks touch. Do not audit the other twenty-six as part of this cutover.

---

## 2. Two layers, and only one of them is Claude's

```
LAYER 1 — HOST / DETERMINISTIC          ← model-free. Survives tomorrow untouched.
  launchd  →  bash  →  Python
    com.gesh.crypto.scheduler   KeepAlive, APScheduler, 4h    → crypto.db, crypto_financial.db
    com.gesh.trading.autopilot  KeepAlive, APScheduler, 14 jobs → gesh_tracker.db
    com.gesh.daily-analysis     StartInterval 600 + ET gate   → run_daily_analysis.sh (5 steps)
                                                    ↑ step 3/5 is the ONLY LLM call in layer 1

LAYER 2 — NARRATION / VERIFICATION      ← Claude agent sessions. Dies with the subscription.
    4 Cowork scheduled tasks, device-bound, read layer 1's output and explain it
```

The single crossing point between the layers is **step 3/5 of the daily chain**
(`run_ai2.py`, which imports `llm_gateway` and `llm_failover`). That call already routes to
Grok. It is not part of layer 2 and does not need re-plumbing.

---

## 3. The four tasks — what each really is

### 3.1 Gesh crypto analysis — **needs no model at all**

| | |
|---|---|
| Schedule | 02:00, 06:00, 14:00, 18:00, 22:00 daily |
| Contract | `../gesh-project-trading/CRYPTO_READER_SKILL.md` (frontmatter `name: gesh-crypto-analysis`) |
| Does | `python3 crypto_report_reader.py --telegram` — live prices + DB state, then reports |
| Dependencies | **stdlib only**, verified: `argparse json os sqlite3 sys time urllib.request datetime pathlib`. No venv. |

The skill file is explicit that this is a **reader**, not a runner: the LaunchAgent already
did the analysis, and re-running `crypto_scheduler.py` from a sandbox breaks because the
macOS 3.14 `.so` files are not loadable by the sandbox's Linux Python. The task's entire job
is one deterministic command plus a formatted summary.

**Replacement: a launchd job, no model.** The reader already emits the numbers and sends the
Telegram. Wrapping it in an agent adds a failure mode and buys nothing.

### 3.2 Gesh autopilot daily — **needs no model at all**

| | |
|---|---|
| Schedule | Weekdays 08:25 ET |
| Does | Health-check the `com.gesh.trading.autopilot` LaunchAgent; auto-restart + Telegram **only if down** |

This is `launchctl list | grep`, a conditional `kickstart`, and a conditional curl. It is
pure shell logic with a natural-language wrapper. As a launchd job it also gains something
it does not have today: it will run when the agent layer is gone.

**Replacement: bash + launchd, no model.**

### 3.3 Gesh daily portfolio analysis — **model for narration only**

| | |
|---|---|
| Schedule | Weekdays 10:30 ET |
| Contract | account skill `daily-portfolio-analysis` v2.0.0 |
| Does | Triggers `run_daily_analysis.sh`, verifies it, narrates the result. **Never re-runs the engines.** |

The chain is 295 lines and deliberately hostile to improvisation — `caffeinate -i` re-exec,
`ulimit -n 10240`, a lock, a stale-venv guard, `umask 077`. Step 3/5 is fatal on failure and
keeps the previous report rather than writing a bad one. The task's model use is genuinely
just the last mile: read `engine_results.json` and the report, reconcile conflicts per
`references/conflict-resolution.md`, say what to do.

**One Claude dependency lives inside this chain and should be retired.** Step 1/5 fetches the
snapshot in three tiers, with 4 retries at 0 / +15m / +30m / +60m:

| Tier | Path | Tomorrow |
|---|---|---|
| 1 | `fetch_snapshot_direct.py` — direct MCP, **LLM-free, no CLI, no subscription** | ✅ healthy, self-refreshing |
| 2 | `fetch_snapshot_mcp.sh` — `claude -p` at `/opt/homebrew/bin/claude`, OAuth in the **macOS Keychain** | ❌ cannot authenticate |
| 3 | `fetch_live_snapshot.py` — legacy `robin_stocks` pickle | backstop |

Tier 1 is the one that matters and it is fine. Tier 2 becomes dead weight — each failed fetch
will now spend its full timeout on a CLI that cannot log in before falling to tier 3. Failure is
**not** silent: all-tiers-failed sends a `tg_alert`, and `snapshot_state.py` then gates the run
FRESH / SERVICEABLE (degraded, 1440-min limit) / FATAL. Earlier handoffs describe this step as a
bare `echo` with a silent failure mode; that description is stale — the code has moved.

The job that *does* break outright is `refresh_dashboard_snapshot.sh`: it calls tier 2 directly
and skips tier 1 entirely. That is audit H4, and tomorrow it stops being a risk and becomes a
certainty.

**Replacement: launchd runs the chain (it already does), then `grok --prompt-file` narrates.**

### 3.4 Gesh weekly tracker review — **model for narrative only**

| | |
|---|---|
| Schedule | Fridays 10:00 |
| Contract | account skill `weekly-portfolio-review` v2.0.0 |
| Does | Consumes the **9am native `weekly_tracker_review`** autopilot job's snapshot, adds the current-spec gate check and a narrative, short-circuits on a flat week |

`weekly_tracker_review` is one of the 14 jobs registered in `trading/autopilot.py:1433`. It
runs whether or not any agent is watching. The task reads its output.

**Replacement: same pattern as 3.3.**

### A note on the three ⚠️ markers

Three of the four task cards carry a warning marker; **Gesh crypto analysis does not**. I
could not read the task run history from this session, so I will not guess at the cause.
What I can say is that the host chains those three tasks depend on were all healthy at audit
time, so the warnings are far more likely to be about the *agent* runs — a device-bound task
only fires while the Mac is awake and online, and this laptop sleeps. Check each card's run
history before treating any of it as a host problem. Two of the three stop mattering the
moment they become launchd jobs.

---

## 4. Do this tonight, before anything else

**The Grok migration exists only as unstaged edits in a dirty working tree.** 21 modified
files, and scheduled jobs write into this tree while you work.

What is uncommitted right now:

- `llm_gateway.py` — the **C2 fix**: a new `LLMProviderUnconfiguredError(LLMConfigurationError)`
  raised only at the absent-key site and added to `_FAILOVER_TRIGGERS`, while the parent class
  stays out. This is exactly the split the audit prescribed, and it is right: an absent key
  fails over, a malformed setting still fails loudly.
- `tests/test_llm_failover.py` — **+91 lines** covering it. 25 pass.
- `.grok/agents/gesh.md` — `grok-build` → `grok-4.6`. **`grok-build` is retired and the CLI
  rejects it**, so until this is committed the project agent definition is a landmine.
- `.grok/rules/grok-model.md`, `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/gesh-grok-model.mdc` —
  the same correction in the sections read first.
- All 11 `.claude/skills/*/SKILL.md` — one line each.
- `memory-bank/activeContext.md`, two HTML hubs.

```bash
cd ~/Projects/gesh-project
git add llm_gateway.py tests/test_llm_failover.py .grok/ AGENTS.md CLAUDE.md \
        .cursor/rules/gesh-grok-model.mdc .claude/skills/ memory-bank/activeContext.md \
        GESH_FINANCIAL_HUB.html GESH_LEGAL_HUB.html HANDOFF_GROK.md HANDOFF_GROK_SCHEDULER.md
git commit -m "fix(llm): fail over to Grok when ANTHROPIC_API_KEY is absent (audit C2) + grok-4.6 model corrections"
git push -u origin feat/rh-mcp-data-layer
```

**Stage explicit paths. Never `git add -A`** — the four untracked HTML files are generated
reports, and `.claude/settings.local.json` is machine-local.

---

## 5. Driving Grok headless on this Mac

The invocation below is **already in production** in `llm_failover.py::call_grok` and is the
form to copy. Do not invent a new one.

```bash
grok --prompt-file /tmp/prompt.txt \
     --verbatim \
     --output-format json \
     --max-turns 1 \
     --tools read_file \
     --rules "<no tool/MCP/memory calls>" \
     --no-auto-update \
     --model grok-4.6
```

Five things that are load-bearing and non-obvious:

1. **`--prompt-file`, not argv.** A deep-analysis prompt is tens of KB and will hit `ARG_MAX`.
2. **`--max-turns` bounds the loop.** Without it the CLI inherits `~/.claude/CLAUDE.md` global
   agent rules and starts an agent loop instead of returning a completion. Narration jobs need
   more than 1 turn and more tools than `read_file` — raise both deliberately, per job, and keep
   the explicit `--rules` override.
3. **`cwd` is a temp dir** in the failover path, on purpose, so the CLI cannot wander the repo.
   A narration job *does* need the repo as cwd — that is a real difference, not an oversight to
   copy past.
4. **The answer is under the envelope's `text` key.** `result` is `null`.
5. **Model ids rotate.** `call_grok` already retries on the CLI default when it sees
   `unknown model id`. Verify with `grok models` before trusting any doc, this one included.
   Available today: `grok-4.6` (default), `grok-4.5`.

### Grok's environment on this Mac

| | |
|---|---|
| `~/.grok/config.toml` | default `grok-4.6`, `default_reasoning_effort = "xhigh"`, **65 plugins enabled** |
| `permission_mode` | **`always-approve`** (`yolo = false`) |
| `~/.grok/trusted_folders.toml` | `/Users/georgigaydarov/Projects` — covers both repos |
| `~/.grok/skills/` | **empty** |
| `~/.grok/hooks/` | **empty** |

Two gaps worth naming:

**`always-approve` plus a scheduled job is a bad combination.** It is fine interactively. For
anything launchd fires unattended, pass an explicit `--deny` set or a `--sandbox` profile
rather than inheriting blanket approval. `AGENTS.md §4` lists what must never be touched —
production DBs, `.env`, billing, brokerage, LaunchAgents, live snapshots. That list is the
deny list.

**Grok does not auto-discover the skills.** `~/.grok/skills/` is empty, and the 11 project
skills live under `.claude/skills/`, which Grok has no reason to read. `.grok/agents/gesh.md`
tells the agent to read them by path, which works only if the agent complies. For the two
narration jobs, **inline the skill file into the prompt** rather than hoping. The two account
skills those tasks used (`daily-portfolio-analysis`, `weekly-portfolio-review`) are Claude-side
and will not be there tomorrow — their step-by-step bash is what matters and it is reproduced
in the repo skills and in §3 above.

---

## 6. Git working agreement under Grok

- **Never `git add -A`.** Scheduled jobs write `PROGRESS_LOG.md`, `tracker_weekly_log.md`,
  `GESH_COMMERCIAL_ROADMAP.md`, `.crypto_cache.json`, and generated HTML into these trees
  while you work. Stage explicit paths, every time.
- **Three commits exist only on local `main`**, unpushed and on no remote: `40e7195` (a real
  `_create_streaming` mid-stream-stall fix), `bfd23f0` (report fix), `383cf92` (a checkpoint).
  Cherry-pick or push before anyone resets `main`.
- **`stash@{0}` on `main`** — *"Scanner owner-only + margin panel"*, security-relevant, still
  unapplied.
- **Two prunable worktrees.** `git worktree list` before editing an unfamiliar directory:
  `.worktrees/gesh-track-record-scope-hardening` (383cf92, main) and
  `gesh-project-audit-hardening` (c279313, `fix/audit-p0-p1-pr`). Both are worktrees of this
  repo, not copies.
- `gesh-core` is pinned by **40-char SHA, never a tag**. The product is on v0.8.1, the engine
  on v0.8.0 — that mismatch is audit C4, still open.
- `../gesh-project-trading` CI has **no PR trigger** (push-to-main only). A PR there is not
  gated the way one here is.

---

## 7. Monday 2026-08-24 runbook

Before market open:

```bash
grok models                                    # confirm grok-4.6 still exists
cd ~/Projects/gesh-project
env -u ANTHROPIC_API_KEY ./venv/bin/python -c \
  "import llm_failover as f; print(f.grok_available(), f.call_grok('Reply exactly: OK'))"
launchctl list | grep -E 'crypto.scheduler|trading.autopilot|daily-analysis'
```

Then watch, in order:

1. **08:25** — autopilot health. Until it is a launchd job, check it by hand.
2. **~10:30** — `tail -f ~/Projects/gesh-project/.logs/gesh_fullchain.log`. **Step 3/5 is the
   test of the whole migration.** If it survives with no Anthropic key, the cutover worked.
3. **13:35 / 17:35** — crypto reader.

If step 3/5 exits 1 and the C2 commit is in place, the failover did not fire — check
`grok_available()` first; a dead `~/.grok/auth.json` drops silently to Gemini.

**Stopgap needing no code change:** leave a syntactically valid but dead key in `.env`. The
SDK then raises `AuthenticationError`, a subclass of `APIStatusError`, which has always been
in `_FAILOVER_TRIGGERS`. Keep this in your pocket — it makes the chain work even if the C2
commit is somehow lost.

---

## 8. Failure modes, and what each looks like

| Symptom | Cause | Fix |
|---|---|---|
| Step 3/5 exits 1, `LLMConfigurationError` in log | C2 commit lost | Re-apply, or the dead-key stopgap (§7) |
| Step 3/5 succeeds but the narrative is thin | Grok fell through to Gemini | `~/.grok/auth.json` expired — `grok` once interactively to re-auth |
| `unknown model id` | Retired id — `grok-build` still lives in stale docs | `grok models`; `call_grok` self-heals to the CLI default |
| Chain runs at 4pm with off-hours prices | launchd coalesced a 10:30 fire missed during sleep | Intended recovery. The report timestamp shows the real run time. |
| Crypto task reports nothing | Someone ran `crypto_scheduler.py` instead of the reader | Reader only — the scheduler needs the macOS venv |
| Step 1/5 slow, then DEGRADED | Tier 2 (`claude -p`) can no longer authenticate; every retry burns its timeout | Cosmetic Monday, real waste — retire tier 2 |
| 30-min dashboard snapshot **dead** | `refresh_dashboard_snapshot.sh` calls `fetch_snapshot_mcp.sh` (tier 2) directly, skipping tier 1 | Audit H4 — now a certainty, not a risk |

---

## 9. What is still open after this cutover

| Item | Where |
|---|---|
| C2 commit + push | **§4 — tonight** |
| Convert tasks 3.1 and 3.2 to launchd, no model | This file |
| Convert tasks 3.3 and 3.4 to launchd + `grok --prompt-file` | This file |
| C3 — `signal-alerts` + `weekly-digest` have **never sent a message** | Fix is in the repo, never bootstrapped |
| C4 — gesh-core pin mismatch (0.8.1 vs 0.8.0) | Both repos |
| C5 — `strategy_trial_ledger.jsonl` **staged, not yet committed** (585 bytes); losing it makes the Sharpe gate *easier* | Engine repo |
| Standing objective — **23 / 100 engine closes**, gate FAIL, target late Oct 2026 | Outranks everything else in the engine repo |

---

*NOT FINANCIAL ADVICE. `PAPER_MODE=true`, `auto_trade=False`. Never flip either.*
