# GESH Ops Layer — ~/.gesh

This directory contains the durable, Claude-independent monitoring and verification scripts that keep the system reliable even when the laptop sleeps, Claude restarts, or the main process is down.

## Contents (as of 2026-07-09)

- `gesh_gatewatch.sh` — Watches closed trades count via `tracker.check_autonomy_gate`. Notifies on progress toward the 30-trade autonomy gate. Uses state markers to avoid spam.
- `gs_gapwatch.sh` — Tiered proximity watcher for the open "GS" position stop-loss. Escalates (approaching / about to trip / crossed) using live `paper_positions` stop from DB + yfinance pre/post prices. Stands itself down when position closes.
- `morning_allgood.sh` — One-shot (self-destructing) morning status for a specific date. Checks funnel progress + daily chain log.
- `firstprod_verify.*` — Verification artifacts from first production-like runs.
- Various `.log` and `.out` files from the watchers.

## How They Run
- Launched via macOS `launchd` plists (labels like `com.gesh.gatewatch`, `com.gesh.gs-gapwatch`).
- Run from the gesh-project-trading venv (`python3.14`).
- Persistent location (`~/.gesh` not `/tmp`) so they survive restarts (a previous `/tmp` version was wiped).

## Portability (Improved 2026-07-09)
The two main watchers now honor environment variables:
```bash
export GESH_TRADING_DIR=~/Projects/gesh-project-trading
export GESH_PYTHON="$GESH_TRADING_DIR/venv/bin/python3"
```

## Current Health (example from logs)
- Autonomy closed trades progressing (e.g. 16/30 recently logged).
- GS position had buffer in samples.
- No active `~/.gesh/KILL`.

## Related Code
- `trading/tracker.py` (autonomy gate logic)
- `trading/state.py` (markers)
- DB tables: `paper_positions`, `paper_outcomes`

## Additional Layer: Claude Scheduled Skills (Documents/Claude/Scheduled/gesh-*)
From the final broad scans, these surfaced as another automation mechanism (dirs containing SKILL.md, scheduled via Claude tooling):

- **gesh-autopilot-daily**: Pre-market (~8:25 ET) health check of the native `com.gesh.trading.autopilot` LaunchAgent. Uses osascript to inspect launchctl + /tmp/gesh_autopilot.log, restarts once if down, sends Telegram alert only on confirmed failure. Stays silent on healthy.
- **gesh-daily-portfolio-analysis**: Supervisor for the host `run_daily_analysis.sh` 5-step chain. Verifies via /tmp/gesh_fullchain.log + DB (analysis_runs), triggers if needed (carefully, respecting locks), loads JSON results, produces a concise narrated briefing + action plan table. Strict rules: never re-run engines or hand-edit reports.
- **gesh-weekly-tracker-review**: Friday review on top of the native tracker job. Consumes weekly_snapshots, pulls current `check_autonomy_gate()`, handles flat weeks, writes to tracker_weekly_log.md.
- **gesh-crypto-analysis**: Crypto counterpart.

These complement (and sometimes supervise) the native launchd + ~/.gesh shells. They run in a Claude/MCP environment that can reach the host via osascript and mounted paths.

## Recommendations for Best Product
- Document the full hybrid surface (native launchd + ~/.gesh + Claude scheduled skills + host chain) in one place — this file + GAPS_AND_IMPROVEMENTS is a start.
- Generalize `gs_gapwatch.sh` to watch any open positions near their stops (config-driven).
- Add basic error trapping / log rotation to the shell watchers.
- Surface unified status (last chain run, gate, watcher health) via an endpoint on the main servers.
- Consider whether to converge on one primary scheduler (e.g. make the Claude skills the orchestration layer with the host chain as the pure compute, or vice versa) to reduce fragmentation.
- Move critical ops scripts into the repo under `scripts/ops/` for versioning and easier setup.

See also: `GAPS_AND_IMPROVEMENTS_2026-07-09.md` (full footprint + automation notes), the individual SKILL.md files in Documents/Claude/Scheduled/, watcher scripts in ~/.gesh, launchd plists, and `gesh-project/run_daily_analysis.sh`.
