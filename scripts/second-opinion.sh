#!/bin/sh
# Public name for the read-only Codex advisor. The old helper remains compatible.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/second-advisor.sh" "$@"
