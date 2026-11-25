# Hybrid ML Guardrail against Prompt Injection for LLM Agents

Final Year Project, BSc (Hons) Computer Science, Asia Pacific University.
Nye Wei Jun (TP066836). Supervisor: Ms. Mary Ting.

The guardrail protects a mock customer-support LLM agent at two checkpoints:

```
user msg ─► [INPUT GUARD] ─► agent context ─► LLM ─► proposed tool call
                 ▲                                        │
 retrieved docs / tool output ─► [INPUT GUARD]            ▼
                                                   [ACTION GUARD] ─► mock tool
                                                          │
                                                   audit log (JSONL)
```

Layers: rule scanner, ML classifier, source trust, tool-call policy.
Verdicts: `ALLOW`, `SANITIZE`, `ASK_CONFIRM`, `BLOCK`.
LLM backends: DeepSeek API (OpenAI-compatible) and a local Ollama model.

Design documents: [threat model](docs/threat_model.md), [requirements backlog](docs/requirements_backlog.md),
[data card](docs/data_card.md), [P2 baseline results](docs/results_p2_baseline.md),
[P2 transformer results](docs/results_p2_transformers.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.11 and every dependency:

```bash
uv sync
cp .env.example .env      # then fill in your own API keys
uv run python scripts/check_env.py
```

## Project layout

| Path | Contents |
|---|---|
| `docs/` | Threat model, requirements backlog, data card, results |
| `config/` | Engine settings (`guard.yaml`) and injection rule patterns (`rules.yaml`) |
| `domains/` | Domain packs: per application, its tool policy (`tools.yaml`), trust classes (`trust.yaml`), profile and record resolver |
| `data/` | Datasets (`raw/`, `processed/` are not committed), mock customer DB, agent scenarios, synthetic KB docs, recorded LLM responses |
| `notebooks/` | CRISP-DM data understanding and modelling notebooks |
| `guardrail/` | The engine: rule scanner, classifier, judge, trust and taint, generic policy checks, decision engine, output guard, audit log. Imports no application |
| `training/` | Dataset loading, cleaning, leakage removal, baseline training (CRISP-DM) |
| `agent/` | Mock agent loop, mock tools, synthetic NovaMart world, LLM backends (DeepSeek, Ollama) |
| `api/` | FastAPI middleware interface |
| `console/` | The guardrail console: static page, no build step, no network dependencies |
| `evaluation/` | Baselines, ablations, metrics, figures |
| `models/` | Trained model artefacts (not committed) |
| `results/` | Metric tables and figures used in the report |
| `tests/` | Unit, integration and regression tests |

## Reproducing the detection results

```bash
scripts/reproduce_p2.sh               # everything below in one go (~25 min on an RTX 4070)

uv run python -m training.prepare     # download, clean and split the datasets
uv run python -m training.baseline    # rule and TF-IDF baselines -> results/baseline_metrics.json
uv run python -m training.finetune --base-model microsoft/deberta-v3-small --name deberta_v3_small --lr 3e-5
uv run python -m training.reference   # off-the-shelf detectors as external references
```

Watch a run live with `scripts/watch_training.sh logs/<run>.log`, or open TensorBoard:
`uv run tensorboard --logdir logs/tensorboard` → http://localhost:6006.

## Running the agent

```bash
uv run python -m agent.cli                              # chat as customer C1001 and watch every checkpoint
uv run python -m agent.cli --doc forum_refund_tips.md   # search_kb returns this (poisoned) document
uv run python -m evaluation.agent_runs --guard none     # run all scenarios, judge attacks and utility
```

LLM replies are recorded in `data/recordings/` and replayed for identical requests, so reruns
are free and reproducible (`--mode replay` refuses to call the API at all).

## Running the guard as a service

```bash
uv run uvicorn api.main:app --port 8000     # interactive documentation at http://localhost:8000/docs
```

An application opens a session for an authenticated user and asks at three checkpoints
(`/input`, `/action`, `/output`), then reports what it did (`/outcome`). `api/client.py` wraps
those requests behind the same interface as the in-process guard, and is the reference for a
client in another language.

## The guardrail console (prototype)

A browser view of the guard at work: every check runs down a track past one signal per layer
and stops at the signal that blocks it. It plays back checks recorded from the real guard;
design notes are in [docs/console_design.md](docs/console_design.md).

```bash
uv run python scripts/export_console_demo.py   # record the runs (already committed; rerun after changing the guard)
uv run python scripts/serve_console.py         # then open http://localhost:8765
```

## Running tests

```bash
uv run pytest
uv run python -m evaluation.scenarios   # scenario coverage table
```
