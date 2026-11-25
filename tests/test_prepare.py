"""Iteration-3 data preparation: group-level splits, nested dose subsets, per-source val rows."""

import numpy as np
import pandas as pd

from evaluation.metrics import paired_auc_difference
from training.baseline import evaluate
from training.prepare import SYNTHETIC_SPLIT, nested_group_subsets, split_by_group


def synthetic(calls_per_category: int = 20, rows_per_call: int = 10) -> pd.DataFrame:
    rows = []
    for category in "ABCDE":
        for call in range(calls_per_category):
            job = f"{category}{call:03d}"
            for i in range(rows_per_call):
                pair = f"{job}-p{i // 2}" if category == "D" else None
                rows.append({"text": f"{job} text {i}", "label": i % 2, "category": category,
                             "job_id": job, "pair_id": pair})
    return pd.DataFrame(rows)


def test_a_generation_call_never_spans_two_sets():
    parts = split_by_group(synthetic(), "job_id", SYNTHETIC_SPLIT, stratum="category")
    jobs = [set(df["job_id"]) for df in parts.values()]
    assert all(not (a & b) for i, a in enumerate(jobs) for b in jobs[i + 1:])
    assert sum(len(df) for df in parts.values()) == len(synthetic())


def test_minimal_pairs_stay_together():
    parts = split_by_group(synthetic(), "job_id", SYNTHETIC_SPLIT, stratum="category")
    for df in parts.values():
        sizes = df.dropna(subset=["pair_id"]).groupby("pair_id").size()
        assert (sizes == 2).all()


def test_every_category_is_split_in_the_planned_proportions():
    parts = split_by_group(synthetic(), "job_id", SYNTHETIC_SPLIT, stratum="category")
    for name, share in SYNTHETIC_SPLIT.items():
        calls = parts[name].groupby("category")["job_id"].nunique()
        assert set(calls.index) == set("ABCDE")
        assert (calls == round(20 * share)).all()


def test_split_is_reproducible():
    first = split_by_group(synthetic(), "job_id", SYNTHETIC_SPLIT, stratum="category")
    second = split_by_group(synthetic(), "job_id", SYNTHETIC_SPLIT, stratum="category")
    assert all(first[k].equals(second[k]) for k in SYNTHETIC_SPLIT)


def test_dose_subsets_are_nested_and_sized_by_calls():
    subsets = nested_group_subsets(synthetic(), "job_id", (0, 25, 50, 100), stratum="category")
    calls = {p: set(df["job_id"]) for p, df in subsets.items()}
    assert calls[0] == set()
    assert calls[25] < calls[50] < calls[100]
    assert len(calls[25]) == 5 * 5 and len(calls[100]) == 100


def test_val_is_also_reported_per_source():
    val = pd.DataFrame({"text": list("abcd"), "label": [1, 0, 1, 0],
                        "origin": ["deepset:train", "deepset:train", "synthetic:deepseek", "synthetic:deepseek"]})
    rows = {r["set"]: r for r in evaluate("m", {"val": np.array([0.9, 0.1, 0.2, 0.1])}, 0.5, {"val": val})}
    assert rows["val/deepset"]["f1"] == 1.0
    assert rows["val/synthetic"]["recall"] == 0.0
    assert rows["val"]["n"] == 4


def test_paired_auc_difference_has_the_right_sign():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 400)
    weak, strong = y + rng.normal(0, 2, 400), y + rng.normal(0, 0.5, 400)
    result = paired_auc_difference(y, weak, strong, resamples=300)
    assert result["delta"] > 0 and result["ci_low"] > 0


def test_adoption_reports_minimal_pairs_and_sources():
    from evaluation.adoption import synthetic_report, val_by_source
    test = pd.DataFrame({"label": [1, 0, 1, 0, 1], "category": ["D", "D", "D", "D", "A"],
                         "technique": ["subtle", None, "authority", None, "subtle"],
                         "pair_id": ["p1", "p1", "p2", "p2", None], "origin": "synthetic:deepseek"})
    report = synthetic_report(test, np.array([0.9, 0.1, 0.9, 0.8, 0.2]), 0.5)
    assert report["minimal_pairs"]["n"] == 2 and report["minimal_pairs"]["both_correct"] == 1
    assert report["injection_recall_by_technique"]["subtle"] == {"recall": 0.5, "n": 2}
    val = test.assign(origin=["deepset:train"] * 2 + ["bitext:train"] * 3, label=[1, 0, 0, 0, 0])
    sources = val_by_source(val, np.array([0.9, 0.1, 0.9, 0.1, 0.1]), 0.5)
    assert sources["deepset"]["f1"] == 1.0 and abs(sources["bitext"]["fpr"] - 1 / 3) < 1e-9
