# arch-advisor

Claude owns architecture, coordination and verification. Codex implements through
configurable Luna and Sol lanes. Two independent read-only advisors review each
deliverable: Claude Opus 5.5 and Codex Astra.

| Component | Role | Default model |
|---|---|---|
| Claude session | Architecture and coordination | Your choice via `/model` |
| `implementer-routine` | Routine implementation through Codex CLI | `gpt-6-luna` |
| `implementer-complex` | Complex implementation through Codex CLI | `gpt-6.1-sol` |
| `arch-advisor` | Read-only advice and final review | `claude-opus-5-5` |
| `2nd-advisor` | Independent read-only advice and final review through Codex CLI | `gpt-6-astra` |

The Codex agents have lightweight Claude wrappers. Their frontmatter `model`
selects the wrapper; the actual Codex model comes from `config/lanes.json`.
The Claude reviewer pins Opus 5.5 in `agents/arch-advisor.md`.

This is a fork of [DannyMac180/fable-advisor](https://github.com/DannyMac180/fable-advisor).
Codex models, allowed reasoning efforts and wall-clock caps are resolved at runtime
by `scripts/lane.sh`; effort is chosen per task and validated before a call.

## Install

```bash
claude plugin marketplace add rubensousa-uw/arch-advisor
claude plugin install arch-advisor@arch-advisor
```

Requires `jq`, an installed and authenticated Codex CLI (`codex login`), and
access to the configured models. For consultation timeouts on macOS, install
coreutils (`gtimeout`); without `gtimeout` or `timeout`, the helper warns and
runs without a wall-clock cap. Restart Claude Code after updating the plugin
so it discovers the new agent. Select your session model with `/model`.

## Configure Codex lanes

Run `scripts/lane.sh list` to see the effective configuration. The first existing
configuration wins:

| Priority | Location | Scope |
|---|---|---|
| 1 | `$ARCH_ADVISOR_CONFIG` | Explicit override |
| 2 | `./.arch-advisor/lanes.json` | Current project |
| 3 | `~/.claude/arch-advisor/lanes.json` | All projects |
| 4 | Plugin `config/lanes.json` | Defaults |

Copy the default file to the desired scope and edit it. Keep all three lane
entries; adding a lane also requires an agent definition under `agents/`.

```json
"2nd-advisor": {
  "agent": "2nd-advisor",
  "model": "gpt-6-astra",
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
`efforts: null` means undeclared. With no requested effort, callers omit the flag
and report that Codex used its own default. An explicit effort is rejected before
any Codex call when the allowlist is null; declare the rungs to enable it. A
requested effort is never silently dropped. Account access and accepted efforts should be checked
with a live smoke test; local validation only enforces the declared allowlist.

Changing Codex model selection is a configuration edit. Changing the Claude
reviewer's model is an edit to `agents/arch-advisor.md`; `opus` is a moving alias,
so the default uses the exact `claude-opus-5-5` ID.

## Use the advisors

Ask Claude to use `arch-advisor` or `2nd-advisor` on a decision, or activate
`arch-advisor:orchestration` for the complete architect workflow. For example:

> Use 2nd-advisor to review this plan. Workspace: /absolute/path/to/project.
> Check the API contract and migration risk. REASONING: high. Do not change files.

The second advisor reads referenced files and returns its verdict through:

```bash
scripts/second-advisor.sh --cd /absolute/path/to/project --effort high <<'QUESTION'
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

At commitment boundaries, consult the Claude advisor and add the Astra advisor
for significant decisions or persistent failures. At the end of each deliverable,
obtain **both** reviews before reporting done. Give them the same goal, constraints,
workspace, paths, diff/base reference and verification evidence without showing
either the other's first verdict. The architect reconciles the findings, verifies
fixes, and reports disagreements or unavailable reviews.

Routine and complex agents use the same runner with `workspace-write`
sandboxes. Every implementation delegation must explicitly supply
`WORKSPACE: /absolute/path/to/project`; a missing workspace is a blocker. The
runner enters it before resolving overrides and uses it for Codex `--cd`. The advisors never implement. The optional official Codex plugin can
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
scripts/smoke.sh 2nd-advisor --cd /absolute/path/to/project --effort high
```
