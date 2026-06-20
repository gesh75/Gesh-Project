"""Shim — moved to gesh-core (Phase 3.1).

The implementation now lives in ``gesh_core.allocation_config`` so the engine
and product repos share one source of truth. This stub makes
``import allocation_config`` resolve to that single module object, so:

    from allocation_config import load_config, validate_action   # still works
    import allocation_config as ac; ac.DEFAULT_CONFIG            # still works
    monkeypatch.setattr(ac, "load_config", ...)                  # still works
        (patches reach validate_action's internal call — same namespace)

Config resolution is cwd-based, so this repo keeps using its own
``gesh_config.json`` at the repo root. See gesh-core for the implementation.
"""

import sys

from gesh_core import allocation_config as _impl

# Make this name an alias for the real module so attribute lookups, ``from``
# imports, AND monkeypatching all hit the single gesh_core namespace.
sys.modules[__name__] = _impl
