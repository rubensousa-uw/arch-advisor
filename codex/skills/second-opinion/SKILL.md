---
name: second-opinion
description: Obtain the configured read-only second opinion from GPT-6 Astra through Codex CLI or Claude through Claude Code CLI. Use when the user requests an arch-advisor second opinion or the arch-advisor workflow requires its final review.
---

# Second opinion

Resolve `../../scripts/advisor.py` relative to this SKILL.md directory, then use
its absolute path. Inspect `show --cd <workspace>` to see the selected provider,
model and effort. Both provider settings are persistent and separate.

```sh
python3 <runner> opinion --cd /absolute/path/to/project <<'QUESTION'
Goal and constraints: ...
Evidence: paths, base/diff reference and verification results.
Review the actual evidence. Return ship, fix-first or rethink, decisive risks,
findings with file:line references, and missing evidence. Do not change files.
QUESTION
```

A task-specific `--effort high` overrides the saved effort. `--effort inherit`
omits the provider's effort flag. `--provider claude` or `--provider codex`
overrides the selected provider for this call only when the user requests it.
Never silently switch providers, models or efforts after an error.

The Codex path uses a read-only sandbox. The Claude path uses safe/restricted
mode with only Read, Grep and Glob; shell, write, delegation, browser, skills,
customizations and MCP access are disabled. This is a tool restriction, not an
OS-level Claude sandbox. Supply diffs/test outputs as evidence in the prompt
when needed because Claude cannot execute verification commands itself.

Show the executed command, provider, configured model, effort, exit code and
the original report and verdict. Distinguish configured model IDs from model
identity reported by the provider; an alias such as opus is not a pinned model.
Check actionable file:line references yourself. Astra is a fresh review context,
but is still the same model family as Codex; only Claude is cross-provider.

Report unavailable, timeout, refused or provider-error results as such. No
substitute opinion from the coordinating session counts as this review. An
unchanged workspace is the expected outcome. Smoke tests only establish a
minimal capability probe, never review quality or implementation correctness.
