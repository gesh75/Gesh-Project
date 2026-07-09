# GESH Financial — Agent Harness Rules

## Model routing (required)

Use **Grok 4.5** for every analysis and implementation task in this repo.

| Harness | Model to select | Notes |
|---------|-----------------|-------|
| Cursor / Grok agent | **grok-build** or **grok-4.5** | Do **not** use `grok-composer-2.5-fast` here — it is the fast harness, not the deep 4.5 model |
| Grok CLI | `grok -m grok-build` or `grok --agent gesh` | Project agent pins `grok-build` |
| Subagents | `grok-build` | explore/plan subagents route to 4.5 via global config |

Product inference (Claude Sonnet/Haiku, Gemini) is unchanged — this rule applies to **the coding agent** working in `gesh-project`.

## Scope

- Port **9000** (`server.py`) is the customer product
- Deterministic chains: `run_daily_analysis.sh`, `run_engines2.py` — run on host, not sandbox REPL
- Read `CLAUDE.md` before substantive work

## Analysis workflows

For portfolio analysis, daily report, screener, risk, regime, Kelly, paper trading — invoke the matching skill under `.claude/skills/` (each pins `model: grok-build`).