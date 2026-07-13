# Decision needed: should LT_Macro vote in consensus, or stay display-only until validated?

**Status:** OPEN — needs an explicit call
**Raised:** 2026-07-13, at the close of the PR #39 audit-remediation merge (`main` @ `ae3b533`)
**Owner:** Georgi

---

## The tension

`main` now wires **LT_Macro as a real, weighted consensus node**, but the design doc that
shipped alongside the same work says it should stay **display-only until walk-forward
validation supports a voting role**. These two are in direct conflict and were merged
together — this document tracks the deliberate decision so the merge does not decide it by
accident.

## Evidence — it currently votes

- `consensus.py` — `"LT_Macro": 0.5` in the base node weights (`_BASE_WEIGHTS`).
- `consensus.py` — `nodes["LT_Macro"] = node_long_term_macro(symbol)` wires it into every
  per-symbol consensus vote (graceful abstain only on import/call failure).
- `long_term_macro.py::node_long_term_macro` returns +1 / 0 / −1 from a VIX + Fear&Greed +
  FINRA-margin composite (`combined_score` thresholds ±0.3).
- On a pure data outage all three inputs degrade to `0`, so the node casts an **active
  neutral vote** — it counts toward `active_count`, `weight_total`, and the `n < 4`
  INSUFFICIENT-DATA gate — rather than abstaining. Benign (a neutral vote can only dilute
  `raw_score` toward 0, never fabricate BUY/SELL), but it is an *active participant*, not a
  passive overlay.

## Evidence — the design said display-only

`docs/superpowers/specs/2026-07-12-gesh-full-remediation-design.md`:

> "The long-term macro panel remains a display-only research overlay until walk-forward
> validation supports a voting role."

The dashboard copy was reconciled to the *current* reality during the PR #39 merge
("one long-term macro input to the consensus engine"), so the UI and the code agree today —
but only because the code shipped ahead of the stated validation gate.

## The decision to make

Pick one and make **code + copy + docs** consistent:

### Option 1 — Keep it voting (weight 0.5)
- Retire the "display-only until validated" language in the design doc.
- Back the 0.5 weight with at least a basic walk-forward / backtest so an unvalidated signal
  is not steering real consensus.
- Consider making the outage case **abstain (`return None`)** instead of casting a neutral
  vote, so a data blackout does not silently count as a filled node toward the
  INSUFFICIENT-DATA gate.

### Option 2 — Revert to display-only
- Set the LT_Macro weight to `0` / unregister it from the consensus tally (keep the panel +
  `engine_block` overlay).
- Restore "advisory overlay" dashboard copy.
- Re-enable voting only after a validation study justifies a weight.

## Why it matters

LT_Macro is a market-**wide** signal (the same vote for every holding), so at weight 0.5 it
applies a uniform tilt across the entire book. An unvalidated uniform tilt is exactly the
kind of thing the audit's honesty theme (gaps #3 / #4 / #6 — no fabricated data, honest
labels, honest degraded states) was guarding against. It deserves a conscious call rather
than being settled implicitly by a merge.

## References

- Consensus node registration + weights: `consensus.py`
- Node implementation + degraded behavior: `long_term_macro.py::node_long_term_macro`,
  `engine_block`
- Design intent: `docs/superpowers/specs/2026-07-12-gesh-full-remediation-design.md`
- Merge that surfaced the tension: PR #39 (`main` @ `ae3b533`)
