#!/bin/sh
# ASU tutorial: preserve failed outputs; continue with the next separate stage.
set -eu
set -C
S22ASSETS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export S22ASSETS
: "${PY:?}" "${SF:?}" "${B3SRC:?}" "${RUN:?}"
test ! -e "$RUN"
mkdir "$RUN"
mkdir "$RUN/outputs" "$RUN/inputs" "$RUN/inputs/acq"
"$PY" -I -B "$B3SRC/tools/validate_asu_filter.py" download \
  --case "$B3SRC/benchmarks/cases/asu-meod25-import-filter.json" \
  --protocol "$B3SRC/docs/asu-import-filter-protocol.md" --output "$RUN/input-download"
"$PY" -I -B "$B3SRC/tools/validate_asu_filter.py" prepare \
  --case "$B3SRC/benchmarks/cases/asu-meod25-import-filter.json" \
  --protocol "$B3SRC/docs/asu-import-filter-protocol.md" \
  --archive "$RUN/input-download/source.zip" --output "$RUN/input-prepare"
cp "$RUN/input-prepare/data/acqus" "$RUN/inputs/acq/acqus"
cp "$RUN/input-prepare/data/fid" "$RUN/inputs/acq/fid"
cp "$S22ASSETS/preprocess.recipe.json" "$RUN/preprocess.recipe.json"
cp "$S22ASSETS/session-bridge.py" "$RUN/session-bridge.py"
cp "$S22ASSETS/check-cli-replay.py" "$RUN/check-cli-replay.py"
