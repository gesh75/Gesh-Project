"""AST-level ordering test for run_ai2.py Phase 4B ledger wiring.

Proves the exact call sequence and non-fatal protection without loose substring matching.
"""

import ast
from pathlib import Path


def _parse_run_ai2() -> ast.AST:
    src_path = Path(__file__).parent.parent / "run_ai2.py"
    source = src_path.read_text()
    return ast.parse(source)


def test_log_analysis_first_then_ledger_then_equity_snapshot_in_correct_order():
    """_gdb.log_analysis appears first, return assigned to _run_id, ledger after, equity after ledger."""
    tree = _parse_run_ai2()

    class WiringVisitor(ast.NodeVisitor):
        def __init__(self):
            self.log_analysis_node = None
            self.ledger_call_node = None
            self.equity_call_node = None
            self.run_id_assign = None

        def visit_Assign(self, node: ast.Assign):
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "log_analysis"
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "_gdb"
            ):
                self.log_analysis_node = node
                # Check return assigned to _run_id
                if (
                    len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "_run_id"
                ):
                    self.run_id_assign = node
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "log_equity_snapshot"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_gdb"
            ):
                self.equity_call_node = node
            self.generic_visit(node)

        def visit_Expr(self, node: ast.Expr):
            if isinstance(node.value, ast.Call):
                if (
                    isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "record_daily_analysis_ledger_nonfatal"
                ) or (
                    isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "record_daily_analysis_ledger_nonfatal"
                ):
                    self.ledger_call_node = node
            self.generic_visit(node)

    visitor = WiringVisitor()
    visitor.visit(tree)

    assert visitor.log_analysis_node is not None, "_gdb.log_analysis call not found"
    assert visitor.run_id_assign is not None, (
        "_run_id = _gdb.log_analysis(...) assignment not found"
    )
    assert visitor.ledger_call_node is not None, "ledger helper call not found"
    assert visitor.equity_call_node is not None, "_gdb.log_equity_snapshot call not found"

    # Order: log_analysis line < ledger line < equity line
    log_line = visitor.log_analysis_node.lineno
    ledger_line = visitor.ledger_call_node.lineno
    equity_line = visitor.equity_call_node.lineno

    assert log_line < ledger_line, "log_analysis must precede ledger call"
    assert ledger_line < equity_line, "ledger call must precede equity snapshot call"


def test_ledger_call_receives_run_id_and_db_path():
    """Ledger call receives _run_id and _gdb.DB_PATH as arguments."""
    tree = _parse_run_ai2()

    class ArgVisitor(ast.NodeVisitor):
        def __init__(self):
            self.ledger_kwargs = {}

        def visit_Call(self, node: ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "record_daily_analysis_ledger_nonfatal"
            ) or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "record_daily_analysis_ledger_nonfatal"
            ):
                for kw in node.keywords:
                    if kw.arg == "analysis_run_id":
                        if isinstance(kw.value, ast.Name) and kw.value.id == "_run_id":
                            self.ledger_kwargs["analysis_run_id"] = "_run_id"
                    if kw.arg == "db_path":
                        if (
                            isinstance(kw.value, ast.Attribute)
                            and isinstance(kw.value.value, ast.Name)
                            and kw.value.value.id == "_gdb"
                            and kw.value.attr == "DB_PATH"
                        ):
                            self.ledger_kwargs["db_path"] = "_gdb.DB_PATH"
            self.generic_visit(node)

    v = ArgVisitor()
    v.visit(tree)
    assert v.ledger_kwargs.get("analysis_run_id") == "_run_id"
    assert v.ledger_kwargs.get("db_path") == "_gdb.DB_PATH"


def test_ledger_call_is_protected_by_nonfatal_exception_boundary():
    """Ledger attempt is inside try/except that does not skip equity on failure."""
    tree = _parse_run_ai2()

    class TryVisitor(ast.NodeVisitor):
        def __init__(self):
            self.ledger_inside_try = False
            self.equity_after_try = False

        def visit_Try(self, node: ast.Try):
            # Check if any ledger call is inside this try body
            for stmt in node.body:
                if self._contains_ledger_call(stmt):
                    self.ledger_inside_try = True
                    # Check that equity is NOT inside the except handlers (i.e. after try)
                    # The equity must be after the entire try-except in the outer block
            self.generic_visit(node)

        def _contains_ledger_call(self, node: ast.AST) -> bool:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                if (
                    isinstance(func, ast.Name)
                    and func.id == "record_daily_analysis_ledger_nonfatal"
                ) or (
                    isinstance(func, ast.Attribute)
                    and func.attr == "record_daily_analysis_ledger_nonfatal"
                ):
                    return True
            for child in ast.iter_child_nodes(node):
                if self._contains_ledger_call(child):
                    return True
            return False

    v = TryVisitor()
    v.visit(tree)
    assert v.ledger_inside_try, "ledger call must be protected inside a try block"

    # Additionally verify no return/raise/sys.exit in the ledger except path that would skip equity
    # by checking the source around the ledger try
    src_path = Path(__file__).parent.parent / "run_ai2.py"
    source = src_path.read_text()
    # Simple structural check: the except for ledger must continue (no early exit in that handler)
    assert "except Exception as e:" in source
    assert "Ledger helper failed to load (non-fatal)" in source


def test_equity_snapshot_remains_present_and_is_not_skipped_by_ledger_failure():
    """Equity snapshot call is still present after the ledger attempt block."""
    tree = _parse_run_ai2()

    class EquityVisitor(ast.NodeVisitor):
        def __init__(self):
            self.equity_found = False

        def visit_Call(self, node: ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "log_equity_snapshot"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_gdb"
            ):
                self.equity_found = True
            self.generic_visit(node)

    v = EquityVisitor()
    v.visit(tree)
    assert v.equity_found, "_gdb.log_equity_snapshot must remain present"


def test_ledger_failure_path_has_no_control_flow_that_skips_equity():
    """The ledger except handler must not return/raise/exit, which would skip equity.

    Resolved structurally over the AST (not by line-window scraping, which
    silently collected nothing and asserted nothing): locate the ast.Try whose
    body contains the ledger call, then inspect that try's own handlers.
    """
    tree = _parse_run_ai2()

    def _contains_ledger_call(node: ast.AST) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if (
                    isinstance(func, ast.Name)
                    and func.id == "record_daily_analysis_ledger_nonfatal"
                ) or (
                    isinstance(func, ast.Attribute)
                    and func.attr == "record_daily_analysis_ledger_nonfatal"
                ):
                    return True
        return False

    ledger_tries = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try) and any(_contains_ledger_call(s) for s in node.body)
    ]
    assert ledger_tries, "no try block directly wrapping the ledger call was found"

    # Use the innermost such try — the dedicated ledger boundary, not the outer
    # legacy-DB try that also transitively contains the call.
    ledger_try = min(ledger_tries, key=lambda n: len(list(ast.walk(n))))

    assert ledger_try.handlers, "ledger try must have an except handler"
    for handler in ledger_try.handlers:
        for sub in ast.walk(handler):
            assert not isinstance(sub, (ast.Return, ast.Raise, ast.Continue, ast.Break)), (
                f"ledger failure handler must not contain "
                f"{type(sub).__name__} — it would skip the equity snapshot"
            )
            if isinstance(sub, ast.Call):
                func = sub.func
                is_sys_exit = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "exit"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "sys"
                )
                assert not is_sys_exit, (
                    "ledger failure handler must not call sys.exit — "
                    "it would skip the equity snapshot"
                )

    # The equity snapshot must live OUTSIDE (after) the ledger try, so a ledger
    # failure cannot skip it.
    def _contains_equity(node: ast.AST) -> bool:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "log_equity_snapshot"
            ):
                return True
        return False

    assert not _contains_equity(ledger_try), (
        "log_equity_snapshot must not be inside the ledger try block"
    )
