# LLM Operations — Claude Integration

Operational contract for Anthropic Claude calls on the GESH product surface
(port 9000). All Anthropic-backed analysis paths must go through `llm_gateway`
for model resolution, spend reservation, retries, and telemetry. Strict response
shape and semantic checks live in `llm_contracts`.

Offline adversarial fixtures and regression tests live under
`tests/fixtures/llm/` and `tests/test_llm_*.py` / `tests/test_analyze_ticker.py`.

---

## 1. Model matrix

One canonical Anthropic model ID per route. Aliases `sonnet` / `haiku` resolve via
`llm_gateway.resolve_model()`. Dated legacy IDs (e.g. `claude-sonnet-4-20250514`)
are rejected fail-closed — never silently remapped to another tier.

| Route / operation | Alias | Canonical ID | Module |
|---|---|---|---|
| Deep portfolio analysis | `sonnet` | `claude-sonnet-4-6` | `ai_analyzer.deep_analyze_portfolio` |
| Single-ticker research card | `sonnet` | `claude-sonnet-4-6` | `ai_analyzer.deep_analyze_ticker` |
| Fundamental momentum scan | `sonnet` | `claude-sonnet-4-6` | `fundamental_agent` |
| Bull/Bear PM synthesis | `sonnet` | `claude-sonnet-4-6` | `bull_bear_debate` |
| News sentiment | `haiku` | `claude-haiku-4-5` | `news_agent` |
| SEC filing analysis | `haiku` | `claude-haiku-4-5` | `sec_agent` |
| Bull / Bear debate sides | `haiku` | `claude-haiku-4-5` | `bull_bear_debate` |
| Screener (default) | `haiku` | `claude-haiku-4-5` | `ai_analyzer.analyze_screener` |

Env overrides (must still be an alias or exact canonical ID):

- `CLAUDE_MODEL` — deep / ticker / debate synthesis default (`sonnet`)
- `CLAUDE_SCREENER_MODEL` — screener override (`haiku`)

---

## 2. Failure matrix

Typed gateway / contract errors map through `server._server_error` to safe
customer-facing HTTP responses. Provider internals, request IDs, and exception
text must never escape into the response body.

| Condition | Exception | HTTP | Customer copy |
|---|---|---|---|
| Provider unreachable / budget exceeded | `LLMUnavailableError`, `LLMBudgetExceededError` | **503** | `AI provider temporarily unavailable` |
| Model refusal (`stop_reason=refusal`) or contract invalid | `LLMRefusalError`, `LLMContractError` | **422** | `AI research could not be completed for this request` |
| Output truncated (`stop_reason=max_tokens`) | `LLMTruncatedError` | **503** | `AI response exceeded its configured output limit` |
| Unavailable evidence (no articles, provider not configured, contract failure on news/SEC) | agent envelope | data / UI | `availability: "UNAVAILABLE"` — **never** fabricate `NEUTRAL` / score `0` |

Unavailable evidence is an explicit product state (`market_mood` /
`news_mood` / per-symbol `availability`), not a soft default to neutral mood.

---

## 3. Telemetry contract

Durable provider telemetry is the SQLite table `llm_calls` (path from
`LLM_USAGE_DB`, default `llm_usage.db` next to `llm_gateway.py`).

Exact columns:

| Column | Notes |
|---|---|
| `call_id` | Primary key (hex UUID) |
| `started_at` | ISO UTC |
| `completed_at` | ISO UTC (nullable until terminal) |
| `operation` | Caller-supplied operation name |
| `model` | Canonical model ID |
| `status` | `reserved` · `succeeded` · `failed_unknown_cost` · `rejected` · `truncated` |
| `attempt_no` | 1-based attempt within the gateway retry loop |
| `request_id` | Provider request id when available |
| `input_tokens` / `output_tokens` | Settled usage |
| `cache_read_tokens` / `cache_creation_tokens` | Prompt-cache telemetry |
| `reserved_microusd` | Admission reservation (always set) |
| `settled_microusd` | Actual/settled micro-USD when known |
| `error_kind` | Exception class name only (not message text) |

**Data retained:** the columns above for spend + reliability dashboards.

**Never stored:** prompts, messages, tools, holdings, symbols, emails, customer
identifiers, or raw provider response bodies. Exception *text* is not persisted —
only `error_kind`.

---

## 4. Budget behavior

- Every gateway `create` / `stream` attempt **reserves** spend (`reserved_microusd`)
  under `LLM_DAILY_BUDGET_USD` **before** the provider call (`BEGIN IMMEDIATE`).
- `LLM_DAILY_BUDGET_USD` ≤ 0 (default) means unlimited — local/test convenience only.
- On failed / interrupted streams and other exceptions, the reservation is marked
  `failed_unknown_cost` so unknown spend still counts against the daily ceiling.
- Refusals settle as `rejected`; truncations as `truncated`; successes as `succeeded`
  with token-based `settled_microusd`.
- Owner-only usage visibility:
  - `GET /usage` — HTML cockpit
  - `GET /api/usage/summary` — JSON including `llm_provider` from
    `llm_gateway.daily_usage_summary()`
- Customer quota metering (`usage_events` / reservations) is separate from this
  provider ledger; both gates may apply.

---

## 5. Change-control checklist

Before shipping a Claude route or prompt change:

1. **Offline fixture suite first** — run the LLM regression gate with no live
   Anthropic calls:
   ```bash
   venv/bin/python -m pytest \
     tests/test_llm_gateway.py \
     tests/test_llm_contracts.py \
     tests/test_llm_agent_contracts.py \
     tests/test_llm_error_mapping.py \
     tests/test_ai_analyzer_caching.py \
     tests/test_analyze_ticker.py \
     tests/test_news_cache_fallback.py \
     tests/test_news_public_contract.py \
     tests/test_intel_agents.py \
     tests/test_mybook.py -q
   ```
   Adversarial fixtures: `tests/fixtures/llm/news_injection.json`,
   `sec_injection.json`, `ticker_card_valid.json`, `ticker_card_invalid_price.json`.

2. **Single owner-only staging canary** for each changed route (not a blanket
   portfolio re-run).

3. **Inspect on canary:**
   - canonical model ID in the request / ledger row
   - strict tool result (expected tool name + schema)
   - request ID in the server log (not the customer body)
   - cache token telemetry (`cache_read_tokens` / `cache_creation_tokens`)
   - safe customer copy on failure (422/503 strings above)

4. **Do not** run canaries from the sandbox, and **do not** canary against
   customer or production portfolio data.

5. Financial-outcome evaluation (verdict quality vs realized returns) remains a
   **separate open gap** — this checklist does not close an eval harness.
