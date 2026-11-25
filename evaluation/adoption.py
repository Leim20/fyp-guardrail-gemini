"""Apply iteration 3's adoption rule (docs/distillation_plan.md §1), fixed before any result.

The iteration-3 DeBERTa replaces the iteration-2 one only if
  1. ROC-AUC on LLMail-dev improves, and
  2. val F1 falls by at most 0.01 on deepset and on PromptShield, and
  3. Bitext val FPR stays at or below 1%.

Both models are scored here on the same iteration-3 data, each with its own threshold (chosen
on its own val at training time), so the comparison is like for like. The AUC difference also
gets a paired bootstrap interval: the rule says "improves", the interval says by how much we
can trust it. The synthetic held-out test is reported alongside (per category, per technique,
minimal pairs), but it plays no part in the decision, because it shares the teacher's style.

Usage (after training.prepare --iteration 3 and training the candidate):
    uv run python -m evaluation.adoption
    uv run python -m evaluation.adoption --candidate deberta_v3_small_it3_s50
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.metrics import classification_metrics, paired_auc_difference, wilson
from guardrail.classifier import TransformerClassifier
from training.baseline import data_iteration
from training.prepare import OUT_DIR

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "results" / "adoption_iteration3.json"
MAX_F1_DROP = 0.01
MAX_BITEXT_FPR = 0.01


def source_of(origin: pd.Series) -> np.ndarray:
    return origin.str.split(":").str[0].to_numpy()


def val_by_source(val: pd.DataFrame, scores: np.ndarray, threshold: float) -> dict[str, dict]:
    source = source_of(val["origin"])
    return {name: classification_metrics(val["label"][source == name], scores[source == name] >= threshold,
                                         scores[source == name]) for name in sorted(set(source))}


def synthetic_report(test: pd.DataFrame, scores: np.ndarray, threshold: float) -> dict:
    """Where the model stands on the teacher's held-out calls: per category and technique, and
    whether it gets both texts of each minimal pair right (the keyword-shortcut test)."""
    test = test.assign(correct=(scores >= threshold) == test["label"].astype(bool))
    by_category = test.groupby("category")["correct"].mean().round(3).to_dict()
    injections = test[test["label"] == 1]
    by_technique = injections.groupby("technique")["correct"].agg(["mean", "size"]).round(3)
    pairs = test.dropna(subset=["pair_id"]).groupby("pair_id")["correct"].agg(["all", "size"])
    pairs = pairs[pairs["size"] == 2]
    both = int(pairs["all"].sum())
    low, high = wilson(both, len(pairs))
    return {"accuracy_by_category": by_category,
            "injection_recall_by_technique": by_technique.rename(columns={"mean": "recall", "size": "n"}).to_dict("index"),
            "minimal_pairs": {"n": len(pairs), "both_correct": both, "rate": both / len(pairs) if len(pairs) else None,
                              "ci95": [low, high]}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--incumbent", default="deberta_v3_small", help="model in use (iteration 2)")
    parser.add_argument("--candidate", default="deberta_v3_small_it3")
    args = parser.parse_args()
    if data_iteration() < 3:
        raise SystemExit("data/processed/ holds iteration-2 sets; run training.prepare --iteration 3 first")

    sets = {name: pd.read_parquet(OUT_DIR / f"{name}.parquet") for name in ("val", "ood_llmail_dev", "test_synthetic")}
    report: dict = {"incumbent": args.incumbent, "candidate": args.candidate, "models": {}}
    scores: dict[str, dict[str, np.ndarray]] = {}
    for name in (args.incumbent, args.candidate):
        clf = TransformerClassifier.load(name, device="cuda")
        scores[name] = {s: clf.score(df["text"].tolist()) for s, df in sets.items()}
        llmail = sets["ood_llmail_dev"]
        report["models"][name] = {
            "threshold": clf.threshold,
            "val_by_source": val_by_source(sets["val"], scores[name]["val"], clf.threshold),
            "llmail_dev": classification_metrics(llmail["label"], scores[name]["ood_llmail_dev"] >= clf.threshold,
                                                 scores[name]["ood_llmail_dev"]),
            "synthetic_test": synthetic_report(sets["test_synthetic"], scores[name]["test_synthetic"], clf.threshold),
        }

    old, new = report["models"][args.incumbent], report["models"][args.candidate]
    auc = paired_auc_difference(sets["ood_llmail_dev"]["label"], scores[args.incumbent]["ood_llmail_dev"],
                                scores[args.candidate]["ood_llmail_dev"])
    checks = {
        "llmail_dev_auc_improves": {"incumbent": old["llmail_dev"]["roc_auc"], "candidate": new["llmail_dev"]["roc_auc"],
                                    **auc, "pass": auc["delta"] > 0},
        "bitext_val_fpr": {"candidate": new["val_by_source"]["bitext"]["fpr"], "limit": MAX_BITEXT_FPR,
                           "pass": new["val_by_source"]["bitext"]["fpr"] <= MAX_BITEXT_FPR},
    }
    for source in ("deepset", "promptshield"):
        drop = old["val_by_source"][source]["f1"] - new["val_by_source"][source]["f1"]
        checks[f"{source}_val_f1"] = {"incumbent": old["val_by_source"][source]["f1"],
                                      "candidate": new["val_by_source"][source]["f1"],
                                      "drop": drop, "limit": MAX_F1_DROP, "pass": drop <= MAX_F1_DROP}
    report["checks"] = checks
    report["adopt"] = all(c["pass"] for c in checks.values())

    print(f"{args.candidate} vs {args.incumbent} on iteration-3 data\n")
    for name, check in checks.items():
        detail = {k: round(v, 4) if isinstance(v, float) else v for k, v in check.items() if k != "pass"}
        print(f"  {'PASS' if check['pass'] else 'FAIL'}  {name:<26} {detail}")
    print(f"\n  decision: {'ADOPT' if report['adopt'] else 'KEEP ITERATION 2'}\n")
    print("Synthetic held-out test (reported only; same teacher as the training data):")
    for name in (args.incumbent, args.candidate):
        syn = report["models"][name]["synthetic_test"]
        pairs = syn["minimal_pairs"]
        print(f"  {name:<28} minimal pairs both right {pairs['both_correct']}/{pairs['n']} "
              f"(95% CI {pairs['ci95'][0]:.2f}-{pairs['ci95'][1]:.2f}), by category {syn['accuracy_by_category']}")

    results = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else {}
    results[args.candidate] = report
    RESULTS_PATH.write_text(json.dumps(results, indent=2, default=float))


if __name__ == "__main__":
    main()
