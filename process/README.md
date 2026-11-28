# Process notes

## Read this first (for a reviewer)

- **Known limitations are already documented. Please do not report them as new findings.**
  - `GUARDRAIL_CHANGES.md`, sections "过程中新发现的问题" and "尚未处理".
  - Each tool's `rationale` in `domains/novamart/tools.yaml`. Some exclusions there are
    deliberate scope decisions tied to `docs/threat_model.md`.
- **The current state as of 2026-11-28:**
  - The guard uses the iteration-3 classifier. This is a reported deviation from the adoption
    rule: `docs/distillation_plan.md` §10 and `GUARDRAIL_CHANGES.md` C1.
  - Probe numbers in the change log before C1 refer to iteration 2.
  - R1 fixes the small issues from the Gemini review that checked out (`GUARDRAIL_CHANGES.md`
    R1). 189 tests pass, and probe and scenario outcomes are unchanged.
  - Next is the second domain pack (G2, a workspace assistant). It adds two generic
    primitives: confidential values must not leave to external recipients, and webhook hosts
    must be on an allowlist.
- **Deadline:** the FYP is due on 2026-12-16, and the report is still to be written.
  Suggestions should fit that time.

## What is in this folder

- `gemini_review/guardrail_architecture_review.md`: Gemini's review of the 2026-11-25 snapshot.
- `gemini_review/decisions_asked.txt`: the decisions that review asked for.
- `gemini_review/verification.md`: each claim of that review checked against the code, docs
  and IR. It says which claims hold, which were already documented, which are out of date,
  and the plan to the deadline.
- `claude_notes/console_prototype_plan.md`: the approved plan for the Guardrail Console
  prototype.
- `claude_notes/fyp-guardrail-project.md` and the other notes: the coding assistant's running
  notes on the project (progress, decisions, working preferences), kept between sessions.

## The main process documents

These are in the repository root and in `docs/`:
- `GUARDRAIL_REVIEW.md`: the review of prompting and guardrail design (gap analysis, plan E1–E22).
- `GUARDRAIL_CHANGES.md`: what changed for each item, with evidence and remaining weaknesses.
- `docs/`: threat model, distillation plan and results, requirements backlog, console design,
  data card.

## Not included

- `.env` (API keys)
- `.venv/`
- `models/` (5.5 GB of trained weights)

This repository is a snapshot of the main project's working tree. It is updated by hand and
does not track every change.
