---
name: operating-gesh-pay-marketing
description: Use when touching the pay.geshfinance.com email signup (double opt-in, marketing_capture.py, gesh_marketing.db), the weekly learning note sender (weekly_note.py, docs/weekly_notes, com.gesh.weekly-note plist), unsubscribe links, the privacy-policy signup paragraph, or before mailing the subscriber list.
---

# Operating GESH pay marketing mail

Repo `~/Projects/gesh-project` (FastAPI, live on `com.gesh.main` at 127.0.0.1:9000, cloudflared to pay.geshfinance.com). Always work in a worktree under `~/Projects/.worktrees/` and fast-forward the live checkout after the PR merges. Test with the live venv: `~/Projects/gesh-project/venv/bin/python -m pytest`.

Promise made on the signup form: **confirmed address only, at most weekly, one-click unsubscribe**. Deterministic code keeps that promise. Prose never does.

## Signup (merged in PR #335)

- Double opt-in. Own SQLite file `gesh_marketing.db` (`GESH_MARKETING_DB` overrides), tables `marketing_subscribers` and `marketing_rate_events`. It is in `ops_backup.py`.
- HMAC tokens from `GESH_AUTH_SECRET` via `issue_token(sub, typ, ttl)` / `verify_token`. Types `mkt_confirm` (7 days) and `mkt_unsub` (365 days).
- GET pages are interstitials and never mutate. POST changes state.
- Rate limits: 5 per IP per hour, 3 mails per address per day with a 15-minute cooldown, 300 per day globally.
- Public paths are an exact allowlist in `auth_users.PUBLIC_EXACT_PATHS`: `/api/marketing/subscribe`, `/marketing/subscribe`, `/marketing/confirm`, `/marketing/unsubscribe`. A new public path must be added there with a test.
- Send with `email_sender.send_email(..., log_recipient=False)` so addresses never reach logs.
- `docs/GESH_PRIVACY_POLICY_DRAFT.md` and the live `/privacy` page carry the "Weekly learning note signup" paragraph. If you change what is collected or how often it is mailed, change that paragraph in the same PR.

## Weekly note sender (merged in PR #337)

Files: `weekly_note.py`, `scripts/send_weekly_note.py`, `run_weekly_note.sh`, `launchd/com.gesh.weekly-note.plist`, `docs/weekly_notes/NNN-slug.md`, `tests/test_weekly_note.py`.

Guards, all in code:

- Dry run unless `--send`.
- Confirmed subscribers only; unsubscribed get nothing.
- One note per subscriber per 6 days (`MIN_GAP_SECONDS`).
- Never the same note twice: unique `(sub_id, note_id)` row in `marketing_note_sends`. **Insert the row before calling the provider; delete it on failure or exception.** A crash between "sent" and "recorded" would otherwise mail someone twice.
- Window decided in code, America/New_York, Tuesday to Thursday, 10:00-14:59. `--ignore-window` is for manual tests only.
- Notes need front matter `status: ready`. `draft` only sends with `--include-drafts`.
- Whole-list sends are refused unless `GESH_MAIL_POSTAL_ADDRESS` is set. Single-address `--only-email` tests are allowed.
- `--limit` defaults to 50 recipients per run.
- Each email gets its own unsubscribe token, `List-Unsubscribe` and one-click headers, the disclaimer, and escaped body text. Only `https://pay.geshfinance.com/...` URLs become links.
- Output prints counts, never addresses.

### Add a note

1. New file `docs/weekly_notes/NNN-slug.md` with front matter `id`, `subject`, `status: draft`.
2. Educational only. No picks, no returns promised, hypothetical numbers labelled. Recompute every figure by hand or script before writing it.
3. Link only to pay.geshfinance.com pages.
4. `tests/test_weekly_note.py` has a "shipped notes parse" test; run it.
5. Flip to `ready` only when the owner approves the text.

### Test send (owner only)

```bash
cd ~/Projects/gesh-project
OWNER_EMAIL=the-owner-address   # set in your shell; never commit it
./venv/bin/python3 scripts/send_weekly_note.py --only-email "$OWNER_EMAIL" --ignore-window          # dry run, expect eligible=1
./venv/bin/python3 scripts/send_weekly_note.py --send --only-email "$OWNER_EMAIL" --ignore-window    # real
```

Verify with the Gmail MCP (read only): subject, sender, one unsubscribe link, disclaimer, working pay link. The owner address must be a confirmed subscriber first.

## Scheduler: keep uninstalled until the owner says so

The plist runs hourly (`StartInterval 3600`, `RunAtLoad false`, log `/tmp/gesh_weekly_note.log`) and relies on the in-code window. As of 2026-10-04 it is **not loaded**, by the owner's decision. He will say when to set `GESH_MAIL_POSTAL_ADDRESS` in `.env` and load it. Before loading: dry run without `--only-email` and check the recipient count looks right; census the running agents; then `launchctl bootstrap gui/$(id -u) <plist>`. Unloading: `launchctl bootout gui/$(id -u)/com.gesh.weekly-note`.

## Gotchas

- Cloudflare blocks the python-urllib UA. Probe live pages with `curl -A "Mozilla/5.0"`.
- Pre-commit runs gitleaks. A deliberately public token (IndexNow key) needs an inline `gitleaks:allow` with a reason. Commit alone, then `git log --oneline -1` before pushing; a rejected commit plus a quiet push looks like success.
- `tests/test_gaps_hardening_2026_08_04.py::test_ops_backup_targets_are_repo_relative_not_hardcoded_users` fails inside a worktree for path reasons only; it passes in the live checkout.
- Pay CI takes about 5 minutes (test, customer-isolation, pr-lint, security). Merge with `gh pr merge N --merge`.
- Never mail the list from chat or an agent turn without the owner's explicit approval.

## Excuses

| Excuse | Reality |
|---|---|
| "Just send note 001 to everyone to test" | Test sends go to the owner address only. |
| "Load the plist, it's harmless" | Owner said: keep it uninstalled. |
| "Record the send after the provider returns" | Crash window mails twice. Insert first. |
| "Skip the postal address for now" | The sender refuses; the law needs it. |
| "Add the note as ready, owner can read later" | Draft until approved. |
