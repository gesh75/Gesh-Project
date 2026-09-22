"""Product-side precision overlay wiring (paper gate + autopilot).

The overlay implementation lives in gesh-core. These tests lock the *callers*:
BUY-only consultation, a blocked BUY is not recorded/placed, a runtime error
fails OPEN (does not abort the chain), and SELL/TRIM never consult the overlay.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid as uuid_mod
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
_PAPER = REPO / "run_paper_gate2.py"
_AUTO = REPO / "trading" / "autopilot.py"


def _install_core(overlay=None) -> dict[str, object]:
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

    def entry_floor_blocks(action, raw, floor):
        return action == "BUY" and raw < floor

    ac.entry_floor_blocks = entry_floor_blocks
    ac.CANDIDATE_MARKER = "candidates"
    ac.candidates_marker_json = lambda *a, **k: "{}"
    ac.restore_candidate_symbols = lambda *a, **k: []
    ac.stop_sweep_intent = lambda *a, **k: None
    if overlay is not None:
        ac.precision_overlay_blocks = overlay
    core.trading = trading
    trading.autopilot_core = ac
    manifest = ModuleType("run_manifest")
    manifest.available_rsi_by_symbol = lambda *a, **k: {}
    sys.modules["gesh_core"] = core
    sys.modules["gesh_core.trading"] = trading
    sys.modules["gesh_core.trading.autopilot_core"] = ac
    sys.modules["run_manifest"] = manifest
    return previous


def _restore(previous: dict[str, object]) -> None:
    for name, mod in previous.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def _load_paper_gate(overlay=None):
    previous = _install_core(overlay)
    try:
        spec = importlib.util.spec_from_file_location("gesh_paper_gate_overlay_w", _PAPER)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("gesh_paper_gate_overlay_w", None)
        _restore(previous)


def _intent_builder(events, intents):
    def build(symbol, side, *, confidence, target_position_pct, rationale=None, **kw):
        intent = SimpleNamespace(
            symbol=symbol,
            action=side,
            confidence=confidence,
            target_position_pct=target_position_pct,
            intent_uuid=str(uuid_mod.uuid4()),
            rationale=rationale,
        )
        events.append(("intent", symbol))
        intents.append(intent)
        return intent

    return build


def _gate(events, verdicts):
    def evaluate(intent, payload):
        events.append(("gate", intent.symbol))
        approved = verdicts.get(intent.symbol, True)
        return SimpleNamespace(
            approved=approved,
            reason=None if approved else "POSITION_CAP",
            gates={"POSITION_CAP": approved},
        )

    return evaluate


def _recorder(events, calls):
    def record(plan, holdings, total_eq, scanner_source="daily-analysis", **_):
        events.append(("record", plan[0]["symbol"]))
        calls.append(plan)
        return [{"id": len(calls), "symbol": plan[0]["symbol"]}]

    return record


HOLDINGS = [
    {"symbol": "NVDA", "equity": 2000.0, "price": 100.0},
    {"symbol": "MU", "equity": 1500.0, "price": 50.0},
]
ORDERS = [
    {
        "symbol": "NVDA",
        "action": "BUY",
        "shares": 5,
        "current_price": 100.0,
        "signal_reason": "Kelly ADD",
    },
    {
        "symbol": "MU",
        "action": "TRIM",
        "shares": 10,
        "current_price": 50.0,
        "signal_reason": "Kelly TRIM",
    },
]


def _run(gate_mod, overlay, verdicts=None):
    events: list = []
    intents: list = []
    rec_calls: list = []
    gate_mod.precision_overlay_blocks = overlay
    results, created = gate_mod.gate_and_execute(
        ORDERS,
        HOLDINGS,
        10_000.0,
        gate_payload={"holdings": HOLDINGS},
        confidence_by_sym={"NVDA": 0.89, "MU": 0.78},
        raw_score_by_sym={"NVDA": 0.62, "MU": 0.62},
        min_entry_score=0.50,
        consensus_by_sym={
            "NVDA": {"verdict": "BUY", "votes": {"Rel.Strength": -1}},
        },
        intent_builder=_intent_builder(events, intents),
        gate_evaluate=_gate(events, verdicts or {}),
        recorder=_recorder(events, rec_calls),
    )
    return SimpleNamespace(
        events=events,
        intents=intents,
        rec_calls=rec_calls,
        gate_results=results,
        created=created,
    )


def test_paper_gate_blocked_buy_is_not_recorded():
    class _Block:
        blocked = True
        reason = "Rel.Strength bearish"

    class _Open:
        blocked = False
        reason = "ok"

    def overlay(action, row=None):
        votes = (row or {}).get("votes") or {}
        if votes.get("Rel.Strength") == -1:
            return _Block()
        return _Open()

    r = _run(_load_paper_gate(overlay), overlay)
    nvda = next(g for g in r.gate_results if g["symbol"] == "NVDA")
    mu = next(g for g in r.gate_results if g["symbol"] == "MU")
    assert nvda["approved"] is False
    assert "precision overlay" in (nvda["reason"] or "").lower()
    assert ("record", "NVDA") not in r.events
    assert mu["approved"] is True
    assert ("record", "MU") in r.events


def test_paper_gate_overlay_exception_fails_open_not_abort():
    def boom(action, row=None):
        raise RuntimeError("overlay exploded")

    r = _run(_load_paper_gate(boom), boom)
    nvda = next(g for g in r.gate_results if g["symbol"] == "NVDA")
    assert nvda["approved"] is True
    assert ("record", "NVDA") in r.events
    assert ("record", "MU") in r.events


def test_paper_gate_does_not_consult_overlay_for_trim():
    seen: list[str] = []

    class _AlwaysBlock:
        blocked = True
        reason = "should not apply to TRIM"

    def overlay(action, row=None):
        seen.append(action)
        return _AlwaysBlock()

    r = _run(_load_paper_gate(overlay), overlay)
    assert seen == ["BUY"]
    assert ("record", "MU") in r.events


def _from_consensus(row, max_position_pct=0.02):
    verdict = str(row.get("verdict") or "HOLD").upper()
    if "BUY" in verdict:
        action = "BUY"
    elif "SELL" in verdict:
        action = "SELL"
    else:
        action = "HOLD"
    conf = float(row.get("confidence") or 0)
    if conf > 1:
        conf = conf / 100.0
    return SimpleNamespace(
        action=action,
        confidence=conf,
        symbol=row.get("symbol"),
        target_position_pct=max_position_pct,
    )


def _load_autopilot(overlay):
    previous = _install_core(overlay)
    stub_name = "gesh_autopilot_overlay_w_stubs"
    stub = ModuleType(stub_name)
    stub.load_risk_config = lambda: SimpleNamespace(
        min_confidence=0.75,
        min_entry_score=0.50,
        max_position_pct=0.02,
    )
    stub.from_consensus = _from_consensus
    stub.TradeIntent = SimpleNamespace
    stub.evaluate = lambda *a, **k: SimpleNamespace(approved=True, reason=None)
    placed: list = []
    stub.place = lambda intent, verdict, snapshot, cfg: (
        placed.append(intent.symbol)
        or (SimpleNamespace(qty=1, target_price=100.0, status="SIMULATED"))
    )
    stub.placed = placed
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
        spec = importlib.util.spec_from_loader("gesh_autopilot_overlay_w", loader=None)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gesh_autopilot_overlay_w"] = mod
        exec(compile(rewritten, str(_AUTO), "exec"), mod.__dict__)  # noqa: S102
        return mod, stub
    finally:
        # Keep the loaded module for the test; restore core stubs after the
        # caller finishes. The unique module name avoids poisoning trading.autopilot.
        _restore({k: v for k, v in previous.items() if k != stub_name})
        sys.modules.pop(stub_name, None)
        previous.clear()


def _scan_rows():
    return {
        "NVDA": {
            "symbol": "NVDA",
            "verdict": "BUY",
            "raw_score": 0.62,
            "confidence": 89.0,
            "votes": {"Rel.Strength": -1},
        },
        "AMD": {
            "symbol": "AMD",
            "verdict": "BUY",
            "raw_score": 0.62,
            "confidence": 89.0,
            "votes": {"Rel.Strength": 1, "Momentum": 1, "Trend": 1},
        },
        "EXIT": {
            "symbol": "EXIT",
            "verdict": "SELL",
            "raw_score": -0.40,
            "confidence": 89.0,
            "votes": {"Rel.Strength": -1},
        },
    }


def test_pre_market_scan_drops_overlay_blocked_buy_keeps_sell(monkeypatch):
    class _Block:
        blocked = True
        reason = "Rel.Strength bearish"

    class _Open:
        blocked = False
        reason = "ok"

    def overlay(action, row=None):
        if action != "BUY":
            return _Open()
        votes = (row or {}).get("votes") or {}
        if votes.get("Rel.Strength") == -1:
            return _Block()
        return _Open()

    mod, _stub = _load_autopilot(overlay)
    rows = _scan_rows()
    snapshot = {
        "holdings": [],
        "watchlist": [{"symbol": s, "last_trade_price": 100.0} for s in rows],
        "equity_summary": {"portfolio_equity": 100_000.0},
    }
    monkeypatch.setattr(mod, "_ensure_logged_in", lambda: True)
    monkeypatch.setattr(mod, "_load_data_fetcher", lambda: lambda _syms: snapshot)
    monkeypatch.setattr(mod, "_load_consensus", lambda: lambda sym, **kw: rows[sym])
    monkeypatch.setattr(mod, "alert_scan_summary", lambda *a, **k: None)
    monkeypatch.setattr(mod, "precision_overlay_blocks", overlay)
    mod.pre_market_scan()
    cands = dict(mod._CANDIDATES)
    mod._CANDIDATES.clear()
    assert "NVDA" not in cands
    assert "AMD" in cands
    assert "EXIT" in cands


def test_open_window_overlay_veto_never_places(monkeypatch):
    class _Block:
        blocked = True
        reason = "Rel.Strength bearish"

    class _Open:
        blocked = False
        reason = "ok"

    def overlay(action, row=None):
        votes = (row or {}).get("votes") or {}
        if action == "BUY" and votes.get("Rel.Strength") == -1:
            return _Block()
        return _Open()

    mod, stub = _load_autopilot(overlay)
    rows = _scan_rows()
    snapshot = {
        "holdings": [],
        "watchlist": [{"symbol": s, "last_trade_price": 100.0} for s in rows],
        "equity_summary": {"portfolio_equity": 100_000.0},
    }
    mod._CANDIDATES.update(
        {
            "NVDA": (SimpleNamespace(action="BUY", confidence=0.89), rows["NVDA"]),
            "AMD": (SimpleNamespace(action="BUY", confidence=0.89), rows["AMD"]),
        }
    )
    monkeypatch.setattr(mod, "_ensure_logged_in", lambda: True)
    monkeypatch.setattr(mod, "_load_data_fetcher", lambda: lambda _syms: snapshot)
    monkeypatch.setattr(mod, "_load_consensus", lambda: lambda sym, **kw: rows[sym])
    monkeypatch.setattr(mod, "_consume_candidates", lambda: True)
    monkeypatch.setattr(mod, "precision_overlay_blocks", overlay)
    monkeypatch.setattr(mod, "evaluate", stub.evaluate)
    monkeypatch.setattr(mod, "place", stub.place)
    mod.open_window()
    assert "NVDA" not in stub.placed
    assert "AMD" in stub.placed
    mod._CANDIDATES.clear()
