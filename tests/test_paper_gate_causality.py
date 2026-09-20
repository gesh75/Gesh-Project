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
    {
        "symbol": "NVDA",
        "action": "BUY",
        "shares": 5,
        "current_price": 100.0,
        "signal_reason": "Kelly ADD: now 20.0% vs optimal 25.0%",
    },
    {
        "symbol": "MU",
        "action": "TRIM",
        "shares": 10,
        "current_price": 50.0,
        "signal_reason": "Kelly TRIM: now 15.0% vs optimal 10.0%",
    },
]
CONFIDENCE = {"NVDA": 0.89, "MU": 0.78}


def _run(
    gate_mod,
    verdicts: dict | None = None,
    orders=ORDERS,
    conf=CONFIDENCE,
    **gate_kw,
):
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
        **gate_kw,
    )
    return SimpleNamespace(
        events=events,
        intents=intents,
        rec_calls=rec_calls,
        gate_results=gate_results,
        created=created,
    )


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
    provenance: dict[str, str] = {}

    def evaluate(intent, payload):
        seen[intent.symbol] = payload["as_of"]
        rec = (payload.get("quote_provenance") or {}).get(intent.symbol) or {}
        provenance[intent.symbol] = rec.get("quote_as_of")
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
    assert provenance == seen, (
        "daily-chain substitution must also stamp quote_provenance so the "
        "gate cannot fall back to the transport as_of"
    )


# ── real confidence derivation: consensus first, kelly win_rate fallback ──────
def test_build_confidence_map_uses_real_signals(gate_mod):
    consensus = {"NVDA": {"confidence": 89.0}}
    kelly = {"NVDA": {"win_rate": 53.5}, "MU": {"win_rate": 54.5}, "OVER": {"win_rate": 250.0}}
    conf = gate_mod.build_confidence_map(consensus, kelly)
    assert conf["NVDA"] == pytest.approx(0.89)  # consensus beats kelly
    assert conf["MU"] == pytest.approx(0.545)  # kelly win_rate fallback
    assert conf["OVER"] == 1.0  # clamped to [0, 1]
    assert conf.get("MISSING", 0.0) == 0.0  # absent → fail-closed


# ── recorder fail-soft: approved intent, no fill, remaining orders still gated ─
def test_recorder_exception_sets_record_error_without_fill(gate_mod):
    events: list = []
    intents: list = []

    def boom(plan, holdings, total_eq, scanner_source="daily-analysis"):
        events.append(("record", plan[0]["symbol"]))
        raise RuntimeError("db locked")

    gate_results, created = gate_mod.gate_and_execute(
        [ORDERS[0]],
        HOLDINGS,
        TOTAL_EQ,
        gate_payload={"holdings": HOLDINGS},
        confidence_by_sym=CONFIDENCE,
        intent_builder=_make_intent_builder(events, intents),
        gate_evaluate=_make_gate(events, {}),
        recorder=boom,
    )
    assert created == []
    nvda = gate_results[0]
    assert nvda["approved"] is True
    assert nvda["record_error"] == "db locked"
    assert ("intent", "NVDA") in events
    assert ("gate", "NVDA") in events
    assert ("record", "NVDA") in events


def test_recorder_failure_does_not_stop_remaining_orders(gate_mod):
    events: list = []
    intents: list = []
    rec_calls: list = []

    def flaky(plan, holdings, total_eq, scanner_source="daily-analysis"):
        sym = plan[0]["symbol"]
        events.append(("record", sym))
        if sym == "NVDA":
            raise RuntimeError("nvda write failed")
        rec_calls.append(plan)
        return [{"id": 1, "symbol": sym}]

    gate_results, created = gate_mod.gate_and_execute(
        ORDERS,
        HOLDINGS,
        TOTAL_EQ,
        gate_payload={"holdings": HOLDINGS},
        confidence_by_sym=CONFIDENCE,
        intent_builder=_make_intent_builder(events, intents),
        gate_evaluate=_make_gate(events, {}),
        recorder=flaky,
    )
    by_sym = {g["symbol"]: g for g in gate_results}
    assert by_sym["NVDA"]["approved"] is True
    assert by_sym["NVDA"]["record_error"] == "nvda write failed"
    assert "record_error" not in by_sym["MU"]
    assert by_sym["MU"]["approved"] is True
    assert [c["symbol"] for c in created] == ["MU"]
    # both orders still ran intent → gate; only MU produced a fill
    assert ("intent", "MU") in events and ("gate", "MU") in events


def test_recorder_blocked_row_is_not_left_approved(gate_mod):
    """Allocation block at record must not keep the 8-gate row APPROVED."""
    events: list = []
    intents: list = []

    def blocked_rec(plan, holdings, total_eq, scanner_source="daily-analysis"):
        events.append(("record", plan[0]["symbol"]))
        return [
            {
                "symbol": plan[0]["symbol"],
                "action": plan[0]["action"],
                "blocked": True,
                "reason": "Already at max open positions (2).",
                "code": "POSITION_CAP",
            }
        ]

    gate_results, created = gate_mod.gate_and_execute(
        [ORDERS[0]],
        HOLDINGS,
        TOTAL_EQ,
        gate_payload={"holdings": HOLDINGS},
        confidence_by_sym=CONFIDENCE,
        intent_builder=_make_intent_builder(events, intents),
        gate_evaluate=_make_gate(events, {}),
        recorder=blocked_rec,
    )
    assert gate_results[0]["approved"] is False
    assert "max open positions" in (gate_results[0]["reason"] or "")
    assert created[0]["blocked"] is True


def _buy(symbol: str, shares: float = 1.0, price: float = 80.0) -> dict:
    return {
        "symbol": symbol,
        "action": "BUY",
        "shares": shares,
        "current_price": price,
        "signal_reason": "Kelly ADD",
    }


def test_gate_recorder_sees_decremented_cash_across_buys(gate_mod):
    """Daily chain records one order per call — remaining cash must still shrink.

    $600 − $80 first BUY leaves $520 for the second recorder call. Passing the
    starting cash (or omitting it) would skip the session floor we added to
    run_paper_session.
    """
    seen: list[tuple[str, float | None]] = []

    def record(plan, holdings, total_eq, scanner_source="daily-analysis", current_cash=None, **_):
        seen.append((plan[0]["symbol"], current_cash))
        return [{"symbol": plan[0]["symbol"], "blocked": False}]

    gate_mod.gate_and_execute(
        [_buy("AAA"), _buy("BBB")],
        HOLDINGS,
        TOTAL_EQ,
        gate_payload={"holdings": HOLDINGS},
        confidence_by_sym={"AAA": 0.9, "BBB": 0.9},
        intent_builder=_make_intent_builder([], []),
        gate_evaluate=_make_gate([], {}),
        recorder=record,
        current_cash=600.0,
    )
    assert seen == [("AAA", 600.0), ("BBB", 520.0)]


def test_gate_recorder_does_not_spend_trim_proceeds(gate_mod):
    seen: list[tuple[str, str, float | None]] = []

    def record(plan, holdings, total_eq, scanner_source="daily-analysis", current_cash=None, **_):
        seen.append((plan[0]["symbol"], plan[0]["action"], current_cash))
        return [{"symbol": plan[0]["symbol"], "blocked": False}]

    gate_mod.gate_and_execute(
        [
            _buy("AAA"),
            {
                "symbol": "AAA",
                "action": "TRIM",
                "shares": 1.0,
                "current_price": 80.0,
                "signal_reason": "Kelly TRIM",
            },
            _buy("BBB"),
        ],
        HOLDINGS,
        TOTAL_EQ,
        gate_payload={"holdings": HOLDINGS},
        confidence_by_sym={"AAA": 0.9, "BBB": 0.9},
        intent_builder=_make_intent_builder([], []),
        gate_evaluate=_make_gate([], {}),
        recorder=record,
        current_cash=600.0,
    )
    assert seen[0] == ("AAA", "BUY", 600.0)
    assert seen[1] == ("AAA", "TRIM", 520.0)
    assert seen[2] == ("BBB", "BUY", 520.0)


def test_gate_blocked_record_does_not_decrement_cash(gate_mod):
    seen: list[float | None] = []

    def record(plan, holdings, total_eq, scanner_source="daily-analysis", current_cash=None, **_):
        seen.append(current_cash)
        if plan[0]["symbol"] == "AAA":
            return [{"symbol": "AAA", "blocked": True, "reason": "allocation blocked"}]
        return [{"symbol": plan[0]["symbol"], "blocked": False}]

    gate_mod.gate_and_execute(
        [_buy("AAA"), _buy("BBB")],
        HOLDINGS,
        TOTAL_EQ,
        gate_payload={"holdings": HOLDINGS},
        confidence_by_sym={"AAA": 0.9, "BBB": 0.9},
        intent_builder=_make_intent_builder([], []),
        gate_evaluate=_make_gate([], {}),
        recorder=record,
        current_cash=600.0,
    )
    assert seen == [600.0, 600.0]


def test_deployable_cash_prefers_deployable_over_cash(gate_mod):
    assert gate_mod._deployable_cash({"deployable": 123.0, "cash": 999.0}) == 123.0


def test_deployable_cash_falls_back_to_portfolio_cash_then_cash(gate_mod):
    assert gate_mod._deployable_cash({"portfolio_cash": 50.0, "cash": 9.0}) == 50.0
    assert gate_mod._deployable_cash({"cash": 7.0}) == 7.0
    assert gate_mod._deployable_cash({}) == 0.0


def test_order_target_pct_zero_equity_is_zero(gate_mod):
    pct = gate_mod._order_target_pct(
        {"symbol": "NVDA", "action": "BUY", "shares": 5, "current_price": 100.0},
        HOLDINGS,
        0.0,
    )
    assert pct == 0.0


def test_order_target_pct_negative_equity_is_zero(gate_mod):
    pct = gate_mod._order_target_pct(
        {"symbol": "NVDA", "action": "BUY", "shares": 5, "current_price": 100.0},
        HOLDINGS,
        -1.0,
    )
    assert pct == 0.0


def test_build_confidence_map_uses_coverage_not_conviction(gate_mod):
    """CONFIDENCE_FLOOR is a data-coverage gate, not call strength.

    Putting conviction on intent.confidence would also reject TRIMs of
    weak-vote names. Coverage 89 with conviction 43 must still clear 0.75.
    """
    consensus = {"NVDA": {"confidence": 89.0, "conviction": 43.0}}
    conf = gate_mod.build_confidence_map(consensus, {"NVDA": {"win_rate": 53.5}})
    assert conf["NVDA"] == pytest.approx(0.89)
    assert conf["NVDA"] >= 0.75


def test_build_confidence_map_conviction_zero_does_not_replace_coverage(gate_mod):
    consensus = {"NVDA": {"confidence": 89.0, "conviction": 0.0}}
    conf = gate_mod.build_confidence_map(consensus, {})
    assert conf["NVDA"] == pytest.approx(0.89)


def test_buy_below_entry_floor_is_rejected_trim_is_not(gate_mod):
    """Conviction belongs on the BUY-only entry floor, not CONFIDENCE_FLOOR.

    NVDA raw_score 0.237 → conviction ~43, below min_entry_score 0.35.
    Coverage stays 0.89 on the intent so a TRIM of the same book still clears
    the 0.75 floor.
    """
    r = _run(
        gate_mod,
        raw_score_by_sym={"NVDA": 0.237, "MU": 0.237},
        min_entry_score=0.35,
    )
    nvda_intent = next(i for i in r.intents if i.symbol == "NVDA")
    mu_intent = next(i for i in r.intents if i.symbol == "MU")
    assert nvda_intent.confidence == pytest.approx(0.89)
    assert mu_intent.confidence == pytest.approx(0.78)
    nvda = next(g for g in r.gate_results if g["symbol"] == "NVDA")
    mu = next(g for g in r.gate_results if g["symbol"] == "MU")
    assert nvda["approved"] is False
    assert "entry floor" in (nvda["reason"] or "").lower()
    assert mu["approved"] is True
    assert ("record", "NVDA") not in r.events
    assert ("record", "MU") in r.events


def test_buy_blocked_by_precision_overlay_even_above_floor(gate_mod, monkeypatch):
    """Rel.Strength bearish vetoes a BUY that clears min_entry_score."""

    class _Block:
        blocked = True
        reason = "Rel.Strength bearish"

    class _Open:
        blocked = False
        reason = "ok"

    def _overlay(action, row=None):  # noqa: ARG001
        votes = (row or {}).get("votes") or {}
        if votes.get("Rel.Strength") == -1:
            return _Block()
        return _Open()

    monkeypatch.setattr(gate_mod, "precision_overlay_blocks", _overlay)
    r = _run(
        gate_mod,
        raw_score_by_sym={"NVDA": 0.62, "MU": 0.62},
        min_entry_score=0.50,
        consensus_by_sym={
            "NVDA": {
                "verdict": "STRONG BUY",
                "votes": {
                    "Momentum": 1,
                    "Trend": 1,
                    "Rel.Strength": -1,
                    "Volume": 1,
                    "Macro": 1,
                },
                "bullish": 4,
                "bearish": 1,
            },
        },
    )
    nvda = next(g for g in r.gate_results if g["symbol"] == "NVDA")
    mu = next(g for g in r.gate_results if g["symbol"] == "MU")
    assert nvda["approved"] is False
    assert "precision overlay" in (nvda["reason"] or "").lower()
    assert mu["approved"] is True
    assert ("record", "NVDA") not in r.events
    assert ("record", "MU") in r.events
