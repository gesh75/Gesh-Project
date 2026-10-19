# AGENT_LEARNINGS

Repo-only memory for `gesh-project`. Cross-repo lessons stay in `~/.cursor/AGENT_LEARNINGS.md`. Read this before substantive work in this checkout. Append one entry after a verified mistake. No secrets, customer lists, or position sizes.

## Entries

### [2026-10-19] [server] A new import above `llm_gateway` fails the dotenv probe
- **Mistake:** `import gesh_exit_advice` was placed at the top of `server.py`, ahead of `import llm_gateway`. CI then failed `test_server_env_bootstrap`: that test copies only `server.py` and a stub `llm_gateway.py`, so the new import raised `ModuleNotFoundError` before the probe could run. The same route was also absent from the customer leak-sweep lists, so `/api/exit-advice` failed `test_all_customer_gets_categorized`. The local pre-push gate reported green because its pytest warning path was the live checkout, which did not contain the new route.
- **Avoid:** Adding any local import above `llm_gateway` in `server.py`. Adding a `/api/` GET without a `COVERED` or `EXCLUDED` row in `tests/test_customer_leak_sweep.py`. Trusting the pre-push isolation count when it was collected from a different checkout.
- **Instead:** Import the owner-only helper inside the route. Exclude the route in the leak sweep with the test that locks the customer 403. Run `tests/test_server_env_bootstrap.py` and `tests/test_customer_leak_sweep.py::test_all_customer_gets_categorized` from the worktree that holds the edit.

### [2026-10-03] [seo] A baseline run is only a baseline if nothing moves under it
- **Mistake:** Started the origin/main pytest baseline inside the worktree, then kept editing; a later `git archive` copy had no `.git`, so a `git ls-files` test failed there while the real tree passed. On macOS `sed -i 's/a/b/' file` also aborted a `&&` chain (BSD sed needs `-i ''`).
- **Avoid:** Editing the tree a background pytest is importing from. Reading a failure count from a scratch copy without checking it is a git repo. A path under `/Users/` trips `test_ops_backup_targets_are_repo_relative_not_hardcoded_users` in any worktree.
- **Instead:** Baseline from a frozen copy that is a git repo (or `git worktree add` outside the work area), and compare failing test names, not counts. Use a python one-liner for edits instead of `sed -i`.

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
