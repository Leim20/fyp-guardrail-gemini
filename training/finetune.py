"""Fine-tune a pretrained transformer encoder as the injection classifier (P2, refinement U1).

The encoder already models language from large-scale pretraining; fine-tuning trains it,
together with a new two-class head, on our labelled train set. The protocol matches the
baselines: the best epoch is chosen on val (ROC-AUC), the decision threshold on val (max F1),
and every evaluation set is scored once at the end.

Training loop, written out explicitly rather than with the Hugging Face Trainer:
  AdamW with weight decay, linear warm-up then linear decay of the learning rate,
  bf16 autocast on the GPU, gradient clipping at 1.0, and length-bucketed batches with
  dynamic padding (most texts are short, so padding every batch to 512 would waste time).

Training curves, val score histograms and PR curves are written to logs/tensorboard/<name>/.

Usage:
    uv run python -m training.finetune --base-model microsoft/deberta-v3-small --name deberta_v3_small --lr 3e-5
    uv run python -m training.finetune --base-model answerdotai/ModernBERT-base --name modernbert_base --lr 5e-5
    add --smoke for a two-minute end-to-end check on a small subset (nothing is written to results/)

Iteration 3 (training.prepare --iteration 3) also scores test_synthetic and LLMail-dev, and
--train-set picks a dose-response train set:
    uv run python -m training.finetune --base-model microsoft/deberta-v3-small --name deberta_v3_small_it3_s25 \
        --lr 3e-5 --train-set train_synth25
"""

import argparse
import json
import math
import os
import shutil
import time
from pathlib import Path

# Reduce CUDA memory fragmentation on the 8 GB laptop GPU; must be set before CUDA starts.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import log_loss, roc_auc_score
from torch.utils.tensorboard import SummaryWriter
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from evaluation.metrics import best_f1_threshold
from guardrail.classifier import MODELS_DIR, TransformerClassifier, pad_batch
from training.baseline import data_iteration, evaluate, latency, latency_texts, load_sets
from training.prepare import OUT_DIR, SEED

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "results" / "transformer_metrics.json"
TENSORBOARD_DIR = ROOT / "logs" / "tensorboard"  # view with: uv run tensorboard --logdir logs/tensorboard
LOG_EVERY = 100  # steps between progress lines
CPU_LATENCY_SAMPLE = 100  # CPU timing is slower, so a smaller slice of the same fixed sample


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-model", required=True, help="Hugging Face id of the pretrained encoder")
    p.add_argument("--name", required=True, help="saved as models/<name>/")
    p.add_argument("--lr", type=float, required=True)
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument("--head-tokens", type=int, default=128)
    p.add_argument("--warmup", type=float, default=0.1, help="fraction of steps with rising learning rate")
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--smoke", action="store_true", help="tiny run to check the pipeline end to end")
    p.add_argument("--train-set", default="train", help="data/processed/<name>.parquet to train on")
    return p.parse_args()


def set_seed(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def bucketed_batches(lengths: list[int], batch_size: int, rng: np.random.Generator) -> list[np.ndarray]:
    """Shuffle, sort by length inside chunks of 50 batches, then shuffle the batches.
    Similar-length texts share a batch (little padding) while the order stays random."""
    order = rng.permutation(len(lengths))
    chunk = batch_size * 50
    batches = []
    for start in range(0, len(order), chunk):
        part = order[start : start + chunk]
        part = part[np.argsort([lengths[i] for i in part], kind="stable")]
        batches += [part[i : i + batch_size] for i in range(0, len(part), batch_size)]
    rng.shuffle(batches)
    return batches


def linear_warmup_decay(optimizer: torch.optim.Optimizer, warmup_steps: int, total_steps: int):
    def factor(step: int) -> float:
        if step < warmup_steps:
            return step / max(1, warmup_steps)
        return max(0.0, (total_steps - step) / max(1, total_steps - warmup_steps))
    return torch.optim.lr_scheduler.LambdaLR(optimizer, factor)


def evaluate_and_time(name: str, gpu_clf: TransformerClassifier, cpu_clf: TransformerClassifier,
                      sets: dict[str, pd.DataFrame], timing: list[str]) -> tuple[list[dict], dict, dict]:
    """Score every evaluation set once and time single-text requests on GPU and CPU."""
    scores = {s: gpu_clf.score(sets[s]["text"].tolist()) for s in sets if s != "train"}
    rows = evaluate(name, scores, gpu_clf.threshold, sets)
    gpu = latency(lambda t: gpu_clf.score([t])[0], timing)
    cpu = latency(lambda t: cpu_clf.score([t])[0], timing[:CPU_LATENCY_SAMPLE])
    timing_info = {"gpu_latency_median_ms": gpu["latency_median_ms"], "gpu_latency_p95_ms": gpu["latency_p95_ms"],
                   "cpu_latency_median_ms": cpu["latency_median_ms"], "cpu_latency_p95_ms": cpu["latency_p95_ms"]}
    table = pd.DataFrame(rows).set_index("set")
    print(table[["n", "precision", "recall", "f1", "fpr", "roc_auc", "tpr_at_1pct_fpr"]].astype(float).round(3).to_string())
    print({k: round(v, 2) for k, v in timing_info.items()})
    return rows, timing_info, scores


def save_results(path: Path, name: str, info: dict, rows: list[dict], sets: dict[str, pd.DataFrame],
                 scores: dict[str, np.ndarray]) -> None:
    """Merge this model's entry into the results JSON and save per-text predictions."""
    results = json.loads(path.read_text()) if path.exists() else {}
    results[name] = {"info": info, "results": rows}
    path.write_text(json.dumps(results, indent=2))
    predictions = pd.concat([sets[s].assign(set=s, score=scores[s]) for s in scores])
    predictions.to_parquet(OUT_DIR / f"predictions_{name}.parquet")


def minutes(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def log_validation(writer: SummaryWriter, labels: np.ndarray, scores: np.ndarray, step: int) -> tuple[float, float]:
    """Val ROC-AUC and loss, plus how the two classes' scores separate as training goes on.
    A val loss that rises while train loss falls is the usual sign of overfitting."""
    auc = float(roc_auc_score(labels, scores))
    loss = float(log_loss(labels, np.clip(scores, 1e-7, 1 - 1e-7), labels=[0, 1]))
    writer.add_scalar("val/roc_auc", auc, step)
    writer.add_scalar("val/loss", loss, step)
    writer.add_histogram("val/scores_injection", scores[labels == 1], step)
    writer.add_histogram("val/scores_benign", scores[labels == 0], step)
    writer.add_pr_curve("val/precision_recall", labels, scores, step)
    return auc, loss


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    name = args.name + ("_smoke" if args.smoke else "")
    out_dir = MODELS_DIR / name
    sets = load_sets(args.train_set)
    if args.smoke:
        args.epochs = 1
        # A smoke run checks the code path only; LLMail-dev stays unscored until it is needed.
        sets.pop("ood_llmail_dev", None)
        sets = {k: v.sample(min(len(v), 512 if k == "train" else 128), random_state=args.seed) for k, v in sets.items()}
    train, val = sets["train"], sets["val"]

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    # Keep master weights in fp32 (bf16 is used only inside autocast). Some checkpoints, such as
    # deberta-v3-small, are stored in fp16; training them as-is makes AdamW's epsilon underflow
    # to zero and the weights turn into NaN after the first step.
    model = AutoModelForSequenceClassification.from_pretrained(args.base_model, num_labels=2, dtype=torch.float32)
    clf = TransformerClassifier(name, model, tokenizer, threshold=0.5, max_length=args.max_length,
                                head_tokens=args.head_tokens, device="cuda")
    train_ids = clf.encode(train["text"].tolist())
    train_labels = train["label"].to_numpy()

    steps_per_epoch = math.ceil(len(train_ids) / args.batch_size)
    total_steps = steps_per_epoch * args.epochs
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = linear_warmup_decay(optimizer, int(args.warmup * total_steps), total_steps)
    rng = np.random.default_rng(args.seed)
    writer = SummaryWriter(str(TENSORBOARD_DIR / name))
    writer.add_text("config", f"```\n{json.dumps(vars(args), indent=2)}\n```")
    val_labels = val["label"].to_numpy()

    history, best_auc, start, global_step = [], -1.0, time.perf_counter(), 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        losses = []
        for step, idx in enumerate(bucketed_batches([len(x) for x in train_ids], args.batch_size, rng), start=1):
            input_ids, attention_mask = pad_batch([train_ids[i] for i in idx], tokenizer.pad_token_id)
            labels = torch.tensor(train_labels[idx], device="cuda")
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = model(input_ids=input_ids.cuda(), attention_mask=attention_mask.cuda(), labels=labels).loss
            loss.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            global_step += 1
            writer.add_scalar("train/loss", loss.item(), global_step)
            writer.add_scalar("train/learning_rate", optimizer.param_groups[0]["lr"], global_step)
            writer.add_scalar("train/grad_norm", grad_norm.item(), global_step)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            losses.append(loss.item())
            if step % LOG_EVERY == 0:
                elapsed = time.perf_counter() - start
                eta = elapsed / global_step * (total_steps - global_step)
                print(f"epoch {epoch} step {step}/{steps_per_epoch} loss {np.mean(losses[-LOG_EVERY:]):.4f} "
                      f"| elapsed {minutes(elapsed)} | training ETA {minutes(eta)}", flush=True)

        val_auc, val_loss = log_validation(writer, val_labels, clf.score(val["text"].tolist()), global_step)
        history.append({"epoch": epoch, "train_loss": float(np.mean(losses)), "val_loss": val_loss, "val_auc": val_auc})
        print(f"epoch {epoch} done: train loss {np.mean(losses):.4f}, val loss {val_loss:.4f}, "
              f"val ROC-AUC {val_auc:.4f}", flush=True)
        if val_auc > best_auc:
            best_auc = val_auc
            clf.save(out_dir)
    fit_seconds = time.perf_counter() - start

    # Reload the best epoch, fix its threshold on val, then score every evaluation set once.
    best = TransformerClassifier.load(out_dir, device="cuda")
    best.threshold = best_f1_threshold(val["label"], best.score(val["text"].tolist()))
    best_epoch = max(history, key=lambda h: h["val_auc"])["epoch"]
    best.metadata = {
        "base_model": args.base_model, "lr": args.lr, "epochs": args.epochs, "batch_size": args.batch_size,
        "warmup": args.warmup, "weight_decay": args.weight_decay, "seed": args.seed,
        "best_epoch": best_epoch, "history": history, "train_rows": len(train),
        "data_iteration": data_iteration(), "train_set": args.train_set,
        "fit_seconds": round(fit_seconds, 1), "threshold_chosen_on": "val (max F1)",
    }
    best.save(out_dir)

    timing = sets["val"]["text"].tolist()[:20] if args.smoke else latency_texts(sets)
    cpu_model = TransformerClassifier.load(out_dir, device="cpu")
    rows, timing_info, scores = evaluate_and_time(name, best, cpu_model, sets, timing)
    info = best.metadata | {"threshold": best.threshold} | timing_info
    # Final results in TensorBoard's HParams tab, so runs can be compared side by side.
    by_set = {r["set"]: r for r in rows}
    writer.add_hparams(
        {"base_model": args.base_model, "lr": args.lr, "epochs": args.epochs, "batch_size": args.batch_size},
        {f"final/{s}_{m}": by_set[s][m] for s, m in [("test_deepset", "f1"), ("test_promptshield", "f1"),
         ("test_promptshield", "fpr"), ("ood_lakera", "recall"), ("ood_bitext", "fpr")] if by_set[s][m] is not None},
        run_name="final",
    )
    writer.close()
    if args.smoke:
        shutil.rmtree(TENSORBOARD_DIR / name, ignore_errors=True)
        shutil.rmtree(out_dir)
        print("smoke run OK (model and results discarded)")
        return
    save_results(RESULTS_PATH, name, info, rows, sets, scores)


if __name__ == "__main__":
    main()
