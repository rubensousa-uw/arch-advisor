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
restrict a role to a fixed provider. EVERY role picks the cheapest frontier model
meeting its minimum general-intelligence percentile: session P75, routine P50,
complex/review P90 by default. Use `--min-percentile ROLE=0..100` to override a
floor explicitly; repeat for several roles. These are advisor defaults, not
OpenRouter tiers or official AA coding percentiles. They use nearest-rank
percentiles across all valid AA Intelligence Index entries (including effort
variants), before subscription filtering. Show the percentile AND its raw score
cutoff. If a role has no qualifying model, preserve that result without lowering
the bar or inventing a substitute. Models may repeat; review uses a fresh context.
The `--host` option only labels current plugin route support; it never filters
rankings. Preserve manual/unsupported route labels rather than claiming an agent
can execute them. The helper also returns the frontier and global suggestions. Retain exact model names including effort, AA IDs, source/date and
access routes. The global balanced point in JSON is only a comparison reference;
role selection uses minimum quality then minimum cost, not maximum score alone. Attribute data to https://artificialanalysis.ai/.

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
