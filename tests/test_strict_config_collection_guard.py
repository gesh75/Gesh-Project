"""Regression test for Issue #140.

Proves that test_strict_config_integration.py *skips* (rather than crashing
collection with AttributeError) when gesh-core is importable but the installed
version predates PR #8 (i.e., lacks ConfigError / _parse_strict_bool).

The test simulates an old gesh-core install by patching sys.modules with a
minimal stub that exports only the symbols present before PR #8.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INTEGRATION_FILE = Path(__file__).resolve().parents[1] / "tests" / "test_strict_config_integration.py"
_MODULE_NAME = "test_strict_config_integration"


def _stub_old_gesh_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace gesh_core.trading.config with a pre-PR-#8 stub (no ConfigError)."""

    def _env_bool(key: str, default: bool) -> bool:
        import os
        raw = os.getenv(key)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

    class _FakeRiskConfig:
        paper_mode: bool = True
        require_approval: bool = True
        market_regime_gate: bool = True

    def _load_risk_config() -> _FakeRiskConfig:
        return _FakeRiskConfig()

    stub = types.ModuleType("gesh_core.trading.config")
    stub._env_bool = _env_bool  # type: ignore[attr-defined]
    stub.RiskConfig = _FakeRiskConfig  # type: ignore[attr-defined]
    stub.load_risk_config = _load_risk_config  # type: ignore[attr-defined]
    # Intentionally NOT adding ConfigError / _parse_strict_bool / _env_strict_bool

    parent_stub = types.ModuleType("gesh_core")
    trading_stub = types.ModuleType("gesh_core.trading")

    monkeypatch.setitem(sys.modules, "gesh_core", parent_stub)
    monkeypatch.setitem(sys.modules, "gesh_core.trading", trading_stub)
    monkeypatch.setitem(sys.modules, "gesh_core.trading.config", stub)


# ---------------------------------------------------------------------------
# Regression test: guard converts AttributeError into a SKIP
# ---------------------------------------------------------------------------


def test_collection_guard_skips_on_pre_pr8_gesh_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an old gesh-core stub (no ConfigError), importing the integration
    module must skip cleanly rather than raising AttributeError at module level.

    We exercise the guard by exec-loading the integration module in-process
    with a sys.modules stub in place of real gesh-core.
    """
    _stub_old_gesh_core(monkeypatch)

    # Remove any previously imported copy of the integration module so we
    # re-execute the module-level code with the stub in place.
    monkeypatch.delitem(sys.modules, _MODULE_NAME, raising=False)

    # pytest.importorskip triggers the module-level guard; it raises Skipped
    # (a BaseException subclass) on allow_module_level=True.  We catch it to
    # assert the correct exception type and message.
    with pytest.raises(pytest.skip.Exception) as exc_info:
        # Directly exec the module's source so we exercise its guard path
        # rather than the running test session's collection machinery.
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _INTEGRATION_FILE)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        # Inject stub BEFORE loader.exec_module runs the module-level code.
        sys.modules[_MODULE_NAME] = mod
        assert spec.loader is not None
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]

    assert "ConfigError" in str(exc_info.value)
    assert "d4e9f467" in str(exc_info.value)


def test_collection_guard_absent_on_correct_gesh_core() -> None:
    """With the real (post-PR-#8) gesh-core, the integration module collects
    normally — no skip is raised at module level."""
    try:
        import gesh_core.trading.config as cfg
    except ImportError:
        pytest.skip("gesh-core not installed in this environment")

    if not hasattr(cfg, "ConfigError"):
        pytest.skip("gesh-core predates PR #8 — skipping this guard check too")

    # If we reach here, the integration module should load without raising Skipped.
    # Import it (or re-use cached); any module-level Skipped would propagate here.
    if _MODULE_NAME in sys.modules:
        del sys.modules[_MODULE_NAME]

    try:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _INTEGRATION_FILE)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    except pytest.skip.Exception:
        pytest.fail(
            "test_strict_config_integration raised module-level Skipped even "
            "though gesh-core has ConfigError — guard over-fired"
        )
    finally:
        sys.modules.pop(_MODULE_NAME, None)
