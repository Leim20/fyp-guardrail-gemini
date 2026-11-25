"""Shared metrics for the classifier (P2) and the end-to-end evaluation (P5).

Text-level measures follow NFR02 (precision, recall, F1 and false-positive rate alongside
accuracy) and NFR01 (median and 95th-percentile latency). Metrics that are undefined for a
set, such as recall on an all-benign set, are returned as None rather than a misleading 0.
"""

import math

import numpy as np
from sklearn.metrics import roc_auc_score, roc_curve


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def tpr_at_fpr(y_true: np.ndarray, y_score: np.ndarray, max_fpr: float = 0.01) -> float:
    """Highest true-positive rate reachable while keeping the false-positive rate <= max_fpr."""
    fpr, tpr, _ = roc_curve(y_true, y_score)
    return float(tpr[fpr <= max_fpr].max())


def classification_metrics(y_true, y_pred, y_score=None) -> dict[str, float | int | None]:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    result = {
        "n": len(y_true), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "accuracy": _ratio(tp + tn, len(y_true)),
        "precision": precision, "recall": recall, "f1": f1,
        "fpr": _ratio(fp, fp + tn),
        "roc_auc": None, "tpr_at_1pct_fpr": None,
    }
    # Ranking metrics need both classes present.
    if y_score is not None and len(set(y_true.tolist())) == 2:
        result["roc_auc"] = float(roc_auc_score(y_true, y_score))
        result["tpr_at_1pct_fpr"] = tpr_at_fpr(y_true, np.asarray(y_score))
    return result


def latency_summary(seconds: list[float]) -> dict[str, float]:
    ms = np.asarray(seconds) * 1000
    return {"latency_median_ms": float(np.median(ms)), "latency_p95_ms": float(np.percentile(ms, 95))}


def best_f1_threshold(y_true, y_score) -> float:
    """Decision threshold that maximises F1, chosen on validation data only.

    Predictions only change at observed scores, so every threshold in (previous score, s] acts
    like s. When validation is easy, a wide range of thresholds ties on the best F1; the midpoint
    of that range is returned. Taking its lowest end instead makes the model as trigger-happy as
    validation allows (iteration 1 chose 0.0018 for DeBERTa this way).
    """
    y_true, y_score = np.asarray(y_true), np.asarray(y_score)
    candidates = np.unique(y_score)
    f1 = np.array([classification_metrics(y_true, y_score >= t)["f1"] or 0.0 for t in candidates])
    best = np.isclose(f1, f1.max())
    first = int(np.argmax(best))
    last = first
    while last + 1 < len(candidates) and best[last + 1]:
        last += 1
    lower = candidates[first - 1] if first > 0 else 0.0
    return float((lower + candidates[last]) / 2)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion; sensible for small n and rates near 0 or 1."""
    if n == 0:
        return math.nan, math.nan
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def paired_auc_difference(y_true, score_a, score_b, resamples: int = 2000, seed: int = 42) -> dict[str, float]:
    """ROC-AUC of b minus a on the same texts, with a 95% percentile bootstrap interval.
    Both models are scored on the same resampled texts each round, so the interval reflects
    the difference itself rather than two independent uncertainties."""
    y_true, score_a, score_b = np.asarray(y_true), np.asarray(score_a), np.asarray(score_b)
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(resamples):
        idx = rng.integers(0, len(y_true), len(y_true))
        if len(set(y_true[idx].tolist())) == 2:
            deltas.append(roc_auc_score(y_true[idx], score_b[idx]) - roc_auc_score(y_true[idx], score_a[idx]))
    low, high = np.percentile(deltas, [2.5, 97.5])
    delta = roc_auc_score(y_true, score_b) - roc_auc_score(y_true, score_a)
    return {"delta": float(delta), "ci_low": float(low), "ci_high": float(high)}
