---
name: configure
description: Choose persistent models and default reasoning effort for routine, complex and second-opinion Codex lanes, using selection boxes inside Claude Code.
allowed-tools: AskUserQuestion, Bash, Read
---

# Configure arch-advisor

Use AskUserQuestion selection boxes. Configure only **routine**, **complex** and
**second-opinion**. The user chooses the Claude session model and effort with
Claude Code's `/model` and `/effort`; there is no separate Claude advisor.

The helper is `${CLAUDE_PLUGIN_ROOT}/scripts/configure.py`. Use `python3`, an
explicit existing workspace, and quoted arguments. Respect user/project shell
instructions (e.g. rtk proxy). Plugin path interpolation happens in this skill's
body, not necessarily in the Bash environment.

1. Ask scope: **All projects on this machine** (user, recommended), or **This
   project**. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/configure.py" show --cd
   "$WORKSPACE" --scope "$SCOPE"` and show the current values briefly. Display
   the stored `2nd-advisor` lane as **second-opinion**. If ARCH_ADVISOR_CONFIG is
   set, explain that it takes priority; do not save changes that it would mask.
2. Ask which components to change, with three options and multiSelect: routine,
   complex, second-opinion. For each selected component, ask its model and default
   effort. Present `gpt-6-luna`, `gpt-6.1-sol`, `gpt-6-astra`, **Custom model ID**.
   Accept other exact IDs through free text, including models released later.
   Never correct or substitute a chosen model silently.
3. Effort boxes: **Inherit**, **low**, **medium**, **More levels**; if More levels,
   show **high**, **xhigh**, **max**. Inherit saves JSON null. Only show declared
   rungs; if efforts is null, only Inherit is valid. The helper validates defaults
   like explicit task efforts. Picker options are not a claim that the API or
   account supports every model/effort. No inference probe is necessary to save
   preferences; report any later API rejection verbatim.
4. Accumulate chosen fields and save once through stdin using a **quoted
   heredoc**, never model IDs interpolated into shell commands. Example:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/configure.py" set --cd "$WORKSPACE" --scope "$SCOPE" <<'SELECTIONS'
   {"lanes":{"routine":{"model":"gpt-6-luna","default_effort":"medium"},"second-opinion":{"model":"gpt-6-astra","default_effort":"high"}}}
   SELECTIONS
   ```

   Omit unselected components/fields. Cancelling before saving writes nothing.
   The helper preserves unrelated settings and retires only its own obsolete
   Claude selections/marked generated agents from 6.2.0 in the selected scope.
   Never edit plugin cache, installed_plugins.json or main session settings to
   apply selections.
5. Display actual saved/effective paths, model/default effort per changed
   component, and any error. If active_in_workspace is false, explain the project
   override and offer that scope; don't claim the user values are active here.
   No plugin update is needed for later selections.

Invoke **arch-advisor:second-opinion** for independent Codex advice. The old
**arch-advisor:2nd-advisor** name remains an alias, using the same configuration.
The stored `2nd-advisor` key is retained so existing overrides keep working;
the helper accepts either spelling for selections, but not both at once.

Effort priority: explicit task effort > saved lane default > global Codex default.
Selections stay on this machine, outside the plugin cache, across plugin updates.
