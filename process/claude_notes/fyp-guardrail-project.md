---
name: fyp-guardrail-project
description: "User's final-year project (APU BSc CS): hybrid ML guardrail against prompt injection for LLM agents; repo ~/fyp-guardrail, final deadline 2026-12-16"
metadata:
  node_type: memory
  type: project
  originSessionId: a10e5f01-3876-4764-aad6-92018a752d5e
  modified: 2026-11-02T09:06:19.266Z
---

FYP title: "Improving Large Language Model Agent Security Using a Hybrid Machine Learning Guardrail Mechanism against Prompt Injection". Student Nye Wei Jun (TP066836), APU / De Montfort. Supervisor Ms. Mary Ting, 2nd marker Ms. Maznah Mohd Zin. The Investigation Report (IR) was submitted 2026-08-05; its PDF is in ~/Downloads (filename starts `AI-Nye_Wei_Jun-TP066836`).

Design locked by the IR: mock customer-support agent (own loop + OpenAI API, mock tools), two checkpoints (input guard + action guard), layers = rules + ML classifier + source trust + tool policy, decision engine → ALLOW/SANITIZE/ASK_CONFIRM/BLOCK, JSONL audit log, FastAPI. Evaluation: P/R/F1/FPR, latency (median/p95), attack-mitigation rate vs system-prompt-only / rule-only / classifier-only baselines + ablation.

LLM provider: **DeepSeek API** (OpenAI-compatible, used via the `openai` SDK with base_url), chosen 2026-10-03 because OpenAI was too expensive for the user as a student. The IR said OpenAI. Ollama local is the second backend.

Evaluation must isolate the guardrail from model capability, a concern the user raised. Plan: a no-guard baseline (model-resisted attacks excluded from the mitigation rate) plus a forced-compromise test that feeds `attack_tool_call` from each scenario straight to the action guard.

P1 done 2026-10-03 (commit 6a84c97): tools.yaml (6 tools), trust.yaml, 10 KB docs, 16 scenarios, docs/threat_model.md, docs/requirements_backlog.md, tests/test_specs.py.

P2 week 1 done 2026-10-03 (commit a92dffb):
- Training data: deepset + PromptShield (Apache-2.0).
- Test sets: deepset test and PromptShield test, kept untouched. OOD sets: SPML, Lakera (positives only) and Bitext (in-domain benign).
- Rules + TF-IDF LR/SVM baselines. TF-IDF F1 falls from 0.94 on val to 0.43–0.50 on PromptShield test (FPR 15–20%), because it confuses benign task instructions with injections. Rules are precise but low-recall. Rules OR SVM raises recall on every set.
- Results are in docs/results_p2_baseline.md.

P2 transformers done 2026-10-05, in two CRISP-DM iterations:
- Iteration 1 is tagged p2-iteration1, with results archived in results/iteration1/. Iteration 2 is commit d3abd10.
- Main classifier: DeBERTa-v3-small (models/deberta_v3_small), chosen by the pre-declared criterion of val F1 then CPU latency. ModernBERT-base is the comparison model; ProtectAI is the external reference.
- Iteration 2 fixes: midpoint thresholds, and the Bitext pool added to train/val. This took in-domain FPR to 0%.
- Text detectors all miss the two subtle docs, which supports the action guard.
- Full write-up in docs/results_p2_transformers.md; `scripts/reproduce_p2.sh` reruns P2.
- Machine quirks: gcc was installed for Triton; fp16 checkpoints must be loaded as fp32.
- The user likes visual monitoring: scripts/watch_training.sh (opened in ptyxis) and TensorBoard at logs/tensorboard.

Advanced techniques the user chose on 2026-10-05:
- (1) Calibrated confidence cascade in the P4 decision engine, after "JEV-as-a-Judge, accept when confident, escalate when unsure" (arXiv 2609.26550). Jev is TypeSafe AI's decision-only model, released Sep 2026, after Claude's cutoff.
- (2) DeepSeek → DeBERTa distillation as P2 iteration 3: synthetic data plus teacher labels, aimed at known gaps (prompt leak, subtle injections, "ignore" hard negatives, non-customer-service domains). DeepSeek ToS permits using outputs for training.
- Not chosen yet: span localisation, red-teaming, MCP gateway, Jev/Kev comparison.

DeepSeek API ready 2026-11-02 (.env set, balance CNY 50). Model ids: `deepseek-flash` (V4.1-Flash), `deepseek-v4-pro`. Thinking is ON by default; disable with `extra_body={"thinking": {"type": "disabled"}}`. top_logprobs works in non-thinking mode, but the teacher is one-hot on clear cases, so iteration 3 uses hard labels and uses logprobs only for filtering. Off-peak pricing is half price.
- Iteration-3 plan: docs/distillation_plan.md, approved by the user 2026-11-02. The timebox is 2–7 Nov, with P3 running in parallel because the schedule is behind the Gantt.
- LLMail-Inject is the new blind set: data/processed/ood_llmail_dev and ood_llmail_test, both frozen. Do NOT score anything on them until the synthetic data is frozen.
- The pipeline lives in training/distill/ (generate → validate → verify) plus scripts/spot_check.py, and data goes to data/synthetic/<stage>/.
- Pilot 1 findings:
  - Injections named the assistant 72% of the time vs 11% for benign texts, which is a shortcut in the data.
  - The non-thinking verifier wrongly rejected subtle injections. Disagreements are now escalated to thinking mode instead of dropped.
- Pilot 2 used prompt v2, which balanced assistant mentions. All items still disputed after thinking mode were generator errors.
- Next: the full run, about CNY 5 off-peak or CNY 10 at peak. The user plans to run `scripts/run_distill.sh full` themselves in the evening (after 18:00 MYT).

Architecture decision, 2026-11-02:
- The user chose to add an output guard (canary, URL allowlist, cross-customer data), spotlighting, and an alignment check as the cascade's escalation tier.
- All of these live in the guardrail middleware, not in the agent. The agent only calls three checkpoints, defined in guardrail/interface.py.

P3 started 2026-11-02 (uncommitted):
- New files: guardrail/interface.py (with NoGuard), agent/llm.py (OpenAI-compatible backends, record/replay), agent/tools.py (permissive mock tools that log side effects), agent/loop.py, agent/cli.py, evaluation/agent_runs.py, tests/test_agent.py. 98 tests pass.
- **Key finding:** no-guard DeepSeek flash resists all 10 scenario attacks (ASR 0%), with 100% benign utility. So the forced-compromise test, weaker local models (Ollama is not installed yet) and harder adaptive scenarios are needed to measure the guardrail.

Iteration-3 data prep done 2026-11-03 (uncommitted):
- `prepare --iteration 3` splits the synthetic data by generation call (job_id) 75/15/10 within each category, giving test_synthetic. It also writes nested train_synth0/25/50 dose sets.
- Iteration-2 results are archived in results/iteration2/.
- New: evaluation/adoption.py (adoption rule plus a paired-bootstrap AUC CI), evaluation/dose_response.py, and evaluation/blind_test.py (scores LLMail-test once; a rescore requires --rescore with a reason).
- scripts/run_iteration3.sh refuses to start until the spot-check CSV is complete.
- The user argued the distilled data would be redundant. The rebuttal: the iteration-2 DeBERTa catches only 5% of subtle and 21% of indirect synthetic injections, so the data covers real gaps. The dose-response curve answers "is the amount enough?".

P4 core done 2026-11-13 (uncommitted; P3 and iter-3 committed as f9a279e and c28828e, not pushed):
- New files: guardrail/trust.py (ledger + taint), policy.py (one named check per tools.yaml rule), detector.py (rules + DeBERTa, sentence-level SANITIZE), decision.py (policy precedence + session risk), audit.py (JSONL, redacted), guard.py (HybridGuard + ablation VARIANTS), calibrate.py, and config/guard.yaml (strict threshold at 1% val FPR).
- evaluation/forced_compromise.py: full guard stops 10/10 attacks; policy+taint alone blocks 9/9 attack calls. DeepSeek end to end with the full guard: ASR 0, utility 92% (BEN-005 false block).
- Write-up: docs/results_p4_core.md.
- Spotlighting (per-session token tags) and the output guard (canary, 20% 8-gram coverage, link/contact allowlist, cross-customer PII) were added the same day.
- Finding: no_classifier scores better than full on the 16 scenarios (6/6 vs 4/6 benign) because of the iteration-2 'ignore' shortcut.
- Cascade done the same day: confident band 0.999713, uncertain band 1.4% of val, uncertain flags go to the DeepSeek judge. The alignment judge sees only customer messages plus the call, for write actions after untrusted content.
- Full guard results: 10/10 attacks stopped, 6/6 benign, 43/45 checkpoints; end to end, utility 100%. Alignment judge dev check: 7/7.
- Still to do: FastAPI (FR07), and P5 policy-permitted-harm scenarios (frozen before scoring).
- P5 needs policy-permitted harmful-action scenarios.

Agreed refinements (2026-10-03): fine-tuned small encoder (DeBERTa-v3-small / ModernBERT-base) becomes the main classifier, with TF-IDF+SVM as the baseline. Also: bigger mixed dataset, sentence-level SANITIZE, taint tracking of tool args, adaptive-attack test set, and Ollama as a second LLM backend in Iteration 3. Full roadmap: ~/.claude/plans/im-going-to-start-binary-dongarra.md.

Semester 2 Gantt: Iter1 scope 21/09–04/10, Iter2 detection 05–18/10, Iter3 action control 19/10–01/11, Iter4 integration 02–22/11 (break 09–15/11), Iter5 evaluation 23/11–06/12, report 07–13/12, submit 16/12/2026.

**Why:** It's a deadline-driven academic project. The user is anxious it will look "可有可无" (pointless), so prioritize a working, evaluated core and a live attack demo over breadth.

**How to apply:** Keep work aligned with the IR objectives (O1–O4) and FR/NFR IDs, and explain the code as you go because the user must defend it at the viva. The real environment is Ubuntu 26.04 + RTX 4070 8 GB (the IR said Windows 11), and this change must be noted in the final report. Commit rules: [[no-claude-coauthor-trailer]].

## 2026-11-16 guardrail review (Fable 5.2 session)
- User asked for a 4-phase review (understand → research → gap analysis → approved fixes → fresh-agent verification). Phases 0–2 done; **stopped for approval**, no existing code changed.
- Output: `GUARDRAIL_REVIEW.md` (repo root), probe set `data/review_probes/probes.json`, runner `evaluation/review_baseline.py`, results `results/review_baseline.json`.
- Baseline on probes (full guard): 3/17 attacks executed (repeat refunds A10, NaN amount A11, translated prompt leak A13), 1 confirm-only (A15), 5/17 stopped only by the alignment judge; 5/19 benign refused (B01, B09, B17, B18, B19). Probe set is a dev set, not held-out.
- Known self-inflicted issue: judge prompt (`guardrail/judge.py:47-51`) contains examples matching BEN-005/BEN-006 → must be de-contaminated (item E10) before any held-out scoring.
- Plan items E1–E22 and decisions D1–D5 are in the review; phase 3 uses opus subagents for mechanical items only, phase 4 needs a fresh subagent to review/attack and `GUARDRAIL_CHANGES.md`.
- Cascade work from 2026-11-13 is still uncommitted.

## 2026-11-16 phase 3 started (user: "按新顺序开始")
- User's concern: guard must be domain-agnostic middleware (IR §1.5.1 target users = developers; FR07), not a NovaMart-specific guard; UI must be a guardrail console, not an agent chat UI. Review doc §11 records the revised plan: G1 → E1–E10 → E19 → G2 (mail-assistant domain pack) → E11, E12 → G4 (console).
- Done, uncommitted: **G1** engine/domain split (`guardrail/domain.py`, `domains/novamart/`, generic policy checks, `principal_id`, `guardrail/llm.py`; engine no longer imports `agent/`), **E1** non-finite numbers, **E2** cumulative limit + `record_outcome`, **E3** audit fidelity, **E4** Unicode canonicalisation for scanning. 153 tests pass; forced compromise identical; probes full guard: executed 3/17 → 1/17, benign refused 5/19 unchanged.
- Baseline snapshot of pre-G1 working tree: git ref `refs/snapshots/pre-g1` (not on a branch). Running log: `GUARDRAIL_CHANGES.md`.
- Open: D2 (judge may clear rule/confident flags on user messages) and D3 (alignment judge on every write) not yet answered by the user; calibrate rounding bug (confident 0.999713 < max benign val 0.999713123) found, not fixed. Nothing committed since 8fdbfff.
- 2026-11-16 later: committed on main (not pushed): 4f87d15 cascade, cf85102 review+probes, cf15fe6 G1+E1–E4. Snapshot ref deleted. User accepted D2/D3 as recommended.
- Then done, **uncommitted**: E5 (`allowed_content` check + `content_args`), E6 (alignment on every permitted write; prose args withheld), E7 (phone tail match; `not_tainted` takes `args`; probe A19 added), E8 (every flag on a user message goes to the judge), E9 (bare hosts; typed details not masked), E10 (judge prompt de-contaminated, no regression). 169 tests; probes full: executed 1/18, confirm-only 0, benign refused 1/19; no_judge still 6 confirm-only (alignment judge remains the only layer for policy-permitted harm). E2E DeepSeek full: ASR 0/10, utility 12/12.
- Next in plan: E16, E19 (FastAPI), G2 (mail domain), G3, E11, E12, G4 (console), then phase 4 fresh-agent review.

## 2026-11-17 guardrail console prototype
- Committed e8c0e74 (E5–E10). Then built the console prototype, **uncommitted**: `console/` (static, vanilla JS modules, SVG; no build, no network), `scripts/export_console_demo.py` (records 16 scenarios + 37 probes × 5 variants → `console/data/demo_events.json`), `scripts/serve_console.py` (no-cache dev server, port 8765), `.claude/launch.json`, `docs/console_design.md`, README section.
- **Design direction (user's decision):** first version (dark navy, cyan glow, rounded cards) was rejected as "ugly, strong AI flavour". Kept direction: terminal vocabulary (monospace, Tango ANSI colours, no cards) + railway-signal metaphor (checkpoint = track, layer = signal lamp, check = train that stops at the red signal). User prefers the look of their original CLI output. Keep polishing this; ask the supervisor's opinion when it is shown (marker's view decides).
- Lesson: let the user choose visual direction from real rendered pixels, never from ASCII previews or descriptions.
- Console is only the Live view on recorded data; live SSE, layer toggles, other views wait for FastAPI (E19). Agreed order after the prototype: FastAPI → second domain (G2) → held-out eval (E11, E12) → wire console.
- 2026-11-17 later: console prototype committed (d1b6452; `.claude/` kept out of the repo via `.git/info/exclude`). Then **E19 FastAPI done, uncommitted**: `api/main.py` (sessions, /input /action /outcome /output, /policy, /health; confirmation tokens bound to the exact call; fail closed), `api/client.py` (`RemoteGuard`), `tests/test_api.py` (10 tests; 16 scenarios identical over HTTP vs in process); engine `record_outcome` now returns a violation (executed after BLOCK / unchecked). 179 tests; forced and probes unchanged. Next: G2 (mail-assistant domain pack), G3, E11, E12, E16; console live wiring needs `GET /events` + engine trace.
