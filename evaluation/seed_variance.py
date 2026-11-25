"""Seed-variance check (docs/distillation_plan.md §10): how much do single training runs vary?

Two arms, 0% and 100% of the synthetic train calls, three seeds each, everything else
identical. Reads results/transformer_metrics.json and reports every run, each arm's mean,
standard deviation and range, and the reading rule declared before the runs:

  every 100% run above every 0% run   -> synthetic data consistently helps on LLMail-dev
  every 100% run below every 0% run   -> it consistently hurts
  otherwise                           -> not distinguishable from run-to-run variance (n=3)

This is a secondary analysis. It does not revisit iteration 3's adoption decision.

Usage:
    uv run python -m evaluation.seed_variance
"""

import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = ROOT / "results" / "transformer_metrics.json"
OUT_PATH = ROOT / "results" / "seed_variance.json"
FIGURE_PATH = ROOT / "results" / "figures" / "seed_variance.png"
SEEDS = (42, 1, 2)
ARMS = {  # arm -> model name for each seed
    "0% synthetic": {42: "deberta_v3_small_it3_s0", 1: "deberta_v3_small_it3_s0_seed1", 2: "deberta_v3_small_it3_s0_seed2"},
    "100% synthetic": {42: "deberta_v3_small_it3", 1: "deberta_v3_small_it3_seed1", 2: "deberta_v3_small_it3_seed2"},
}
INCUMBENT = "deberta_v3_small"   # iteration 2, one run, for reference only
ADOPTION_PATH = ROOT / "results" / "adoption_iteration3.json"   # iteration 2 scored on iteration-3 data
MEASURES = [  # (set, metric, title)
    ("ood_llmail_dev", "roc_auc", "LLMail-dev ROC-AUC (primary)"),
    ("val/deepset", "f1", "deepset val F1"),
    ("test_synthetic", "f1", "Synthetic held-out F1"),
    ("val/bitext", "fpr", "Bitext val FPR"),
]
SERIES = {"0% synthetic": "#8a8984", "100% synthetic": "#2a78d6"}
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def value(results: dict, model: str, set_name: str, metric: str) -> float | None:
    if model not in results:
        return None
    return {r["set"]: r for r in results[model]["results"]}.get(set_name, {}).get(metric)


def incumbent_value(set_name: str, metric: str) -> float | None:
    """Iteration 2's value on the same data, from evaluation.adoption (its training-time
    results predate LLMail-dev and the iteration-3 val)."""
    if not ADOPTION_PATH.exists():
        return None
    scored = json.loads(ADOPTION_PATH.read_text()).get(ARMS["100% synthetic"][42], {}).get("models", {}).get(INCUMBENT, {})
    if set_name == "ood_llmail_dev":
        return scored.get("llmail_dev", {}).get(metric)
    if set_name.startswith("val/"):
        return scored.get("val_by_source", {}).get(set_name[4:], {}).get(metric)
    return None


def reading(low: list[float], high: list[float]) -> str:
    if min(high) > max(low):
        return "every 100% run is above every 0% run: synthetic data consistently helps"
    if max(high) < min(low):
        return "every 100% run is below every 0% run: synthetic data consistently hurts"
    return "the runs overlap: the effect is not distinguishable from run-to-run variance (n=3 per arm)"


def summary(values: list[float]) -> dict:
    return {"mean": float(np.mean(values)), "sd": float(np.std(values, ddof=1)) if len(values) > 1 else None,
            "min": min(values), "max": max(values)}


def main() -> None:
    results = json.loads(METRICS_PATH.read_text())
    missing = [m for arm in ARMS.values() for m in arm.values() if m not in results]
    if missing:
        print(f"not trained yet: {missing}")

    report = {"declared_in": "docs/distillation_plan.md §10", "seeds": list(SEEDS), "measures": {}}
    for set_name, metric, title in MEASURES:
        entry = {"runs": {}, "arms": {}}
        for arm, models in ARMS.items():
            runs = {seed: value(results, model, set_name, metric) for seed, model in models.items()}
            entry["runs"][arm] = runs
            known = [v for v in runs.values() if v is not None]
            entry["arms"][arm] = summary(known) if known else None
        incumbent = incumbent_value(set_name, metric)
        entry["iteration_2_single_run"] = incumbent
        low = [v for v in entry["runs"]["0% synthetic"].values() if v is not None]
        high = [v for v in entry["runs"]["100% synthetic"].values() if v is not None]
        if len(low) == len(high) == len(SEEDS) and set_name == "ood_llmail_dev":
            entry["reading"] = reading(low, high)
        report["measures"][f"{set_name}:{metric}"] = entry

        print(f"\n{title}")
        for arm, runs in entry["runs"].items():
            cells = "  ".join(f"seed {s:>2}: {'  n/a' if v is None else f'{v:.3f}'}" for s, v in runs.items())
            stats = entry["arms"][arm]
            tail = "" if not stats or stats["sd"] is None else \
                f"   mean {stats['mean']:.3f}  sd {stats['sd']:.3f}  range {stats['min']:.3f}-{stats['max']:.3f}"
            print(f"  {arm:<15} {cells}{tail}")
        if incumbent is not None:
            print(f"  {'iteration 2':<15} {incumbent:.3f} (one run, same data, for reference)")
        if "reading" in entry:
            print(f"  -> {entry['reading']}")

    OUT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\nsaved {OUT_PATH.relative_to(ROOT)}")
    draw(report)


def draw(report: dict) -> None:
    """One panel per measure: every run as a dot, the arm's mean as a short bar."""
    fig, axes = plt.subplots(1, len(MEASURES), figsize=(14, 3.4))
    for ax, (set_name, metric, title) in zip(axes, MEASURES):
        entry = report["measures"][f"{set_name}:{metric}"]
        for x, (arm, runs) in enumerate(entry["runs"].items()):
            ys = [v for v in runs.values() if v is not None]
            jitter = np.linspace(-0.08, 0.08, len(ys)) if len(ys) > 1 else [0.0]
            ax.scatter([x + j for j in jitter], ys, s=46, color=SERIES[arm], edgecolor="white", linewidth=1.5, zorder=3)
            if ys:
                mean = float(np.mean(ys))
                ax.plot([x - 0.22, x + 0.22], [mean, mean], color=SERIES[arm], linewidth=2, zorder=2)
                ax.annotate(f"{mean:.3f}", (x + 0.24, mean), va="center", fontsize=8, color=INK)
        if entry["iteration_2_single_run"] is not None:
            ref = entry["iteration_2_single_run"]
            ax.axhline(ref, color=MUTED, linewidth=1, linestyle="--", zorder=1)
            dots = [v for runs in entry["runs"].values() for v in runs.values() if v is not None]
            below = ref < float(np.mean(dots))   # write the label on the side away from the dots
            ax.annotate(f"iteration 2: {ref:.3f}", (-0.45, ref), textcoords="offset points",
                        xytext=(0, 4 if below else -12), ha="left", fontsize=8, color=MUTED)
        ax.set_xlim(-0.5, 1.6)
        ax.set_xticks([0, 1], list(ARMS))
        ax.set_title(title, fontsize=9.5, color=INK, loc="left")
        ax.tick_params(colors=MUTED, labelsize=8, length=0)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.set_facecolor(SURFACE)
    fig.supxlabel("each dot is one training run (seeds 42, 1, 2); the bar is the arm's mean", fontsize=9, color=MUTED)
    fig.tight_layout()
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_PATH, dpi=160, facecolor=SURFACE)
    print(f"saved {FIGURE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
