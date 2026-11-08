#!/bin/sh
# ASU tutorial: preserve failed outputs; continue with the next separate stage.
set -eu
set -C
S22ASSETS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export S22ASSETS
: "${SF:?}" "${PY:?}"
export MPLBACKEND=Agg
test -d outputs
"$SF" --version
"$SF" doctor > outputs/doctor.json
"$SF" inspect inputs/acq --format bruker-raw-1d --json --memory 64MiB > outputs/input-description.json
"$SF" plan preprocess.recipe.json --backend native --threads 1 --memory 64MiB > outputs/preprocess-plan.json
"$SF" run preprocess.recipe.json --backend native --threads 1 --memory 64MiB > outputs/preprocess-run.json
"$SF" figure outputs/unphased.nmrh5 outputs/diagnostic-unphased-real.png \
  --component 0 --title 'Before phase: real — relative mathematical Hz' --memory 64MiB > outputs/unphased-real-command.json
"$SF" figure outputs/unphased.nmrh5 outputs/diagnostic-unphased-imaginary.png \
  --component 1 --title 'Before phase: imaginary — relative mathematical Hz' --memory 64MiB > outputs/unphased-imaginary-command.json
"$SF" autophase outputs/unphased.nmrh5 outputs/phased.nmrh5 \
  --report outputs/phase.json --pivot 32767.5 --max-p1 180 --max-peaks 8 \
  --min-snr 15 --backend native --threads 1 --memory 64MiB > outputs/phase-command.json
"$SF" figure outputs/phased.nmrh5 outputs/diagnostic-phased-real.png \
  --component 0 --title 'After automatic phase: real — provisional illustration' --memory 64MiB > outputs/phased-real-command.json
"$SF" figure outputs/phased.nmrh5 outputs/diagnostic-phased-imaginary.png \
  --component 1 --title 'After automatic phase: imaginary — provisional illustration' --memory 64MiB > outputs/phased-imaginary-command.json
"$PY" -I -B "$S22ASSETS/phase-summary.py"
printf '%s\n' 'Stage 1 complete. Open the four diagnostic PNGs and phase.json before reader continuation. Engineering preview remains provisional. Stage 2 is a different file; do not repeat Stage 1.'
