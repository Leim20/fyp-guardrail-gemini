"""Public datasets for the injection classifier (P2, CRISP-DM data understanding).

Every loader returns {split: DataFrame} with columns `text`, `label` (1 = injection) and
`origin` (dataset:split), so the preparation step can treat all sources the same way.
Why each dataset is used, and in which role, is documented in docs/data_card.md.
"""

import json
from pathlib import Path

import pandas as pd
from datasets import load_dataset
from huggingface_hub import hf_hub_download

LICENSES = {
    "deepset": ("deepset/prompt-injections", "Apache-2.0"),
    "promptshield": ("hendzh/PromptShield", "Apache-2.0"),
    "spml": ("reshabhs/SPML_Chatbot_Prompt_Injection", "MIT"),
    "lakera": ("Lakera/gandalf_ignore_instructions", "MIT"),
    "bitext": ("bitext/Bitext-customer-support-llm-chatbot-training-dataset", "CDLA-Sharing-1.0"),
    "llmail": ("microsoft/llmail-inject-challenge", "MIT"),
}


def _frame(texts, labels, origin: str) -> pd.DataFrame:
    return pd.DataFrame({"text": list(texts), "label": [int(x) for x in labels], "origin": origin})


def load_deepset() -> dict[str, pd.DataFrame]:
    ds = load_dataset(LICENSES["deepset"][0])
    return {split: _frame(d["text"], d["label"], f"deepset:{split}") for split, d in ds.items()}


def load_promptshield() -> dict[str, pd.DataFrame]:
    ds = load_dataset(LICENSES["promptshield"][0])
    return {split: _frame(d["prompt"], d["label"], f"promptshield:{split}") for split, d in ds.items()}


def load_spml() -> dict[str, pd.DataFrame]:
    # Only the user prompt is used: the guardrail scans incoming content, not the system prompt.
    d = load_dataset(LICENSES["spml"][0])["train"]
    return {"train": _frame(d["User Prompt"], d["Prompt injection"], "spml:train")}


def load_lakera() -> dict[str, pd.DataFrame]:
    # Every row is an injection attempt from the Gandalf game, so the label is always 1.
    ds = load_dataset(LICENSES["lakera"][0])
    return {split: _frame(d["text"], [1] * len(d), f"lakera:{split}") for split, d in ds.items()}


def load_bitext() -> dict[str, pd.DataFrame]:
    # Genuine customer-support requests (refunds, orders, accounts), so the label is always 0.
    d = load_dataset(LICENSES["bitext"][0])["train"]
    return {"train": _frame(d["instruction"], [0] * len(d), "bitext:train")}


def load_llmail() -> dict[str, pd.DataFrame]:
    # Phase-2 submissions are adaptive attacks on an email assistant written by challenge
    # participants; only those the organisers' annotation marks as attack attempts are kept
    # (label 1). The benign emails are the organisers' false-positive test set (label 0). Both
    # use the same "Subject of the email: ... Body: ..." format, so format is not a label cue.
    repo = LICENSES["llmail"][0]
    attacks = json.loads(Path(hf_hub_download(repo, "data/labelled_unique_submissions_phase2.json",
                                              repo_type="dataset")).read_text())
    benign = json.loads(Path(hf_hub_download(repo, "data/emails_for_fp_tests.json", repo_type="dataset")).read_text())
    texts = [text for text, note in attacks.items() if str(note["attack_attempt"]) == "True"]
    return {"attacks": _frame(texts, [1] * len(texts), "llmail:phase2"),
            "benign": _frame(benign, [0] * len(benign), "llmail:fp_tests")}


SYNTHETIC_PATH = Path(__file__).resolve().parent.parent / "data" / "synthetic" / "full" / "verified.jsonl"
SYNTHETIC_COLUMNS = ["job_id", "category", "technique", "pair_id"]


def load_synthetic(path: Path = SYNTHETIC_PATH) -> pd.DataFrame:
    """Teacher-generated, teacher-verified texts (P2 iteration 3, docs/distillation_plan.md).

    Besides text/label/origin it keeps the generation call (job_id), so the split can keep a
    call's near-identical siblings together, plus category, technique and the minimal-pair id
    for the per-technique and minimal-pair evaluation."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    df = pd.DataFrame(rows)
    return _frame(df["text"], df["label"], "synthetic:deepseek").join(df[SYNTHETIC_COLUMNS])
