---
name: configure
description: Choose persistent models and default reasoning effort for arch-advisor's Codex lanes and Claude advisor, using selection boxes inside Claude Code.
allowed-tools: AskUserQuestion, Bash, Read
---

# Configure arch-advisor

Use AskUserQuestion selection boxes, never ask the user to edit plugin files.
This command changes only arch-advisor preferences and its generated read-only
Claude advisor definitions. It does not change the main session's model/effort.

The helper is `${CLAUDE_PLUGIN_ROOT}/scripts/configure.py`. Use `python3`, an
explicit existing workspace, and quoted arguments. Respect user/project shell
instructions (e.g. rtk proxy). Plugin path interpolation happens in this skill's
body, not necessarily in the Bash environment.

1. Ask scope: **All projects on this machine** (user, recommended), or **This
   project**. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/configure.py" show --cd
   "$WORKSPACE" --scope "$SCOPE"` and show the current values briefly. If
   ARCH_ADVISOR_CONFIG is set, explain that it takes priority; do not save changes
   that it would mask.
2. Ask which components to change, with four options and multiSelect: routine,
   complex, 2nd-advisor, Claude advisor. For each selected component, ask its model
   and default effort. Present Codex model options `gpt-6-luna`, `gpt-6.1-sol`,
   `gpt-6-astra`, **Custom model ID**. Claude options: `claude-opus-5-5`, `sonnet`,
   `haiku`, **Custom model ID**. Accept other exact IDs through free text, including
   models released later. Never correct or substitute a chosen model silently.
3. Effort boxes: **Inherit**, **low**, **medium**, **More levels**; if More levels,
   show **high**, **xhigh**, **max**. Inherit saves JSON null. For Codex only show
   declared rungs; if efforts is null, only Inherit is valid. The helper validates
   configured defaults just like explicit task efforts. Picker options are not a
   claim that the account/API supports every model or effort; no inference probe
   is necessary to save preferences. Report any later API rejection verbatim.
4. Accumulate the chosen fields and save once through stdin, using a **quoted
   heredoc**, never model IDs interpolated into shell commands. Example:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/configure.py" set --cd "$WORKSPACE" --scope "$SCOPE" <<'SELECTIONS'
   {"lanes":{"routine":{"model":"gpt-6-luna","default_effort":"medium"}},"claude_advisor":{"model":"claude-opus-5-5","default_effort":"high"}}
   SELECTIONS
   ```

   Omit unselected components/fields. Cancelling before saving writes nothing.
   Preserve unrelated settings; never edit cache, installed_plugins.json, global
   session settings, or the original plugin agent to apply selections.
5. Display the helper's actual saved/effective paths, model/default effort per
   changed component, and any error. If active_in_workspace is false, explain the
   project override and offer to configure that scope; don't claim the new user
   values are active here. No plugin update is needed for later selections.

For the configured Claude reviewer, invoke **arch-advisor-selected**, a native
read-only agent with the selected model/effort. The original scoped
`arch-advisor:arch-advisor` remains the factory default. If this creates the
scope's first agents directory, restart Claude Code once to discover it; existing
watched agent directories reload in recent Claude Code versions. A per-task
`REASONING: high` uses `arch-advisor-selected-high` (likewise for the other five
rungs). These generated variants use the same selected model and read-only
tools; text in a prompt alone cannot override native model/effort frontmatter.
Explicit Agent model overrides and CLAUDE_CODE_EFFORT_LEVEL take precedence in
Claude Code; flag conflicts rather than claiming the saved values were used.

For Codex: explicit task effort > saved lane default > global Codex default.
Selections stay on this machine, outside the plugin cache, across plugin updates.
