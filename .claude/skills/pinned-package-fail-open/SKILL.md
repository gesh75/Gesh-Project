---
name: pinned-package-fail-open
description: Use when adding a new gesh-core or other SHA-pinned shared-package symbol to trading.autopilot, run_paper_gate2, or any long-lived job, or when a missing import would take down close_scan or exits.
---

# Pinned-package fail-open import

A SHA pin lags the local editable checkout. A new symbol that `ImportError`s at module load of `trading.autopilot` kills **exits** (`tracker_close_scan`, `close_risk_check`), not just the new BUY path.

## Pattern

Degraded stub must be local (duck-typed). Do not import the return type from the pin.

```python
def _unavailable(*_a, **_k):
    class _Degraded:
        blocked = False
        reason = "overlay unavailable"  # announce; never a silent pass
    return _Degraded()

try:
    from gesh_core.trading import autopilot_core as _ac
except ImportError:
    new_symbol = _unavailable
else:
    new_symbol = getattr(_ac, "new_symbol", _unavailable)
```

`new_symbol` is whatever was just added (today: `precision_overlay_blocks`). The degraded path **says so** in `reason`. A silent pass that reads as "overlay allowed this" is a fabricated measurement.

## Rules

- Fail OPEN on a missing **optional** symbol (precision overlay, measurement helpers). Safety interlocks (`DATA_FRESH`, kill-switch) stay fail-CLOSED.
- After the shared package is committed, bump **every** consumer `requirements.txt` to that 40-char SHA. Local `pip install -e ../pkg` is not CI.
- Guard test: deleting the symbol still **imports** the job module.

## Red flags

- `from gesh_core.trading.autopilot_core import brand_new_fn` at top of autopilot
- Letting `ruff --fix` delete the getattr fallback or the guard test
- Assuming the pin already has the symbol because the laptop venv is editable
