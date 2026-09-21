# LLM Operations — Gateway + Failover

Operational contract for analysis LLM calls on the GESH product surface
(port 9000). All Anthropic-backed analysis paths must go through `llm_gateway`
for model resolution, spend reservation, retries, and telemetry. Strict response
shape and semantic checks live in `llm_contracts`.

When Claude is genuinely *unavailable* (retries exhausted, auth/connection
failure, daily Anthropic budget spent), `llm_gateway.Gateway._call` hands the
same kwargs to `llm_failover.attempt`. The fallback returns an
Anthropic-`Message`-shaped object with a `tool_use` block so
`llm_contracts.extract_tool_input` and every downstream reader
(`renest_analysis`, `fair_value`, `gen_report2`, `db.log_analysis`) stay
unchanged.

Offline adversarial fixtures and regression tests live under
`tests/fixtures/llm/` and `tests/test_llm_*.py` / `tests/test_analyze_ticker.py`
/ `tests/test_llm_failover.py`.

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
| Provider unreachable / budget exceeded | `LLMUnavailableError`, `LLMBudgetExceededError` | **503** | `AI provider temporarily unavailable` — **after** failover is tried (or disabled) |
| Model refusal (`stop_reason=refusal`) or contract invalid | `LLMRefusalError`, `LLMContractError` | **422** | `AI research could not be completed for this request` — **never** failed over |
| Output truncated (`stop_reason=max_tokens`) | `LLMTruncatedError` | **503** | `AI response exceeded its configured output limit` — **never** failed over |
| All fallback providers also failed | original Anthropic exception (chained from `FailoverUnavailable`) | **503** | same as unreachable — the original error is re-raised, not the fallback text |
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
- A failover hop reports **zero** Anthropic tokens (`FailoverMessage.usage`).
  The Anthropic reservation for the failed attempt still settles as
  `failed_unknown_cost` — that is the failed Claude call, not Grok/Gemini spend.

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
     tests/test_llm_failover.py \
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

6. **Failover canary (when changing `llm_failover` / gateway triggers):** force
   Anthropic dead in a test (`APIConnectionError`) and confirm a schema-shaped
   Grok/Gemini payload is accepted; confirm refusal and truncation still *do
   not* fail over (`tests/test_llm_failover.py`).

---

## 6. Provider failover (PR #236)

The daily chain and every gateway call site (deep analysis, ticker card,
screener, sentiment, SEC) survive an Anthropic outage by trying a second
vendor. Failover is **opt-out** and **loud** — a silent swap would let a
degraded run read as a clean Claude run.

### Triggers (and non-triggers)

Tried, in `GESH_LLM_FAILOVER` order (default `grok,gemini`):

| Trigger | Why it fails over |
|---|---|
| `LLMUnavailableError` | Anthropic retries exhausted |
| `LLMBudgetExceededError` | daily Anthropic budget spent |
| `anthropic.APIStatusError` / `APIConnectionError` / `APITimeoutError` | transport / auth / outage |

Deliberately **not** triggers:

| Exception | Why it must surface |
|---|---|
| `LLMRefusalError` | a refusal must not be laundered through a second vendor |
| `LLMTruncatedError` | truncation is a schema-too-large bug; a silent fallback would hide it |

Set `GESH_LLM_FAILOVER=` (empty string) to disable. Unknown names in the list
are skipped (`unknown provider`); if every entry fails, the **original**
Anthropic exception is re-raised.

### Providers

| Name | How it runs | Auth | Notes |
|---|---|---|---|
| `grok` | `grok --prompt-file … --output-format json` | `~/.grok/auth.json` **or** `XAI_API_KEY` | CLI-as-courier, same pattern as the RH MCP snapshot fetch. Needs `grok` on `PATH`. |
| `gemini` | `gemini_agent._generate(..., model="gemini-2.5-pro")` | `GEMINI_API_KEY` | skipped when the key is unset |

Grok CLI pins (load-bearing — do not "simplify"):

- `--tools read_file` + `--rules` that forbid tool / MCP / memory calls. The
  CLI inherits the operator's global agent rules; without the pin it opens a
  memory session and dies with `max turns reached`.
- `--max-turns` from `GESH_GROK_MAX_TURNS` (default `4`).
- `--prompt-file` (deep-analysis prompts are tens of KB; argv would hit `ARG_MAX`).
- `--model` from `GESH_GROK_MODEL` (default `grok-4.6`). A retired id
  (`unknown model id`) retries once on the CLI's own default.
- Answer is read from the envelope's **`text`** key (`result` is often null).

| Env | Default | Meaning |
|---|---|---|
| `GESH_LLM_FAILOVER` | `grok,gemini` | provider order; empty string disables |
| `GESH_GROK_MODEL` | `grok-4.6` | id from `grok models` — ids rotate |
| `GESH_GROK_TIMEOUT_S` | `900` | deep analysis is ~420–460s on Grok vs ~240s on Claude |
| `GESH_GROK_MAX_TURNS` | `4` | headroom so an inherited global rule cannot exhaust the budget |

### Output contract

Fallback providers have **no forced-tool grammar**. `llm_failover` therefore:

1. Flatten the Anthropic system + user messages + first tool schema into a
   "RAW JSON only" prompt (`build_prompt` — requires a tool schema).
2. Recover JSON by brace-matching that respects strings and escapes
   (`extract_json_object`). An empty or prose-only reply raises. When
   `required_any` is set (the schema's required keys), a complete object that
   shares **none** of those keys is skipped so a CLI envelope or `{ok: true}`
   cannot beat a later analysis object in the same text.
3. **Reject before coerce (PR #240).** `extracted_payload_matches_schema`
   must see at least one schema key. A Grok `--output-format json` envelope
   with no `text` / `result` answer raises `FailoverUnavailable`
   (`grok CLI envelope had no text answer`) — it is **not** fed to
   `coerce_to_schema`. That used to mint `portfolio_score=0` + empty holdings
   and persist as a successful analysis.
4. `coerce_to_schema` still fills omitted **required** keys on a
   schema-shaped payload with a *typed blank* (`""`, `0`, `[]`) rather than
   raising mid-chain. A blank renders as blank — it never invents a live
   price. A schema-shaped but incomplete analysis can still show `0` on a
   missing numeric; that is a partial card, not an empty envelope.

`FailoverMessage.usage` reports **zero** Anthropic tokens so the `llm_calls`
ledger is not inflated with invented spend.

### Disclosure (a failover run must not look clean)

| Surface | What it stamps |
|---|---|
| Logs | `LLM_FAILOVER: Anthropic unavailable … falling back to {name}` |
| `run_ai2.py` stdout | `WARNING: ANTHROPIC UNAVAILABLE — this analysis was produced by '{provider}'` |
| `engine_results.json` | `ai_provider` + `ai_failover_count` |
| `analysis_runs.model` | `"{provider} (FAILOVER, anthropic down) (chain)"` — never the Claude id |
| `gesh_report.html` | `MODEL FAILOVER` banner above the AI verdict (`gen_report2._provider_banner`) |

Engine numbers (regime, Kelly, Monte Carlo, gates) are computed, not generated
— they are unaffected by which vendor wrote the narrative.

### Operator pitfalls

| Symptom | Cause | What to do |
|---|---|---|
| Chatty prefix / `max turns reached` on Grok | a new global agent rule is being inherited | confirm `--tools` / `--rules` still pin a completion; raise `GESH_GROK_MAX_TURNS` only as headroom, not as a substitute |
| `unknown model id` then a second hop | `GESH_GROK_MODEL` is retired | `grok models` and update the pin |
| `direct MCP fetch failed` in `/tmp/gesh_fullchain.log` | **not** this failover — that is snapshot step 1/5 tier 2 | keep `fetch_snapshot_direct.py` (tier 1) healthy; see `CLAUDE.md` |
| `FailoverUnavailable: grok CLI envelope had no text answer` | CLI printed a status envelope (`session_id`, `ok`, …) with empty `text` | next provider in `GESH_LLM_FAILOVER` runs; if all fail, the **original** Anthropic error is re-raised. Do not treat a later 0-score card as this bug — #240 rejects the envelope |
| `portfolio_score=0` + empty holdings after a failover | schema-shaped but incomplete JSON (coerce still fills omitted numerics with `0`) **or** a pre-#240 persisted run | check `ai_failover_count` and the `MODEL FAILOVER` banner. A true empty envelope no longer becomes a successful analysis |
| Quota charged on a blank failover card | customer book analysis reserves the slot *before* the LLM call | expected; the reservation is not refunded just because the hop failed or coerced blanks |

### Isolated test

```bash
venv/bin/python -m pytest tests/test_llm_failover.py -q
```
