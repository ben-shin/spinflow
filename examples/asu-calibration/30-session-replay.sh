#!/bin/sh
# ASU tutorial: preserve failed outputs; continue with the next separate stage.
set -eu
set -C
S22ASSETS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export S22ASSETS
: "${PY:?}" "${SF:?}"
"$PY" -I -B session-bridge.py
"$SF" plan session-cli/workflow.json --backend native --threads 1 --memory 64MiB > outputs/replay-plan.json
"$SF" run session-cli/workflow.json --backend native --threads 1 --memory 64MiB > outputs/replay-run.json
"$PY" -I -B check-cli-replay.py --applied outputs/phased.nmrh5 \
  --cli-result session-cli/result.nmrh5 --receipt outputs/exact-cli-replay.json > outputs/exact-cli-replay-command.json
"$SF" compare outputs/phased.nmrh5 session-cli/result.nmrh5 --rtol 0 --atol 0 --json --memory 64MiB > outputs/replay-compare.json
