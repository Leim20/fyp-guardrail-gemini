"""Rule-only and TF-IDF baselines for injection detection (P2, CRISP-DM modelling and
evaluation).

Models:
  rules      rule scanner alone (config/rules.yaml), no training
  tfidf_lr   TF-IDF word 1-2-grams + char 3-5-grams, logistic regression
  tfidf_svm  same features, linear SVM with Platt-scaled probabilities

Protocol: C is chosen by stratified 5-fold cross-validation on train (F1), the decision
threshold is chosen on val (max F1), and then every evaluation set is scored exactly once.

Usage:
    uv run python -m training.prepare     # builds data/processed/ first
    uv run python -m training.baseline
"""

import json
import random
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GridSearchCV, StratifiedKFold
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC

from agent.mock_world import load_kb
from evaluation.metrics import best_f1_threshold, classification_metrics, latency_summary
from evaluation.scenarios import load_scenarios
from guardrail.classifier import InjectionClassifier
from guardrail.rules import FLAG_THRESHOLD, RuleScanner
from training.prepare import OUT_DIR, PROFILE_PATH, SEED

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "results" / "baseline_metrics.json"
PREDICTIONS_PATH = OUT_DIR / "predictions_baseline.parquet"  # regenerable, not committed

EVAL_SETS = ["val", "test_deepset", "test_promptshield", "ood_spml", "ood_lakera", "ood_bitext", "scenarios"]
# Iteration 3 adds the synthetic held-out test and LLMail-dev, which its adoption rule uses.
# LLMail-test is never in an evaluation list: evaluation/blind_test.py scores it once, after
# the adoption decision, so no choice can be tuned on it.
ITERATION3_SETS = ["test_synthetic", "ood_llmail_dev"]
LATENCY_SAMPLE = 300


def scenario_texts() -> pd.DataFrame:
    """Domain test set: scenario user messages (injection = direct attack) and KB documents
    (injection = poisoned). Never used for training or model selection."""
    rows = [(s.user_message, int(s.category == "direct"), f"scenario:{s.id}") for s in load_scenarios()]
    rows += [(d.body, int(d.poisoned), f"kb:{name}") for name, d in load_kb().items()]
    return pd.DataFrame(rows, columns=["text", "label", "origin"])


def data_iteration() -> int:
    """The CRISP-DM iteration data/processed/ was last built for."""
    return json.loads(PROFILE_PATH.read_text())["iteration"]


def eval_set_names() -> list[str]:
    return EVAL_SETS + (ITERATION3_SETS if data_iteration() >= 3 else [])


def load_sets(train_set: str = "train") -> dict[str, pd.DataFrame]:
    """`train` is read from <train_set>.parquet (the dose-response runs use train_synth<p>)."""
    sets = {"train": pd.read_parquet(OUT_DIR / f"{train_set}.parquet")}
    sets |= {name: pd.read_parquet(OUT_DIR / f"{name}.parquet") for name in eval_set_names() if name != "scenarios"}
    sets["scenarios"] = scenario_texts()
    return sets


def tfidf_features() -> FeatureUnion:
    # Character n-grams make the model robust to misspellings and partly to other languages.
    return FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, sublinear_tf=True,
                                 max_features=300_000)),
    ])


def model_search(kind: str) -> GridSearchCV:
    if kind == "tfidf_lr":
        clf, grid = LogisticRegression(solver="liblinear", max_iter=2000), {"clf__C": [0.3, 1, 3, 10, 30]}
    else:
        clf = CalibratedClassifierCV(LinearSVC(), method="sigmoid", cv=3)
        grid = {"clf__estimator__C": [0.03, 0.1, 0.3, 1, 3]}
    pipeline = Pipeline([("features", tfidf_features()), ("clf", clf)])
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    return GridSearchCV(pipeline, grid, scoring="f1", cv=folds, n_jobs=8, refit=True)


def latency_texts(sets: dict[str, pd.DataFrame], n: int = LATENCY_SAMPLE) -> list[str]:
    """The fixed sample every model is timed on, so latencies are comparable."""
    return random.Random(SEED).sample(sets["test_promptshield"]["text"].tolist(), n)


def latency(score_one: Callable[[str], float], texts: list[str]) -> dict[str, float]:
    """Per-request latency: one text at a time, as the guardrail sees traffic."""
    timings = []
    for text in texts:
        start = time.perf_counter()
        score_one(text)
        timings.append(time.perf_counter() - start)
    return latency_summary(timings)


def evaluate(model: str, scores: dict[str, np.ndarray], threshold: float, sets: dict[str, pd.DataFrame]) -> list[dict]:
    """One row per scored set, plus val broken down by source dataset (val/deepset, ...)."""
    rows = [
        {"model": model, "set": name, "threshold": threshold,
         **classification_metrics(sets[name]["label"], scores[name] >= threshold, scores[name])}
        for name in scores
    ]
    if "val" in scores:
        source = sets["val"]["origin"].str.split(":").str[0].to_numpy()
        for name in sorted(set(source)):
            mask = source == name
            rows.append({"model": model, "set": f"val/{name}", "threshold": threshold,
                         **classification_metrics(sets["val"]["label"][mask], scores["val"][mask] >= threshold,
                                                  scores["val"][mask])})
    return rows


def main() -> None:
    sets = load_sets()
    train = sets["train"]
    timing_texts = latency_texts(sets)
    rows, model_info, all_scores = [], {}, {}

    scanner = RuleScanner.from_yaml()
    scores = {name: np.array([scanner.score(t) for t in sets[name]["text"]]) for name in EVAL_SETS}
    rows += evaluate("rules", scores, FLAG_THRESHOLD, sets)
    model_info["rules"] = {"threshold": FLAG_THRESHOLD, **latency(scanner.score, timing_texts)}
    all_scores["rules"] = scores
    print("rules: done")

    for kind in ("tfidf_lr", "tfidf_svm"):
        search = model_search(kind)
        start = time.perf_counter()
        search.fit(train["text"], train["label"])
        fit_seconds = time.perf_counter() - start
        threshold = best_f1_threshold(sets["val"]["label"], search.best_estimator_.predict_proba(sets["val"]["text"])[:, 1])
        info = {
            "best_params": search.best_params_, "cv_f1": float(search.best_score_),
            "threshold": threshold, "train_rows": len(train), "fit_seconds": round(fit_seconds, 1),
        }
        clf = InjectionClassifier(kind, search.best_estimator_, threshold, metadata=info)
        clf.save()
        scores = {name: clf.score(sets[name]["text"]) for name in EVAL_SETS}
        rows += evaluate(kind, scores, threshold, sets)
        model_info[kind] = info | latency(lambda t: clf.score([t])[0], timing_texts)
        all_scores[kind] = scores
        print(f"{kind}: done, {info}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({"models": model_info, "results": rows}, indent=2))
    predictions = pd.concat([
        sets[name].assign(set=name, **{f"score_{m}": all_scores[m][name] for m in all_scores}) for name in EVAL_SETS
    ])
    predictions.to_parquet(PREDICTIONS_PATH)

    table = pd.DataFrame(rows).set_index(["set", "model"])
    cols = ["n", "precision", "recall", "f1", "fpr", "roc_auc", "tpr_at_1pct_fpr"]
    print(table[cols].round(3).to_string())
    print(pd.DataFrame(model_info).T[["threshold", "latency_median_ms", "latency_p95_ms"]].round(3).to_string())


if __name__ == "__main__":
    main()
