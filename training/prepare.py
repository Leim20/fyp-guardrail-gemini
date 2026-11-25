"""Build the train / validation / test sets for the injection classifier (P2, CRISP-DM data
preparation).

Steps:
  1. load every source (training/sources.py)
  2. assign rows to sets (see SETS below)
  3. clean train and val: drop empty rows, exact duplicates, and texts that appear with both
     labels; OOD samples get the same cleaning. The published test splits are left untouched
     so results stay comparable with other work.
  4. remove leakage: drop any train/val row whose normalised text exactly matches, or nearly
     matches (TF-IDF cosine >= NEAR_DUP_THRESHOLD), a row in any evaluation set; drop train rows
     that match val
  5. write data/processed/<set>.parquet and results/data_profile.json

CRISP-DM iterations:
  1  the Bitext pool is held back (written as pool_bitext, unused)
  2  the Bitext pool (genuine customer-support requests) is split 80/20 into train and val, so
     the models see in-domain benign text and thresholds are chosen with it in view. Planned in
     docs/data_card.md; triggered by iteration 1's false positives on customer requests.
     From here on ood_bitext is an in-domain held-out set rather than an out-of-distribution one.
  3  teacher-generated synthetic data (docs/distillation_plan.md) is added. It is split by
     generation call, not by row: one call writes about ten texts that share a domain, carrier
     and phrasing, so a row-level split would put near-siblings on both sides and inflate the
     held-out score. Minimal pairs come from a single call, so they also stay together. Calls
     are split 75/15/10 into train / val / test_synthetic within each category. LLMail-Inject
     joins the leakage references. For the dose-response experiment, train_synth{0,25,50}
     are copies of train holding that share of the synthetic train calls (nested, so 25 is
     inside 50); train itself is the 100% point.

LLMail-Inject (blind set for iteration 3, docs/distillation_plan.md) is split into dev and test
for every iteration, but only iteration 3 onwards uses it as a leakage reference, so the
iteration-1 and iteration-2 train/val sets stay exactly as they were trained.

Usage:
    uv run python -m training.prepare                 # iteration 2 (current)
    uv run python -m training.prepare --iteration 1   # rebuild the iteration-1 sets
    uv run python -m training.prepare --iteration 3   # add the synthetic data
"""

import argparse
import hashlib
import json
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from training import sources

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed"
PROFILE_PATH = ROOT / "results" / "data_profile.json"

SEED = 42
DEEPSET_VAL_FRACTION = 0.15  # deepset train rows moved to val so thresholds also see deepset
OOD_SAMPLE_SIZE = 2000
NEAR_DUP_THRESHOLD = 0.95
POOL_VAL_FRACTION = 0.2  # iteration 2: share of the Bitext pool used for val
LLMAIL_ATTACK_SAMPLE = 2000
LLMAIL_DEV_FRACTION = 0.2  # dev decides whether iteration 3 is adopted; test is reported once

# Sets used for fitting and model selection; everything else is evaluation only.
FIT_SETS = ("train", "val")
# Published test splits, kept exactly as released.
UNTOUCHED_SETS = ("test_deepset", "test_promptshield")
LLMAIL_SETS = ("ood_llmail_dev", "ood_llmail_test")
SYNTHETIC_SPLIT = {"train": 0.75, "val": 0.15, "test_synthetic": 0.10}
DOSES = (0, 25, 50)  # percent of the synthetic train calls in train_synth<dose>; train is 100
DOSE_SETS = tuple(f"train_synth{d}" for d in DOSES)
ITERATION3_SETS = ("test_synthetic",) + DOSE_SETS
# Columns kept in test_synthetic for per-technique and minimal-pair evaluation.
SYNTHETIC_EVAL_COLUMNS = ["category", "technique", "pair_id"]


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).lower().split())


def stratified_sample(df: pd.DataFrame, n: int, seed: int = SEED) -> pd.DataFrame:
    """Sample n rows keeping the label ratio of df."""
    frac = min(1.0, n / len(df))
    return df.groupby("label", group_keys=False).sample(frac=frac, random_state=seed)


def build_sets() -> dict[str, pd.DataFrame]:
    deepset = sources.load_deepset()
    shield = sources.load_promptshield()
    lakera = sources.load_lakera()
    bitext = sources.load_bitext()["train"].sample(frac=1.0, random_state=SEED)
    llmail = sources.load_llmail()
    llmail = pd.concat([llmail["attacks"].sample(n=LLMAIL_ATTACK_SAMPLE, random_state=SEED), llmail["benign"]],
                       ignore_index=True)
    llmail_dev = stratified_sample(llmail, round(len(llmail) * LLMAIL_DEV_FRACTION))

    deepset_val = stratified_sample(deepset["train"], round(len(deepset["train"]) * DEEPSET_VAL_FRACTION))
    deepset_train = deepset["train"].drop(deepset_val.index)

    return {
        "train": pd.concat([deepset_train, shield["train"]], ignore_index=True),
        "val": pd.concat([deepset_val, shield["validation"]], ignore_index=True),
        "test_deepset": deepset["test"],
        "test_promptshield": shield["test"],
        "ood_spml": stratified_sample(sources.load_spml()["train"], OOD_SAMPLE_SIZE),
        "ood_lakera": pd.concat([lakera["validation"], lakera["test"]], ignore_index=True),
        "ood_bitext": bitext.iloc[:OOD_SAMPLE_SIZE],
        # Held back for a possible later CRISP-DM iteration (adding in-domain benign data to
        # training). Disjoint from ood_bitext, so using it later cannot leak into the test.
        "pool_bitext": bitext.iloc[OOD_SAMPLE_SIZE : 2 * OOD_SAMPLE_SIZE],
        "ood_llmail_dev": llmail_dev,
        "ood_llmail_test": llmail.drop(llmail_dev.index),
    }


def split_by_group(df: pd.DataFrame, group: str, fractions: dict[str, float], stratum: str,
                   seed: int = SEED) -> dict[str, pd.DataFrame]:
    """Assign whole groups to sets, with the given fractions of groups taken within each stratum."""
    rng = np.random.default_rng(seed)
    assigned: dict[str, str] = {}
    for _, rows in df.groupby(stratum):
        groups = rng.permutation(sorted(rows[group].unique()))
        cuts = np.round(np.cumsum(list(fractions.values()))[:-1] * len(groups)).astype(int)
        for name, members in zip(fractions, np.split(groups, cuts)):
            assigned.update(dict.fromkeys(members, name))
    target = df[group].map(assigned)
    return {name: df[target == name] for name in fractions}


def nested_group_subsets(df: pd.DataFrame, group: str, percents: tuple[int, ...], stratum: str,
                         seed: int = SEED) -> dict[int, pd.DataFrame]:
    """The first p% of each stratum's groups in one fixed random order, so smaller subsets are
    contained in larger ones and only the amount of data changes between them."""
    rng = np.random.default_rng(seed)
    order = {s: rng.permutation(sorted(rows[group].unique())) for s, rows in df.groupby(stratum)}
    subsets = {}
    for p in percents:
        keep = {g for groups in order.values() for g in groups[:round(len(groups) * p / 100)]}
        subsets[p] = df[df[group].isin(keep)]
    return subsets


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Drop empty rows, label-conflicting texts and exact duplicates."""
    df = df.assign(norm=df["text"].map(normalise))
    empty = df["norm"] == ""
    df = df[~empty]
    labels_per_text = df.groupby("norm")["label"].nunique()
    conflicting = df["norm"].isin(labels_per_text[labels_per_text > 1].index)
    df = df[~conflicting]
    duplicate = df.duplicated("norm")
    df = df[~duplicate]
    return df, {"empty": int(empty.sum()), "label_conflict": int(conflicting.sum()), "duplicate": int(duplicate.sum())}


def near_duplicate_mask(candidates: list[str], references: list[str], chunk: int = 1000) -> np.ndarray:
    """True for each candidate whose TF-IDF cosine similarity to any reference is >= threshold."""
    vectoriser = TfidfVectorizer(sublinear_tf=True).fit(candidates + references)
    cand, ref = vectoriser.transform(candidates), vectoriser.transform(references)
    hit = np.zeros(len(candidates), dtype=bool)
    for start in range(0, ref.shape[0], chunk):
        similarity = (ref[start : start + chunk] @ cand.T).max(axis=0).toarray().ravel()
        hit |= similarity >= NEAR_DUP_THRESHOLD
    return hit


def remove_leakage(df: pd.DataFrame, references: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    exact = df["norm"].isin(set(references["norm"]))
    df = df[~exact]
    near = near_duplicate_mask(df["norm"].tolist(), references["norm"].tolist())
    return df[~near], {"leak_exact": int(exact.sum()), "leak_near": int(near.sum())}


def profile(df: pd.DataFrame) -> dict[str, float | int]:
    lengths = df["text"].str.len()
    return {
        "rows": len(df),
        "injection": int(df["label"].sum()),
        "benign": int((df["label"] == 0).sum()),
        "injection_rate": round(float(df["label"].mean()), 3),
        "chars_median": int(lengths.median()),
        "chars_p90": int(lengths.quantile(0.9)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iteration", type=int, choices=[1, 2, 3], default=2)
    iteration = parser.parse_args().iteration

    sets = build_sets()
    removed: dict[str, dict[str, int]] = {}
    for name, df in sets.items():
        if name in UNTOUCHED_SETS:
            sets[name] = df.assign(norm=df["text"].map(normalise))
        else:
            sets[name], removed[name] = clean(df)

    if iteration >= 2:
        pool = sets.pop("pool_bitext")
        pool_val = pool.sample(frac=POOL_VAL_FRACTION, random_state=SEED)
        sets["train"] = pd.concat([sets["train"], pool.drop(pool_val.index)], ignore_index=True)
        sets["val"] = pd.concat([sets["val"], pool_val], ignore_index=True)
        (OUT_DIR / "pool_bitext.parquet").unlink(missing_ok=True)

    if iteration >= 3:
        synthetic, removed["synthetic"] = clean(sources.load_synthetic())
        parts = split_by_group(synthetic, "job_id", SYNTHETIC_SPLIT, stratum="category")
        sets["train"] = pd.concat([sets["train"], parts["train"]], ignore_index=True)
        sets["val"] = pd.concat([sets["val"], parts["val"]], ignore_index=True)
        sets["test_synthetic"] = parts["test_synthetic"]
    else:
        for name in ITERATION3_SETS:
            (OUT_DIR / f"{name}.parquet").unlink(missing_ok=True)

    not_references = FIT_SETS + ("pool_bitext",) + (LLMAIL_SETS if iteration <= 2 else ())
    eval_rows = pd.concat([df for name, df in sets.items() if name not in not_references])
    sets["val"], leak = remove_leakage(sets["val"], eval_rows)
    removed["val"].update(leak)
    sets["train"], leak = remove_leakage(sets["train"], pd.concat([eval_rows, sets["val"]]))
    removed["train"].update(leak)
    # Bitext is template-generated, so the pool holds many near-copies of ood_bitext rows.
    # (In iteration 2 the pool is already inside train/val and was checked above.)
    if "pool_bitext" in sets:
        sets["pool_bitext"], leak = remove_leakage(sets["pool_bitext"], eval_rows)
        removed["pool_bitext"].update(leak)

    if iteration >= 3:
        # Built after leakage removal, so every dose point gets exactly the same cleaned rows.
        is_synthetic = sets["train"]["origin"].str.startswith("synthetic")
        doses = nested_group_subsets(sets["train"][is_synthetic], "job_id", DOSES, stratum="category")
        for dose, rows in doses.items():
            sets[f"train_synth{dose}"] = pd.concat([sets["train"][~is_synthetic], rows])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {"iteration": iteration, "seed": SEED, "near_dup_threshold": NEAR_DUP_THRESHOLD,
              "sets": {}, "removed": removed}
    if iteration >= 3:  # pins the exact synthetic file this data was built from
        report["synthetic_sha256"] = hashlib.sha256(sources.SYNTHETIC_PATH.read_bytes()).hexdigest()
    for name, df in sets.items():
        columns = ["text", "label", "origin"] + (SYNTHETIC_EVAL_COLUMNS if name == "test_synthetic" else [])
        df[columns].reset_index(drop=True).to_parquet(OUT_DIR / f"{name}.parquet")
        report["sets"][name] = profile(df) | {"origins": df["origin"].value_counts().to_dict()}
        if "job_id" in df and df["job_id"].notna().any():
            report["sets"][name]["synthetic_calls"] = int(df["job_id"].nunique())

    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(report, indent=2))
    print(pd.DataFrame({k: {c: v[c] for c in ("rows", "injection", "benign", "injection_rate")}
                        for k, v in report["sets"].items()}).T.to_string())
    print("\nRemoved rows:\n" + pd.DataFrame(removed).T.fillna(0).astype(int).to_string())


if __name__ == "__main__":
    main()
