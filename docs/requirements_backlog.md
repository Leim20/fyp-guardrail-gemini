# Requirements Backlog

Iteration 1 deliverable (IR Table 3.2). Maps the requirements derived in the IR (Tables 3.5
and 3.6) to the modules that implement them, the iteration that builds them, and the test that
accepts them. Status is updated at the end of each iteration.

## Functional requirements

| ID | Priority | Requirement (short) | Module(s) | Iteration | Acceptance test | Status |
|---|---|---|---|---|---|---|
| FR01 | Must | Inspect user input and retrieved content before it enters the agent context | `guardrail/rules.py`, `guardrail/classifier.py`, input guard | 2–4 | Verdict and reason returned for benign and malicious inputs (all scenarios, `expected.user_message` / `expected.retrieved_doc`) | Rules, TF-IDF and fine-tuned DeBERTa-v3-small classifiers done (P2) |
| FR02 | Must | Preserve the source and trust class of every content item | `domains/novamart/trust.yaml`, `guardrail/trust.py` | 3 | Untrusted provenance reaches the action checkpoint; taint marks values copied from `external_untrusted` (IND-006) | Done: `guardrail/trust.py`; IND-006 blocked by taint only |
| FR03 | Must | Validate tool, argument schema, risk, permission and intent before execution | `domains/novamart/tools.yaml`, `guardrail/policy.py` | 3 | Negative tests block invalid or unauthorised calls (every `attack_tool_call`) | Done: `guardrail/policy.py`; 9/9 attack calls blocked under forced compromise |
| FR04 | Must | Return ALLOW / SANITIZE / ASK_CONFIRM / BLOCK with deterministic policy taking precedence | `guardrail/decision.py` | 3–4 | Tests cover all four verdicts and the precedence rule (a deny rule beats a low classifier score) | Done: `guardrail/decision.py`, confidence cascade and alignment judge (`guardrail/judge.py`) |
| FR05 | Must | Confirm a policy-permitted high-risk action; block a prohibited one | `guardrail/policy.py` | 3 | BEN-003 / BEN-004 → ASK_CONFIRM, DIR-001 / IND-003 → BLOCK | Done (tests/test_guard.py) |
| FR06 | Must | Audit record with verdict, reason, contributing layer and latency, without secrets | `guardrail/audit.py` | 4 | Log tests check required fields and that no API key or full PII is written | Done: `guardrail/audit.py` |
| FR07 | Should | Expose the guardrail through an API middleware interface | `api/main.py`, `api/client.py` | 4 | Integration test submits input and receives a structured decision | Done: five endpoints for the three checkpoints; 16 scenarios give identical verdicts over HTTP and in process (`tests/test_api.py`) |
| FR08 | Could | Monitoring dashboard for decisions and metrics | `console/` | spare | Demo only | Prototype of the main view on recorded data (`docs/console_design.md`); live data pending |
| FR09 | Could | Scan for synthetic personal or secret-like data before logging or tool execution | optional Presidio layer | spare | Synthetic tests detect configured patterns | — |

## Non-functional requirements

| ID | Priority | Requirement (short) | Where | Iteration | Acceptance test | Status |
|---|---|---|---|---|---|---|
| NFR01 | Should | Report median and p95 added latency; keep the core guardrail path within 1 s where feasible | `evaluation/` | 2, 5 | Timed runs report both measures | Reported: DeBERTa 46 ms median / 184 ms p95 on CPU |
| NFR02 | Must | Report precision, recall, F1 and false-positive rate in addition to accuracy | `evaluation/`, notebooks | 2, 5 | Held-out results include the full metric set and confusion matrix | `evaluation/metrics.py` done; baseline results reported |
| NFR03 | Must | Rules and classifier run reproducibly on the stated laptop environment | `uv.lock`, `scripts/check_env.py` | all | One recorded environment and one command reproduce the core results | `scripts/reproduce_p2.sh` reproduces all P2 results |

## Refinements since the IR

Each change keeps the IR objectives and is justified by evidence or a constraint the IR already
named. All of them are to be reported in the final report's implementation chapter.

| Change | IR position | Reason |
|---|---|---|
| Development environment is Ubuntu 26.04 with an RTX 4070 (8 GB) instead of Windows 11 | §2.4.5 | Machine actually used; the GPU allows local transformer fine-tuning while inference stays CPU-capable (NFR03) |
| LLM provider is DeepSeek (OpenAI-compatible API), plus local Ollama in Iteration 3 | §1.5.6 | Cost constraint already named in the IR; the agent design is provider-agnostic; only synthetic data is sent |
| Six mock tools instead of two or three | §1.5.1 | Each documented attack goal (refund fraud, exfiltration, account takeover, cross-customer access) needs a target tool |
| Fine-tuned compact transformer as the main classifier; TF-IDF + linear SVM kept as baseline | §1.5.4 allowed a transformer comparison | Current detectors are fine-tuned small encoders; the comparison stays in the evaluation |
| Sentence-level SANITIZE and taint tracking of tool arguments | §1.5.3 (SANITIZE verdict, source trust) | Makes SANITIZE concrete and lets policy catch injections the classifier misses (IND-004, IND-006) |
| Forced-compromise test in the evaluation | §3 Iteration 5 | Separates the guardrail's effect from the model's own resistance |
