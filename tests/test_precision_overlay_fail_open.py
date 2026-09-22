"""Pinned-package fail-open for precision_overlay_blocks.

A SHA pin that predates the overlay must not take down paper-gate or
autopilot SELL/exit jobs (pinned-package-fail-open skill). The degraded
stub announces ``overlay unavailable`` and never reads as a silent allow.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
_PAPER = REPO / "run_paper_gate2.py"
_AUTO = REPO / "trading" / "autopilot.py"


def _source_contract(path: Path) -> str:
    src = path.read_text()
    assert "except ImportError" in src
    assert "precision_overlay_blocks" in src
    assert "overlay unavailable" in src
    assert "blocked = False" in src
    return src


def test_paper_gate_source_fails_open_on_missing_overlay_symbol():
    _source_contract(_PAPER)


def test_autopilot_source_fails_open_on_missing_overlay_symbol():
    _source_contract(_AUTO)


def _install_core_without_overlay() -> dict[str, ModuleType | None]:
    """Force ImportError on ``from … import precision_overlay_blocks``."""
    previous = {
        name: sys.modules.get(name)
        for name in (
            "gesh_core",
            "gesh_core.trading",
            "gesh_core.trading.autopilot_core",
            "run_manifest",
        )
    }
    core = ModuleType("gesh_core")
    trading = ModuleType("gesh_core.trading")
    ac = ModuleType("gesh_core.trading.autopilot_core")
    ac.entry_floor_blocks = lambda action, raw, floor: False
    ac.CANDIDATE_MARKER = "candidates"
    ac.candidates_marker_json = lambda *a, **k: "{}"
    ac.restore_candidate_symbols = lambda *a, **k: []
    ac.stop_sweep_intent = lambda *a, **k: None
    core.trading = trading
    trading.autopilot_core = ac
    manifest = ModuleType("run_manifest")
    manifest.available_rsi_by_symbol = lambda *a, **k: {}
    sys.modules["gesh_core"] = core
    sys.modules["gesh_core.trading"] = trading
    sys.modules["gesh_core.trading.autopilot_core"] = ac
    sys.modules["run_manifest"] = manifest
    return previous


def _restore(previous: dict[str, ModuleType | None]) -> None:
    for name, mod in previous.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def _load_paper_gate_missing_overlay():
    previous = _install_core_without_overlay()
    try:
        spec = importlib.util.spec_from_file_location("gesh_paper_gate_overlay_iso", _PAPER)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("gesh_paper_gate_overlay_iso", None)
        _restore(previous)


def test_paper_gate_imports_when_overlay_symbol_missing():
    mod = _load_paper_gate_missing_overlay()
    result = mod.precision_overlay_blocks(
        "BUY",
        {"votes": {"Rel.Strength": -1, "Momentum": 1}},
    )
    assert result.blocked is False
    assert "unavailable" in result.reason


def test_missing_overlay_never_reads_as_silent_allow():
    """A fabricated 'ok' reason would hide that the pin could not check votes."""
    mod = _load_paper_gate_missing_overlay()
    result = mod.precision_overlay_blocks("BUY", {})
    assert result.reason == "overlay unavailable"
    assert result.blocked is False


def _load_autopilot_missing_overlay():
    previous = _install_core_without_overlay()
    stub_name = "gesh_autopilot_overlay_stubs"
    stub = ModuleType(stub_name)
    stub.load_risk_config = lambda: SimpleNamespace(
        min_confidence=0.75,
        min_entry_score=0.50,
        max_position_pct=0.02,
    )
    stub.from_consensus = lambda row, max_position_pct=0.02: SimpleNamespace(
        action="HOLD", confidence=0.0, symbol=row.get("symbol")
    )
    stub.TradeIntent = SimpleNamespace
    stub.evaluate = lambda *a, **k: SimpleNamespace(approved=False, reason="stub")
    stub.place = lambda *a, **k: SimpleNamespace(qty=0, target_price=0, status="SKIPPED")
    stub.active_stops = lambda: {}
    stub.get_marker = lambda *a, **k: None
    stub.init_trading_tables = lambda: None
    stub.paper_open_positions = lambda: {}
    stub.recent_intents = lambda *a, **k: []
    stub.set_marker = lambda *a, **k: None
    stub.alert_autopilot_start = lambda *a, **k: None
    stub.alert_autopilot_stop = lambda *a, **k: None
    stub.alert_scan_summary = lambda *a, **k: None
    stub.alert_error = lambda *a, **k: None
    stub.alert_daily_pnl = lambda *a, **k: None
    stub.alert_auth_failure = lambda *a, **k: None
    previous[stub_name] = sys.modules.get(stub_name)
    sys.modules[stub_name] = stub
    rewritten = (
        _AUTO.read_text()
        .replace("from .config import", f"from {stub_name} import")
        .replace("from .contract import", f"from {stub_name} import")
        .replace("from .risk_gate import", f"from {stub_name} import")
        .replace("from .execution import", f"from {stub_name} import")
        .replace("from .state import", f"from {stub_name} import")
        .replace("from .alerts import", f"from {stub_name} import")
    )
    try:
        spec = importlib.util.spec_from_loader("gesh_autopilot_overlay_iso", loader=None)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gesh_autopilot_overlay_iso"] = mod
        exec(compile(rewritten, str(_AUTO), "exec"), mod.__dict__)  # noqa: S102
        return mod
    finally:
        sys.modules.pop("gesh_autopilot_overlay_iso", None)
        sys.modules.pop(stub_name, None)
        _restore(previous)


def test_autopilot_imports_when_overlay_symbol_missing():
    mod = _load_autopilot_missing_overlay()
    result = mod.precision_overlay_blocks("BUY", {"votes": {"Rel.Strength": -1}})
    assert result.blocked is False
    assert result.reason == "overlay unavailable"
