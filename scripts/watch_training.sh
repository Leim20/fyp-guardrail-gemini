#!/usr/bin/env bash
# Live view of a training run: GPU load plus the latest progress lines from its log.
# Usage: scripts/watch_training.sh <log file>      (Ctrl+C to close)
log="${1:?usage: $0 <log file>}"
while true; do
  clear
  echo "== $(date +%H:%M:%S)  $log"
  nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw \
    --format=csv,noheader | awk -F', ' '{printf "GPU load %s | memory %s / %s | %s C | %s\n", $1, $2, $3, $4, $5}'
  echo
  grep -E "epoch|smoke|Error|Traceback|OOM|precision|^(val|test_|ood_|scenarios) |latency|exit=" "$log" | tail -n 25
  sleep 2
done
