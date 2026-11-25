#!/usr/bin/env bash
# Reproduce every P2 detection result for the current CRISP-DM iteration in one go (NFR03).
# Usage: scripts/reproduce_p2.sh        (about 25 minutes on an RTX 4070 laptop GPU)
# Iteration 1 is reproducible from the git tag p2-iteration1.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m training.prepare
uv run python -m training.baseline
uv run python -m training.finetune --base-model microsoft/deberta-v3-small --name deberta_v3_small --lr 3e-5
uv run python -m training.finetune --base-model answerdotai/ModernBERT-base --name modernbert_base --lr 5e-5
uv run python -m training.reference --only protectai_deberta_v2
echo "P2 pipeline finished"
