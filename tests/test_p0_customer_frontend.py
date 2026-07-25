"""Static customer-client inventory for the Phase 1 containment boundary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import server

FORBIDDEN_CUSTOMER_ENDPOINTS = (
    "/api/portfolio",
    "/api/trades",
    "/api/trades/stats",
    "/api/ecg",
    "/api/portfolio/history",
    "/api/sections/status",
)
FORBIDDEN_CUSTOMER_FUNCTIONS = (
    "loadPortfolio",
    "loadPortfolioWithECG",
    "loadRegime",
    "runScanner",
    "loadKpiSparklines",
    "refreshSectionBadges",
)


@pytest.fixture(scope="module")
def customer_document() -> str:
    return server._render_dashboard("customer")


@pytest.fixture(scope="module")
def owner_document() -> str:
    return server._render_dashboard("owner")


@pytest.mark.parametrize("endpoint", FORBIDDEN_CUSTOMER_ENDPOINTS)
def test_customer_bundle_contains_no_owner_domain_endpoint(
    customer_document, endpoint
):
    assert endpoint not in customer_document


def test_customer_bundle_contains_no_exact_global_regime_fetch(customer_document):
    assert not re.search(
        r"""fetch\(\s*(['"])/api/regime\1\s*\)""",
        customer_document,
    )
    assert "/api/regime/${enc}" in customer_document


@pytest.mark.parametrize("function_name", FORBIDDEN_CUSTOMER_FUNCTIONS)
def test_customer_bundle_contains_no_owner_loader_definition(
    customer_document, function_name
):
    assert not re.search(
        rf"(?:async\s+)?function\s+{re.escape(function_name)}\b",
        customer_document,
    )


def test_customer_bundle_contains_no_owner_retry_or_poll(customer_document):
    assert "for(let attempt=0;attempt<3;attempt++)" not in customer_document
    assert "setInterval(loadPortfolioWithECG" not in customer_document
    assert "setInterval(refreshSectionBadges" not in customer_document


def test_customer_has_no_owner_refresh_or_connection_component(customer_document):
    assert 'onclick="loadPortfolio()"' not in customer_document
    assert 'id="live-wrap"' not in customer_document
    assert 'id="ts"' not in customer_document


def test_customer_role_and_command_palette_fail_closed(customer_document):
    assert "|| 'owner'" not in customer_document
    assert "let role='owner'" not in customer_document
    tabs = re.search(r"const CMDK_TABS\s*=\s*(\[[^\n]*\]);", customer_document)
    assert tabs
    assert "Overview" not in tabs.group(1)
    assert "Risk" not in tabs.group(1)
    assert "Scanner" not in tabs.group(1)
    assert "Paper Trades" not in tabs.group(1)
    assert "Home" in tabs.group(1)
    assert "Analyze" in tabs.group(1)


@pytest.mark.parametrize("endpoint", FORBIDDEN_CUSTOMER_ENDPOINTS)
def test_owner_bundle_retains_legitimate_owner_endpoint(owner_document, endpoint):
    assert endpoint in owner_document


@pytest.mark.parametrize("function_name", FORBIDDEN_CUSTOMER_FUNCTIONS)
def test_owner_bundle_retains_legitimate_owner_loader(
    owner_document, function_name
):
    assert re.search(
        rf"(?:async\s+)?function\s+{re.escape(function_name)}\b",
        owner_document,
    )


def test_android_customer_client_has_no_owner_portfolio_request(repo_root: Path):
    android_root = repo_root / "android" / "GeshFinancial" / "app" / "src" / "main"
    source = "\n".join(
        path.read_text()
        for path in sorted(android_root.rglob("*"))
        if path.suffix in {".kt", ".xml"}
    )
    assert "api/portfolio" not in source
    assert "getPortfolio" not in source
    assert "startAutoRefresh" not in source
    assert "30_000L" not in source
