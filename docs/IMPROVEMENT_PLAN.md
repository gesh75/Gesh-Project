# GESH Improvement Plan (pointer)

The **unified, reconciled improvement plan** covering BOTH repos (Claude review + Gemini
audit + reconciliation, with adopt/defer/reject decisions and a phased roadmap) lives in
the engine repo:

> `gesh-project-trading/docs/IMPROVEMENT_PLAN.md`

Product-repo (:9000) items in that plan: `_safe_float` downstream guard, `datetime.utcnow()`
sweep, SQLite WAL hardening, the deferred OAuth2/Postgres calls, and the shared-package
(`gesh-core`) extraction that removes this repo's duplicate-module hazard. Read it with
`docs/AUDIT_GUIDE.md` and `CLAUDE.md` before implementing.
