# Vendored third-party code

`sensitivity_analysis.py` and `dcf_model.py` are vendored verbatim from
[anthropics/claude-cookbooks](https://github.com/anthropics/claude-cookbooks)
(`skills/custom_skills/creating-financial-models/`), MIT License,
Copyright (c) 2023 Anthropic.

Vendored 2026-08-07 so the engine does not depend on a path under the developer's
`~/.claude/skills/` at runtime — that directory is a local Claude Code install, not
a deployment artifact, and reading from it would break any process that is not
Georgi's login shell.

Upstream is unmodified. Re-sync by re-copying; do not edit these files in place —
put GESH-specific logic in `trading/kelly_tornado.py` / `trading/dcf_fair_value.py`.

Known upstream quirks (do not "fix" here, work around in callers):
- `SensitivityAnalyzer.tornado_analysis` divides by `self.base_output`; a zero base
  raises ZeroDivisionError.
- `one_way_sensitivity`'s `output_change` uses a truthiness test on `base_output`,
  so it reports 0 until `analyzer.base_output` is set explicitly.
- SKILL.md advertises Monte Carlo; no Monte Carlo is implemented in either file.
  GESH has its own (`monte_carlo.py`) — use that.
