"""Score the frozen LLMail-test set, once, after the iteration-3 adoption decision.

LLMail-test is the one set no choice has looked at: no threshold, epoch, prompt or dose was
picked with it. Scoring it repeatedly and keeping the best run would quietly turn it into a
validation set, so this script writes results/blind_test.json and refuses to run again. A
re-run needs --rescore "<reason>", and the reason and the earlier results are kept in the file,
so any second look is visible in the report.

Each model uses the threshold it already has; nothing is re-tuned here.

Usage:
    uv run python -m evaluation.blind_test --models deberta_v3_small deberta_v3_small_it3
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.metrics import classification_metrics
from guardrail.classifier import TransformerClassifier
from guardrail.rules import FLAG_THRESHOLD, RuleScanner
from training.prepare import OUT_DIR

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "results" / "blind_test.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", required=True, help="model directories under models/")
    parser.add_argument("--rescore", metavar="REASON", help="run again; the reason is recorded")
    args = parser.parse_args()

    previous = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else None
    if previous and not args.rescore:
        raise SystemExit(f"{RESULTS_PATH.name} already exists (scored {previous['scored_at']}). "
                         "The blind test is scored once; pass --rescore \"<reason>\" if you must.")

    test = pd.read_parquet(OUT_DIR / "ood_llmail_test.parquet")
    labels = test["label"].to_numpy()
    rules = np.array([RuleScanner.from_yaml().score(t) for t in test["text"]])
    rows = {"rules": classification_metrics(labels, rules >= FLAG_THRESHOLD, rules)}
    for name in args.models:
        clf = TransformerClassifier.load(name, device="cuda")
        scores = clf.score(test["text"].tolist())
        rows[name] = classification_metrics(labels, scores >= clf.threshold, scores)
        # The hybrid detector the guardrail actually runs: flag if either layer flags.
        either = (scores >= clf.threshold) | (rules >= FLAG_THRESHOLD)
        rows[f"rules_or_{name}"] = classification_metrics(labels, either)

    table = pd.DataFrame(rows).T[["n", "precision", "recall", "f1", "fpr", "roc_auc", "tpr_at_1pct_fpr"]]
    print(table.astype(float).round(3).to_string())
    record = {"scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "set": "ood_llmail_test",
              "results": rows}
    if previous:
        record["rescore_reason"] = args.rescore
        record["earlier_runs"] = previous.get("earlier_runs", []) + [{k: v for k, v in previous.items()
                                                                     if k != "earlier_runs"}]
    RESULTS_PATH.write_text(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
