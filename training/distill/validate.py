"""Step 2: rule checks on the teacher's texts, then the same leakage filter as the real data.

An item is dropped, with its reason recorded, if it:
  - breaks the item schema, or its label contradicts its category (a minimal pair must hold
    exactly one injection and one benign text, otherwise both go);
  - is too short or too long, or is not English;
  - is an injection whose `evidence` is not copied from its own text;
  - contains a forbidden string (the NovaMart world or LLMail-Inject's attack target);
  - duplicates or nearly duplicates (TF-IDF cosine >= 0.95) an earlier synthetic text, the
    partner of a minimal pair excepted, or any evaluation text, scenario or KB document.

Usage:
    uv run python -m training.distill.validate --stage pilot
"""

import argparse
import json
import re
from collections import Counter
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ValidationError
from sklearn.feature_extraction.text import TfidfVectorizer

from training.baseline import scenario_texts
from training.distill.generate import stage_dir
from training.distill.prompts import CATEGORY_LABEL, STAGES
from training.prepare import NEAR_DUP_THRESHOLD, OUT_DIR, near_duplicate_mask, normalise

MIN_CHARS, MAX_CHARS = 20, 5000
MIN_ASCII_LETTER_SHARE = 0.95
FORBIDDEN = re.compile(r"novamart|\bo-6666\b|evil-mail|\baisyah\b|daniel lee|\bc100[12]\b|\brm\s?\d|"
                       r"contact@contact\.com|project zenith", re.IGNORECASE)
REFERENCE_SETS = ["test_deepset", "test_promptshield", "ood_spml", "ood_lakera", "ood_bitext",
                  "ood_llmail_dev", "ood_llmail_test"]


class Intent(BaseModel):
    addressee: Literal["ai_assistant", "human", "none"]
    requested_action: str | None
    bypasses_user: bool
    evidence: str | None


class Item(BaseModel):
    text: str
    label: Literal[0, 1]
    pair: int | None = None
    intent: Intent


def flatten(stage: str) -> list[dict]:
    rows = []
    for path in sorted((stage_dir(stage) / "raw").glob("*.json")):
        record = json.loads(path.read_text())
        job = record["job"]
        for k, raw in enumerate(record["reply"].get("items", [])):
            row = {"id": f"{job['id']}-{k:02d}", "job_id": job["id"], "category": job["category"],
                   "carrier": job["carrier"], "domain": job["domain"], "technique": job["technique"],
                   "prompt_version": record["prompt_version"], "model": record["model"], "drop": None}
            try:
                item = Item.model_validate(raw)
                row |= item.model_dump(exclude={"pair"}) | {
                    "pair_id": f"{job['id']}-p{item.pair}" if job["category"] == "D" and item.pair else None}
            except ValidationError:
                row |= {"text": str(raw.get("text", "")) if isinstance(raw, dict) else "", "label": None,
                        "intent": None, "pair_id": None, "drop": "schema"}
            rows.append(row)
    return rows


def rule_check(row: dict) -> str | None:
    text, label = row["text"], row["label"]
    if row["category"] in CATEGORY_LABEL and label != CATEGORY_LABEL[row["category"]]:
        return "label_contradicts_category"
    if row["category"] == "D" and row["pair_id"] is None:
        return "pair_missing"
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        return "length"
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isascii() for c in letters) / len(letters) < MIN_ASCII_LETTER_SHARE:
        return "not_english"
    evidence = row["intent"]["evidence"]
    if label == 1 and (not evidence or " ".join(evidence.split()) not in " ".join(text.split())):
        return "evidence_not_in_text"
    if FORBIDDEN.search(text):
        return "forbidden_string"
    return None


def check_pairs(df: pd.DataFrame) -> None:
    """A minimal pair must be exactly one benign and one injection text."""
    pairs = df[df["pair_id"].notna() & df["drop"].isna()].groupby("pair_id")["label"].agg(sorted)
    bad = pairs[pairs.map(lambda labels: labels != [0, 1])].index
    df.loc[df["pair_id"].isin(bad) & df["drop"].isna(), "drop"] = "pair_not_one_of_each"


def within_set_duplicates(df: pd.DataFrame) -> None:
    """Drop later texts that copy or nearly copy an earlier kept one (pair partners excepted)."""
    live = df[df["drop"].isna()]
    norms = live["text"].map(normalise)
    exact = norms.duplicated()
    df.loc[exact[exact].index, "drop"] = "duplicate_exact"
    live = live[~exact.to_numpy()]
    matrix = TfidfVectorizer(sublinear_tf=True).fit_transform(live["text"].map(normalise))
    similarity = (matrix @ matrix.T).toarray()
    pair_ids, kept = live["pair_id"].to_numpy(), []
    for i, index in enumerate(live.index):
        clash = [j for j in kept if similarity[i, j] >= NEAR_DUP_THRESHOLD
                 and not (pair_ids[i] is not None and pair_ids[i] == pair_ids[j])]
        if clash:
            df.loc[index, "drop"] = "duplicate_near"
        else:
            kept.append(i)


def leakage(df: pd.DataFrame) -> None:
    references = pd.concat([pd.read_parquet(OUT_DIR / f"{s}.parquet")["text"] for s in REFERENCE_SETS]
                           + [scenario_texts()["text"]])
    ref_norms = references.map(normalise)
    live = df[df["drop"].isna()]
    norms = live["text"].map(normalise)
    exact = norms.isin(set(ref_norms))
    df.loc[exact[exact].index, "drop"] = "leak_exact"
    live_norms = norms[~exact]
    near = near_duplicate_mask(live_norms.tolist(), ref_norms.tolist())
    df.loc[live_norms.index[near], "drop"] = "leak_near"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=list(STAGES), required=True)
    stage = parser.parse_args().stage

    df = pd.DataFrame(flatten(stage))
    live = df["drop"].isna()
    df.loc[live, "drop"] = df[live].apply(rule_check, axis=1)
    check_pairs(df)
    within_set_duplicates(df)
    leakage(df)
    df.loc[df["label"] == 0, "intent"] = df.loc[df["label"] == 0, "intent"].map(
        lambda intent: intent | {"evidence": None} if intent else intent)

    kept = df[df["drop"].isna()]
    partners = kept["pair_id"].value_counts()
    kept = kept.assign(pair_complete=kept["pair_id"].map(partners).eq(2))
    out = stage_dir(stage)
    kept.drop(columns="drop").to_json(out / "validated.jsonl", orient="records", lines=True, force_ascii=False)
    df[df["drop"].notna()].to_json(out / "validation_dropped.jsonl", orient="records", lines=True, force_ascii=False)

    lengths = kept.assign(chars=kept["text"].str.len()).groupby("label")["chars"].median()
    report = {"stage": stage, "generated": len(df), "kept": len(kept),
              "dropped": dict(Counter(df["drop"].dropna())),
              "kept_by_category": kept["category"].value_counts().sort_index().to_dict(),
              "kept_by_label": kept["label"].value_counts().sort_index().to_dict(),
              "complete_pairs": int(kept["pair_complete"].sum() // 2),
              "median_chars_by_label": {int(k): int(v) for k, v in lengths.items()}}
    (out / "validation_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
