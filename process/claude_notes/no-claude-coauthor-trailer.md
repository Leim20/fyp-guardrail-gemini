---
name: no-claude-coauthor-trailer
description: "For the FYP repo, git commits must NOT include the \"Co-Authored-By: Claude\" trailer"
metadata:
  node_type: memory
  type: feedback
  originSessionId: a10e5f01-3876-4764-aad6-92018a752d5e
  modified: 2026-10-03T11:57:01.386Z
---

Do not add a `Co-Authored-By: Claude ...` trailer (or any Claude attribution line) to git commits in the FYP guardrail project. Commits carry only the user's own git identity. The user chose this explicitly on 2026-10-03, which overrides the default attribution reminder.

**Why:** The user decided this themselves when asked. It's an academic final-year project (see [[fyp-guardrail-project]]), and they will handle disclosing AI assistance under their university's policy on their own terms.

**How to apply:** Whenever committing in `~/fyp-guardrail`, write normal commit messages without the trailer. If PR descriptions come up, ask before adding the "Generated with Claude Code" line.
