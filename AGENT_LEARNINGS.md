# AGENT_LEARNINGS

Repo-only memory for `gesh-project`. Cross-repo lessons stay in `~/.cursor/AGENT_LEARNINGS.md`. Read this before substantive work in this checkout. Append one entry after a verified mistake. No secrets, customer lists, or position sizes.

## Entries

### [2026-09-27] [auth] Logout and the next sign-in
- **Mistake:** Logout only deleted the cookie, so the 7-day token still worked. Creating `revoked_sessions` before the schema-v2 ready check made an empty database look pre-v2 and the backup check failed. Clearing the cookie when the blacklist write failed left the copied token alive.
- **Avoid:** A new auth table before `_schema_v2_ready`. `delete_cookie` without the same Secure, HttpOnly, SameSite, and path used at login. A browser test that signs in as the owner, because `rotate_session` retires that user's other sessions.
- **Instead:** Blacklist the `jti` only after v2 is ready. On a failed revoke return 503 and keep the cookie. Password login and magic-link sign-in call `rotate_session`. Browser tests use `georgi-test@gesh.local`.

### [2026-09-27] [desk] The book had no way to start a read
- **Mistake:** `mybookAnalyze` existed, and a test forbade a Home button, so a book of shares had no control that started the portfolio read. Research is one ticker. "Pick a name to research" was the hero.
- **Avoid:** Treating the Research tab as the portfolio action. Calling `/api/mybook/analyze` during a look-around unless the user asked to run it. Clicking Clear, Delete account, or Sign out on that walk.
- **Instead:** An explicit Analyze my portfolio button under the holdings. Hero target `portfolio` only scrolls to that button. Clear uses `btn-danger`. The consensus chip is labeled 11-node, with a line that one card below can disagree. ★ Save sits on the ticker card.

### [2026-09-27] [report] Dark type and a fake zero
- **Mistake:** The report set `color:#111` and no background, so dark mode hid the text. A book whose quotes had not loaded rendered as Total $0.00 with no rows. Model sector mix and radar groups were shown as one percentage.
- **Avoid:** A print page without `background:#fff` and `color-scheme:light`. Calling an unpriced book $0.
- **Instead:** A light document. "Total: not available" plus the saved symbols when every quote is missing. Label model sector mix and radar groups as different lists. A swap that stays in the same radar group must say it does not shrink that group's weight. The saved sentence changes only on the next portfolio read.

### [2026-09-27] [ci] Landing on the live checkout
- **Mistake:** A draft pull request with green required checks looked stuck. Squash-merge would break `git pull --ff-only` on this launchd tree.
- **Avoid:** `gh pr merge --squash` or `--admin`. Treating a non-required red check as a merge blocker. Bouncing `com.gesh.main` when the process is already running the merged `server.py`.
- **Instead:** Wait for `test` and `customer-isolation`. `gh pr merge N --merge`. Fast-forward live `main`. Bounce only when the running process is still the old Python.
