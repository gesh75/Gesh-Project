"""Safe customer-facing mapping for typed LLM gateway / contract errors."""

from __future__ import annotations

import llm_contracts
import llm_gateway
import server


def test_refusal_maps_to_a_safe_customer_response() -> None:
    response = server._server_error(llm_gateway.LLMRefusalError("provider detail must not escape"))
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
    response = server._server_error(llm_gateway.LLMBudgetExceededError("budget detail req_abc123"))
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


def test_anthropic_credit_exhaustion_maps_to_503_not_500() -> None:
    """Anthropic 400 'credit balance too low' was leaking as Internal server error.

    The live :9000 UI rendered the raw JSON body. A billing outage is a provider
    unavailability, not an application bug.
    """
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    exc = anthropic.BadRequestError(
        "Your credit balance is too low to access the Anthropic API",
        response=response,
        body=None,
    )
    out = server._server_error(exc)
    assert out.status_code == 503
    assert out.detail == "AI provider temporarily unavailable"
    assert "credit" not in str(out.detail).lower()
    assert "req_" not in str(out.detail)
