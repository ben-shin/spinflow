#!/bin/sh
# ASU tutorial: preserve failed outputs; continue with the next separate stage.
set -eu
set -C
S22ASSETS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export S22ASSETS
: "${PY:?}" "${SF:?}"
"$PY" -I -B session-bridge.py --diagnostic-only
"$SF" plan session-cli-diagnostic/workflow.json --backend native --threads 1 --memory 64MiB > outputs/diagnostic-replay-plan.json
"$SF" run session-cli-diagnostic/workflow.json --backend native --threads 1 --memory 64MiB > outputs/diagnostic-replay-run.json
"$PY" -I -B check-cli-replay.py --applied outputs/unphased.nmrh5 \
  --cli-result session-cli-diagnostic/result.nmrh5 --receipt outputs/exact-cli-replay-diagnostic.json > outputs/exact-cli-replay-diagnostic-command.json
"$SF" compare outputs/unphased.nmrh5 session-cli-diagnostic/result.nmrh5 --rtol 0 --atol 0 --json --memory 64MiB > outputs/diagnostic-replay-compare.json
