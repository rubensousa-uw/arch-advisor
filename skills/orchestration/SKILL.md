---
name: orchestration
description: Routing doctrine for the architect-as-orchestrator pattern — how a Claude session delegates routine implementation to the routine codex lane, escalates high-complexity one-offs to the complex lane, picks a reasoning effort per task, and gets every deliverable reviewed independently by the configurable Codex second-opinion advisor before reporting done. Lane models and effort rungs are configuration, not hardcoded. USE WHEN delegating implementation work, choosing between the implementer-routine and implementer-complex lanes, choosing a reasoning effort for a lane, writing a spec for a subagent, deciding whether to request a second opinion, using the Codex plugin's review skills, managing session cost or token spend, or running any multi-task build where the session is the architect.
---

# Orchestration — the architect's routing doctrine

The session is the architect: it owns requirements, architecture, decomposition, specs, routing, and verification. It should almost never type implementation code. Every implementation task gets routed to the cheapest lane and the lowest reasoning effort that is adequate for it — escalation to the complex lane, or to a higher effort, is deliberate, per task, never a fixed binding — and every finished deliverable gets an independent Codex second opinion before the architect reports done.

**Lane models are configuration.** No Codex model slug is hardcoded in an agent. Each lane's codex model, its legal effort rungs and its wall-clock cap live in `lanes.json`, resolved at runtime by `scripts/lane.sh`. Run `lane.sh list` to see what is actually configured before you route — the tables below describe the shipped defaults, and the config is the source of truth.

## Cost discipline — the prime directive

The economics of this pattern: the Claude architect orchestrates (judgment-heavy, volume-light), the routine lane does the typing (volume-heavy, cheap, cross-vendor), the complex lane takes the hard one-offs (cross-vendor, expensive, only when judgment decides the outcome), and the second-opinion advisor reviews independently in a clean context before anything ships. Three rules follow.

**Emit judgment, not volume.** The architect's output is decomposition, specs, routing decisions, verdicts on diffs, and short reports. It does not type implementation code, test bodies, boilerplate, or config files. A code block longer than an interface signature or a few illustrative lines is a spec that hasn't been delegated yet — stop and delegate it. Fixing a lane's bug by hand is the same failure in disguise: send a corrected spec back to the lane instead.

**Keep the context lean.** Everything in the architect's context is re-read at the architect's price on every turn. Delegate broad exploration, codebase searches, and log-grepping to a cheap read-only agent and keep only the conclusions; read files yourself only when the decision genuinely depends on the exact code. Don't paste long files, full diffs, or verbose command output into the conversation when a path reference or an excerpt will do.

**Reason once, then hand off.** Do the hard thinking — the architecture, the interface design, the debugging hypothesis — in one pass, capture it in the spec, and let the lane carry it from there. Re-deriving decisions across turns burns the premium twice.

What stays with the architect regardless of cost: decomposition, interface design, hypothesis selection when debugging, spec writing, lane and effort routing, and judging verification evidence. Those tokens are what the premium is for — everything else is a candidate for delegation.

## The lanes

| Lane | Ships as | Invoke | Route here when |
|---|---|---|---|
| `routine` | GPT-6 Luna (effort per task) | `implementer-routine` agent | The spec fully determines the outcome: boilerplate, wiring, CRUD, mechanical edits, straightforward features. **Default lane.** Requires the codex CLI. |
| `complex` | GPT-6.1 Sol (effort per task, up to `max`) | `implementer-complex` agent | The outcome depends heavily on judgment the spec can't capture: subtle concurrency, non-trivial algorithms, security-sensitive paths, hard debugging, wide-blast-radius refactors — or the routine lane has already failed the task once. Also the second runner when racing two lanes on one spec. One-off escalations, never the default. Requires the codex CLI. |
| `second-opinion` | GPT-6 Astra | `second-opinion` agent | Independent read-only second opinion and mandatory final review; uses Codex CLI, never implements. |

Choose the Claude session model and effort with `/model` and `/effort` inside
Claude Code. The plugin's wrappers inherit that model. Use
native `/config` settings for arch-advisor to choose routine, complex and
second-opinion Codex models/default efforts. Preferences live in Claude Code user settings, outside the plugin
cache. No model call is used to change them. There is no separate Claude reviewer. `arch-advisor:2nd-advisor` remains a
compatibility alias for `arch-advisor:second-opinion`; both use the stored
`2nd-advisor` lane key so existing overrides retain their model and effort.

Deciding rule: how much does the outcome depend on judgment the spec can't capture? Little → the default routine lane; you will verify anyway. A lot, and mistakes are costly → escalate to `implementer-complex`, or keep that piece with the architect. A routine-lane task that fails its spec once gets a corrected spec; twice, it escalates — repetition is evidence the task was misclassified.

The implementation lanes are the cross-vendor half of the pattern: their output comes from a non-Anthropic family, so the Claude architect's verification and the advisor review are genuine cross-vendor checks, not same-family self-review. That property depends on the config — if you re-point a lane at a Claude model, you have thrown it away, and the doctrine below no longer buys what it claims.

If a lane returns `unavailable` or `timeout`, say so explicitly in your report and decide: re-route to another codex lane, or keep the piece with the architect. Never quietly absorb the substitution or the cost change. Every lane fails loudly on a missing or unauthenticated codex CLI, on an unresolvable lane config, and on a spent usage quota — there is no Claude fallback inside a lane by design.

## Choosing the reasoning effort

The architect can name an effort per task; it overrides the saved lane default.
If neither is set, the lane inherits the global Codex default. Pick the lowest
rung that is adequate for the task.

| Rung | Use for |
|---|---|
| `low` / `medium` | Mechanical edits, renames, wiring, boilerplate, config, tests that mirror an existing pattern |
| `high` | Ordinary features with a couple of design decisions left to the lane; most routine work with real logic in it |
| `xhigh` | Tricky logic, multi-file changes with interactions, the second attempt after a spec correction |
| `max` | The hardest single-lane tasks: concurrency, security-sensitive paths, gnarly debugging |
| `ultra` | Maximum reasoning plus codex's own internal task delegation — slow and token-hungry. **Disabled in the shipped config**; it has to be added to a lane's `efforts` before you can route to it. |

**Which rungs a given lane actually accepts is configuration, not doctrine.** `lane.sh list` prints the declared rungs per lane; as shipped, all three Codex lanes declare `low` through `max`; `ultra`, `none` and `minimal` are omitted. GPT-6 Luna does not support `ultra`. A lane refuses an undeclared rung rather than rounding it — the codex CLI itself does *not* validate effort names client-side, so this check is the only thing standing between a typo and a mid-run API rejection. A task that seems to need a rung the default lane lacks is a task for a lane that has it.

An explicit or saved default effort is refused when the lane declares
`efforts: null`; it is never silently dropped. Omission uses the saved default
when present, otherwise `~/.codex/config.toml`. Report the effective value.

The architect's model and effort come from Claude Code's `/model` and `/effort`.

## The spec contract

Implementers share none of your conversation context. Every delegation must first name `WORKSPACE: /absolute/path/to/project`; if it is absent, the lane refuses instead of guessing. This workspace is used for override resolution, Codex execution and verification. The prompt then carries all six parts:

1. **Objective** — what to build or change, one paragraph
2. **Files** — exact paths to create or modify
3. **Interfaces** — signatures, types, or API shapes the code must match
4. **Constraints** — project conventions, things not to touch
5. **Verification** — the command(s) that prove it works
6. **Reasoning** — one line, `REASONING: <effort>`, chosen from the table above and legal for the target lane per `lane.sh list`

A spec you can't finish writing is a signal the decision isn't made yet — that's architect work, not a reason to hand the ambiguity to a cheaper model.

## Parallelism

Independent specs (no shared files, no ordering dependency) launch as parallel agents in a single message. Sequential chains and single-file surgery stay serial. For high-stakes work, run both lanes on the same spec — `implementer-routine` against `implementer-complex`, two vendors' generations of the same idea — and let the architect pick the stronger diff. Tell neither lane about the other: independence is the whole point.

## Commitment boundaries and the final review

Consult **arch-advisor:second-opinion** for significant architectural decisions,
migrations, API designs, refactor strategies, or a problem that has resisted two
attempts. It provides independent advice without implementing.

**Always obtain a second-opinion review at the end of a deliverable before
reporting done.** Give it the goal, constraints, exact workspace, affected paths,
diff/base reference and verification evidence. Specify task reasoning effort
when needed; otherwise its saved default applies. Ask for `ship`, `fix-first`,
or `rethink` in under 300 words. Do not provide the Claude session's verdict
before the first pass.

The agent calls `scripts/second-opinion.sh`, which delegates to the compatible
`scripts/second-advisor.sh` helper and shared read-only runner. The Claude wrapper
inherits the session model and only supervises the CLI. The substantive verdict
must come from the configured Codex model. An unchanged diff is expected for
advice. The wrapper never supplies its own substitute opinion on CLI failure.

The Claude session evaluates the findings against the evidence, owns fixes and
verification, and re-consults when a fix materially changes the reviewed
behaviour. Report unavailable, timed-out or refused reviews explicitly; never
pretend they passed. The advisor never delegates implementation.

## The Codex plugin (optional)

If the official OpenAI Codex plugin for Claude Code is installed (`codex@openai-codex` under `enabledPlugins` in the user's Claude Code settings; `/plugin list` shows it), its commands become available in the session. It talks to the local `codex` binary over its app-server protocol, so it shares the same install and login as the lanes. The doctrine uses it three ways:

- **`/codex:adversarial-review`** — run it on the accumulated diff *before* the `second-opinion` final review on any deliverable that touched a security-sensitive path, a migration, or an API shape. It adds a specialized pass to the independent Astra review already provided by `second-opinion`. Give the advisor the findings as evidence. `/codex:review` is the lighter pass for ordinary deliverables when the user wants cross-vendor review.
- **`/codex:rescue --model <slug> --effort <rung>`** — a write-capable delegation the user can drive directly, with `/codex:status`, `/codex:result`, and `/codex:cancel` for background jobs. Use it when the user asks for it, or for a long-running investigation you want off the session's critical path. It caps effort at `xhigh` and returns Codex's output rather than the lane report, so the architect still reads the diff and re-runs verification itself. For `max`/`ultra`, or whenever you want the structured report and the empty-diff check, use the lanes.
- **`/codex:setup`** — point the user here when a lane reports `unavailable`; it verifies the binary, version, and login.

The plugin's optional stop-time review gate (`/codex:setup --enable-review-gate`) runs a Codex review every time the session stops; it overlaps with the mandatory second-opinion review and can loop, so leave it off under this pattern unless the user chooses otherwise. Without the plugin the pattern is unchanged — it adds a reviewer and a manual delegation path, it is not a dependency.

## Verification

Reports are claims, not evidence. Before accepting any lane's work: read the diff, and re-run the verification command (or spot-check its quoted output against the working tree). "Should work", "tests should pass", or a report with no command output means the task is not done. An empty diff with a clean exit is a refusal, not a success — the lanes report it as `refused`; treat it as one. A lane that reports a spec gap gets a corrected spec, not a "use your judgment".
