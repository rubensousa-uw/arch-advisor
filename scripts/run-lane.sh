#!/bin/sh
# Execute a configured lane. Prompt is read from stdin, without shell interpolation.
# Usage: run-lane.sh LANE --cd DIR --sandbox MODE [--effort RUNG]
#                   [--output-last-message FILE] [--dry-run]
set -eu
die() { printf 'arch-advisor: %s\n' "$1" >&2; exit "${2:-2}"; }
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LANE=${1:-}; [ -n "$LANE" ] || die "A lane is required"
shift
WORKSPACE=""; SANDBOX=""; EFFORT=""; FINAL=""; DRY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --cd|--sandbox|--effort|--output-last-message)
      [ "$#" -ge 2 ] && [ -n "$2" ] || die "Missing value for $1"
      case "$1" in
        --cd) WORKSPACE="$2" ;;
        --sandbox) SANDBOX="$2" ;;
        --effort) EFFORT="$2" ;;
        --output-last-message) FINAL="$2" ;;
      esac
      shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) die "Unknown argument: $1" ;;
  esac
done
[ -n "$WORKSPACE" ] && [ -d "$WORKSPACE" ] || die "An existing --cd workspace is required"
case "$SANDBOX" in read-only|workspace-write) ;; *) die "Sandbox must be read-only or workspace-write" ;; esac
[ "$LANE" != 2nd-advisor ] || [ "$SANDBOX" = read-only ] || die "2nd-advisor only permits read-only consultations"
case "$FINAL" in ""|/*) ;; *) FINAL="$PWD/$FINAL" ;; esac
WORKSPACE=$(CDPATH= cd -- "$WORKSPACE" && pwd)
cd -- "$WORKSPACE"
# Check the resolver status before eval, which can mask a failed substitution.
if resolved=$("$script_dir/lane.sh" resolve "$LANE"); then
  eval "$resolved"
else
  rc=$?; exit "$rc"
fi
# Task effort wins over the saved lane default; omission inherits Codex only
# when neither exists. Both configured and explicit values must be declared.
EFFORT=${EFFORT:-$LANE_DEFAULT_EFFORT}
if [ -n "$EFFORT" ]; then
  if "$script_dir/lane.sh" validate "$LANE" "$EFFORT"; then :; else rc=$?; exit "$rc"; fi
fi
case "$LANE_TIMEOUT" in ""|*[!0-9]*) die "Lane timeout must be a positive integer" 3 ;; esac
[ "$LANE_TIMEOUT" -gt 0 ] || die "Lane timeout must be positive" 3
set -- codex exec --model "$LANE_MODEL" --sandbox "$SANDBOX" \
  -c 'approval_policy="never"' --skip-git-repo-check --cd "$WORKSPACE"
[ -z "$FINAL" ] || set -- "$@" --output-last-message "$FINAL"
[ -z "$EFFORT" ] || set -- "$@" -c "model_reasoning_effort=$EFFORT"
set -- "$@" -
T=$(command -v gtimeout || command -v timeout || true)
if [ -n "$T" ]; then
  set -- "$T" "$LANE_TIMEOUT" "$@"
else
  printf 'WARN: no timeout binary; invocation has no wall-clock cap.\n' >&2
fi
if [ "$DRY" = 1 ]; then
  printf 'DRY lane=%s model=%s effort=%s sandbox=%s workspace=%s\n' \
    "$LANE" "$LANE_MODEL" "${EFFORT:-<codex default>}" "$SANDBOX" "$WORKSPACE"
  exit 0
fi
command -v codex >/dev/null 2>&1 || die "codex not found on PATH" 3
# Preserve every Codex/timeout exit status, including 124 for timeouts.
exec "$@"
