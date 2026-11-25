#!/usr/bin/env bash
# P2 iteration 3: build the data, train the dose-response runs, apply the adoption rule.
# About 40 minutes on the RTX 4070 laptop GPU (four DeBERTa runs of ~8 minutes plus scoring).
#
# Runs only after the blind spot-check of the synthetic data, because the data must be frozen
# before anything is scored on LLMail-dev. LLMail-test is not touched here; once the decision
# is made, score it once with:
#   uv run python -m evaluation.blind_test --models deberta_v3_small deberta_v3_small_it3
set -euo pipefail
cd "$(dirname "$0")/.."

spot="data/synthetic/full/spot_check.csv"
if [[ ! -f "$spot" ]] || (( $(wc -l < "$spot") < 131 )); then
  echo "Spot-check not finished: run  uv run python scripts/spot_check.py --stage full  first." >&2
  exit 1
fi

mkdir -p logs
log="logs/iteration3.log"
echo "Logging to $log (watch TensorBoard: uv run tensorboard --logdir logs/tensorboard)"
train() {  # <name> <train set>
  uv run python -m training.finetune --base-model microsoft/deberta-v3-small --lr 3e-5 --name "$1" --train-set "$2"
}
{
  uv run python -m training.prepare --iteration 3
  sha256sum data/synthetic/full/verified.jsonl
  train deberta_v3_small_it3 train
  train deberta_v3_small_it3_s0 train_synth0
  train deberta_v3_small_it3_s25 train_synth25
  train deberta_v3_small_it3_s50 train_synth50
  uv run python -m evaluation.adoption
  uv run python -m evaluation.dose_response
} 2>&1 | grep --line-buffered -v -i "warning" | tee "$log"
