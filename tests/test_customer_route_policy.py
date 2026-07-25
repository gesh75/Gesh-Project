"""Structural authorization alignment tests for customer_route_policy.

Declaration-only structural tests that inspect every runtime /api route and its
recursive FastAPI dependency graph. All policy imports use importlib.import_module
so that import failures fail hard (no pytest.importorskip or find_spec masking).

Tests derive expectations independently:
- runtime routes: live FastAPI APIRoute inspection on server.app
- static routes: AST walk over literal app.get/app.post/app.delete/put/patch/head/options/trace decorators
- dependency inspection: recursive walk of Depends objects to identify the
  *actual* target callable (by identity), never by source text

Hard-coded literal sets are preserved exactly as declared for the categories
explicitly required by the task (customer-owned, public RAW_SAFE, historical/cached,
known internal owner-only /api/usage/summary, and PUBLIC_ACCESS_ROUTES).
No policy table is used to generate expectations.

All /api/mybook and /api/account routes are asserted CUSTOMER + RAW_SAFE +
NOT_APPLICABLE (CUSTOMER_BOOK or NONE input).

GET /api/portfolio is asserted CURRENT_MODEL + PUBLISHED_PORTFOLIO + TYPED_503.

SANITIZE_NO_SCALE policies require nonempty forbidden_private_fields + schema_proof.
REQUEST_SCALED policies require nonempty financial_fields + scaling_basis_contract.

Exact unknown lookup must raise KeyError (no generic fallback).
"""

from __future__ import annotations

import ast
import importlib
from collections import Counter
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Hard-coded literal sets (deliberate — not generated from any policy table)
# ---------------------------------------------------------------------------

CUSTOMER_OWNED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/mybook"),
        ("POST", "/api/mybook"),
        ("DELETE", "/api/mybook/{symbol}"),
        ("POST", "/api/mybook/clear"),
        ("POST", "/api/mybook/analyze"),
        ("POST", "/api/mybook/import-watchlist"),
        ("POST", "/api/mybook/bulk"),
        ("POST", "/api/mybook/deploy-cash"),
        ("GET", "/api/mybook/summary"),
        ("GET", "/api/account/export"),
        ("POST", "/api/account/delete"),
        ("GET", "/api/me"),
        ("GET", "/api/watchlist"),
        ("POST", "/api/watchlist"),
        ("DELETE", "/api/watchlist/{symbol}"),
        ("GET", "/api/preferences"),
        ("POST", "/api/preferences"),
    }
)

STATELESS_RAW_SAFE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/chart/{symbol}"),
        ("GET", "/api/analyst/{symbol}"),
        ("GET", "/api/fundamentals/{symbol}"),
        ("GET", "/api/news/{symbol}"),
        ("GET", "/api/momentum/{symbol}"),
        ("GET", "/api/levels/{symbol}"),
        ("GET", "/api/earnings/{symbol}"),
        ("GET", "/api/regime/{symbol}"),
        ("GET", "/api/fft-cycles/{symbol}"),
        ("GET", "/api/symbol-search"),
        ("GET", "/api/signals/history/{symbol}"),
        ("GET", "/api/congress/{symbol}"),
        ("GET", "/api/congress"),
        ("GET", "/api/volatility/compare/{symbol}"),
        ("POST", "/api/analyze-ticker"),
        ("GET", "/api/scanner/consolidation/{symbol}"),
        ("GET", "/api/scanner/sector/{symbol}"),
        ("GET", "/api/long-term-macro"),
        ("GET", "/api/long-term-macro/cached"),
        ("GET", "/api/agents/fundamentals/latest"),
        ("GET", "/api/screener/cached"),
        ("GET", "/api/sections/status"),
        ("GET", "/api/security/health"),
        ("GET", "/api/ollama/status"),
        ("GET", "/api/gemini/status"),
        ("POST", "/api/billing/webhook"),
    }
)

HISTORICAL_CACHED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/deep-analysis/cached"),
        ("GET", "/api/consensus/cached"),
        ("GET", "/api/kelly/cached"),
        ("GET", "/api/monte-carlo/cached"),
        ("GET", "/api/screener/cached"),
        ("GET", "/api/long-term-macro/cached"),
        ("GET", "/api/agents/fundamentals/latest"),
        ("GET", "/api/history"),
        ("GET", "/api/history/{run_id}"),
        ("GET", "/api/portfolio/history"),
        ("GET", "/api/agents/sec/history"),
        ("GET", "/api/agents/news/history"),
        ("GET", "/api/trades"),
        ("GET", "/api/trades/stats"),
        ("GET", "/api/paper/summary"),
        ("GET", "/api/signals/history/{symbol}"),
        ("GET", "/api/consensus/{symbol}"),
        ("GET", "/api/explainability/{symbol}"),
    }
)

KNOWN_INTERNAL_OWNER_ONLY: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/usage/summary"),
    }
)

PUBLIC_ACCESS_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/billing/webhook"),
        ("GET", "/api/congress"),
        ("GET", "/api/congress/{symbol}"),
    }
)

CUSTOMER_CAPABILITY_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/signals/history/{symbol}"),
        ("POST", "/api/analyze-ticker"),
        ("POST", "/api/mybook/analyze"),
    }
)

HISTORICAL_BLOCKED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/history"),
        ("GET", "/api/history/{run_id}"),
        ("GET", "/api/portfolio/history"),
        ("GET", "/api/deep-analysis/cached"),
        ("GET", "/api/consensus/cached"),
        ("GET", "/api/kelly/cached"),
        ("GET", "/api/monte-carlo/cached"),
        ("GET", "/api/trades"),
        ("GET", "/api/trades/stats"),
        ("GET", "/api/paper/summary"),
        ("GET", "/api/consensus/{symbol}"),
        ("GET", "/api/explainability/{symbol}"),
    }
)


# Independent exact literals for the two output categories (SANITIZE_NO_SCALE and REQUEST_SCALED)
# declared here as the single source of truth for the new assertions below.
SANITIZE_NO_SCALE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/market-data"),
        ("GET", "/api/regime"),
        ("GET", "/api/agents/sec/history"),
        ("GET", "/api/agents/news/history"),
        ("GET", "/api/fft-cycles"),
        ("GET", "/api/benchmarks/{period}"),
        ("GET", "/api/crypto-correlation"),
    }
)

REQUEST_SCALED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/ecg"),
        ("GET", "/api/yield/landscape"),
    }
)

EXPECTED_REQUEST_SCALED_FIELDS: dict[tuple[str, str], frozenset[tuple[str, str]]] = {
    ("GET", "/api/ecg"): frozenset(
        {
            ("data.states[*].equity_history[*]", "MONEY_ARRAY"),
            ("data.states[*].drawdown_pct", "PERCENTAGE"),
            ("data.states[*].message", "PROSE_REDACTION"),
            ("data.portfolio_message", "PROSE_REDACTION"),
        }
    ),
    ("GET", "/api/yield/landscape"): frozenset(
        {
            ("cash_usd", "PORTFOLIO_DOLLARS"),
            ("recommendation.monthly_income_usd", "PORTFOLIO_DOLLARS"),
            ("recommendation.annual_income_usd", "PORTFOLIO_DOLLARS"),
            ("monthly_projection[*].income_usd", "MONEY_ARRAY"),
            ("monthly_projection[*].cumulative", "MONEY_ARRAY"),
            ("tbill_rate_pct", "PERCENTAGE"),
            ("fed_rate_pct", "PERCENTAGE"),
            ("tiers.*[*].apy", "PERCENTAGE"),
            ("recommendation.apy", "PERCENTAGE"),
            ("tiers.tier3[*].tvl_usd", "PUBLIC_EXTERNAL_USD"),
            ("recommendation.reason", "PROSE_REDACTION"),
        }
    ),
}


# ---------------------------------------------------------------------------
# Independent derivation helpers (runtime, static, dependency inspection)
# ---------------------------------------------------------------------------

def _get_runtime_api_routes_list() -> list[tuple[str, str]]:
    """List version preserved for duplicate detection (filters /api/ + API methods)."""
    from fastapi.routing import APIRoute

    import server

    app = server.app
    routes: list[tuple[str, str]] = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            for method in route.methods:
                routes.append((method, route.path))
    return routes


def _get_runtime_api_routes() -> set[tuple[str, str]]:
    """Set version (exposed separately)."""
    return set(_get_runtime_api_routes_list())


def _get_static_api_routes_list() -> list[tuple[str, str]]:
    """List version preserved for duplicate detection (visits FunctionDef + AsyncFunctionDef)."""
    source = Path("server.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server.py")
    routes: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in {"get", "post", "delete", "put", "patch", "head", "options", "trace"}
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == "app"
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)
                    and dec.args[0].value.startswith("/api/")
                ):
                    method = dec.func.attr.upper()
                    path = dec.args[0].value
                    routes.append((method, path))
    return routes


def _get_static_api_routes() -> set[tuple[str, str]]:
    """Set version (exposed separately)."""
    return set(_get_static_api_routes_list())


def _get_runtime_handler_name(method: str, path: str) -> str:
    """Handler name from live APIRoute.endpoint.__name__."""
    from fastapi.routing import APIRoute

    import server

    app = server.app
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route.endpoint.__name__
    raise KeyError(f"no runtime route {method} {path}")


def _route_uses_dependency(method: str, path: str, target: Any) -> bool:
    """Generic recursive dependency inspection by identity.

    Traverses the FastAPI Dependant graph starting at route.dependant.dependencies,
    comparing each Dependant.call by identity to the supplied target (never source text).
    """
    from fastapi.routing import APIRoute

    import server

    app = server.app

    def _walk(dependants: Any, seen: set[int]) -> bool:
        if not dependants:
            return False
        for d in dependants if isinstance(dependants, (list, tuple)) else [dependants]:
            if id(d) in seen:
                continue
            seen.add(id(d))
            if getattr(d, "call", None) is target:
                return True
            if hasattr(d, "dependencies"):
                if _walk(d.dependencies, seen):
                    return True
        return False

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            if hasattr(route, "dependant") and route.dependant is not None:
                if _walk(route.dependant.dependencies, set()):
                    return True
    return False


def _route_uses_require_api_key(method: str, path: str) -> bool:
    """Thin wrapper: delegates to the generic identity helper for require_api_key."""
    import server

    return _route_uses_dependency(method, path, server.require_api_key)


# ---------------------------------------------------------------------------
# Structural first test (assertion failure, not collection error)
# ---------------------------------------------------------------------------

def test_module_is_importable():
    """Module importability check (permanent docstring, not RED-only)."""
    importlib.import_module("customer_route_policy")


def test_no_duplicate_runtime_routes():
    """Explicit duplicate-runtime assertion: list has no dups."""
    lst = _get_runtime_api_routes_list()
    assert len(lst) == len(set(lst)), "duplicate runtime (method, path) detected"


def test_no_duplicate_static_routes():
    """Explicit duplicate-static assertion: list has no dups."""
    lst = _get_static_api_routes_list()
    assert len(lst) == len(set(lst)), "duplicate static (method, path) detected"


# ---------------------------------------------------------------------------
# Structural tests for the ten requirements + two agreements
# ---------------------------------------------------------------------------

def test_runtime_route_set_equals_declared_policy_set():
    """Requirement 1: runtime inventory == declared policy inventory."""
    runtime = _get_runtime_api_routes()
    mod = importlib.import_module("customer_route_policy")
    declared = { (p.method, p.path) for p in mod.get_all_route_policies() }
    assert runtime == declared


def test_no_duplicate_route_policies():
    """Requirement 2: no duplicate (method, path) policy entries."""
    mod = importlib.import_module("customer_route_policy")
    policies = mod.get_all_route_policies()
    seen = set()
    for p in policies:
        key = (p.method, p.path)
        assert key not in seen, f"duplicate policy for {key}"
        seen.add(key)


def test_every_require_api_key_route_is_owner_only():
    """Requirement 3: recursive dep inspection → OWNER_ONLY + BLOCK + OWNER_ONLY."""
    mod = importlib.import_module("customer_route_policy")
    Access = mod.AccessPolicy
    Output = mod.OutputPolicy
    Missing = mod.MissingContextPolicy

    for method, path in _get_runtime_api_routes():
        if _route_uses_require_api_key(method, path):
            policy = mod.get_route_policy(method, path)
            assert policy.access_policy is Access.OWNER_ONLY
            assert policy.output_policy is Output.BLOCK
            assert policy.missing_context_policy is Missing.OWNER_ONLY


def test_all_mybook_and_account_routes_are_customer_raw_safe():
    """Requirement 4: every mybook/account route is CUSTOMER + RAW_SAFE + NOT_APPLICABLE."""
    mod = importlib.import_module("customer_route_policy")
    Access = mod.AccessPolicy
    Output = mod.OutputPolicy
    Missing = mod.MissingContextPolicy
    InputC = mod.InputContext

    runtime = _get_runtime_api_routes()
    mybook_family = {
        (m, p) for m, p in runtime
        if p == "/api/mybook" or p.startswith("/api/mybook/")
    }
    account_family = {
        (m, p) for m, p in runtime
        if p == "/api/account" or p.startswith("/api/account/")
    }
    live_families = mybook_family | account_family
    union_routes = live_families | CUSTOMER_OWNED_ROUTES

    for method, path in union_routes:
        policy = mod.get_route_policy(method, path)
        assert policy.access_policy is Access.CUSTOMER
        assert policy.output_policy is Output.RAW_SAFE
        assert policy.missing_context_policy is Missing.NOT_APPLICABLE
        if path == "/api/mybook" or path.startswith("/api/mybook/"):
            assert policy.input_context is InputC.CUSTOMER_BOOK
        else:
            assert policy.input_context is InputC.NONE


def test_get_api_portfolio_is_published_portfolio():
    """Requirement 5: /api/portfolio is CURRENT_MODEL + PUBLISHED_PORTFOLIO + TYPED_503."""
    mod = importlib.import_module("customer_route_policy")
    policy = mod.get_route_policy("GET", "/api/portfolio")
    assert policy.access_policy is mod.AccessPolicy.CUSTOMER
    assert policy.input_context is mod.InputContext.CURRENT_MODEL
    assert policy.output_policy is mod.OutputPolicy.PUBLISHED_PORTFOLIO
    assert policy.missing_context_policy is mod.MissingContextPolicy.TYPED_503


def test_public_per_symbol_routes_are_raw_safe():
    """Requirement 6: public prices/ratios/indicators are RAW_SAFE (NONE input)."""
    mod = importlib.import_module("customer_route_policy")
    Output = mod.OutputPolicy
    InputC = mod.InputContext

    for method, path in STATELESS_RAW_SAFE_ROUTES:
        policy = mod.get_route_policy(method, path)
        assert policy.output_policy is Output.RAW_SAFE
        assert policy.input_context is InputC.NONE


def test_no_historical_route_uses_request_scaled_with_current_model():
    """Requirement 7: historical/cached routes never REQUEST_SCALED + CURRENT_MODEL."""
    mod = importlib.import_module("customer_route_policy")
    Output = mod.OutputPolicy
    InputC = mod.InputContext

    for method, path in HISTORICAL_CACHED_ROUTES:
        policy = mod.get_route_policy(method, path)
        if policy.output_policy is Output.REQUEST_SCALED:
            assert policy.input_context is not InputC.CURRENT_MODEL


def test_every_request_scaled_declares_financial_fields_and_scaling_contract():
    """Requirement 8: REQUEST_SCALED policies have nonempty financial_fields + scaling_basis_contract."""
    mod = importlib.import_module("customer_route_policy")
    Output = mod.OutputPolicy

    for policy in mod.get_all_route_policies():
        if policy.output_policy is Output.REQUEST_SCALED:
            assert len(policy.financial_fields) > 0, f"{policy.method} {policy.path} missing financial_fields"
            assert policy.scaling_basis_contract, f"{policy.method} {policy.path} missing scaling_basis_contract"


def test_every_sanitize_no_scale_declares_forbidden_fields_and_schema_proof():
    """Requirement 9: SANITIZE_NO_SCALE policies have nonempty forbidden_private_fields + schema_proof."""
    mod = importlib.import_module("customer_route_policy")
    Output = mod.OutputPolicy

    for policy in mod.get_all_route_policies():
        if policy.output_policy is Output.SANITIZE_NO_SCALE:
            assert len(policy.forbidden_private_fields) > 0, f"{policy.method} {policy.path} missing forbidden_private_fields"
            assert policy.schema_proof, f"{policy.method} {policy.path} missing schema_proof"


def test_no_generic_fallback_exact_lookup_raises_keyerror():
    """Requirement 10: unknown method/path must raise KeyError (no default/fallback)."""
    mod = importlib.import_module("customer_route_policy")
    with pytest.raises(KeyError):
        mod.get_route_policy("GET", "/api/__nonexistent_route_for_red_test__")


def test_runtime_vs_static_inventory_agreement():
    """Extra: runtime set == static AST-derived set (no drift)."""
    runtime = _get_runtime_api_routes()
    static = _get_static_api_routes()
    assert runtime == static, (
        f"runtime {len(runtime)} != static {len(static)}; "
        f"only in runtime: {runtime - static}; only in static: {static - runtime}"
    )


def test_handler_name_agreement():
    """Extra: every policy handler_name matches runtime endpoint.__name__."""
    mod = importlib.import_module("customer_route_policy")
    for policy in mod.get_all_route_policies():
        runtime_name = _get_runtime_handler_name(policy.method, policy.path)
        assert policy.handler_name == runtime_name, (
            f"handler mismatch {policy.method} {policy.path}: "
            f"policy={policy.handler_name} runtime={runtime_name}"
        )


def test_usage_summary_is_owner_only_block_owner_only():
    """Test using KNOWN_INTERNAL_OWNER_ONLY: /api/usage/summary is OWNER_ONLY/BLOCK/OWNER_ONLY."""
    mod = importlib.import_module("customer_route_policy")
    Access = mod.AccessPolicy
    Output = mod.OutputPolicy
    Missing = mod.MissingContextPolicy
    for method, path in KNOWN_INTERNAL_OWNER_ONLY:
        policy = mod.get_route_policy(method, path)
        assert policy.access_policy is Access.OWNER_ONLY
        assert policy.output_policy is Output.BLOCK
        assert policy.missing_context_policy is Missing.OWNER_ONLY


def test_historical_blocked_routes_are_block_typed_503():
    """HISTORICAL_BLOCKED: each exact route must be CUSTOMER + HISTORICAL_DATASET + BLOCK + TYPED_503 with nonempty scaling_basis_contract."""
    mod = importlib.import_module("customer_route_policy")
    Access = mod.AccessPolicy
    InputC = mod.InputContext
    Output = mod.OutputPolicy
    Missing = mod.MissingContextPolicy
    for method, path in HISTORICAL_BLOCKED_ROUTES:
        policy = mod.get_route_policy(method, path)
        assert policy.access_policy is Access.CUSTOMER
        assert policy.input_context is InputC.HISTORICAL_DATASET
        assert policy.output_policy is Output.BLOCK
        assert policy.missing_context_policy is Missing.TYPED_503
        assert policy.scaling_basis_contract, f"{method} {path} scaling_basis_contract must be nonempty"


def test_route_policy_is_frozen_dataclass_and_exact_enum_names():
    """Enforce RoutePolicy is frozen dataclass + exact enum member names."""
    mod = importlib.import_module("customer_route_policy")
    assert is_dataclass(mod.RoutePolicy)
    params = getattr(mod.RoutePolicy, "__dataclass_params__", None)
    assert params is not None and params.frozen

    # exact enum member names via __members__ key set equality
    assert set(mod.AccessPolicy.__members__.keys()) == {"PUBLIC", "CUSTOMER", "OWNER_ONLY"}
    assert set(mod.InputContext.__members__.keys()) == {"NONE", "CUSTOMER_BOOK", "CURRENT_MODEL", "HISTORICAL_DATASET"}
    assert set(mod.OutputPolicy.__members__.keys()) == {"RAW_SAFE", "PUBLISHED_PORTFOLIO", "REQUEST_SCALED", "DATASET_SCALED", "SANITIZE_NO_SCALE", "BLOCK"}
    assert set(mod.MissingContextPolicy.__members__.keys()) == {"PASS_THROUGH", "TYPED_503", "NOT_APPLICABLE", "OWNER_ONLY"}


def test_structural_authorization_alignment():
    """Structural authorization-alignment test: iterates every runtime route using _route_uses_require_api_key + sets."""
    mod = importlib.import_module("customer_route_policy")
    Access = mod.AccessPolicy
    Output = mod.OutputPolicy
    Missing = mod.MissingContextPolicy

    for method, path in _get_runtime_api_routes():
        key = (method, path)
        if _route_uses_require_api_key(method, path):
            policy = mod.get_route_policy(method, path)
            assert policy.access_policy is Access.OWNER_ONLY
            assert policy.output_policy is Output.BLOCK
            assert policy.missing_context_policy is Missing.OWNER_ONLY
        elif key in PUBLIC_ACCESS_ROUTES:
            policy = mod.get_route_policy(method, path)
            assert policy.access_policy is Access.PUBLIC
        elif key in KNOWN_INTERNAL_OWNER_ONLY:
            policy = mod.get_route_policy(method, path)
            assert policy.access_policy is Access.OWNER_ONLY
            assert policy.output_policy is Output.BLOCK
            assert policy.missing_context_policy is Missing.OWNER_ONLY
        else:
            policy = mod.get_route_policy(method, path)
            assert policy.access_policy is Access.CUSTOMER


def test_customer_capability_routes_exact_and_customer_policy():
    """Live runtime set whose recursive graph uses server.require_customer_or_owner
    must equal the independent CUSTOMER_CAPABILITY_ROUTES literal exactly;
    every such route must be AccessPolicy.CUSTOMER.
    """
    mod = importlib.import_module("customer_route_policy")
    Access = mod.AccessPolicy

    import server

    target = server.require_customer_or_owner
    live_set = {
        (method, path)
        for method, path in _get_runtime_api_routes()
        if _route_uses_dependency(method, path, target)
    }
    assert live_set == CUSTOMER_CAPABILITY_ROUTES
    for method, path in live_set:
        policy = mod.get_route_policy(method, path)
        assert policy.access_policy is Access.CUSTOMER


def test_public_routes_via_auth_users_is_public_path():
    """Live public routes via auth_users.is_public_path(path) must equal
    PUBLIC_ACCESS_ROUTES exactly; for every runtime API route, policy is PUBLIC
    if and only if the route is in that live public set.
    """
    mod = importlib.import_module("customer_route_policy")
    Access = mod.AccessPolicy

    import auth_users

    live_public = {
        (method, path)
        for method, path in _get_runtime_api_routes()
        if auth_users.is_public_path(path)
    }
    assert live_public == PUBLIC_ACCESS_ROUTES
    for method, path in _get_runtime_api_routes():
        policy = mod.get_route_policy(method, path)
        is_in_live = (method, path) in live_public
        assert (policy.access_policy is Access.PUBLIC) == is_in_live


def test_market_data_public_insider_shares_are_not_forbidden():
    """GET /api/market-data: forbidden_private_fields must NOT contain unqualified 'shares';
    schema_proof must include both 'insider_trades' and 'shares'."""
    mod = importlib.import_module("customer_route_policy")
    policy = mod.get_route_policy("GET", "/api/market-data")
    assert "shares" not in policy.forbidden_private_fields, (
        "unqualified string 'shares' must not appear in forbidden_private_fields for /api/market-data"
    )
    assert "insider_trades" in policy.schema_proof
    assert "shares" in policy.schema_proof


def test_route_policy_category_counts():
    """Category counts over all policies using independent literal dicts + Counter;
    zero-valued categories are included by pre-initializing Counter with every enum member."""
    mod = importlib.import_module("customer_route_policy")

    # independent literal expectations (corrected values)
    expected_access = {"PUBLIC": 3, "CUSTOMER": 63, "OWNER_ONLY": 29}
    expected_input = {"NONE": 63, "CUSTOMER_BOOK": 9, "CURRENT_MODEL": 11, "HISTORICAL_DATASET": 12}
    expected_output = {
        "RAW_SAFE": 43,
        "PUBLISHED_PORTFOLIO": 1,
        "REQUEST_SCALED": 2,
        "DATASET_SCALED": 0,
        "SANITIZE_NO_SCALE": 7,
        "BLOCK": 42,
    }
    expected_missing = {"PASS_THROUGH": 0, "TYPED_503": 23, "NOT_APPLICABLE": 43, "OWNER_ONLY": 29}

    policies = mod.get_all_route_policies()

    # actual counters: initialize every enum member first to include zeros
    actual_access = Counter({name: 0 for name in mod.AccessPolicy.__members__})
    actual_input = Counter({name: 0 for name in mod.InputContext.__members__})
    actual_output = Counter({name: 0 for name in mod.OutputPolicy.__members__})
    actual_missing = Counter({name: 0 for name in mod.MissingContextPolicy.__members__})

    for p in policies:
        actual_access[p.access_policy.name] += 1
        actual_input[p.input_context.name] += 1
        actual_output[p.output_policy.name] += 1
        actual_missing[p.missing_context_policy.name] += 1

    assert dict(actual_access) == expected_access
    assert dict(actual_input) == expected_input
    assert dict(actual_output) == expected_output
    assert dict(actual_missing) == expected_missing


# ---------------------------------------------------------------------------
# New independent exact-set assertions for SANITIZE_NO_SCALE and REQUEST_SCALED
# (preserve all prior nonempty tests; these are additive and must pass)
# ---------------------------------------------------------------------------

def test_sanitize_no_scale_routes_exact_policy_set():
    """Assert the policy-declared SANITIZE_NO_SCALE routes exactly match the independent literal."""
    mod = importlib.import_module("customer_route_policy")
    Output = mod.OutputPolicy
    actual = frozenset(
        (p.method, p.path)
        for p in mod.get_all_route_policies()
        if p.output_policy is Output.SANITIZE_NO_SCALE
    )
    assert actual == SANITIZE_NO_SCALE_ROUTES


def test_request_scaled_routes_exact_policy_set_fields_and_contracts():
    """Assert the policy-declared REQUEST_SCALED routes exactly match the literal,
    every such route has exact (field_name, FieldKind.member_name) pairs,
    and nonempty scaling_basis_contract (in addition to the preserved generic test)."""
    mod = importlib.import_module("customer_route_policy")
    Output = mod.OutputPolicy
    actual_routes = frozenset(
        (p.method, p.path)
        for p in mod.get_all_route_policies()
        if p.output_policy is Output.REQUEST_SCALED
    )
    assert actual_routes == REQUEST_SCALED_ROUTES

    for method, path in REQUEST_SCALED_ROUTES:
        policy = mod.get_route_policy(method, path)
        # nonempty scaling contract (per-route)
        assert policy.scaling_basis_contract, f"{method} {path} scaling_basis_contract must be nonempty"
        # exact field-pair set using (name, FieldKind member name)
        expected_pairs = EXPECTED_REQUEST_SCALED_FIELDS[(method, path)]
        actual_pairs = frozenset(
            (f.name, f.kind.name) for f in policy.financial_fields
        )
        assert actual_pairs == expected_pairs, (
            f"field pairs mismatch for {method} {path}: got {actual_pairs}"
        )
