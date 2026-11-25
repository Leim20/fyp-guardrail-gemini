#!/usr/bin/env bash
# Seed-variance check (docs/distillation_plan.md §10): a secondary analysis, not a model choice.
# Trains the 0% and 100% synthetic arms with seeds 1 and 2; seed 42 already exists as
# deberta_v3_small_it3_s0 and deberta_v3_small_it3. About 35 minutes on the RTX 4070 laptop GPU.
# LLMail-test is not touched.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs
log="logs/seed_check.log"
echo "Logging to $log"
train() {  # <name> <train set> <seed>
  uv run python -m training.finetune --base-model microsoft/deberta-v3-small --lr 3e-5 --name "$1" --train-set "$2" --seed "$3"
}
{
  for seed in 1 2; do
    train "deberta_v3_small_it3_s0_seed$seed" train_synth0 "$seed"
    train "deberta_v3_small_it3_seed$seed" train "$seed"
  done
  uv run python -m evaluation.seed_variance
} 2>&1 | grep --line-buffered -v -i "warning" | tee "$log"
