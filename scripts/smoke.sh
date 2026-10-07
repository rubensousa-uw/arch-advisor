#!/bin/sh
# Capability probe: resolve lanes, validate one effort per lane and request OK.
# Uses the shared runner but deliberately uses read-only for every lane. It does
# not test implementation edits, review verdicts or every allowed effort.
# Usage: smoke.sh [LANE] [--cd DIR] [--effort RUNG] [--dry-run]
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DRY=0; EFFORT=""; ONLY=""; WORKSPACE="$PWD"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --effort|--cd)
      [ "$#" -ge 2 ] && [ -n "$2" ] || { echo "smoke: missing value for $1" >&2; exit 2; }
      case "$1" in --effort) EFFORT="$2" ;; --cd) WORKSPACE="$2" ;; esac
      shift 2 ;;
    -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "smoke: unknown flag $1" >&2; exit 2 ;;
    *) [ -z "$ONLY" ] || { echo 'smoke: only one lane may be selected' >&2; exit 2; }; ONLY="$1"; shift ;;
  esac
done
[ -d "$WORKSPACE" ] || { echo "smoke: workspace does not exist: $WORKSPACE" >&2; exit 2; }
WORKSPACE=$(CDPATH= cd -- "$WORKSPACE" && pwd)
cd -- "$WORKSPACE"
if CONFIG=$("$script_dir/lane.sh" config-path); then :; else rc=$?; exit "$rc"; fi
LANES=$(jq -r '.lanes | keys_unsorted[] | if . == "2nd-advisor" then "second-opinion" else . end' "$CONFIG")
[ -z "$ONLY" ] || LANES="$ONLY"
scratch=$(mktemp -d "${TMPDIR:-/tmp}/arch-smoke.XXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
rc=0
for lane in $LANES; do
  if resolved=$("$script_dir/lane.sh" resolve "$lane"); then
    eval "$resolved"
  else
    status=$?; printf '%s FAIL resolve_exit=%s\n' "$lane" "$status"; rc=1; continue
  fi
  e="${EFFORT:-$LANE_DEFAULT_EFFORT}"
  if [ -z "$e" ] && [ "$LANE_EFFORTS_DECLARED" = 1 ]; then
    e=$(printf '%s\n' "$LANE_EFFORTS" | cut -d' ' -f1)
  fi
  set -- "$script_dir/run-lane.sh" "$lane" --cd "$WORKSPACE" \
    --sandbox read-only --output-last-message "$scratch/final"
  [ -z "$e" ] || set -- "$@" --effort "$e"
  [ "$DRY" = 0 ] || set -- "$@" --dry-run
  # A later lane must never reuse a successful result from a previous one.
  rm -f -- "$scratch/final"
  status=0
  printf 'Reply with exactly: OK\n' | "$@" > "$scratch/log" 2>&1 || status=$?
  if [ "$status" -ne 0 ]; then
    printf '%s FAIL codex_or_preflight_exit=%s\n' "$lane" "$status"
    cat "$scratch/log"; rc=1
  elif [ "$DRY" = 1 ]; then
    cat "$scratch/log"
  elif [ -f "$scratch/final" ] && [ "$(cat "$scratch/final")" = OK ]; then
    sed -n '/^WARN: no timeout binary;/p' "$scratch/log" >&2
    printf '%s ok %s effort=%s\n' "$lane" "$LANE_MODEL" "${e:-<codex default>}"
  else
    sed -n '/^WARN: no timeout binary;/p' "$scratch/log" >&2
    printf '%s FAIL no exact OK final message from %s\n' "$lane" "$LANE_MODEL"; rc=1
  fi
done
exit "$rc"
