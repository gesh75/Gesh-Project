# Model routing — gesh-project

Use **`grok-4.6`** (CLI default). `grok-4.5` is the only other available id.

`grok-build` and `grok-composer-2.5-fast` are **retired**; the CLI rejects `grok-build` with
`unknown model id`. Model ids rotate — verify with `grok models` before trusting any doc,
including this one.

This repo also invokes the Grok CLI *programmatically* as an LLM failover provider
(`llm_failover.py::call_grok`). That path pins `--tools read_file`, an explicit `--rules` override
forbidding tool/MCP/memory calls, and `--max-turns 4` — because the CLI otherwise inherits
`~/.claude/CLAUDE.md` global agent rules and starts an agent loop instead of returning a
completion. Do not remove those flags. The answer arrives under the envelope's `text` key
(`result` is null).
