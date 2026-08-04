"""Safe customer-facing mapping for typed LLM gateway / contract errors."""

from __future__ import annotations

import llm_contracts
import llm_gateway
import server


def test_refusal_maps_to_a_safe_customer_response() -> None:
    response = server._server_error(
        llm_gateway.LLMRefusalError("provider detail must not escape")
    )
    assert response.status_code == 422
    assert response.detail == "AI research could not be completed for this request"
    assert "provider detail" not in str(response.detail)


def test_unavailable_maps_to_503() -> None:
    response = server._server_error(
        llm_gateway.LLMUnavailableError("provider detail must not escape")
    )
    assert response.status_code == 503
    assert response.detail == "AI provider temporarily unavailable"
    assert "provider detail" not in str(response.detail)


def test_truncated_maps_to_503() -> None:
    response = server._server_error(
        llm_gateway.LLMTruncatedError("provider detail must not escape")
    )
    assert response.status_code == 503
    assert response.detail == "AI response exceeded its configured output limit"
    assert "provider detail" not in str(response.detail)


def test_budget_exceeded_maps_to_503() -> None:
    response = server._server_error(
        llm_gateway.LLMBudgetExceededError("budget detail req_abc123")
    )
    assert response.status_code == 503
    assert response.detail == "AI provider temporarily unavailable"
    assert "budget detail" not in str(response.detail)
    assert "req_abc123" not in str(response.detail)


def test_contract_error_maps_to_422() -> None:
    response = server._server_error(
        llm_contracts.LLMContractError("provider detail must not escape")
    )
    assert response.status_code == 422
    assert response.detail == "AI research could not be completed for this request"
    assert "provider detail" not in str(response.detail)
