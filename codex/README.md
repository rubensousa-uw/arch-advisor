# Arch Advisor for Codex — 1.2.3

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

## Personal usage and change suggestions

Execution through the plugin now records numeric usage locally. Outcomes remain
unknown until verified: exit code 0 is never treated as accepted work. The personal
Pareto comparison uses actual outcomes and resources for comparable tasks, with
retries included. It filters your active subscriptions and available models.

No models change by default. Review each suggestion, then accept or reject it;
accepted native lane changes are project-scoped and can be undone. Optional
`auto-adjust` requires an explicit warning acknowledgment and only runs when you
request suggestions. AA remains the benchmark starting point when local evidence
is insufficient; its percentile settings are not learned from your usage.

See [local collection, evidence policy and commands](ADAPTIVE.md) for all five roles,
feedback, privacy, manual routes and the limits of subscription quota measurement.

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

Use the `installedPath` returned at installation. For version 1.2.3 and the
default Codex home:

```sh
ADVISOR_RUNNER="$HOME/.codex/plugins/cache/arch-advisor-codex/arch-advisor/1.2.3/scripts/advisor.py"
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

## Automatic suggestions from Artificial Analysis

Choose **Suggest models (Artificial Analysis)** in `configure`, run
`python3 "$ADVISOR_RUNNER" recommend`, or ask for `$arch-advisor:recommend-models`.
The terminal asks **Claude ✓/✗, Codex ✓/✗, OpenCode Go ✓/✗**, then fetches live AA
data and prints a table for the session, routine implementation, complex
implementation, independent review and verification/tests, with model/AA effort, invocation and
routing guidance. No inference is used and no lane or
session model is changed. Recommendations are independent of pending menu edits.

This optional feature needs a free [AA API key](https://artificialanalysis.ai/api-key-management-redirect).
The helper checks `ARTIFICIAL_ANALYSIS_API_KEY`, then macOS Keychain, and prompts
invisibly only if neither has a key. On macOS, a prompted key is saved after a
successful AA fetch and reused by both packages (service
`arch-advisor.artificial-analysis`, account `api-key`). Keys never enter preferences
or process arguments. Unlock a locked Keychain; to replace a key, delete that item
in Keychain Access and rerun, or override with the environment variable. Other
platforms use the environment variable for reuse. Noninteractive usage:

```sh
python3 "$ADVISOR_RUNNER" recommend --providers claude codex opencode --json
```

On macOS, the helper automatically loads `/etc/ssl/cert.pem` when Python has no
default CA file or directory. Existing certificates and explicit `SSL_CERT_FILE`
or `SSL_CERT_DIR` settings take priority; HTTPS verification remains enabled.

All active subscriptions compete for every role on the same Pareto frontier:
Codex only uses Codex candidates; Claude + Codex uses both; OpenCode Go joins when
selected. Every role picks the **cheapest frontier model above its quality floor**:
session P75, routine P50, complex/review P90, verification P75 by default. This adapts the idea from
[OpenRouter Pareto Router](https://openrouter.ai/docs/guides/routing/routers/pareto-router),
without calling OpenRouter or importing its coding tiers. Override per invocation
with `--min-percentile routine=40 --min-percentile complex=80` (0–100).

The fifth role, `verifier`, runs tests, validates acceptance criteria and checks
regressions after implementation. It uses the same active-subscription Pareto
selection; override with `--min-percentile verification=80`. It is separate from
read-only review and currently marked for manual use: no dedicated executor is added.

The helper calculates nearest-rank percentiles across all valid general Intelligence
Index entries in the AA response, including effort variants and entries missing
costs, before subscription filtering. The output shows both the percentile and raw
score cutoff. These defaults are advisor policies; the percentiles are not official
coding ranks or success probabilities. A missing qualifying model leaves a role
without a suggestion, never an automatic downgrade. Models may repeat; review
uses a separate context. Threshold overrides are not persisted.
`--host codex` (default) or `--host claude` only labels current execution support;
it never filters rankings. A winning provider without a native route is marked
for manual use, including OpenCode Go. JSON retains the global recommendations and adds
`lane_recommendations`; suggestions are not applied automatically.

Only interactive access choices are saved, in
`$XDG_CONFIG_HOME/arch-advisor/recommendations.json` (default `~/.config/arch-advisor/recommendations.json`).
The helper uses the AA v2 `/language/models/free` endpoint and models.dev's
`anthropic`, `openai`, and `opencode-go` catalogs. Catalog membership does not prove
subscription entitlement. Restrict to confirmed model IDs with `--available file.json`,
for example `{"claude":["claude-haiku-5-5"],"codex":["gpt-6-luna"]}` for those
two selected services. OpenCode means Go here, not arbitrary connected providers.

AA costs are **benchmark API costs, not your bill or Go allowance usage**. The
score is the general Intelligence Index, not a coding-only score. Missing scores
or costs are excluded; unmatched model IDs are reported, not guessed. Exact
effort variants are retained, but CLI support, account access, quotas, regions
and privacy eligibility still need confirmation. The global balanced point in JSON
is a comparison reference only; role selection minimizes cost above a quality floor.
Cost ties prefer higher intelligence, then AA ID. AA attribution, date, index version
and limitations accompany output.

`--data complete-aa.json --catalog models.json` runs without network or a key and
labels the result as a snapshot. AA data must be a complete v2 single-page
envelope; its read timestamp does not establish freshness. API failures never
fall back to remembered rankings. All recommendation files are inside this
package; the Claude package calls the same implementation through a thin entrypoint.

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
