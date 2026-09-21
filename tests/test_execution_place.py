"""place() status mapping — SKIPPED / ERROR / HOLD / reject / live.

Sizing math lives in test_execution_port.py + test_paper_book.py. This file
pins the *fill* contract: a zero-size or unpriced approved intent must never
become a SIMULATED fill (that pads the autonomy-gate close count), a rejected
intent never fills, HOLD writes qty 0, and live mode stays unwired.

Loaded by rewriting relative imports onto an in-process stub so this suite
does not need gesh-core (``trading/__init__.py`` aliases ``trading.state``).
Both ``from .state import …`` and ``from . import state as _trading_state``
must be remapped — the latter reads ``DB_PATH`` at call time so ledger
sandboxing still works. When gesh-core *is* installed the real RiskConfig /
GateVerdict / state DB are still not used — the stub is the only I/O
surface, so CI and this VM exercise the same place() branches.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_STUB_NAME = "gesh_exec_place_stubs"
_SRC = Path(__file__).resolve().parents[1] / "trading" / "execution.py"


def _install_stubs() -> ModuleType:
    stub = ModuleType(_STUB_NAME)
    stub._orders: list[dict] = []
    stub._seen_coids: set[str] = set()
    stub._paper_book: dict = {}
    stub._paper_book_exc: Exception | None = None
    stub.alert_calls: list[tuple] = []

    def alert_order_fill(*a, **k):
        stub.alert_calls.append(("fill", a, k))

    def alert_rejection(*a, **k):
        stub.alert_calls.append(("reject", a, k))

    class RiskConfig:
        def __init__(self, paper_mode: bool = True, paper_cash_usd: float = 100_000.0):
            self.paper_mode = paper_mode
            self.paper_cash_usd = paper_cash_usd

    def load_risk_config():
        return RiskConfig()

    class GateVerdict:
        def __init__(
            self,
            approved: bool,
            reason: str | None = None,
            gates: dict | None = None,
            intent_id: int = 1,
            decision_id: int = 1,
        ):
            self.approved = approved
            self.reason = reason
            self.gates = gates or {}
            self.intent_id = intent_id
            self.decision_id = decision_id

    def log_order(**kwargs):
        coid = kwargs.get("client_order_id")
        if coid in stub._seen_coids:
            raise sqlite3.IntegrityError("UNIQUE constraint failed: orders.client_order_id")
        stub._seen_coids.add(coid)
        stub._orders.append(kwargs)
        return len(stub._orders)

    def paper_open_positions():
        if stub._paper_book_exc is not None:
            raise stub._paper_book_exc
        return stub._paper_book

    def record_stop(**_k):
        return None

    def deactivate_stops(_symbol):
        return None

    def active_stops():
        return {}

    stub.alert_order_fill = alert_order_fill
    stub.alert_rejection = alert_rejection
    stub.RiskConfig = RiskConfig
    stub.load_risk_config = load_risk_config
    stub.TradeIntent = SimpleNamespace
    stub.GateVerdict = GateVerdict
    stub.log_order = log_order
    stub.paper_open_positions = paper_open_positions
    stub.record_stop = record_stop
    stub.deactivate_stops = deactivate_stops
    stub.active_stops = active_stops
    # Overridden by the exe fixture with a real temp ledger (orders table).
    stub.DB_PATH = ":memory:"
    return stub


def _rewrite_execution_src() -> str:
    """Alias every trading.* relative import onto the in-process stub."""
    return (
        _SRC.read_text()
        .replace("from .alerts import", f"from {_STUB_NAME} import")
        .replace("from .config import", f"from {_STUB_NAME} import")
        .replace("from .contract import", f"from {_STUB_NAME} import")
        .replace("from .risk_gate import", f"from {_STUB_NAME} import")
        .replace("from .state import", f"from {_STUB_NAME} import")
        .replace("from . import state as _trading_state", f"import {_STUB_NAME} as _trading_state")
    )


def _load_execution(stub: ModuleType):
    sys.modules[_STUB_NAME] = stub
    rewritten = _rewrite_execution_src()
    spec = importlib.util.spec_from_loader("gesh_execution_place", loader=None)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    # dataclass looks up the class module in sys.modules; register first.
    sys.modules["gesh_execution_place"] = mod
    exec(compile(rewritten, str(_SRC), "exec"), mod.__dict__)  # noqa: S102
    return mod


@pytest.fixture
def exe(tmp_path):
    stub = _install_stubs()
    stub.DB_PATH = str(tmp_path / "orders.db")
    with sqlite3.connect(stub.DB_PATH) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, client_order_id TEXT)"
        )
    mod = _load_execution(stub)
    return mod, stub


def _intent(action="BUY", symbol="HOOD", target=0.10, generated_at="2026-09-05T10:00:00"):
    return SimpleNamespace(
        symbol=symbol,
        action=action,
        target_position_pct=target,
        generated_at=generated_at,
        confidence=0.9,
        rationale=None,
        stop_loss=None,
        take_profit=None,
    )


def _snap(equity=10_000.0, symbol="HOOD", price=100.0, qty=0.0, held_equity=0.0):
    holdings = []
    if price is not None:
        holdings.append(
            {
                "symbol": symbol,
                "quantity": qty,
                "current_price": price,
                "equity": held_equity,
            }
        )
    return {
        "equity_summary": {"portfolio_equity": equity},
        "holdings": holdings,
        "watchlist": [],
    }


def test_rewriter_aliases_state_module_import():
    """``from . import state as _trading_state`` must not survive as a relative import."""
    rewritten = _rewrite_execution_src()
    assert "from . import state" not in rewritten
    assert f"import {_STUB_NAME} as _trading_state" in rewritten
    stub = _install_stubs()
    mod = _load_execution(stub)
    assert mod._trading_state is stub
    assert stub.DB_PATH == ":memory:"


def test_zero_size_buy_is_skipped_not_simulated(exe):
    """Already at target → qty 0 must not become a SIMULATED fill (autonomy pad)."""
    execution, stub = exe
    # Paper mode sizes from the paper book, not the snapshot holdings.
    stub._paper_book = {"HOOD": {"qty": 10.0}}  # 10 × $100 = $1000 = 10% of $10k
    result = execution.place(
        _intent("BUY", target=0.10),
        stub.GateVerdict(True),
        _snap(price=100.0),
        stub.RiskConfig(paper_mode=True),
    )
    assert result.status == "SKIPPED"
    assert result.ok is False
    assert result.fill_price is None
    assert stub._orders[-1]["status"] == "SKIPPED"
    assert stub._orders[-1]["status"] != "SIMULATED"
    assert not any(kind == "fill" for kind, *_ in stub.alert_calls)


def test_paper_sell_with_no_position_is_skipped(exe):
    """SELL of a name the paper book does not hold must not invent a short fill."""
    execution, stub = exe
    result = execution.place(
        _intent("SELL", target=0.0),
        stub.GateVerdict(True),
        _snap(price=100.0),
        stub.RiskConfig(paper_mode=True),
    )
    assert result.status == "SKIPPED"
    assert result.ok is False
    assert not any(kind == "fill" for kind, *_ in stub.alert_calls)


def test_live_already_at_target_is_skipped_not_live_order(exe):
    """qty 0 is decided before the live NotImplementedError — no accidental broker call."""
    execution, stub = exe
    result = execution.place(
        _intent("BUY", target=0.10),
        stub.GateVerdict(True),
        _snap(price=100.0, qty=10.0, held_equity=1_000.0),
        stub.RiskConfig(paper_mode=False),
    )
    assert result.status == "SKIPPED"
    assert result.ok is False


def test_missing_price_is_error_not_crash(exe):
    execution, stub = exe
    cfg = stub.RiskConfig(paper_mode=True)
    result = execution.place(
        _intent("BUY", symbol="ZZZZ"),
        stub.GateVerdict(True),
        {
            "equity_summary": {"portfolio_equity": 10_000.0},
            "holdings": [],
            "watchlist": [],
        },
        cfg,
    )
    assert result.status == "ERROR"
    assert result.ok is False
    assert "no reference price" in (result.error or "")
    assert stub._orders[-1]["status"] == "ERROR"


def test_zero_price_is_unpriced_error(exe):
    """$0 last is missing, not a free share — fail-soft ERROR, not a fill."""
    execution, stub = exe
    result = execution.place(
        _intent("BUY"),
        stub.GateVerdict(True),
        _snap(price=0.0),
        stub.RiskConfig(paper_mode=True),
    )
    assert result.status == "ERROR"
    assert result.ok is False


def test_hold_is_simulated_qty_zero_without_fill_alert(exe):
    execution, stub = exe
    result = execution.place(
        _intent("HOLD", target=None),
        stub.GateVerdict(True),
        _snap(),
        stub.RiskConfig(paper_mode=True),
    )
    assert result.ok is True
    assert result.status == "SIMULATED"
    assert result.qty == 0.0
    assert result.fill_price is None
    assert stub._orders[-1]["action"] == "HOLD"
    assert not any(kind == "fill" for kind, *_ in stub.alert_calls)


def test_rejected_intent_never_fills(exe):
    execution, stub = exe
    result = execution.place(
        _intent("BUY"),
        stub.GateVerdict(False, reason="POSITION_CAP", gates={"POSITION_CAP": False}),
        _snap(),
        stub.RiskConfig(paper_mode=True),
    )
    assert result.ok is False
    assert result.status == "REJECTED"
    assert result.fill_price is None
    assert stub._orders[-1]["status"] == "REJECTED"
    assert stub.alert_calls[0][0] == "reject"
    assert not any(kind == "fill" for kind, *_ in stub.alert_calls)


def test_live_mode_stays_unwired(exe):
    execution, stub = exe
    with pytest.raises(NotImplementedError, match="Live execution is not wired"):
        execution.place(
            _intent("BUY", target=0.10),
            stub.GateVerdict(True),
            _snap(price=100.0, qty=0.0, held_equity=0.0),
            stub.RiskConfig(paper_mode=False),
        )
    assert stub._orders == []  # raised before any live broker row


def test_paper_book_read_failure_does_not_block_sizing(exe):
    """Broken paper-book read degrades to qty-from-zero, never crashes place()."""
    execution, stub = exe
    stub._paper_book_exc = RuntimeError("db locked")
    result = execution.place(
        _intent("BUY", target=0.10),
        stub.GateVerdict(True),
        _snap(price=100.0),
        stub.RiskConfig(paper_mode=True),
    )
    assert result.status == "SIMULATED"
    assert result.ok is True
    assert result.qty == pytest.approx(10.0)  # $1000 / $100, as if no current pos


def test_duplicate_client_order_id_is_swallowed(exe):
    """UNIQUE replay is DUPLICATE, not a second SIMULATED fill (Astra R4)."""
    execution, stub = exe
    intent = _intent("BUY", target=0.02, generated_at="2026-09-05T10:00:00")
    first = execution.place(intent, stub.GateVerdict(True), _snap(), stub.RiskConfig())
    assert first.status == "SIMULATED"
    # Same minute-bucket seed → UNIQUE on client_order_id
    second = execution.place(intent, stub.GateVerdict(True), _snap(), stub.RiskConfig())
    assert second.status == "DUPLICATE"
    assert second.ok is False
    assert second.order_row_id is None  # stub ledger has no sqlite row to reuse
    assert len(stub._orders) == 1
    assert sum(1 for kind, *_ in stub.alert_calls if kind == "fill") == 1


def test_client_order_id_is_stable_across_uuid(exe):
    execution, _stub = exe
    a = SimpleNamespace(
        symbol="HOOD",
        action="BUY",
        target_position_pct=0.02,
        generated_at="2026-09-05T10:00:11",
        intent_uuid="aaa",
    )
    b = SimpleNamespace(
        symbol="HOOD",
        action="BUY",
        target_position_pct=0.02,
        generated_at="2026-09-05T10:00:59",
        intent_uuid="bbb",
    )
    # Minute bucket is [:16] → both 2026-09-05T10:00
    assert execution._client_order_id(a) == execution._client_order_id(b)
    c = SimpleNamespace(
        symbol="HOOD",
        action="BUY",
        target_position_pct=0.02,
        generated_at="2026-09-05T10:01:00",
        intent_uuid="aaa",
    )
    assert execution._client_order_id(a) != execution._client_order_id(c)
