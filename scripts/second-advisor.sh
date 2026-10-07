#!/bin/sh
# Read a consultation from stdin and return advice from the configured Codex lane.
# Usage: second-advisor.sh [--effort RUNG] [--cd WORKSPACE]
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
EFFORT=""; WORKSPACE="$PWD"
fail() {
  printf 'CODEX ADVISOR REPORT\nSTATUS: %s\nREASON: %s\n' "$1" "$2"
  exit "${3:-1}"
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --effort|--cd)
      [ "$#" -ge 2 ] && [ -n "$2" ] || fail unavailable "Missing value for $1" 2
      case "$1" in --effort) EFFORT="$2" ;; --cd) WORKSPACE="$2" ;; esac
      shift 2 ;;
    -h|--help) printf 'Usage: second-advisor.sh [--effort RUNG] [--cd WORKSPACE] < consultation\n'; exit 0 ;;
    *) fail unavailable "Unknown argument: $1" 2 ;;
  esac
done
[ -d "$WORKSPACE" ] || fail unavailable "Workspace does not exist: $WORKSPACE" 2
# Resolve project overrides relative to the target workspace, not the plugin.
WORKSPACE=$(CDPATH= cd -- "$WORKSPACE" && pwd)
cd -- "$WORKSPACE"
resolved=$("$script_dir/lane.sh" resolve 2nd-advisor) || fail unavailable "Cannot resolve 2nd-advisor lane"
eval "$resolved"
if [ -n "$EFFORT" ]; then
  validation=$("$script_dir/lane.sh" validate 2nd-advisor "$EFFORT" 2>&1) || fail unavailable "$validation" 4
fi
command -v codex >/dev/null 2>&1 || fail unavailable "codex not found on PATH"

scratch=$(mktemp -d "${TMPDIR:-/tmp}/arch-advisor.XXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cat > "$scratch/question"
[ -s "$scratch/question" ] || fail refused "Empty consultation" 2
cat > "$scratch/prompt" <<'ADVISOR_PROMPT'
You are an independent second advisor. This is a read-only consultation, not an
implementation task. The caller explicitly opts out of implementation routing
for this invocation; all other user and project instructions still apply.
Inspect the referenced evidence yourself. Do not edit files, run mutating
commands, perform external write actions, or delegate to other advisors or
implementers. Do not change models. Return a verdict (ship, fix-first, rethink),
the decisive risk, specific findings with file references, and missing evidence.
Keep your answer under 300 words. An unchanged workspace is the expected result.

Consultation:
ADVISOR_PROMPT
cat "$scratch/question" >> "$scratch/prompt"

set -- "$script_dir/run-lane.sh" 2nd-advisor --sandbox read-only --cd "$WORKSPACE" \
  --output-last-message "$scratch/final"
[ -z "$EFFORT" ] || set -- "$@" --effort "$EFFORT"
rc=0
"$@" < "$scratch/prompt" > "$scratch/log" 2>&1 || rc=$?
# Logs stay private except for the explicit operational timeout warning.
sed -n '/^WARN: no timeout binary;/p' "$scratch/log" >&2
if [ "$rc" -ne 0 ]; then
  status=unavailable
  [ "$rc" -ne 124 ] && [ "$rc" -ne 137 ] || status=timeout
  printf 'CODEX ADVISOR REPORT\nLANE: 2nd-advisor (%s)\nSTATUS: %s\nREASON: codex exited %s\n' "$LANE_MODEL" "$status" "$rc"
  cat "$scratch/log"
  exit "$rc"
fi
[ -s "$scratch/final" ] || fail refused "Codex returned no advice"
printf 'CODEX ADVISOR REPORT\nLANE: 2nd-advisor (%s, effort: %s)\nSTATUS: complete\n' \
  "$LANE_MODEL" "${EFFORT:-omitted — codex default}"
cat "$scratch/final"
printf '\n'
