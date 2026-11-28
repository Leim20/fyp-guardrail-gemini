"""Learning curves of the guard's classifier: is it under-, over- or well fitted?

Left: training loss per step (rolling mean) and validation loss per epoch, from TensorBoard.
The saved checkpoint is the epoch with the best val ROC-AUC (training.finetune).
Right: ROC-AUC on val (the training distribution) against sets from other distributions.
A fitting problem shows on the left; a generalisation gap shows on the right.

Usage:
    uv run python -m evaluation.learning_curves
    uv run python -m evaluation.learning_curves --model deberta_v3_small_it3_seed1
"""

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = ROOT / "results" / "transformer_metrics.json"
TENSORBOARD_DIR = ROOT / "logs" / "tensorboard"
FIGURE_DIR = ROOT / "results" / "figures"
SETS = [  # (results set, label), training distribution first
    ("val", "val (same distribution as train)"),
    ("test_synthetic", "synthetic held-out"),
    ("test_deepset", "deepset test"),
    ("test_promptshield", "PromptShield test"),
    ("ood_llmail_dev", "LLMail-dev (external)"),
]
TRAIN, VAL = "#2a78d6", "#eb6834"   # categorical slots 1 and 2 of the reference palette
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SMOOTH = 400   # steps in the rolling mean of the training loss


def scalars(run: str, tag: str) -> tuple[np.ndarray, np.ndarray]:
    events = EventAccumulator(str(TENSORBOARD_DIR / run), size_guidance={"scalars": 0})
    events.Reload()
    points = events.Scalars(tag)
    return np.array([p.step for p in points]), np.array([p.value for p in points])


def style(ax) -> None:
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_facecolor(SURFACE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="deberta_v3_small_it3")
    args = parser.parse_args()
    entry = json.loads(METRICS_PATH.read_text())[args.model]
    info, rows = entry["info"], {r["set"]: r for r in entry["results"]}

    steps, train_loss = scalars(args.model, "train/loss")
    val_steps, val_loss = scalars(args.model, "val/loss")
    per_epoch = val_steps[0]                       # validation runs at the end of each epoch
    smooth = np.convolve(train_loss, np.ones(SMOOTH) / SMOOTH, mode="valid")
    smooth_x = (steps[SMOOTH - 1:]) / per_epoch

    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 3.8), gridspec_kw={"width_ratios": [1.15, 1]})

    left.plot(smooth_x, smooth, color=TRAIN, linewidth=2, zorder=3)
    left.plot(val_steps / per_epoch, val_loss, color=VAL, linewidth=2, marker="o", markersize=8,
              markeredgecolor=SURFACE, markeredgewidth=2, zorder=4)
    left.set_yscale("log")
    best = info["best_epoch"]
    left.axvline(best, color=MUTED, linewidth=1, linestyle=":", zorder=1)
    left.annotate(f"saved: epoch {best}\n(best val ROC-AUC)", (best, left.get_ylim()[1]), textcoords="offset points",
                  xytext=(4, -24), fontsize=8, color=MUTED)
    left.annotate("train loss", (smooth_x[len(smooth_x) // 6], smooth[len(smooth) // 6]), textcoords="offset points",
                  xytext=(0, -16), ha="center", fontsize=8.5, color=INK)
    left.annotate("val loss", (val_steps[0] / per_epoch, val_loss[0]), textcoords="offset points", xytext=(-8, 0),
                  ha="right", va="center", fontsize=8.5, color=INK)
    for x, y in zip(val_steps / per_epoch, val_loss):
        left.annotate(f"{y:.3f}", (x, y), textcoords="offset points", xytext=(0, 10), ha="center", fontsize=8, color=INK)
    left.set_xlim(0, len(val_steps) + 0.05)
    left.set_xticks(range(0, len(val_steps) + 1))
    left.set_xlabel("epoch", fontsize=8.5, color=MUTED)
    left.set_title("Loss (log scale): train per step, val per epoch", fontsize=9.5, color=INK, loc="left")
    style(left)

    labels = [label for name, label in SETS if rows.get(name, {}).get("roc_auc") is not None]
    values = [rows[name]["roc_auc"] for name, _ in SETS if rows.get(name, {}).get("roc_auc") is not None]
    y = np.arange(len(values))[::-1]
    right.hlines(y, 0.85, values, color=GRID, linewidth=2, zorder=1)
    right.scatter(values, y, s=70, color=TRAIN, edgecolor=SURFACE, linewidth=2, zorder=3)
    for value, row in zip(values, y):
        right.annotate(f"{value:.4f}", (value, row), textcoords="offset points", xytext=(8, -3), fontsize=8, color=INK)
    right.set_yticks(y, labels)
    right.set_xlim(0.85, 1.01)
    right.set_xlabel("ROC-AUC", fontsize=8.5, color=MUTED)
    right.set_title("ROC-AUC: training distribution vs other distributions", fontsize=9.5, color=INK, loc="left")
    style(right)
    right.grid(axis="y", visible=False)
    right.grid(axis="x", color=GRID, linewidth=0.8)

    fig.suptitle(f"{args.model}", fontsize=10, color=MUTED, x=0.01, ha="left")
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURE_DIR / f"learning_curves_{args.model}.png"
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    print(f"saved {path.relative_to(ROOT)}")
    for h in info["history"]:
        print(f"  epoch {h['epoch']}: train loss {h['train_loss']:.4f}, val loss {h.get('val_loss', float('nan')):.4f}, "
              f"val ROC-AUC {h['val_auc']:.4f}")


if __name__ == "__main__":
    main()
