#!/usr/bin/env bash
# Distillation data run (P2 iteration 3): teacher generation -> validation -> verification.
#
# DeepSeek charges half price off-peak. Peak is 01:00-04:00 and 06:00-10:00 UTC on weekdays,
# i.e. 09:00-12:00 and 14:00-18:00 Malaysia time, so the script refuses to start then unless
# --anytime is given. Every step is resumable: rerunning after an interruption continues.
#
#   scripts/run_distill.sh full              # ~3,000 texts, about 30-40 minutes
#   scripts/run_distill.sh full --anytime    # ignore the peak-hour check
set -euo pipefail
cd "$(dirname "$0")/.."

stage="${1:?usage: scripts/run_distill.sh <pilot|pilot2|full> [--anytime]}"
hour=$((10#$(date -u +%H)))
if [[ "${2:-}" != "--anytime" && $(date -u +%u) -le 5 ]] && (( (hour >= 1 && hour < 4) || (hour >= 6 && hour < 10) )); then
  echo "DeepSeek peak hours now ($(date +%H:%M) local). Run after 18:00 Malaysia time, or add --anytime." >&2
  exit 1
fi

mkdir -p logs
log="logs/distill_${stage}.log"
echo "Logging to $log"
{
  uv run python -m training.distill.generate --stage "$stage"
  uv run python -m training.distill.validate --stage "$stage"
  uv run python -m training.distill.verify --stage "$stage"
} 2>&1 | grep --line-buffered -v -i "warning" | tee "$log"
