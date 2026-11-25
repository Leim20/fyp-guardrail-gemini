"""Evaluate off-the-shelf prompt-injection detectors as external references (P2).

These models were trained by others and are not fine-tuned here. They are scored on the same
sets, with the same head+tail preprocessing, at their native decision rule (probability >= 0.5,
i.e. the argmax of their two classes), without any tuning on our data.

Caveat for the report: their training data is only partly disclosed. ProtectAI lists 21 source
datasets but names 7, and 8 unnamed ones are MIT-licensed, as are SPML and Lakera Gandalf, so
overlap with our evaluation sets cannot be ruled out. Their numbers are context, not a
controlled comparison.

Usage:
    uv run python -m training.reference                              # every reference you can access
    uv run python -m training.reference --only protectai_deberta_v2
Prompt Guard 2 is gated: request access on its Hugging Face page and set HF_TOKEN in .env first.
"""

import argparse
import os
from pathlib import Path

import torch
from dotenv import load_dotenv
from huggingface_hub.errors import GatedRepoError
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from guardrail.classifier import TransformerClassifier
from training.baseline import latency_texts, load_sets
from training.finetune import evaluate_and_time, save_results

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "results" / "reference_metrics.json"
REFERENCES = {
    "protectai_deberta_v2": "protectai/deberta-v3-base-prompt-injection-v2",
    "prompt_guard_2_86m": "meta-llama/Llama-Prompt-Guard-2-86M",
}
# Label names that mean "attack" in each model's config.
POSITIVE_LABELS = {"injection", "malicious", "label_1", "jailbreak"}


def load_reference(name: str, model_id: str, device: str) -> TransformerClassifier:
    token = os.getenv("HF_TOKEN") or None
    model = AutoModelForSequenceClassification.from_pretrained(model_id, dtype=torch.float32, token=token)
    labels = {i: label.lower() for i, label in model.config.id2label.items()}
    if model.config.num_labels != 2 or labels[1] not in POSITIVE_LABELS:
        raise ValueError(f"{model_id}: expected a two-class model with class 1 = attack, got {labels}")
    tokenizer = AutoTokenizer.from_pretrained(model_id, token=token)
    return TransformerClassifier(name, model, tokenizer, threshold=0.5, device=device,
                                 metadata={"model_id": model_id, "id2label": labels, "trained_by_us": False})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=sorted(REFERENCES), help="evaluate a single reference model")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    sets = load_sets()
    timing = latency_texts(sets)
    for name, model_id in REFERENCES.items():
        if args.only and name != args.only:
            continue
        try:
            gpu_clf = load_reference(name, model_id, "cuda")
        except (GatedRepoError, OSError) as exc:
            print(f"skipping {name}: cannot download {model_id} ({type(exc).__name__}); "
                  "request access on Hugging Face and set HF_TOKEN in .env")
            continue
        print(f"== {name} ({model_id})")
        cpu_clf = load_reference(name, model_id, "cpu")
        rows, timing_info, scores = evaluate_and_time(name, gpu_clf, cpu_clf, sets, timing)
        save_results(RESULTS_PATH, name, gpu_clf.metadata | {"threshold": 0.5} | timing_info, rows, sets, scores)


if __name__ == "__main__":
    main()
