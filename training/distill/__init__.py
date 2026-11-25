"""Teacher-student distillation, P2 CRISP-DM iteration 3 (docs/distillation_plan.md).

DeepSeek generates labelled training texts aimed at the gaps iteration 2 left; a second, larger
DeepSeek model re-labels each text without seeing the first label; disagreements are dropped and
a human spot-checks a sample. The DeBERTa student is then fine-tuned on real plus synthetic data.

    uv run python -m training.distill.generate --stage pilot   # 1. teacher writes texts
    uv run python -m training.distill.validate --stage pilot   # 2. rule checks and leakage filter
    uv run python -m training.distill.verify --stage pilot     # 3. independent teacher re-label
    uv run python scripts/spot_check.py --stage pilot          # 4. blind human labels
"""
