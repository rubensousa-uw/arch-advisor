---
name: recommend-models
description: Suggest models for the user's Claude, Codex and optional OpenCode Go access using live Artificial Analysis intelligence versus cost-per-task Pareto data. Use for model selection and subscription-aware candidate comparisons, without changing execution lanes.
---

Resolve `../../scripts/recommend.py` relative to this file. The helper needs
Python 3.10+ and an AA API key. It checks `ARTIFICIAL_ANALYSIS_API_KEY` first, then
macOS Keychain; reuse either without prompting. Never ask for a key in chat. The
terminal prompts invisibly only when neither has one, and on macOS saves the key
in Keychain after a successful AA fetch. Other platforms reuse the environment
variable. Never put credentials in preference files or command-line arguments.

Ask which services the user has: Claude ✓/✗, Codex ✓/✗, OpenCode Go ✓/✗. Reuse
answers already provided. OpenCode means Go here, not arbitrary connected API providers.
For an interactive terminal, `python3 <helper>` asks the questions and remembers
the choices. In chat/headless tools pass them explicitly:

```sh
python3 <helper> --providers claude codex --json
```

Present the returned `lane_recommendations` as a table: component, role, model
with AA effort, provider/model ID, invocation and routing guidance. All active
subscriptions compete for EVERY role on the same filtered Pareto frontier. Never
restrict a role to a fixed provider. EVERY role uses the balanced price/quality
point, never the highest score alone. Equal metrics/constraints produce the same
winner for several roles; do not invent task-specific differences. This balance
is an advisor heuristic.
The `--host` option only labels current plugin route support; it never filters
rankings. Preserve manual/unsupported route labels rather than claiming an agent
can execute them. The helper also returns the frontier and global suggestions. Retain exact model names including effort, AA IDs, source/date and
access routes. Explain that balance is a normalized equal-weight heuristic,
not an AA endorsement. Attribute data to https://artificialanalysis.ai/.

The helper matches catalog model IDs/names exactly apart from punctuation and
reasoning labels. It reports unmatched models and skips missing metrics. Do not
invent matches for Free, Contributor, Preview or differently dated releases.
Public catalogs do not prove account entitlement. If the user supplies their
available model IDs, put a provider-to-ID-list mapping in a temporary JSON file
and pass `--available <file>` to restrict candidates. Keep the file to model IDs,
never credentials. No model invocation is needed to make recommendations.

AA API costs are benchmark reference costs, not the user's subscription bill,
Go quota consumption or an estimate of remaining usage. The general Intelligence
Index is not a coding-only score. API failures and absent coverage are limitations,
not grounds to substitute remembered rankings. Local `--data` snapshots must be
labelled as snapshots. Applying model choices is a separate user request; never
put an AA slug directly into execution configuration or change the session model.
