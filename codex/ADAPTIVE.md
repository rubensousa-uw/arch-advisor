# Personal Pareto recommendations

Use AA to shortlist models before you have local evidence. Use `adaptive` to
compare what actually worked in this project, role and task class. Neither
approach proves an account's entitlement or remaining subscription quota.

The feature collects numeric metadata from the plugin's routine, complex and
second-opinion CLI executions. It does not install global session hooks. Session,
verification and OpenCode work can be recorded explicitly or wrapped with
`capture`. All five roles participate in personal comparisons. The host
(`claude` or `codex`) separates histories and project configuration targets.

## Start collecting and verify outcomes

In the Claude package use `python3 /path/to/plugin/scripts/adaptive.py`.
In the Codex package use `python3 /path/to/plugin/scripts/advisor.py adaptive`
(or its `scripts/adaptive.py` directly). These commands make no inference calls.
All examples below use the helper path and run from your project directory;
otherwise put `--cd /path/to/project` **before** the subcommand.

```sh
# Give comparable work a stable class. Reuse this ID only for retries/escalations.
export ARCH_ADVISOR_TASK_CLASS=python-unit-tests
export ARCH_ADVISOR_TASK_ID=issue-42-tests
# Execute the existing plugin lane as usual; it prints ADAPTIVE_TASK_ID.

python3 /path/to/plugin/scripts/adaptive.py feedback issue-42-tests accepted --evidence test-run:42
python3 /path/to/plugin/scripts/adaptive.py history
unset ARCH_ADVISOR_TASK_ID ARCH_ADVISOR_TASK_CLASS
```

Codex `advisor.py run` and `opinion` also accept `--task-id` and `--context`.
Without an ID, each invocation is a separate task. Without a class, it is
`general`; create narrower classes for meaningful comparisons. Separate review
and verification jobs need their own IDs. Retry/escalation attempts share an ID,
are attributed to the **initial** role/model/effort, and include all attempts'
resources. Starting another attempt invalidates earlier feedback.

Record `accepted` only after checking task acceptance criteria, tests and relevant
review evidence. `rejected` means the completed task failed those criteria;
`unknown` means it has not been assessed. A passing test that does not exercise
the requirement is not enough. Evidence is a short reference to a local test run,
review or user decision; raw logs, prompts, responses and code are refused.
An orchestrator can record verified results; do not use the producer's own claim
or CLI exit code as the outcome. Revise feedback if a regression is discovered.

The orchestration coordinator records feedback after checking the result. Each feedback command returns a 30-day summary for that task class. Once two options have ten assessed tasks each, all five roles can produce advice for the specific task class using the saved subscriptions. For session and verification the reference is the latest observed choice in that class. A catalog failure leaves the feedback saved. This comparison never applies a model change.

For the selected provider, model and effort, feedback reports verified acceptances, retries, and mean duration from the first assessed task. Within the same role, class, project and host it compares the ten latest assessed tasks by last attempt time with the ten immediately before them, all within 30 days. Unknown outcomes are counted separately. Directional signals use non-overlapping 95% Wilson intervals for acceptance and a bootstrap interval for elapsed time. These rolling observations can reflect a changing mix of tasks. Review the counts even when a difference is inconclusive. Monitoring uses just the chosen model.

Feedback includes a category recommendation. A detected decline in the reference model’s acceptance or speed triggers a comparison of the latest ten assessed tasks per option in this class. Otherwise it uses up to 30 days of assessed work. An alternative qualifies under the existing time-saving policy or when its acceptance is demonstrably higher and the lower bound on time saved is at least zero. Among qualifying pending proposals, the strongest acceptance lower bound wins, with the savings lower bound breaking ties. If only the reference model has enough data and it declines, the advice is to evaluate an alternative; no untried model is reported as a proven upgrade. Applying a supported proposal manually changes the project’s lane default across task classes.

## Suggest, accept, reject, undo

```sh
python3 /path/to/plugin/scripts/adaptive.py suggest --role routine --context python-unit-tests --providers claude codex
python3 /path/to/plugin/scripts/adaptive.py accept PROPOSAL_ID --providers claude codex
python3 /path/to/plugin/scripts/adaptive.py reject PROPOSAL_ID
python3 /path/to/plugin/scripts/adaptive.py reopen PROPOSAL_ID
python3 /path/to/plugin/scripts/adaptive.py undo PROPOSAL_ID
```

Reuse saved subscription choices from the AA recommender by omitting `--providers`.
Use `--providers codex` for Codex only, or add `opencode` for OpenCode Go. The live
models.dev catalog filters candidates **before** Pareto selection. `--available`
accepts the same provider-to-model-ID-list JSON as the AA helper and restricts
candidates to account-confirmed access. Observed models outside that selection
cannot become recommendations. Catalogs cannot verify supported effort levels
or remaining quota. Use exact versioned model IDs where possible: aliases and
inherited effort can change, making old observations less comparable.

Output contains the current choice, observed frontier, sample counts, accepted
fraction, quality intervals, mean resources, resources per accepted task, attempt
counts, evidence references, and qualifying proposals. A proposal includes the
before/after model and effort and an interval for the resource saving. An empty
suggestion list is valid: inspect missing outcomes, insufficient samples and
uncertainty instead of forcing a change. No AA key is needed for personal analysis.

Acceptance rechecks the evidence, current configuration and current selected
catalog; proposals expire after 24 hours. New evidence requires `suggest` again.
Rejection stays recorded for that model pair, role, class and metric until you
`reopen`. Acceptance changes only `.arch-advisor/codex.json` (Codex host) or
`.arch-advisor/lanes.json` (Claude host), preserving other lane settings. Explicit
configuration overrides and symlinks are refused. Configuration writes are atomic;
undo refuses to overwrite subsequent edits. Keep generated project preferences
out of version control unless you intend to share them.

Session and verification have no configurable native lane. Supply `--current
current.json` when suggesting those roles, with e.g.:

```json
{"provider":"codex","model":"gpt-6-luna","effort":"max"}
```

They receive the same evidence criteria. Suggestions for any unsupported route
(e.g. OpenCode implementation) are labelled manual. Accepting one records
`accepted-manual` and tells you what to select; it changes no execution setting.
Session models remain selected by you in the host application.

## What is optimized and why

Default: **less elapsed CLI time without an evidenced loss of acceptance rate**.
Change the resource axis with `--metric tokens`, `api_cost_usd`, or `quota_units`.
For quota, also supply `--quota-key provider:plan:unit`; differing units cannot be
combined. Missing metrics remain unknown, including unreported consumption from
failed/partial invocations. Retries cannot silently discard their missing cost.

The observed Pareto frontier maximizes acceptance rate and minimizes mean total
resources per task. It includes options with complete measurements even after a
single task. For proposals, the frontier and confidence adjustments use only
options meeting the sample and measurement requirements below.

- At least 10 assessed tasks per option in the last 30 days, within the same
  project, role and task class; every task must have the selected resource metric.
- A conservative acceptance-rate comparison: the candidate's Wilson lower bound
  minus the current option's upper bound must be at least `-quality_margin`.
- A positive lower bound on resource savings from 1,000 deterministic bootstrap
  resamples. Quality intervals and savings intervals adjust for option count.

Defaults are 95% nominal confidence and zero tolerated quality loss. They are
**policy defaults, not empirically calibrated constants or a guarantee**. Configure
`--min-samples`, `--days`, `--confidence` and `--quality-margin` explicitly. For
example, `--quality-margin 0.03` permits at most a three-percentage-point loss
under that interval criterion; it is never silently inferred. Equal small samples
of all-successful tasks generally cannot establish strict noninferiority, even
when one model is cheaper. Ten tasks are only an eligibility floor.

This is a conservative observational screen, not a trained router or a causal
experiment. Task difficulty, test quality, correlated work, repeated analyses
and changes in the repository can bias results; the intervals do not provide
sequential-testing guarantees. Prefer comparable tasks and independent outcomes.
It learns from models you actually use, without launching paid exploration or
claiming to know how untried models would perform. AA stays a separate cold-start
reference; its scores are not mixed with your acceptance rates.

Tokens measure workload, not subscription allowance. Claude-reported dollar
costs are API-price estimates; AA prices are benchmark costs. Neither represents
your subscription bill. Codex usage events provide tokens but no reliable
per-task subscription quota. OpenCode normalized terminal usage can provide
`usage.opencode.allowance` for Go, kept under a provider/plan-specific unit key.
The helper never infers task quota from shared account-wide reset counters.
No adapter polls remaining allowances, changes subscriptions or schedules work
around resets in this version.

## Optional auto-adjust

```sh
python3 /path/to/plugin/scripts/adaptive.py auto-adjust status
python3 /path/to/plugin/scripts/adaptive.py auto-adjust enable --acknowledge
python3 /path/to/plugin/scripts/adaptive.py auto-adjust disable
```

Off by default, scoped to this project and host. Enabling prints a warning and
requires `--acknowledge`. When enabled, **running `suggest` can change a supported
project lane**, using that command's evidence policy and subscription selection.
It prints another warning and applies at most one qualifying proposal. Rejected
proposals, manual routes and offline `--catalog` snapshots are never auto-applied.
There is no daemon or scheduled polling; collect/feedback never change models.
Use `undo` for an applied proposal, or disable to prevent future automatic changes.

## Other executions and local storage

Wrap a CLI that emits terminal usage JSON on stdout; stdin, stdout, stderr and
exit status are preserved. Claude needs JSON output and Codex needs `--json`.
Do not wrap an already instrumented plugin lane (that would count it twice).

```sh
python3 /path/to/plugin/scripts/adaptive.py capture --role verification --provider codex --model gpt-6-luna --effort max --context python-unit-tests -- codex exec --json --model gpt-6-luna -c model_reasoning_effort=max --sandbox read-only -
```

Alternatively `record observation.json` imports one normalized observation:

```json
{
  "id": "unique-run-id",
  "task_id": "issue-42-tests",
  "role": "verification",
  "context": "python-unit-tests",
  "provider": "codex",
  "model": "gpt-6-luna",
  "effort": "max",
  "exit_code": 0,
  "duration_seconds": 42.5,
  "tokens": 18200,
  "api_cost_usd": null,
  "quota_units": null,
  "usage_source": "manual"
}
```

Duplicate run IDs are refused. Imports are user-supplied evidence, not independent
verification. Optional `quota_key` is required when quota units are supplied.
The collector only extracts numeric terminal usage events; unknown event formats
remain unknown. Stdout still reaches the caller, but is not persisted by this
feature. Dry-runs and plugin smoke probes do not collect observations.

SQLite data stays at `$ARCH_ADVISOR_STATE_DIR/history.sqlite3`, otherwise
`$XDG_STATE_HOME/arch-advisor/history.sqlite3`, otherwise
`~/.local/state/arch-advisor/history.sqlite3`. The database has mode 0600. It stores
identifiers, timings, numeric metrics, outcomes, evidence references, decisions
and prior lane configuration for undo. Workspace paths are hashed for grouping.
Nothing is uploaded. `ARCH_ADVISOR_TELEMETRY=0` disables automatic collection;
explicit imports and feedback remain available. The rolling analysis window does
not delete old records. Close plugin commands and remove the database to erase
history, decisions and auto-adjust policies; this does not undo project settings.
