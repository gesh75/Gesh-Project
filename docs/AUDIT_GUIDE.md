# GESH Audit Guide (pointer)

The **canonical multi-agent audit playbook** covering BOTH repos lives in the engine repo:

> `gesh-project-trading/docs/AUDIT_GUIDE.md`

It includes the repo map, environment setup, ordered audit phases with exact commands,
must-not-break invariants, known pre-existing test failures, the reporting format, and the
prioritized improvement backlog. Read it (and this repo's `CLAUDE.md`) before auditing.

Product-repo (port 9000) specifics to audit are listed there under Phases 3–5; this repo's
duplicate modules must stay in sync with the engine repo (rules #37/#40/#43).
