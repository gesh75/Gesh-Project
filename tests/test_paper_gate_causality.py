"""
Causality guard for run_paper_gate2.py (TASK F).

The paper-trade audit chain must be causal: intent → risk decision →
(only if APPROVED) record/fill. A REJECTED intent leaves NO paper record,
the gate evaluates the order's REAL confidence/target (never the invented
0.85 / 0.02 literals), and one immutable uuid threads intent → decision →
record. All gate/recorder collaborators are mocked — zero network, zero DB.
"""

from __future__ import annotations

import ast
import importlib
import uuid as uuid_mod
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "run_paper_gate2.py"


# ── import safety: the script body must be __main__-guarded ───────────────────
def _module_is_import_safe() -> bool:
    """True only when importing runs no script body (no DB/file side effects)."""
    tree = ast.parse(SCRIPT.read_text())
    return any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )


@pytest.fixture(scope="module")
def gate_mod():
    if not _module_is_import_safe():
        pytest.fail(
            "run_paper_gate2.py executes its script body at import time "
            "(no __main__ guard) — gate_and_execute cannot be imported safely"
        )
    return importlib.import_module("run_paper_gate2")


# ── mock collaborators (record every interaction into a shared event log) ─────
def _make_intent_builder(events: list, intents: list):
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


def _make_gate(events: list, verdicts: dict):
    def evaluate(intent, payload):
        events.append(("gate", intent.symbol))
        approved = verdicts.get(intent.symbol, True)
        return SimpleNamespace(
            approved=approved,
            reason=None if approved else "POSITION_CAP: over cap",
            gates={"POSITION_CAP": approved},
        )

    return evaluate


def _make_recorder(events: list, calls: list):
    def record(plan, holdings, total_eq, scanner_source="daily-analysis"):
        events.append(("record", plan[0]["symbol"]))
        calls.append(plan)
        return [{"id": len(calls), "symbol": plan[0]["symbol"]}]

    return record


HOLDINGS = [
    {"symbol": "NVDA", "equity": 2000.0, "price": 100.0},
    {"symbol": "MU", "equity": 1500.0, "price": 50.0},
]
TOTAL_EQ = 10_000.0
ORDERS = [
    {"symbol": "NVDA", "action": "BUY", "shares": 5, "current_price": 100.0,
     "signal_reason": "Kelly ADD: now 20.0% vs optimal 25.0%"},
    {"symbol": "MU", "action": "TRIM", "shares": 10, "current_price": 50.0,
     "signal_reason": "Kelly TRIM: now 15.0% vs optimal 10.0%"},
]
CONFIDENCE = {"NVDA": 0.89, "MU": 0.78}


def _run(gate_mod, verdicts: dict | None = None, orders=ORDERS, conf=CONFIDENCE):
    events: list = []
    intents: list = []
    rec_calls: list = []
    gate_results, created = gate_mod.gate_and_execute(
        orders,
        HOLDINGS,
        TOTAL_EQ,
        gate_payload={"holdings": HOLDINGS},
        confidence_by_sym=conf,
        intent_builder=_make_intent_builder(events, intents),
        gate_evaluate=_make_gate(events, verdicts or {}),
        recorder=_make_recorder(events, rec_calls),
    )
    return SimpleNamespace(events=events, intents=intents, rec_calls=rec_calls,
                           gate_results=gate_results, created=created)


# ── (a) causal order: intent → decision → record ──────────────────────────────
def test_gate_decides_before_any_record(gate_mod):
    r = _run(gate_mod)
    for sym in ("NVDA", "MU"):
        idx = {kind: r.events.index((kind, sym)) for kind in ("intent", "gate", "record")}
        assert idx["intent"] < idx["gate"] < idx["record"], (
            f"{sym}: expected intent → gate → record, got {r.events}"
        )


# ── (b) a REJECTED intent leaves NO paper-trade record ────────────────────────
def test_rejected_intent_is_never_recorded(gate_mod):
    r = _run(gate_mod, verdicts={"NVDA": False})
    recorded_syms = {plan[0]["symbol"] for plan in r.rec_calls}
    assert "NVDA" not in recorded_syms
    assert ("record", "NVDA") not in r.events
    assert all(rec["symbol"] != "NVDA" for rec in r.created)
    nvda = next(g for g in r.gate_results if g["symbol"] == "NVDA")
    assert nvda["approved"] is False
    # the approved order still records normally
    assert recorded_syms == {"MU"}


# ── (c) the gate sees the order's REAL confidence/target, not 0.85/0.02 ───────
def test_gate_evaluates_real_confidence_and_target(gate_mod):
    r = _run(gate_mod)
    nvda = next(i for i in r.intents if i.symbol == "NVDA")
    assert nvda.action == "BUY"
    assert nvda.confidence == pytest.approx(0.89)
    assert nvda.confidence != pytest.approx(0.85)
    # BUY 5 sh @ $100 on a $2,000 position of $10,000 equity → 25% post-trade
    assert nvda.target_position_pct == pytest.approx(0.25)
    assert nvda.target_position_pct != pytest.approx(0.02)

    mu = next(i for i in r.intents if i.symbol == "MU")
    assert mu.action == "SELL"  # TRIM maps to SELL for the gate contract
    assert mu.confidence == pytest.approx(0.78)
    # TRIM 10 sh @ $50 off a $1,500 position of $10,000 equity → 10% post-trade
    assert mu.target_position_pct == pytest.approx(0.10)


def test_missing_confidence_fails_closed_not_invented(gate_mod):
    r = _run(gate_mod, conf={})
    assert all(i.confidence == 0.0 for i in r.intents), (
        "unknown signal confidence must be 0.0 (fail-closed), never invented"
    )


def test_no_invented_literals_in_source():
    src = SCRIPT.read_text()
    assert "confidence=0.85" not in src
    assert "0.02 if side" not in src


# ── (d) one immutable uuid threads intent → decision → record ─────────────────
def test_uuid_threads_intent_decision_record(gate_mod):
    r = _run(gate_mod)
    assert len(r.gate_results) == len(r.intents) == len(ORDERS)
    seen: set[str] = set()
    for intent, result, plan, rec in zip(r.intents, r.gate_results, r.rec_calls, r.created):
        uid = intent.intent_uuid
        uuid_mod.UUID(uid)  # must be a real uuid
        assert result["intent_uuid"] == uid
        assert plan[0]["intent_uuid"] == uid
        assert rec["intent_uuid"] == uid
        seen.add(uid)
    assert len(seen) == len(ORDERS), "each order must carry its own uuid"


def test_input_orders_are_not_mutated(gate_mod):
    orders = [dict(o) for o in ORDERS]
    _run(gate_mod, orders=orders)
    assert orders == ORDERS, "gate_and_execute must not mutate its input plan"


def test_each_symbol_uses_its_own_provider_quote_timestamp(gate_mod):
    seen: dict[str, str] = {}

    def evaluate(intent, payload):
        seen[intent.symbol] = payload["as_of"]
        return SimpleNamespace(approved=False, reason="test", gates={})

    gate_mod.gate_and_execute(
        ORDERS,
        HOLDINGS,
        TOTAL_EQ,
        gate_payload={"holdings": HOLDINGS, "as_of": "transport-time"},
        confidence_by_sym=CONFIDENCE,
        quote_as_of_by_symbol={
            "NVDA": "2026-07-17T19:59:59Z",
            "MU": "2026-07-17T19:58:00Z",
        },
        intent_builder=_make_intent_builder([], []),
        gate_evaluate=evaluate,
        recorder=_make_recorder([], []),
    )
    assert seen == {
        "NVDA": "2026-07-17T19:59:59Z",
        "MU": "2026-07-17T19:58:00Z",
    }


# ── real confidence derivation: consensus first, kelly win_rate fallback ──────
def test_build_confidence_map_uses_real_signals(gate_mod):
    consensus = {"NVDA": {"confidence": 89.0}}
    kelly = {"NVDA": {"win_rate": 53.5}, "MU": {"win_rate": 54.5},
             "OVER": {"win_rate": 250.0}}
    conf = gate_mod.build_confidence_map(consensus, kelly)
    assert conf["NVDA"] == pytest.approx(0.89)   # consensus beats kelly
    assert conf["MU"] == pytest.approx(0.545)    # kelly win_rate fallback
    assert conf["OVER"] == 1.0                   # clamped to [0, 1]
    assert conf.get("MISSING", 0.0) == 0.0       # absent → fail-closed
