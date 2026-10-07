# arch-advisor

Claude owns architecture, coordination and verification. Codex implements through
configurable routine and complex lanes. A read-only Codex second opinion reviews
each deliverable. Choose your Claude model directly inside Claude Code.

| Component | Role | Default model |
|---|---|---|
| Claude session | Architecture and coordination | Your choice via `/model` |
| `implementer-routine` | Routine implementation through Codex CLI | `gpt-6-luna` |
| `implementer-complex` | Complex implementation through Codex CLI | `gpt-6.1-sol` |
| `second-opinion` | Independent read-only advice and final review through Codex CLI | `gpt-6-astra` |

The agents' Claude wrappers inherit the session model. The actual Codex models
come from `config/lanes.json` and your persistent selections. There is no separate
Claude advisor or pinned Opus reviewer.

This is a fork of [DannyMac180/fable-advisor](https://github.com/DannyMac180/fable-advisor).
Codex models, allowed reasoning efforts and wall-clock caps are resolved at runtime
by `scripts/lane.sh`; explicit task effort overrides saved defaults and is
validated before a call.

## Install

```bash
claude plugin marketplace add rubensousa-uw/arch-advisor
claude plugin install arch-advisor@arch-advisor
```

Requires `jq`, Python 3 for reading native settings, an installed and authenticated Codex CLI (`codex login`), and
access to the configured models. For consultation timeouts on macOS, install
coreutils (`gtimeout`); without `gtimeout` or `timeout`, the helper warns and
runs without a wall-clock cap. Restart Claude Code after updating the plugin
so it discovers the new agent. Select your session model with `/model`.

## Update an existing installation (Ubuntu)

Update the marketplace and the existing plugin from a terminal:

```bash
claude plugin marketplace update arch-advisor
claude plugin update arch-advisor@arch-advisor
```

Restart Claude Code once to load version 6.3.0 and the native settings. Ubuntu uses
`timeout` from `coreutils`; no Homebrew is needed. If dependencies are missing:

```bash
sudo apt update
sudo apt install jq python3 coreutils
```

## Choose models and default effort

Requires **Claude Code 2.1.271 or later** for native plugin pickers. Check
`claude --version`; update Claude Code first if needed with `claude update`.

Inside Claude Code, open **`/config`** and find the **arch-advisor** option rows.
There are model and default effort lists for **Routine**, **Complex** and
**Second opinion**, plus a custom model ID field for each lane. These are native
Claude Code controls: changing a value does not prompt a model, spend inference
tokens, or run a conversational skill. `/arch-advisor:configure` has been removed.

- Model: choose `gpt-6-luna`, `gpt-6.1-sol`, `gpt-6-astra`, or `custom`. For
  `custom`, fill the lane's custom model ID field, then select `custom`. Future
  IDs need no plugin update.
- Effort: choose `low`, `medium`, `high`, `xhigh`, `max`, or `inherit` to use the
  global Codex default.
- `default` preserves the previous lane setting, or the shipped setting if none
  exists. This keeps configurations from 6.2.x working until you change them.

Claude Code stores the non-sensitive fields under
`pluginConfigs["arch-advisor@arch-advisor"].options` in user `settings.json`.
The runner reads those saved values on every invocation; changes apply to the
next Codex call and survive plugin updates. They are local to this machine.
Choose the Claude session model/effort separately with `/model` and `/effort`.

You can also inspect or set these same native values from the terminal, without
using a model:

```bash
claude plugin configure arch-advisor@arch-advisor --json
claude plugin configure arch-advisor@arch-advisor --values-stdin <<'OPTIONS'
{"routine_model":"gpt-6-luna","routine_effort":"medium","complex_model":"gpt-6.1-sol","complex_effort":"high","second_opinion_model":"gpt-6-astra","second_opinion_effort":"high"}
OPTIONS
```

Omitted options keep their saved values. Choices do not prove API/account access
or accepted model efforts; runtime validates against the lane allowlist and
reports API failures. Empty/invalid custom IDs fail before Codex starts.

Explicit task `REASONING` overrides the effective saved effort. `inherit` removes
the lane default for that invocation. The smoke probe uses the saved effort when
present; otherwise it probes the first declared rung.

`ARCH_ADVISOR_CONFIG` and project `./.arch-advisor/lanes.json` overrides take
priority over the native user preferences. `scripts/lane.sh list` shows the
base configuration, effective model/effort and native settings source; a project
or explicit override shows no applied native settings. Legacy user preferences
still supply timeouts/allowlists and any model/effort set to `default`.

Use `arch-advisor:second-opinion` for advice. `arch-advisor:2nd-advisor` remains a
compatible alias with the same read-only sandbox and stable stored lane key.

If you previously used the 6.2.0 Claude reviewer, this version does not use its
obsolete generated agents; there is no separate Claude reviewer in the workflow.

Native picker reference: [Claude Code user configuration](https://code.claude.com/docs/en/plugins-reference#user-configuration).

## Advanced lane configuration

Run `scripts/lane.sh list` to see the effective configuration. The first existing
configuration wins:

| Priority | Location | Scope |
|---|---|---|
| 1 | `$ARCH_ADVISOR_CONFIG` | Explicit override |
| 2 | `./.arch-advisor/lanes.json` | Current project |
| 3 | `~/.claude/arch-advisor/lanes.json` (or under `CLAUDE_CONFIG_DIR`) | All projects |
| 4 | Plugin `config/lanes.json` | Defaults |

Native controls preserve unrelated settings. For advanced manual
configuration, copy the default file to the desired scope and edit it. Keep all three lane
entries; adding a lane also requires an agent definition under `agents/`.

```json
"2nd-advisor": {
  "agent": "second-opinion",
  "model": "gpt-6-astra",
  "default_effort": "high",
  "efforts": ["low", "medium", "high", "xhigh", "max"],
  "timeout_seconds": 900
}
```

All shipped Codex lanes declare `low`, `medium`, `high`, `xhigh` and `max`.
`none`, `minimal` and `ultra` are omitted. Earlier repository CLI probes
(Astra on 2026-09-05, Luna on 2026-09-22) reported additional options, including
`none`/`ultra` on Luna; those historical observations do not enable them here.
CLI settings and API model support can differ; see the
[official model guidance](https://learn.chatgpt.com/docs/models) for current support.
`efforts: null` means undeclared. When neither a task effort nor a saved default
exists, callers omit the flag and report that Codex used its own default. An
explicit or saved effort is rejected before any Codex call when the allowlist is
null; declare the rungs to enable it. A
requested effort is never silently dropped. Account access and accepted efforts should be checked
with a live smoke test; local validation only enforces the declared allowlist.

`ARCH_ADVISOR_CONFIG` and project configurations take priority over native user
preferences. Use `lane.sh list` in the target workspace to check the actual
model and effort before running.

## Use the second opinion

Ask Claude to use `arch-advisor:second-opinion` on a decision, or activate
`arch-advisor:orchestration` for the complete architect workflow. For example:

> Use arch-advisor:second-opinion to review this plan. Workspace: /absolute/path/to/project.
> Check the API contract and migration risk. REASONING: high. Do not change files.

The second advisor reads referenced files and returns its verdict through:

```bash
scripts/second-opinion.sh --cd /absolute/path/to/project --effort high <<'QUESTION'
Review the proposed migration against docs/plan.md and the current schema.
Return the decisive risks and missing evidence. Do not change files.
QUESTION
```

The helper resolves the `2nd-advisor` lane relative to the target workspace,
validates the effort, passes the prompt through stdin, and uses the shared
`scripts/run-lane.sh` to run `codex exec`
with `--sandbox read-only` and approvals disabled. Temporary prompt and result
files are removed afterwards. No code change is expected from this lane, so an
unchanged diff is a successful consultation rather than an implementation refusal.
Missing CLI, invalid configuration, unavailable models and timeouts are reported
explicitly; the Claude wrapper never silently substitutes its own advice.

Consult second-opinion for significant decisions or persistent failures. At the
end of each deliverable, obtain its review before reporting done. Supply the
goal, constraints, workspace, paths, diff/base reference and verification
evidence. The Claude session evaluates the findings, verifies fixes and reports
any disagreement or unavailable review.

Routine and complex agents use the same runner with `workspace-write`
sandboxes. Every implementation delegation must explicitly supply
`WORKSPACE: /absolute/path/to/project`; a missing workspace is a blocker. The
runner enters it before resolving overrides and uses it for Codex `--cd`. The
second-opinion advisor never implements. The optional official Codex plugin can
add specialized review commands, but is not required for the second advisor.

## Validate

Without inference calls:

```bash
scripts/lane.sh list
scripts/lane.sh validate routine high
scripts/smoke.sh --dry-run
python3 -m unittest discover -s tests -v
claude plugin validate .
```

The smoke test is a minimal capability probe: it checks model access,
authentication and one effort per lane (uses tokens). It uses the shared runner
in read-only mode, applies the configured timeout when a timeout binary exists,
and requires both exit status 0 and an exact `OK` in the final-message file. It
does not verify implementation edits, advisory verdicts or all effort levels.
Run it against the intended workspace:

```bash
scripts/smoke.sh --cd /absolute/path/to/project
scripts/smoke.sh second-opinion --cd /absolute/path/to/project --effort high
```
