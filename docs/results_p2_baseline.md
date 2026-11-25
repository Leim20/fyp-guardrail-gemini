# P2 Results — Rule and TF-IDF Baselines

CRISP-DM evaluation of the first detection models (Iteration 2, week 1). Reproduce with:

```bash
uv run python -m training.prepare
uv run python -m training.baseline
```

Full numbers: `results/iteration1/baseline_metrics.json`. Data and protocol: [data card](data_card.md).

> The tables below are **CRISP-DM iteration 1** (git tag `p2-iteration1`). Iteration 2 retrained
> the TF-IDF models with in-domain benign data and a revised threshold rule; their numbers moved
> only slightly (current values in `results/baseline_metrics.json`, compared in the
> [transformer results](results_p2_transformers.md)). The rule scanner is unchanged.

## Models

| Model | Description | Selected settings | CV F1 (train) |
|---|---|---|---|
| `rules` | Rule scanner, `config/rules.yaml`, noisy-OR of rule weights | threshold 0.5 (fixed by design) | — |
| `tfidf_lr` | TF-IDF word 1–2-grams + char 3–5-grams, logistic regression | C = 30, threshold 0.62 (val) | 0.924 |
| `tfidf_svm` | Same features, linear SVM with Platt scaling | C = 3, threshold 0.51 (val) | 0.925 |

## Results

F1 / false-positive rate at the chosen threshold, plus two threshold-free measures: ROC-AUC
and TPR at 1% FPR (how many attacks are caught while keeping false alarms at 1%).

| Set | Model | Precision | Recall | F1 | FPR | ROC-AUC | TPR@1%FPR |
|---|---|---|---|---|---|---|---|
| val (in-distribution) | rules | 1.000 | 0.279 | 0.436 | 0.000 | 0.637 | 0.279 |
| | tfidf_lr | 0.972 | 0.915 | 0.943 | 0.026 | 0.983 | 0.791 |
| | tfidf_svm | 0.948 | 0.934 | 0.941 | 0.049 | 0.981 | 0.810 |
| test_deepset | rules | 1.000 | 0.233 | 0.378 | 0.000 | 0.617 | 0.233 |
| | tfidf_lr | 1.000 | 0.350 | 0.519 | 0.000 | 0.912 | 0.550 |
| | tfidf_svm | 1.000 | 0.317 | 0.481 | 0.000 | 0.917 | 0.550 |
| test_promptshield | rules | 0.866 | 0.278 | 0.421 | 0.016 | 0.635 | 0.278 |
| | tfidf_lr | 0.497 | 0.386 | 0.434 | 0.149 | 0.779 | 0.023 |
| | tfidf_svm | 0.495 | 0.509 | 0.502 | 0.198 | 0.789 | 0.018 |
| ood_spml | rules | 1.000 | 0.234 | 0.380 | 0.000 | 0.634 | 0.272 |
| | tfidf_lr | 1.000 | 0.110 | 0.198 | 0.000 | 0.947 | 0.726 |
| | tfidf_svm | 1.000 | 0.181 | 0.306 | 0.000 | 0.952 | 0.722 |
| ood_lakera (recall only) | rules | — | 0.596 | — | — | — | — |
| | tfidf_lr | — | 0.422 | — | — | — | — |
| | tfidf_svm | — | 0.368 | — | — | — | — |
| ood_bitext (FPR only) | all three | — | — | — | 0.000 | — | — |
| scenarios (26 texts) | rules | 1.000 | 0.800 | 0.889 | 0.000 | — | — |
| | tfidf_lr / svm | 1.000 | 0.200 | 0.333 | 0.000 | — | — |

**Latency** (one text per call, CPU, median / p95): rules 0.11 / 0.62 ms, tfidf_lr 1.2 / 2.5 ms,
tfidf_svm 2.1 / 3.7 ms. All are far inside the NFR01 one-second budget.

## Findings

1. **In-distribution validation badly overstates TF-IDF quality.** F1 falls from 0.94 on val
   to 0.43–0.50 on the PromptShield test split, and the false-positive rate rises to 15–20%.
   Error analysis: most false positives are long but benign task prompts ("In this task you are
   given a story… you must judge whether…"), and many false negatives are injections appended
   after code or long passages. A bag-of-words model learns "instruction-like wording = attack"
   and cannot tell a legitimate task instruction from an instruction injected into data, which
   is exactly the indirect-injection problem.
2. **Ranking transfers better than the threshold.** On deepset test and SPML, ROC-AUC stays at
   0.91–0.95 while recall at the val-chosen threshold is only 0.11–0.35: the models put attacks
   above benign text but on a different score scale. Threshold-free measures are therefore
   reported alongside F1.
3. **Rules are precise but narrow.** The rule scanner almost never raises a false alarm
   (FPR ≤ 1.6%, 0% on in-domain Bitext) but catches only 23–28% of attacks on the large
   datasets. On the NovaMart scenarios it is the strongest model (8/10 attacks), because the
   scenarios use the explicit phrasings it was written for. It misses only the two *subtle*
   documents, as designed (IND-004, IND-006).
4. **The two methods are complementary (IR sub-problem 1).** Flagging a text when either the
   rules or the SVM flag it, with no re-tuning, raises recall on every set without adding false
   positives: test_deepset 0.317 → 0.367, SPML 0.181 → 0.320, Lakera 0.368 → 0.655,
   PromptShield 0.509 → 0.588 (FPR unchanged). This is early evidence for the hybrid design;
   the real fusion is the P4 decision engine.
5. **No in-domain false-positive problem.** All models flag none of the 1,972 genuine
   customer-support requests, so adding the held-back Bitext pool to training is not needed
   at this stage.

## Implications for the next step

- A contextual model is needed to separate task instructions from injected instructions:
  fine-tune a compact transformer (refinement U1) and compare it on the same sets.
- Sentence-level scanning of retrieved documents (U4) should help with injections hidden at
  the end of long, otherwise benign content.
- The subtle scenarios confirm that text detection alone is not enough, which is the case for
  the action guard's deterministic policy and taint checks (P4).
