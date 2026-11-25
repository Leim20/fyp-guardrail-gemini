"""Dose-response curve for the synthetic data: does more of it keep helping?

Reads results/transformer_metrics.json for the four iteration-3 runs (0, 25, 50 and 100% of
the synthetic train calls; everything else identical) and draws one small panel per measure.
The measures have different scales, so they get separate panels rather than a shared axis.

  still rising at 100%  -> generating more data would probably pay off
  flat after 25-50%     -> the amount is not the bottleneck

One seed per point, so differences of about 0.01 are within run-to-run noise.

Usage:
    uv run python -m evaluation.dose_response
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = ROOT / "results" / "transformer_metrics.json"
FIGURE_PATH = ROOT / "results" / "figures" / "dose_response.png"
RUNS = {0: "deberta_v3_small_it3_s0", 25: "deberta_v3_small_it3_s25", 50: "deberta_v3_small_it3_s50",
        100: "deberta_v3_small_it3"}
INCUMBENT = "deberta_v3_small"  # iteration 2, scored on iteration-3 data by evaluation.adoption
PANELS = [  # (set, metric, title, higher is better)
    ("ood_llmail_dev", "roc_auc", "LLMail-dev ROC-AUC (adoption measure)", True),
    ("test_synthetic", "f1", "Synthetic held-out F1", True),
    ("val/promptshield", "f1", "PromptShield val F1 (regression check)", True),
    ("val/bitext", "fpr", "Bitext val FPR (must stay <= 1%)", False),
]
SERIES, INK, MUTED, GRID, SURFACE = "#2a78d6", "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
MIN_SPAN = 0.06  # smallest y-range shown, so a 0.005 wobble does not fill the panel like a collapse


def value(results: dict, model: str, set_name: str, metric: str) -> float | None:
    rows = {r["set"]: r for r in results[model]["results"]}
    return rows.get(set_name, {}).get(metric)


def main() -> None:
    results = json.loads(METRICS_PATH.read_text())
    missing = [name for name in RUNS.values() if name not in results]
    if missing:
        raise SystemExit(f"train these first: {missing}")
    adoption_path = ROOT / "results" / "adoption_iteration3.json"
    incumbent = json.loads(adoption_path.read_text()).get(RUNS[100], {}).get("models", {}).get(INCUMBENT) \
        if adoption_path.exists() else None

    doses = list(RUNS)
    fig, axes = plt.subplots(1, len(PANELS), figsize=(14, 3.4))
    for ax, (set_name, metric, title, _) in zip(axes, PANELS):
        ys = [value(results, RUNS[d], set_name, metric) for d in doses]
        ax.plot(doses, ys, color=SERIES, linewidth=2, marker="o", markersize=7, markeredgecolor="white",
                markeredgewidth=2, zorder=3)
        for d, y in zip(doses, ys):
            if y is not None:
                ax.annotate(f"{y:.3f}", (d, y), textcoords="offset points", xytext=(0, 9), ha="center",
                            fontsize=8, color=INK)
        if incumbent and set_name == "ood_llmail_dev":
            ref = incumbent["llmail_dev"]["roc_auc"]
            ax.axhline(ref, color=MUTED, linewidth=1, linestyle="--", zorder=2)
            ax.annotate(f"iteration 2: {ref:.3f}", (100, ref), textcoords="offset points", xytext=(0, -12),
                        ha="right", fontsize=8, color=MUTED)
        if metric == "fpr":
            ax.axhline(0.01, color=MUTED, linewidth=1, linestyle=":", zorder=2)
            ax.annotate("limit 1%", (0, 0.01), textcoords="offset points", xytext=(0, 4), fontsize=8, color=MUTED)
        known = [y for y in ys if y is not None]
        centre = (max(known) + min(known)) / 2
        half = max(MIN_SPAN, (max(known) - min(known)) * 1.5) / 2
        ax.set_ylim(max(0.0, centre - half) if metric == "fpr" else centre - half, centre + half)
        ax.set_title(title, fontsize=9.5, color=INK, loc="left")
        ax.set_xticks(doses, [f"{d}%" for d in doses])
        ax.tick_params(colors=MUTED, labelsize=8, length=0)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.set_facecolor(SURFACE)
    fig.supxlabel("share of the synthetic train calls added (same base data, same val, one seed)", fontsize=9,
                  color=MUTED)
    fig.tight_layout()
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_PATH, dpi=160, facecolor=SURFACE)
    print(f"saved {FIGURE_PATH.relative_to(ROOT)}")
    for set_name, metric, title, _ in PANELS:
        cells = (value(results, RUNS[d], set_name, metric) for d in doses)
        print(f"  {title:<42}" + "  ".join(f"{d:>3}%: {'n/a' if v is None else f'{v:.3f}'}" for d, v in zip(doses, cells)))


if __name__ == "__main__":
    main()
