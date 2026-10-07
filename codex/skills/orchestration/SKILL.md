---
name: orchestration
description: "Coordinate the arch-advisor workflow in Codex: delegate implementation to configured routine or complex Codex CLI lanes, verify the result, and obtain an independent Astra or Claude second opinion. Use when the user requests this workflow or its implementation lanes."
---

# Arch Advisor for Codex

The current Codex session owns architecture, task specifications, verification
and acceptance. Its model and effort remain the user's session choices.

The bundled runner is `../../scripts/advisor.py` relative to this skill directory.
Resolve its absolute path from this SKILL.md location; do not assume a repository
checkout or a particular plugin cache version. Use Python 3.10 or later.

Inspect the effective configuration in the intended workspace:

```sh
python3 <runner> show --cd /absolute/path/to/project
```

Use routine for bounded implementation whose design is settled. Use complex
when correctness depends on substantial judgment, or routine fails after a
corrected specification. Models and efforts come from configuration, never from
hardcoded routing metadata. Supply a complete specification through stdin:
objective, absolute workspace, affected files, interfaces, constraints and
verification commands. Include the user's task-specific effort with `--effort`
only when requested or justified; otherwise preserve the saved default.

```sh
python3 <runner> run routine --cd /absolute/path/to/project <<'SPEC'
Objective: ...
Files and interfaces: ...
Constraints: ...
Verification: ...
SPEC
```

`run complex` selects the other implementation lane. Both use Codex CLI with a
workspace-write sandbox. Keep dependent work sequential and preserve unrelated
changes. Do not launch concurrent implementations against the same files.

Read the diff and execute the relevant verification yourself. A zero CLI exit
code and the implementer's claims do not prove implementation success; explain
an unchanged diff against the actual task instead of imposing an empty-diff rule.
Do not silently change a selected model/provider or take over a failed lane.
Report the failure and use an explicitly authorized alternative.

Before reporting an implementation deliverable complete, use the packaged
[second-opinion skill](../second-opinion/SKILL.md). Give the reviewer the goal,
constraints, workspace, affected paths, diff/base reference and verification
evidence. Evaluate its findings, verify any fixes and re-review material changes.
An unavailable review is not approval; disclose it without fabricating a verdict.

Preferences are changed directly through the terminal menu:
`python3 <runner> configure`. Do not launch the interactive menu through a
headless execution tool or substitute a conversational questionnaire. Give the
user the command when they want the menu. Noninteractive `set` is available for
explicitly requested exact settings; `show` is always read-only. User preferences
live outside the installed package and survive updates.
