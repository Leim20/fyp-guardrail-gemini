"""Unit tests for the transformer classifier's input handling, using a stub tokenizer and model
so no download or GPU is needed."""

from types import SimpleNamespace

import numpy as np
import torch

from guardrail.classifier import TransformerClassifier, head_tail_ids, pad_batch

CLS, SEP, PAD = 101, 102, 0


class StubTokenizer:
    """Whitespace tokenizer whose tokens are the integers written in the text."""
    cls_token_id, sep_token_id, pad_token_id = CLS, SEP, PAD

    def __call__(self, texts, add_special_tokens=False):
        return {"input_ids": [[int(w) for w in t.split()] for t in texts]}


class LengthModel(torch.nn.Module):
    """Logit for "injection" grows with sequence length; records every batch it sees."""

    def __init__(self):
        super().__init__()
        self.batches = []

    def forward(self, input_ids, attention_mask):
        self.batches.append(tuple(input_ids.shape))
        lengths = attention_mask.sum(dim=1).float()
        return SimpleNamespace(logits=torch.stack([torch.zeros_like(lengths), lengths / 10], dim=1))


def test_short_text_gets_only_special_tokens():
    assert head_tail_ids(StubTokenizer(), ["5 6 7"], max_length=10, head_tokens=3) == [[CLS, 5, 6, 7, SEP]]


def test_long_text_keeps_head_and_tail():
    text = " ".join(str(i) for i in range(1, 21))  # tokens 1..20
    ids = head_tail_ids(StubTokenizer(), [text], max_length=10, head_tokens=3)[0]
    # budget = 10 - 2 = 8 -> first 3 tokens + last 5 tokens
    assert ids == [CLS, 1, 2, 3, 16, 17, 18, 19, 20, SEP]


def test_pad_batch_pads_and_masks():
    input_ids, mask = pad_batch([[1, 2, 3], [4]], PAD)
    assert input_ids.tolist() == [[1, 2, 3], [4, PAD, PAD]]
    assert mask.tolist() == [[1, 1, 1], [1, 0, 0]]


def test_scores_come_back_in_input_order_and_batches_respect_token_budget():
    model = LengthModel()
    clf = TransformerClassifier("stub", model, StubTokenizer(), threshold=0.5, max_length=64, device="cpu")
    # Deliberately unsorted lengths, so a wrong index mapping would scramble the scores.
    texts = [" ".join(["7"] * n) for n in (30, 2, 15, 50, 5)]
    scores = clf.score(texts, batch_size=4, max_batch_tokens=64)
    expected = 1 / (1 + np.exp(-np.array([32, 4, 17, 52, 7]) / 10))  # softmax over [0, len/10]
    assert np.allclose(scores, expected, atol=1e-5)
    assert all(rows * width <= 64 or rows == 1 for rows, width in model.batches)
