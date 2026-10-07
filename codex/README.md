# Arch Advisor for Codex — 1.0.0

The Codex session coordinates architecture, specifications, verification and
acceptance. Choose its model and effort in Codex itself. The packaged workflow
delegates implementation to two Codex CLI lanes and obtains a read-only second
opinion from **Astra or Claude**.

| Component | Factory model | Factory effort |
|---|---|---|
| Codex session | Your session choice | Your session choice |
| Routine | `gpt-6-luna` | `high` |
| Complex | `gpt-6.1-sol` | `high` |
| Second opinion, Codex | `gpt-6-astra` | `medium` |
| Second opinion, Claude | `opus` | `high` |

The selected second-opinion provider initially is Codex. Both provider choices
are saved separately, so switching to Claude and back preserves each model and
effort. `opus` is a moving Claude alias; use a full model ID to pin a version.
An Astra review has a fresh context but belongs to the same model family as the
coordinator. Claude provides a cross-provider review.

## Add to Codex

Requires Python **3.10+**, an authenticated Codex CLI and access to the selected
Codex models. The Claude option additionally requires an authenticated Claude
Code CLI with `--safe-mode`, `--restricted` and `--permission-prompts` support.
Update Claude Code if its CLI rejects these flags. No API SDK or separate API key
is required by this plugin; each CLI uses its own configured authentication and
billing. On Ubuntu, Python 3 is available through apt, without Homebrew.

For a recent Codex CLI with plugin marketplace/add commands:

```sh
codex plugin marketplace add rubensousa-uw/arch-advisor --ref main
codex plugin add arch-advisor@arch-advisor-codex --json
```

The first command reads `.agents/plugins/marketplace.json`; the Claude Code
catalog remains separately in `.claude-plugin/marketplace.json`. The second
command returns `installedPath`. Restart Codex or open a new chat to discover
the skills. In the desktop plugin directory, choose the **Arch Advisor — Codex**
marketplace and the **Arch Advisor for Codex** plugin when using the UI instead.

This is GitHub marketplace distribution, not publication in OpenAI's universal
public plugin directory. For local development you can instead add the repository
directory with `codex plugin marketplace add /absolute/path/to/arch-advisor`.
The installed `codex/` package is self-contained and does not need the repository's
Claude scripts or settings.

## Configure directly, without a model

Use the `installedPath` returned at installation. For version 1.0.0 and the
default Codex home:

```sh
ADVISOR_RUNNER="$HOME/.codex/plugins/cache/arch-advisor-codex/arch-advisor/1.0.0/scripts/advisor.py"
python3 "$ADVISOR_RUNNER" configure
```

Run this in an interactive terminal. Use **↑/↓ and Enter** to select Routine,
Complex or Second opinion, the provider (second opinion only), the model and
default effort. `custom` accepts a future model ID without updating the plugin.
`inherit` omits the effort flag so that the chosen CLI applies its own default.
Choose **Save and exit** to persist the selections. Cancel/Esc/Ctrl-C discards
pending changes. Dumb terminals use numbered choices.

The menu reads/writes a normal preferences file and makes **no model calls**.
It does not expose a conversational configure skill or pretend to add Claude's
`/config` controls to Codex. OpenAI does not import Claude `userConfig` pickers.

Inspect the effective configuration from the intended workspace:

```sh
python3 "$ADVISOR_RUNNER" show --cd /absolute/path/to/project
python3 "$ADVISOR_RUNNER" show --cd /absolute/path/to/project --json
```

For exact noninteractive settings, with no inference:

```sh
python3 "$ADVISOR_RUNNER" set routine --model gpt-6-luna --effort high
python3 "$ADVISOR_RUNNER" set complex --model gpt-6.1-sol --effort high
python3 "$ADVISOR_RUNNER" set second-opinion --provider claude --model opus --effort high
python3 "$ADVISOR_RUNNER" set second-opinion --provider codex --model gpt-6-astra --effort medium
```

Omitted fields keep their saved values. Neither menu nor `set` changes the Codex
session model or effort. Choosing a model/effort does not prove account access
or API acceptance. Failures are reported instead of substituting another model.

## Preferences and overrides

Partial JSON configurations merge in this order, with later fields winning:

1. Package `config/defaults.json`.
2. `$CODEX_HOME/arch-advisor/config.json`, or `~/.codex/arch-advisor/config.json`.
3. Target workspace `.arch-advisor/codex.json`.
4. `$ARCH_ADVISOR_CODEX_CONFIG`, when set. A relative path resolves in the target workspace.

User preferences survive plugin updates and are local to each machine. Claude
Code's `.arch-advisor/lanes.json`, `ARCH_ADVISOR_CONFIG` and native settings are
independent and are not imported. The menu edits user preferences; project and
explicit overrides still take priority. `show` prints all applied sources.
Unknown keys, malformed JSON, invalid model IDs/efforts or missing explicit
config files fail before any inference. Python enforces timeouts on macOS and
Ubuntu without requiring `timeout`, `gtimeout` or Homebrew.

Example project override:

```json
{
  "config_version": 1,
  "lanes": {
    "second-opinion": {
      "provider": "claude",
      "claude": {"model": "opus", "effort": "high"}
    }
  }
}
```

Effort values are `low`, `medium`, `high`, `xhigh`, `max`, or JSON `null` for
inheritance. The allowlist validates local input; live model support may differ.
`timeout_seconds` is an integer from 1 to 86400. Factory caps are 600 seconds for
routine, 1800 for complex and 900 for second opinion.

## Use

In Codex:

> Use $arch-advisor:orchestration to implement this task in /absolute/path/to/project.

> Use $arch-advisor:second-opinion to review this plan. REASONING: high. Do not change files.

The coordinator verifies the implementation and obtains the final second opinion
before reporting completion. Both skills resolve their packaged runner relative
to their own location instead of guessing a checkout/cache path.

Direct second opinion:

```sh
python3 "$ADVISOR_RUNNER" opinion --cd /absolute/path/to/project <<'QUESTION'
Review docs/plan.md and the current API contract. Check migration risk.
Return decisive findings with file:line references and missing evidence.
Do not change files.
QUESTION
```

`--provider claude` or `--provider codex` selects a provider for this call only.
`--effort high` overrides its saved effort, and `--effort inherit` omits the flag.
Implementation uses `run routine` or `run complex`, with a complete specification
on stdin and a required `--cd` workspace. Implementation uses Codex's
workspace-write sandbox; the Codex opinion uses read-only, both without runtime
approval prompts. These limits do not override host or managed policies.

The Claude opinion enables safe/restricted mode, disables customizations, skills,
Chrome and MCP, and exposes only Read/Grep/Glob. It has **no shell, write or
delegation tools**. This is a CLI tool restriction, not an OS sandbox. Give Claude
diffs and test outputs in the question when needed; it cannot run tests itself.

Reports show the actual command, target workspace, provider, configured model,
effort, timeout, CLI exit code and original response. When Claude JSON reports
model usage, the report also displays those model IDs separately. Configured IDs
do not independently prove server-side model identity. Timeouts return 124;
provider failures preserve their exit codes. Missing/invalid final responses
cannot pass as successful advice. CLI failure has no automatic fallback.

## Validate and update

Without inference calls:

```sh
python3 "$ADVISOR_RUNNER" opinion --cd /absolute/path/to/project --dry-run
python3 "$ADVISOR_RUNNER" opinion --cd /absolute/path/to/project --provider claude --dry-run
```

A live minimal capability probe uses tokens and the selected CLI's account:

```sh
python3 "$ADVISOR_RUNNER" smoke --cd /absolute/path/to/project
python3 "$ADVISOR_RUNNER" smoke second-opinion --cd /absolute/path/to/project --provider claude
```

The probe requires CLI exit 0 and an exact final `OK`. It does not verify review
quality, implementation edits or every effort level. All probes are read-only.
Repository regressions use mocked CLIs and isolated profiles:
`python3 -m unittest discover -s tests -v`, from the repository root.

To update an existing **Codex** installation:

```sh
codex plugin marketplace upgrade arch-advisor-codex
codex plugin add arch-advisor@arch-advisor-codex --json
```

Use the returned current `installedPath` for terminal commands after an update;
preferences stay at the same external location. Restart Codex. The existing
Claude Code installation continues to use its own update commands in the root README.

References: [Codex plugin packages and marketplaces](https://developers.openai.com/plugins/build/plugins),
[Claude userConfig migration](https://developers.openai.com/plugins/guides/submit-claude-plugin#replace-claude-userconfig).
