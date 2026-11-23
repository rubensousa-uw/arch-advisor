---
name: adaptive-models
description: Compare locally measured model usage and verified outcomes, show personal Pareto suggestions for active subscriptions, and accept or reject project model changes. Use when the user asks to learn from their usage, inspect evidence or manage auto-adjust.
---

Resolve `../../scripts/adaptive.py` relative to this file. It uses Python's standard
library and local SQLite; no inference or AA key is needed. Read
`../../ADAPTIVE.md` for commands, evidence policy and collection limits.

1. Reuse the user's active subscriptions and target project; ask only if unknown.
   Run `history` and `suggest --role ROLE --context CLASS --providers ...` with
   `--cd PROJECT` before the subcommand. Available roles are session, routine,
   complex, second-opinion and verification. Keep the same task class; do not pool
   unrelated work to manufacture sufficient samples. Use the user's actual
   available IDs with `--available` when supplied.
2. Before running `suggest`, check `auto-adjust status`. If enabled, explain that
   the analysis can apply one qualifying supported change. Do not enable the mode
   unless the user explicitly requests it; enabling requires `--acknowledge`.
3. Show current/proposed provider, exact model and effort, sample counts, accepted
   fraction, uncertainty, resource saving and evidence references. Label manual
   routes and observational limits. Missing data stays unknown. An empty proposal
   list means no evidenced change; use the AA recommender only as a clearly labelled
   benchmark starting point, never as fabricated local evidence.
4. Default mode makes no changes. Run `accept ID` or `reject ID` only for the user's
   decision on that concrete proposal (or their explicitly enabled auto-adjust
   policy). Accepted supported changes affect only the selected role and task category in the project; unsupported/session
   routes are recorded as accepted-manual without changing execution settings.
   `undo ID` restores an applied change only if the file has not since changed.
5. Record outcomes with `feedback TASK_ID accepted|rejected|unknown --evidence REF`
   only after actual acceptance checks or an explicit user result. CLI success and
   a producer's claim are insufficient. Never send prompts, responses or code to
   `record`; imports accept numeric metadata and short identifiers only.

Tokens and API-price estimates are not subscription quota. Use only the same
provider/plan/unit key for quota comparisons. Default 10 samples, 30 days, 95%
confidence and zero tolerated acceptance-rate loss are conservative policy
settings, not learned truths; don't weaken them merely to obtain a suggestion.
No hidden exploration calls, session hooks or scheduled background changes.

For quota relief, show the source and target subscription windows, the reset times and the measured time tradeoff. Pass the same task class on subsequent executions.
