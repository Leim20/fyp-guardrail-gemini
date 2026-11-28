"""Choose the strict-scan threshold and the cascade's confident band on val, and write both
into config/guard.yaml.

strict     the lowest classifier threshold whose false-positive rate on val is at most
           strict_max_fpr, i.e. the most sensitive setting the false-alarm budget allows.
confident  just above the highest val score of any benign text: flags at or above it are acted
           on directly, flags below it (with no rule) go to the judge.
Only val is used, so no test set influences the guard.

Usage:
    uv run python -m guardrail.calibrate
"""

import re

import numpy as np
import pandas as pd
import torch

from guardrail.guard import _classifier, load_settings
from guardrail.specs import CONFIG_DIR
from training.prepare import OUT_DIR


def strict_threshold(labels: np.ndarray, scores: np.ndarray, max_fpr: float) -> float:
    benign = np.sort(scores[labels == 0])[::-1]
    allowed = int(np.floor(max_fpr * len(benign)))  # benign texts that may score at or above it
    # Just above the (allowed+1)-th highest benign score, so at most `allowed` benign texts pass it.
    return float(np.nextafter(benign[allowed], 1.0)) if allowed < len(benign) else 0.0


def main() -> None:
    settings = load_settings()
    clf_settings = settings["classifier"]
    clf = _classifier(clf_settings["model"], "cuda" if torch.cuda.is_available() else "cpu")
    val = pd.read_parquet(OUT_DIR / "val.parquet")
    labels, scores = val["label"].to_numpy(), clf.score(val["text"].tolist())
    threshold = strict_threshold(labels, scores, clf_settings["strict_max_fpr"])
    fpr = float((scores[labels == 0] >= threshold).mean())
    recall = float((scores[labels == 1] >= threshold).mean())
    normal_recall = float((scores[labels == 1] >= clf.threshold).mean())
    normal_fpr = float((scores[labels == 0] >= clf.threshold).mean())
    print(f"{clf.name}: normal {clf.threshold:.4g} (val recall {normal_recall:.3f}, FPR {normal_fpr:.3f}); "
          f"strict {threshold:.4g} (val recall {recall:.3f}, FPR {fpr:.3f})")
    confident = float(np.nextafter(scores[labels == 0].max(), 1.0))
    above = scores >= confident
    band = (scores >= clf.threshold) & ~above
    print(f"confident band >= {confident:.6g}: {int(above.sum())} val texts, all injections; "
          f"uncertain band holds {band.mean():.1%} of val ({int(band.sum())} texts, {int((labels[band] == 0).sum())} benign)")
    path = CONFIG_DIR / "guard.yaml"
    text = path.read_text(encoding="utf-8")
    # Written in full (repr round-trips a float exactly): rounding could put either threshold
    # below the benign score it was chosen to clear.
    text = re.sub(r"(\n    strict: )[0-9.e+-]+", rf"\g<1>{threshold!r}", text)
    text = re.sub(r"(\n  confident: )[0-9.e+-]+", rf"\g<1>{confident!r}", text)
    path.write_text(text, encoding="utf-8")
    print(f"wrote strict: {threshold!r}, confident: {confident!r} to {path.name}")


if __name__ == "__main__":
    main()
