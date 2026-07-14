# Decision: LT_Macro is display-only by default (voting behind a validated flag)

**Status:** DECIDED — 2026-07-14 (Codex review of PRs #30–#59, finding #5).
Supersedes the open tracking PR #41.

## Context
`LT_Macro` (VIX + CNN Fear&Greed + FINRA margin debt) was wired as a real consensus
node at weight `0.5` (`consensus.py`), applying a **market-wide tilt to every ticker**
uniformly. It has **no walk-forward validation**, and unavailable macro data was being
cast as a neutral `0` vote — which sits in the confidence denominator and dilutes the
validated per-name signals.

## Decision
1. **Display-only by default.** LT_Macro is **excluded from the weighted tally and the
   confidence denominator** unless `GESH_LT_MACRO_VOTING` is explicitly truthy. Its vote
   is still computed and returned in `votes` for the dashboard card.
2. **Abstain on unavailable.** When the macro sources are down, `node_long_term_macro`
   returns `None` (abstain), never a neutral `0`. Availability is derived from whether
   VIX/F&G returned real data (`get_long_term_macro_sentiment().available`).
3. **Enabling voting requires evidence.** Flip `GESH_LT_MACRO_VOTING=1` only after
   walk-forward validation shows the tilt improves out-of-sample outcomes; when enabled,
   the abstain-on-unavailable behavior from (2) applies.

## Rationale
An unvalidated, market-wide 0.5 tilt applied uniformly is a correlated risk across the
whole book. Display-only keeps the signal visible for research while preventing it from
moving sizing/verdicts until it earns a voting role. Matches the original design intent
recorded in PR #39.

## Implementation
- `consensus.py` — `_skip` adds `LT_Macro` unless `GESH_LT_MACRO_VOTING` is set.
- `long_term_macro.py` — `available`/`sources_live` on the sentiment; node abstains
  (`None`) when unavailable.
- Tests: `tests/test_long_term_macro.py` (abstain + availability).
