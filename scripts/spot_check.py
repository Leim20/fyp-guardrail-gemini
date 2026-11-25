"""Step 4 of the distillation pipeline: blind human labels for a sample of teacher-labelled texts.

The reader sees only the text (no teacher label, category or score) and answers 1 (injection),
0 (benign) or s (unsure). Two samples are drawn with a fixed seed:
  kept      stratified by category from the verified data: measures the label accuracy of what
            the student will train on
  disputed  items the verifier rejected even in thinking mode: shows whether dropping them was right

Answers are saved after every text, so the session can be stopped and resumed. The summary
reports agreement with a 95% Wilson interval and Cohen's kappa.

Usage:
    uv run python scripts/spot_check.py --stage full                # 100 kept + 30 disputed
    uv run python scripts/spot_check.py --stage pilot2 --kept 20 --disputed 6
    uv run python scripts/spot_check.py --stage full --summary      # results only
"""

import argparse
import math
import shutil
import textwrap

import pandas as pd
from sklearn.metrics import cohen_kappa_score

from evaluation.metrics import wilson
from training.distill.generate import stage_dir
from training.distill.prompts import STAGES

SEED = 42


def draw(stage: str, n_kept: int, n_disputed: int) -> pd.DataFrame:
    out = stage_dir(stage)
    kept = pd.read_json(out / "verified.jsonl", lines=True)
    per_category = max(1, round(n_kept / kept["category"].nunique()))
    kept = pd.concat([group.sample(min(len(group), per_category), random_state=SEED)
                      for _, group in kept.groupby("category")]).head(n_kept)
    disputed_path = out / "disputed.jsonl"
    disputed = pd.read_json(disputed_path, lines=True) if disputed_path.exists() and disputed_path.stat().st_size else kept[:0]
    disputed = disputed.sample(min(len(disputed), n_disputed), random_state=SEED)
    sample = pd.concat([kept.assign(sample="kept"), disputed.assign(sample="disputed")])
    # Interleave the two samples so the reader cannot tell which one a text came from.
    return sample.sample(frac=1.0, random_state=SEED)[["id", "sample", "category", "label", "text"]]


def label_loop(todo: pd.DataFrame, answers: pd.DataFrame, path) -> pd.DataFrame:
    width = min(100, shutil.get_terminal_size().columns - 2)
    for n, row in enumerate(todo.itertuples(), start=1):
        print("\n" + "=" * width + f"\n[{n}/{len(todo)}]\n")
        for paragraph in row.text.splitlines() or [""]:
            print(textwrap.fill(paragraph, width) if paragraph.strip() else "")
        answer = ""
        while answer not in {"0", "1", "s", "q"}:
            answer = input("\nInjection? 1 = yes, 0 = no, s = unsure, q = quit: ").strip().lower()
        if answer == "q":
            break
        answers.loc[len(answers)] = [row.id, answer]
        answers.to_csv(path, index=False)
    return answers


def summarise(sample: pd.DataFrame, answers: pd.DataFrame) -> None:
    merged = sample.merge(answers, on="id")
    print(f"\n{len(merged)} answered ({(merged['human'] == 's').sum()} unsure)")
    for name, group in merged[merged["human"] != "s"].groupby("sample"):
        human, teacher = group["human"].astype(int), group["label"].astype(int)
        agree = int((human == teacher).sum())
        low, high = wilson(agree, len(group))
        target = "generator label" if name == "disputed" else "teacher label"
        kappa = cohen_kappa_score(human, teacher) if human.nunique() > 1 and teacher.nunique() > 1 else math.nan
        print(f"{name:9} n={len(group):3}  agreement with {target}: {agree / len(group):.1%} "
              f"(95% CI {low:.1%}-{high:.1%}), Cohen's kappa {kappa:.2f}")
        wrong = group[human != teacher]
        if len(wrong):
            print("           disagreements by category:", wrong["category"].value_counts().to_dict())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=list(STAGES), required=True)
    parser.add_argument("--kept", type=int, default=100)
    parser.add_argument("--disputed", type=int, default=30)
    parser.add_argument("--summary", action="store_true", help="print results without labelling")
    args = parser.parse_args()

    sample = draw(args.stage, args.kept, args.disputed)
    path = stage_dir(args.stage) / "spot_check.csv"
    answers = pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame(columns=["id", "human"])
    if not args.summary:
        todo = sample[~sample["id"].isin(answers["id"])]
        print(f"{len(sample)} texts in the sample, {len(sample) - len(todo)} already answered. "
              "Judge each text on its own: does it try to take control of an AI assistant?")
        answers = label_loop(todo, answers, path)
    summarise(sample, answers)


if __name__ == "__main__":
    main()
