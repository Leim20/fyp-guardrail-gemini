"""ML classifier layer of the input guard: the probability that a text is a prompt injection.

Two model families share the same score / predict interface, so the guardrail can swap them:
  - InjectionClassifier: a scikit-learn pipeline (TF-IDF baselines), saved as models/<name>.joblib
  - TransformerClassifier: a fine-tuned encoder (DeBERTa-v3, ModernBERT), saved as models/<name>/
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


class InjectionClassifier:
    def __init__(self, name: str, pipeline: Any, threshold: float, metadata: dict | None = None):
        self.name = name
        self.pipeline = pipeline
        self.threshold = threshold
        self.metadata = metadata or {}

    def score(self, texts: Sequence[str]) -> np.ndarray:
        """Injection probability for each text."""
        return self.pipeline.predict_proba(list(texts))[:, 1]

    def predict(self, texts: Sequence[str]) -> np.ndarray:
        return self.score(texts) >= self.threshold

    def save(self, path: Path | None = None) -> Path:
        path = path or MODELS_DIR / f"{self.name}.joblib"
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"name": self.name, "pipeline": self.pipeline, "threshold": self.threshold, "metadata": self.metadata},
            path,
        )
        return path

    @classmethod
    def load(cls, name_or_path: str | Path) -> "InjectionClassifier":
        path = Path(name_or_path)
        if path.suffix != ".joblib":
            path = MODELS_DIR / f"{name_or_path}.joblib"
        return cls(**joblib.load(path))


def head_tail_ids(tokenizer: Any, texts: Sequence[str], max_length: int, head_tokens: int) -> list[list[int]]:
    """Token ids for each text, keeping the first `head_tokens` and the last
    `max_length - 2 - head_tokens` tokens when a text is too long.

    Injections are often appended after benign content, so plain right-truncation would cut
    them off. Head+tail truncation is the best-performing scheme in Sun et al. (2019), "How to
    Fine-Tune BERT for Text Classification?".
    """
    budget = max_length - 2  # room for [CLS] and [SEP]
    tail_tokens = budget - head_tokens
    encoded = []
    for ids in tokenizer(list(texts), add_special_tokens=False)["input_ids"]:
        if len(ids) > budget:
            ids = ids[:head_tokens] + ids[len(ids) - tail_tokens:]
        encoded.append([tokenizer.cls_token_id, *ids, tokenizer.sep_token_id])
    return encoded


def pad_batch(sequences: Sequence[list[int]], pad_id: int) -> tuple[torch.Tensor, torch.Tensor]:
    """Right-pad to the longest sequence in the batch; returns input_ids and attention_mask."""
    width = max(len(s) for s in sequences)
    input_ids = torch.full((len(sequences), width), pad_id, dtype=torch.long)
    attention_mask = torch.zeros((len(sequences), width), dtype=torch.long)
    for row, seq in enumerate(sequences):
        input_ids[row, : len(seq)] = torch.tensor(seq)
        attention_mask[row, : len(seq)] = 1
    return input_ids, attention_mask


class TransformerClassifier:
    """Fine-tuned encoder with a two-class head; class 1 is "injection"."""

    def __init__(self, name: str, model: Any, tokenizer: Any, threshold: float, max_length: int = 512,
                 head_tokens: int = 128, metadata: dict | None = None, device: str | None = None):
        self.name = name
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = model.to(self.device)
        self.tokenizer = tokenizer
        self.threshold = threshold
        self.max_length = max_length
        self.head_tokens = head_tokens
        self.metadata = metadata or {}

    def encode(self, texts: Sequence[str]) -> list[list[int]]:
        return head_tail_ids(self.tokenizer, texts, self.max_length, self.head_tokens)

    @torch.no_grad()
    def score(self, texts: Sequence[str], batch_size: int = 64, max_batch_tokens: int = 8192) -> np.ndarray:
        """Injection probability for each text. Texts are sorted by length and batched under a
        token budget, so long texts go in smaller batches and memory use stays bounded."""
        self.model.eval()
        encoded = self.encode(texts)
        scores = np.empty(len(encoded), dtype=np.float32)
        order = np.argsort([len(e) for e in encoded])
        batches, current = [], []
        for i in order:
            # Sorted ascending, so the newest text is the longest in its batch.
            if current and (len(current) == batch_size or (len(current) + 1) * len(encoded[i]) > max_batch_tokens):
                batches.append(current)
                current = []
            current.append(i)
        batches.append(current)
        for idx in batches:
            input_ids, attention_mask = pad_batch([encoded[i] for i in idx], self.tokenizer.pad_token_id)
            with torch.autocast(self.device, dtype=torch.bfloat16, enabled=self.device == "cuda"):
                logits = self.model(input_ids=input_ids.to(self.device), attention_mask=attention_mask.to(self.device)).logits
            scores[idx] = torch.softmax(logits.float(), dim=-1)[:, 1].cpu().numpy()
        return scores

    def predict(self, texts: Sequence[str]) -> np.ndarray:
        return self.score(texts) >= self.threshold

    def save(self, directory: Path | None = None) -> Path:
        directory = directory or MODELS_DIR / self.name
        self.model.save_pretrained(directory)
        self.tokenizer.save_pretrained(directory)
        config = {"name": self.name, "threshold": self.threshold, "max_length": self.max_length,
                  "head_tokens": self.head_tokens, "metadata": self.metadata}
        (directory / "classifier.json").write_text(json.dumps(config, indent=2))
        return directory

    @classmethod
    def load(cls, name_or_dir: str | Path, device: str | None = None) -> "TransformerClassifier":
        directory = Path(name_or_dir)
        if not directory.is_dir():
            directory = MODELS_DIR / str(name_or_dir)
        config = json.loads((directory / "classifier.json").read_text())
        model = AutoModelForSequenceClassification.from_pretrained(directory, dtype=torch.float32)
        tokenizer = AutoTokenizer.from_pretrained(directory)
        return cls(model=model, tokenizer=tokenizer, device=device, **config)


def load_classifier(name: str, device: str | None = None) -> InjectionClassifier | TransformerClassifier:
    """Load any saved classifier by name."""
    if (MODELS_DIR / name).is_dir():
        return TransformerClassifier.load(name, device=device)
    return InjectionClassifier.load(name)
