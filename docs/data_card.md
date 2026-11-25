# Data Card — Injection Classifier (P2)

CRISP-DM data understanding and data preparation for the ML layer of the input guard.
Produced by `uv run python -m training.prepare`; exact counts are in
`results/data_profile.json`.

## 1. Sources

| Source | Hugging Face id | Licence | Role | Why |
|---|---|---|---|---|
| deepset | `deepset/prompt-injections` | Apache-2.0 | train + val (85/15 of its train split), **test** (its published test split) | Named in the IR as the primary dataset; makes results comparable with other work |
| PromptShield | `hendzh/PromptShield` | Apache-2.0 | train, val, **test** (published splits) | Large (18.9k train, balanced); many positives are instructions **embedded inside task data**, which is the indirect-injection form this project focuses on |
| SPML | `reshabhs/SPML_Chatbot_Prompt_Injection` | MIT | **OOD test** (stratified sample of 2,000, user prompt only) | Different style (chatbot persona override), so it measures generalisation beyond the training distribution |
| Lakera Gandalf | `Lakera/gandalf_ignore_instructions` | MIT | **OOD test** (validation + test, 223 rows) | Real attacks typed by people in the Gandalf game; positives only, so it measures recall |
| Bitext | `bitext/Bitext-customer-support-llm-chatbot-training-dataset` | CDLA-Sharing-1.0 | **OOD test** (2,000 rows) + held-back pool | Genuine customer-support requests (refunds, orders, accounts) in the same domain as the NovaMart agent; benign only, so it measures the false-positive rate where the guardrail will actually run |
| NovaMart scenarios | `data/scenarios/`, `data/kb_docs/` | own | **domain test** (16 messages + 10 documents) | The agent's own traffic; never used for training or tuning |

### Considered and rejected

| Dataset | Reason |
|---|---|
| `jackhhao/jailbreak-classification` | Jailbreaks (role-play to bypass safety) are outside the IR scope unless they redirect the agent (IR §1.5.2) |
| `xTRam1/safe-guard-prompt-injection` | No licence stated on the dataset card |
| `allenai/wildjailbreak`, `qualifire/prompt-injections-benchmark` | Gated; the latter is non-commercial (CC-BY-NC) and mainly jailbreaks |
| Lakera Gandalf as **training** data | Positive-only and almost every row starts with "Ignore…"; training on it would teach the model that the word *ignore* means attack, causing false positives on requests like scenario BEN-005 |

## 2. Data understanding findings

- **deepset is small and noisy.** About a third of its train rows are German, and some are
  Croatian or romanised Hindi. Several labels are questionable; for example, a plain German
  request for book recommendations concatenated with an English question is labelled as an
  injection (its "context switch" construction). This supports the IR's own limitation note
  (§4.3) and is the reason a larger second source was added.
- **PromptShield is template-based.** Its validation split shares templates with its train
  split, so 730 train rows were near-duplicates of validation rows (see §3).
- **Bitext is template-generated** with placeholders such as `{{Order Number}}`, so many rows
  are near-copies of each other.

## 3. Preparation

1. **Set assignment** as in §1 (seed 42 for every sample).
2. **Cleaning** (train, val, OOD samples): drop empty rows, texts that appear with both labels,
   and exact duplicates after normalisation (Unicode NFKC, lower-case, collapsed whitespace).
   The published test splits are **left untouched** so results remain comparable.
3. **Leakage removal**: any train or val row whose normalised text exactly matches, or nearly
   matches (word TF-IDF cosine ≥ 0.95), a row of any evaluation set is dropped; train rows that
   match val are dropped too. The 0.95 threshold was checked by reading pairs on both sides of
   it: pairs above it differ only in names or placeholders, pairs at 0.90–0.95 are genuinely
   different prompts that share a common jailbreak template.

| Set | Rows removed | Main cause |
|---|---|---|
| train | 123 duplicates, 15 exact leaks, 761 near leaks | 730 near-copies of PromptShield validation rows; 6 of PromptShield test |
| val | 2 duplicates, 1 near leak | — |
| pool_bitext | 35 duplicates, 41 exact + 61 near leaks | template copies of ood_bitext rows |

## 4. Final sets (iteration 1)

| Set | Rows | Injection | Benign | Injection rate |
|---|---|---|---|---|
| train | 18,474 | 8,730 | 9,744 | 47% |
| val | 1,079 | 531 | 548 | 49% |
| test_deepset | 116 | 60 | 56 | 52% |
| test_promptshield | 23,516 | 6,486 | 17,030 | 28% |
| ood_spml | 1,997 | 1,567 | 430 | 78% |
| ood_lakera | 223 | 223 | 0 | 100% |
| ood_bitext | 1,972 | 0 | 1,972 | 0% |
| pool_bitext | 1,863 | 0 | 1,863 | 0% (held back, unused) |

`pool_bitext` is reserved for a possible later CRISP-DM iteration that adds in-domain benign
text to training. It is disjoint from `ood_bitext`, with near-copies removed, so using it later
cannot leak into the test.

## 4b. Iteration 2 sets (current)

Iteration 1 showed the fine-tuned models flagging genuine customer-support requests (see
[transformer results](results_p2_transformers.md)), so the reserved pool was used:
`uv run python -m training.prepare` now splits `pool_bitext` 80/20 into train and val
(`--iteration 1` rebuilds the sets above). Leakage removal runs after the split, so templated
Bitext rows that nearly copy `ood_bitext` or val are dropped from train.

| Set | Rows | Injection | Benign | Change from iteration 1 |
|---|---|---|---|---|
| train | 19,949 | 8,730 | 11,219 | +1,475 in-domain benign |
| val | 1,454 | 531 | 923 | +375 in-domain benign |
| evaluation sets | unchanged | | | |

Rows removed in iteration 2: train 123 duplicates, 52 exact and 821 near leaks; val 2 duplicates,
4 exact and 15 near leaks. Because Bitext text is now in training, `ood_bitext` is reported as an
**in-domain held-out** set from iteration 2 on; the NovaMart scenarios remain the only fully
unseen domain test.

## 5. Evaluation rules

- Hyper-parameters are chosen by stratified 5-fold cross-validation on train; decision
  thresholds on val. Every test and OOD set is scored once, after all choices are fixed.
- The rule scanner (`config/rules.yaml`) was written from the IR's in-scope attack patterns
  and training data, not from any test set.
- Single-class OOD sets report only the metric they can support: recall on `ood_lakera`,
  false-positive rate on `ood_bitext`.

## 6. Known limitations

- deepset label noise limits how much its test score can be trusted as a measure of quality.
- PromptShield's templates mean in-distribution scores may overstate real-world performance;
  the OOD sets and the NovaMart scenarios are there to expose this.
- The domain test (26 texts) is too small for statistical claims; it is used for per-case
  error analysis, not headline numbers.
- Most data is English; German is present only through deepset.
