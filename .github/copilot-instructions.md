# Gesh-Project

GESH Financial - AI portfolio intelligence dashboard. FastAPI + Claude, with an 11-node consensus engine, risk gates, Kelly sizing, Monte Carlo simulation, a diversification radar, and a live paper-trade track record.

**Stack.** Python (FastAPI, SQLAlchemy, Postgres, Redis, Docker). Anthropic multi-model routing. React/Next.js dashboard.

**Layout.** `trading/` engine, `scripts/`, `security/`, `templates/`, `tests/`, `android/`, `docs/`

## Build and test

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pytest pytest-cov ruff
python -m pytest -q
```

Run the tests before proposing a change is done. If you cannot run them, say so explicitly
rather than claiming the change is verified.

## Engineering conventions (non-negotiable)

- **Type hints on every function signature.** No bare `def f(x):`.
- **async/await for all I/O.** Never block the event loop with sync network or disk calls.
- **Immutable data.** Return new objects; do not mutate arguments in place.
- **Tests first.** Write the failing test, watch it fail, then implement. Target 80%+ coverage.
- **Small files.** 200-400 lines typical, 800 hard max. Extract modules rather than growing a file.
- **Small functions.** Under 50 lines. Nesting no deeper than 4 levels - use early returns.
- **Handle every error explicitly.** Never swallow an exception silently. Log context server-side,
  return a friendly message user-side.
- **Validate at boundaries.** Never trust user input, API responses, or file contents.
- **No hardcoded secrets, ever.** Environment variables or a secret manager only. No credentials
  in code, comments, logs, tests, or fixtures.
- **Conventional Commits**: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`.
  Imperative mood, lower case, no trailing period. Do **not** add `Co-authored-by` trailers.

## Before you propose a change

1. Read the surrounding code and match its idiom, naming, and comment density.
2. Prefer a battle-tested library over hand-rolled utility code.
3. If you touch auth, user input, DB queries, file paths, or external calls, re-read the
   security rules above before finishing.

## Financial-engine rules

- **This system moves money decisions.** Any change to sizing, gating, or consensus needs a test
  that proves the new behaviour on a concrete numeric example.
- **Never widen a risk gate to make a test pass.** If a gate blocks, that is the gate working.
  Fix the input or the caller, not the threshold.
- Position sizing and risk bands are canonical in `gesh-core`. Import them; do not re-implement.
- **Robinhood quirk:** use `profile['withdrawable_amount']` for available cash, never
  `account['cash']` - the latter includes unsettled funds and will over-size positions.
- Price fetching must degrade gracefully. Yahoo/stooq/Alpaca can all fail (rate limits, SNI
  blocking on VPN). Preserve the per-symbol fallback chain and the `price_source` tagging;
  an all-NaN batch must not silently become "no candidates".
- Never commit API keys, account numbers, or real position data. `.env.example` only.
- Reports must carry the "NOT FINANCIAL ADVICE" disclaimer.

## Pull requests

- Title in Conventional Commits form.
- Body covers: what changed, why, blast radius, and a test plan as a checklist.
- Summarise the whole commit range, not just the last commit.
