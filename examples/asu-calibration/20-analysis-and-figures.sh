#!/bin/sh
# ASU tutorial: preserve failed outputs; continue with the next separate stage.
set -eu
set -C
S22ASSETS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export S22ASSETS
: "${SF:?}" "${CONTINUATION_MODE:?choose engineering-preview or reader-reviewed}"
case "$CONTINUATION_MODE" in
  engineering-preview) HUMAN_REVIEW=false; TITLE='Engineering preview: relative mathematical Hz; provisional phase' ;;
  reader-reviewed) HUMAN_REVIEW=true; TITLE='Reader-reviewed illustration: relative mathematical Hz' ;;
  *) printf '%s\n' 'Unknown continuation mode' >&2; exit 2 ;;
esac
export MPLBACKEND=Agg
test -f outputs/phased.nmrh5
test -f outputs/phase.json
test -f outputs/diagnostic-phased-real.png
test -f outputs/diagnostic-phased-imaginary.png
printf '{"continuation_mode":"%s","reader_visual_review_declared":%s,"scientific_phase_acceptance":false,"independent_human_acceptance":false}\n' \
  "$CONTINUATION_MODE" "$HUMAN_REVIEW" > outputs/continuation.json
"$SF" peaks outputs/phased.nmrh5 --output outputs/analysis.json --csv outputs/analysis.csv \
  --channel 0 --sign both --snr 5 --baseline none --min-separation 8 --separation-unit Hz \
  --max-peaks 20 --integrate --half-width 2 --half-width-unit Hz \
  --integral-baseline none --memory 64MiB > outputs/analysis-command.json
"$SF" figure outputs/phased.nmrh5 outputs/calibration.png --analysis outputs/analysis.json \
  --component 0 --title "$TITLE" --memory 64MiB > outputs/png-command.json
"$SF" figure outputs/phased.nmrh5 outputs/calibration.svg --analysis outputs/analysis.json \
  --component 0 --title "$TITLE" --memory 64MiB > outputs/svg-command.json
"$SF" figure outputs/phased.nmrh5 outputs/calibration.pdf --analysis outputs/analysis.json \
  --component 0 --title "$TITLE" --memory 64MiB > outputs/pdf-command.json
printf '%s\n' 'Stage 2 complete. Areas/flags are in CSV/JSON. Continue with the separate GUI guide OR supplied isolated API acceptance appendix for session/replay.'
