# GESH — Model & API Cost Report

_Generated 2026-07-03. Sources: code config (`ai_analyzer.py`), the usage log
(`llm_usage.jsonl`, gitignored), and per-endpoint token estimates. One call is
measured live; the rest are estimates from `max_tokens` + typical payload sizes
and will be replaced by real numbers as `_record_usage` accumulates._

## 1. What the `claude-sonnet-4-6` badge controls

The header badge reflects the backend `CLAUDE_MODEL` env var
(`ai_analyzer.py:14`, default `sonnet` → `claude-sonnet-4-6`). It **is** wired
to the live calls: `analyze_portfolio` and `deep_analyze_portfolio` use `MODEL`;
the screener uses `SCREENER_MODEL` (`CLAUDE_SCREENER_MODEL`, default **Haiku**).
The badge is display-only — changing it in the UI does nothing; set the env var
and restart to change models.

## 2. Per-tab Claude usage (calls + tokens + est. cost per run)

| Tab / action | Function | Model | max_tokens | Calls/run | Est. cost/run |
|---|---|---|---|---|---|
| **AI Analysis** ▸ Run Deep | `deep_analyze_portfolio` | Sonnet 4.6 | 10 000 | 1 | **~$0.13–0.17** |
| **Daily Analysis** ▸ Run | `deep_analyze_portfolio` | Sonnet 4.6 | 10 000 | 1 | ~$0.13–0.17 |
| **Radar** ▸ Scan Market | `analyze_screener` | **Haiku 4.5** | 6 000 | 1 | **$0.012** (measured) |
| Quick risk (`/api/analyze`) | `analyze_portfolio` | Sonnet 4.6 | 2 048 | 1 | ~$0.03–0.04 |
| **Intelligence** ▸ Run Agents | sec + news + fundamentals | Sonnet + Haiku/Gemini | mixed | 3 | ~$0.05–0.10 |
| Bull/Bear debate | `bull_bear_debate` | Sonnet 4.6 | mixed | 2 | ~$0.08–0.12 |

**Pricing basis** (`_PRICE_PER_MTOK`, per 1M tokens): Sonnet 4.6 = $3 in / $15 out;
Haiku 4.5 = $1 in / $5 out.

**Measured example** (screener, real): 1 991 in + 1 952 out on Haiku → **$0.0118**.

**A full "run everything once" day** ≈ **$0.35–0.55** in Claude spend (deep +
screener + agents + debate). At 10 alpha users viewing *cached* research (they
can't trigger runs — owner-only since B1), incremental customer cost is
**~$0** — only the owner's daily chain + manual runs spend.

## 3. Model routing recommendation (per task, complexity vs cost)

| Task | Current | Recommended | Why |
|---|---|---|---|
| Deep portfolio analysis, debate | Sonnet 4.6 | **Sonnet 5** (or Opus 4.8 for max quality) | Multi-factor reasoning + fair-value + predictions; Sonnet 5 is the sweet spot, Opus 4.8 only if quality complaints surface (higher $) |
| Screener / weekly picks | Haiku 4.5 ✓ | keep Haiku 4.5 | Structured scoring over pre-computed candidates — cheap task, already routed right |
| News sentiment (batch) | Haiku / Gemini Flash | keep Haiku 4.5 / Gemini Flash | High-volume, low-complexity classification |
| Quick risk (`/api/analyze`) | Sonnet 4.6 | **downgrade to Haiku 4.5** | 2 048-token summary; Sonnet is overkill — ~4× cost for marginal gain |
| SEC / fundamentals agents | Sonnet | Sonnet 5 for SEC (filing nuance); Haiku for fundamentals | SEC parsing benefits from depth; fundamentals are numeric |

**Net:** the expensive path (deep analysis) is worth Sonnet-tier; two paths
(quick risk, fundamentals) can drop to Haiku for ~50% savings with no material
quality loss. Cost is dominated by `max_tokens=10000` deep-analysis output.

## 4. Guardrails already in place

- **Per-call caps**: every Anthropic client is bounded (240 s timeout, 2 retries).
- **Budget guard**: `LLM_DAILY_BUDGET_USD` (env, `0` = off) hard-stops new calls
  once today's estimated spend crosses the cap — a runaway loop can't multiply cost.
- **Usage log**: every call appends `{fn, model, input_tokens, output_tokens,
  est_cost_usd}` to `llm_usage.jsonl` — this report's real numbers grow as tabs
  are used; re-run the aggregation for live figures.
- **Owner-only spend**: customers consume cached output; only the owner (session
  or API key) can trigger a live model call (Codex P1 / B1).

## 5. How to get exact numbers

Run the daily chain + each tab once, then:
```bash
python3 - <<'PY'
import json, collections
agg=collections.defaultdict(lambda:{"calls":0,"in":0,"out":0,"cost":0.0})
for line in open("llm_usage.jsonl"):
    r=json.loads(line); a=agg[r["fn"]]
    a["calls"]+=1; a["in"]+=r["input_tokens"]; a["out"]+=r["output_tokens"]; a["cost"]+=r["est_cost_usd"]
for k,v in agg.items(): print(k, v)
PY
```
