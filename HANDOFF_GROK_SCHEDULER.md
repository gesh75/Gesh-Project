# HANDOFF → Grok: the scheduler layer

> Rewritten **2026-08-22** after the cutover ACT. The 2026-08-21 20:30 EDT Claude draft
> is **stale** — do not follow its §4 commit block or its “still open” table.
>
> Repo handoffs: `HANDOFF_GROK.md` (product, :9000) and
> `../gesh-project-trading/HANDOFF_GROK.md` (engine, :9003).
> **This file covers the clock.**
>
> Authority order: live code > `CLAUDE.md` > the two repo handoffs > this file.

---

## 0. The short version

Four Cowork cards were Claude agent sessions bound to this Mac. They die with the
subscription. **launchd does not.**

Layer 1 (trades, scans, prices, writes DBs) is launchd + Python, no model, and was
already healthy. Layer 2 was narration/verification. As of 2026-08-22 those four
cards are LaunchAgents on this Mac:

| Cowork card | LaunchAgent | ET window | Model? |
|---|---|---|---|
| Gesh crypto analysis | `com.gesh.crypto-reader` | 02/06/14/18/22 | **no** — stdlib reader + Telegram |
| Gesh autopilot daily | `com.gesh.autopilot-health` | weekdays 08:25 | **no** — kickstart + Telegram only if down |
| Gesh daily portfolio analysis | `com.gesh.daily-analysis` (chain) + `com.gesh.daily-report` + `com.gesh.daily-narrate` | 10:30 chain, 10:45 HTML report, 10:50 briefing | Grok **last mile + step 3/5 failover**, never fatal on narration |
| Gesh weekly tracker review | native Fri 09:00 autopilot job + `com.gesh.weekly-narrate` | Fri 10:00 briefing | Grok **last mile only**, never fatal |

The C2 missing-key failover **is committed** (`263472f`, PR #239). The PATH hole that hid
the Grok CLI from launchd **is committed** (`8da2dca`). Dashboard snapshot H4 **is fixed**
(`refresh_dashboard_snapshot.sh` prefers `fetch_snapshot_direct.py`).

Monday risk that is still owner-side: **unrotated `ROBINHOOD_PASS`** (audit transcript)
and merging PR #239 so `main` matches this checkout.

---

## 1. Verified host state (2026-08-22, post-cutover)

| Component | State |
|---|---|
| `com.gesh.trading.autopilot` | **running** — pid 1026, last exit 0 |
| `com.gesh.crypto.scheduler` | **running** — pid 959, last exit 0 |
| `com.gesh.daily-analysis` | loaded, last exit 0 (`StartInterval 600` + ET gate) |
| `com.gesh.crypto-reader` | loaded, last exit 0 |
| `com.gesh.autopilot-health` | loaded, last exit 0 |
| `com.gesh.daily-report` | loaded — 10:45 ET `gesh_report.html` publish/retry |
| `com.gesh.daily-narrate` | loaded, last exit 0 |
| `com.gesh.weekly-narrate` | loaded, last exit 0 |
| `com.gesh.dashboard-snapshot` | loaded, last exit 0 |
| `com.gesh.signal-alerts` / `weekly-digest` | loaded, last exit 0, `StartInterval 1800` |
| Grok CLI | `~/.local/bin/grok`, logged in, default **`grok-4.6`** (also `grok-4.5`) |
| C2 on this checkout | `LLMProviderUnconfiguredError` is in `_FAILOVER_TRIGGERS` |
| Product git | `feat/rh-mcp-data-layer` @ `07c3899` (scheduler commit). PR #239 HEAD is `e687442`. **Do not push this branch onto #239** — local also has `2b64100` / `d57fc29` from another session. |
| Trading git | local `main` @ `90c1ae2`, on [PR #127](https://github.com/gesh75/gesh-project-trading/pull/127) |

~34 `com.gesh.*` LaunchAgents are installed. The table above is the cutover set.
Do not audit the rest as part of this file.

---

## 2. Two layers — crossing point is still step 3/5

```
LAYER 1 — HOST / DETERMINISTIC          ← model-free. Survives without Claude.
  launchd  →  bash  →  Python
    com.gesh.crypto.scheduler   KeepAlive, 4h     → crypto.db, crypto_financial.db
    com.gesh.trading.autopilot  KeepAlive, 14 jobs → gesh_tracker.db
    com.gesh.daily-analysis     StartInterval 600 + ET gate → run_daily_analysis.sh
                                                    ↑ step 3/5 is the ONLY LLM call in layer 1
    com.gesh.daily-report       StartInterval 600 + 10:45 ET gate → gesh_report.html
                                                    (wait on 10:30 lock; retry chain if no today's report)

LAYER 2 — NARRATION / HEALTH            ← was Cowork; now launchd (2026-08-22)
    com.gesh.crypto-reader       stdlib reader --telegram
    com.gesh.autopilot-health    kickstart if down
    com.gesh.daily-report        gesh_report.html publish / chain retry
    com.gesh.daily-narrate       grok --prompt-file (best-effort)
    com.gesh.weekly-narrate      grok --prompt-file (best-effort)
```

Step 3/5 (`run_ai2.py` → `llm_gateway` / `llm_failover`) already routes to Grok.
It is not a Cowork task and does not need re-plumbing. If Anthropic is unset,
`LLMProviderUnconfiguredError` fails over. If that class is missing, the dead-key
stopgap in §7 still works.

---

## 3. The four tasks — current wiring

Every new plist is **`StartInterval 600` with no `StartCalendarInterval`** (rule #56).
launchd here is Europe/Sofia; the script decides in `America/New_York` via
`../gesh-project-trading/scripts/et_gate.sh`.

### 3.1 Gesh crypto analysis — **no model**

| | |
|---|---|
| LaunchAgent | `com.gesh.crypto-reader` |
| Script | `../gesh-project-trading/scripts/crypto_reader_et.sh` |
| Slots | `02:00,06:00,14:00,18:00,22:00` ET ±25 min, `et_gate_require_slots` (per-slot stamp) |
| Does | `/opt/homebrew/bin/python3 crypto_report_reader.py --telegram` |
| Log | `/tmp/gesh_crypto_reader.log` |
| Heavy pipeline | still `com.gesh.crypto.scheduler` — **do not** run `crypto_scheduler.py` from an agent |

### 3.2 Gesh autopilot daily — **no model**

| | |
|---|---|
| LaunchAgent | `com.gesh.autopilot-health` |
| Script | `../gesh-project-trading/scripts/autopilot_health_et.sh` |
| Window | weekdays 08:25 ET ±20 min |
| Does | if `com.gesh.trading.autopilot` is missing or not running → bootstrap/kickstart + Telegram. Healthy days are silent. |
| Log | `/tmp/gesh_autopilot_health.log` |

### 3.3 Gesh daily portfolio analysis — **chain is layer 1; Grok narrates**

| | |
|---|---|
| Chain | `com.gesh.daily-analysis` → `scripts/daily_analysis_et.sh` → `run_daily_analysis.sh` (10:30 ET weekdays) |
| HTML report | `com.gesh.daily-report` → `scripts/daily_report_et.sh` (10:45 ET weekdays) — waits for the 10:30 lock, Telegrams if `gesh_report.html` is today's ≥60KB, otherwise reruns the chain |
| Narration | `com.gesh.daily-narrate` → `scripts/narrate_daily_et.sh` (10:50 ET weekdays) — does **not** stamp until today's report exists |
| Report log | `/tmp/gesh_daily_report.log` |
| Narration log | `/tmp/gesh_daily_narrate.log` |
| Contract | `.claude/skills/daily-portfolio-analysis/SKILL.md` — inlined into the grok prompt, not discovered from `~/.grok/skills/` |

Step 1/5 snapshot tiers:

| Tier | Path | Monday |
|---|---|---|
| 1 | `fetch_snapshot_direct.py` — LLM-free MCP | **primary** |
| 2 | `fetch_snapshot_mcp.sh` — `claude -p` | dead weight; burns timeout if tier 1 fails |
| 3 | `fetch_live_snapshot.py` — robin_stocks pickle | backstop |

`refresh_dashboard_snapshot.sh` **used to call tier 2 only**. As of 2026-08-22 it prefers
tier 1 (`venv/bin/python`, not host 3.9). That was audit H4.

Grok narration is **best-effort**: a CLI failure logs and exits 0. The HTML report
still exists if Grok is down.

### 3.4 Gesh weekly tracker review — **same pattern**

| | |
|---|---|
| Layer 1 | autopilot job `weekly_tracker_review` Fri 09:00 → `tracker_weekly_log.md` + Telegram |
| Narration | `com.gesh.weekly-narrate` Fri 10:00 ET ±20 → `scripts/narrate_weekly_et.sh` |
| Log | `/tmp/gesh_weekly_narrate.log` |

The ⚠️ markers on the Cowork cards were agent-layer (Mac asleep / device-bound).
They do not mean the host jobs were down. launchd still coalesces a missed
`StartInterval` fire after sleep — expected, not a defect.

---

## 4. Do not re-run Friday's commit block

The 2026-08-21 “stage these 21 files tonight” list is **done**. C2 lives on PR #239.
Re-running that `git add` on this dirty tree would sweep generated HTML and another
session's CVaR/Gemini commits (`2b64100`, `d57fc29`) into the harvest PR.

**Stage explicit paths. Never `git add -A`.**

---

## 5. Driving Grok headless on this Mac

Failover path (already in `llm_failover.py::call_grok`) — cwd is a **temp dir**:

```bash
grok --prompt-file /tmp/prompt.txt \
     --verbatim \
     --output-format json \
     --max-turns 4 \
     --tools read_file \
     --rules "Answer using ONLY the text of this prompt. Do not call any tool, MCP, or memory." \
     --no-auto-update \
     --model grok-4.6
```

Narration jobs (`narrate_daily_et.sh`, `narrate_weekly_et.sh`) copy that shape but
**cwd is the repo** so `read_file` can see `engine_results.json` / `tracker_weekly_log.md`.
They use `--max-turns 4` and a read-only `--rules` override.

Load-bearing:

1. **`--prompt-file`, not argv** — ARG_MAX.
2. **`--max-turns` + `--rules`** — without them the CLI inherits `~/.claude/CLAUDE.md` and
   starts an agent loop (OMEGA, etc.).
3. Failover cwd = temp; narration cwd = repo. Do not copy one onto the other.
4. JSON envelope: answer is under **`text`**. `result` is often `null`.
5. Model ids rotate. `call_grok` retries on `unknown model id`. Check `grok models`.

| | |
|---|---|
| `~/.grok/config.toml` | default `grok-4.6`, `default_reasoning_effort = "xhigh"` |
| `permission_mode` | **`always-approve`** — fine interactively; narration jobs constrain via `--tools` + `--rules` |
| `~/.grok/trusted_folders.toml` | `/Users/georgigaydarov/Projects` |
| `~/.grok/skills/` | **empty** — inline the skill text into the prompt (already done in the two narrate scripts) |

---

## 6. Git working agreement

- **Never `git add -A`.** Jobs write `PROGRESS_LOG.md`, `tracker_weekly_log.md`,
  `GESH_COMMERCIAL_ROADMAP.md`, `.crypto_cache.json`, generated HTML.
- Product **local `main`** still has three commits not on `origin/main`: `40e7195`,
  `bfd23f0`, `383cf92`. **Do not cherry-pick `40e7195`** — it patches `_create_streaming`
  in `ai_analyzer.py`, which the failover refactor removed. Intent is in
  `llm_gateway._is_retryable` (includes `httpx.ReadTimeout`). Do not `git reset --hard` local `main`.
- Product `stash@{0}` on `main`: *"Scanner owner-only + margin panel"* — unapplied, security-relevant.
- Worktrees of this repo, not copies: re-run `git worktree list` before editing an unfamiliar
  directory. Do not treat a sandbox `prunable` marker as fact.
- `gesh-core` is pinned by **40-char SHA**. Trading PR #127 bumped the declared pin to
  `4931239` (v0.8.1), matching product. Live venvs were already there.
- Trading **pytest** still runs on push-to-`main` only (secret-bearing). PR #127 added a
  secret-free whole-repo parse gate to the required `pr-test` check. Do not add
  `GESH_CORE_PAT` to `pull_request`.

---

## 7. Monday 2026-08-24 runbook

Stay on checkout `~/Projects/gesh-project` branch `feat/rh-mcp-data-layer` so launchd
sees C2 + harvest + snapshot fix. Switching this tree to another branch changes what
the agents execute.

Before market open:

```bash
grok models
cd ~/Projects/gesh-project
env -u ANTHROPIC_API_KEY ./venv/bin/python -c \
  "import llm_failover as f; print(f.grok_available(), f.call_grok('Reply exactly: OK'))"
launchctl list | grep -E 'crypto.scheduler|crypto-reader|trading.autopilot|autopilot-health|daily-analysis|daily-report|daily-narrate|weekly-narrate|dashboard-snapshot'
```

Watch:

1. **08:25** — `/tmp/gesh_autopilot_health.log` (`HEALTHY — no action` is the good line).
2. **~10:30** — `tail -f ~/Projects/gesh-project/.logs/gesh_fullchain.log`. **Step 3/5 is
   the migration test.** Survives with no Anthropic key → cutover worked.
3. **~10:45** — `/tmp/gesh_daily_report.log` (`today's gesh_report.html is ready` or a chain retry).
4. **~10:50** — `/tmp/gesh_daily_narrate.log` (best-effort; a grok fail is not a chain fail).
5. **13:35 / 17:35** (and 02/06/22) — `/tmp/gesh_crypto_reader.log`.

If step 3/5 exits 1: `grok_available()` first. Dead `~/.grok/auth.json` drops to Gemini.

**Stopgap if C2 is somehow not on the checkout:** leave a syntactically valid but dead
`ANTHROPIC_API_KEY` in `.env`. The SDK raises `AuthenticationError` ⊂ `APIStatusError`,
which has always been in `_FAILOVER_TRIGGERS`.

---

## 8. Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Step 3/5 exits 1, `LLMConfigurationError` | C2 not on this checkout | Stay on `feat/rh-mcp-data-layer`, or dead-key stopgap |
| Step 3/5 OK but briefing thin/missing | Grok auth or narrate script | `grok` once interactively; `/tmp/gesh_daily_narrate.log` |
| `unknown model id` | retired pin | `grok models`; `call_grok` self-heals to CLI default |
| Chain at 4pm, off-hours prices | launchd coalesced a sleep-missed 10:30 | Intended. Trust the report timestamp |
| Crypto Telegram empty | ran `crypto_scheduler.py` instead of the reader | Reader only |
| Step 1/5 slow then DEGRADED | tier 2 `claude -p` timeout on every retry | Cosmetic; tier 1 is primary. Retire tier 2 later |
| Dashboard snapshot stale | `.rh_mcp_oauth.json` expired | `./venv/bin/python fetch_snapshot_direct.py --auth` |
| Autopilot Telegram at 08:25 | pid `-` or last_exit ≠ 0 | `launchctl kickstart -k gui/$(id -u)/com.gesh.trading.autopilot` |

---

## 9. Still open (owner)

| Item | State |
|---|---|
| Merge [PR #239](https://github.com/gesh75/Gesh-Project/pull/239) (harvest + C2 + MCP) | Open, CI green. Local branch has extra commits — peel before pushing |
| Merge [PR #127](https://github.com/gesh75/gesh-project-trading/pull/127) (handoff + parse gate + pin + scheduler jobs) | Open |
| Rotate `ROBINHOOD_PASS` | P0-1, owner |
| Retire step 1/5 tier 2 (`claude -p`) so a tier-1 miss does not burn 4 timeouts | later |
| Standing objective — **23 / 100 engine closes**, gate FAIL, late Oct 2026 | outranks everything in the engine repo |

---

*NOT FINANCIAL ADVICE. `PAPER_MODE=true`, `auto_trade=False`. Never flip either.*
