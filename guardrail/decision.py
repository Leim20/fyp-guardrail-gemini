"""Decision engine: turns layer signals into ALLOW / SANITIZE / ASK_CONFIRM / BLOCK (FR04).

Precedence for actions, highest first:
  1. A policy deny rule (incl. taint) -> BLOCK. Absolute: no classifier or judge can override it.
  2. The alignment judge says the user did not ask for this action -> BLOCK. If the judge
     was asked but failed, the action needs confirmation at least (fail closed).
  3. The tool's default verdict from tools.yaml (ALLOW or ASK_CONFIRM).
  4. Session risk can only add friction: if the input guard flagged anything earlier in this
     session, a write or external action that policy would ALLOW becomes ASK_CONFIRM.
The probabilistic layers can make a decision stricter, never looser. That is what lets the
guard rely on an imperfect classifier: a miss costs one layer of defence, not the whole guard.
The one exception is deliberate and narrow: the judge may clear an input flag (the confidence
cascade). On data it may clear only an uncertain classifier-only flag; on the user's own
message it reviews every flag, because a false alarm there refuses a person and policy still
bounds what that person can do. It never touches a policy decision.

Inputs, by the item's trust class (trust.yaml):
  scan none                    -> ALLOW without scanning
  can instruct (the user)      -> flagged: BLOCK (it is a direct injection; never rewrite
                                  what the user said)
  data (documents, results)    -> flagged sentences: SANITIZE (remove them, keep the rest)
                                  flagged as a whole, no single sentence: BLOCK (withhold it)
"""

from guardrail.detector import Detection
from guardrail.interface import Content, Decision
from guardrail.judge import JudgeResult
from guardrail.policy import PolicyResult
from guardrail.specs import ToolSpec, TrustClass, Verdict


def decide_input(item: Content, trust: TrustClass, detection: Detection | None) -> Decision:
    if detection is None:
        return Decision(Verdict.ALLOW, item.text, layer="trust", reasons=[f"{item.trust}: not scanned"])
    judged = [f"judge: {detection.judge['reason']}"] if detection.judge else []
    if not detection.flagged:
        return Decision(Verdict.ALLOW, item.text, layer="judge" if detection.judge else "detector", reasons=judged)
    # Name the layer that fired, not every rule that merely matched: a weak rule below the rule
    # threshold did not cause the flag, and the audit log must say which layer did (FR06).
    layers = detection.layers()
    reasons = (([f"rule:{r}" for r in detection.rule_ids] if "rules" in layers else [])
               + (["classifier"] if "classifier" in layers else []) + judged)
    if trust.can_instruct:
        return Decision(Verdict.BLOCK, None, reasons=reasons, layer="detector")
    if detection.sanitized is not None:
        return Decision(Verdict.SANITIZE, detection.sanitized, layer="detector",
                        reasons=reasons + [f"removed {len(detection.flagged_sentences)} sentence(s)"])
    return Decision(Verdict.BLOCK, None, reasons=reasons + ["withheld: no single sentence to remove"],
                    layer="detector")


def decide_action(policy: PolicyResult, spec: ToolSpec | None, flagged_sources: list[str],
                  session_risk: bool, alignment: JudgeResult | None = None, principal: str = "user") -> Decision:
    if policy.verdict == Verdict.BLOCK:
        layer = "taint" if policy.taint_only else "policy"
        return Decision(Verdict.BLOCK, reasons=[f"{rule}: {policy.details[rule]}" for rule in policy.violations],
                        layer=layer)
    if alignment is not None and alignment.answer is False:
        return Decision(Verdict.BLOCK, reasons=[f"not requested by the {principal}: {alignment.reason}"], layer="alignment")
    if alignment is not None and alignment.answer is None:
        return Decision(Verdict.ASK_CONFIRM, reasons=[alignment.reason], layer="alignment")
    if policy.verdict == Verdict.ASK_CONFIRM:
        return Decision(Verdict.ASK_CONFIRM, reasons=[f"{spec.risk}-risk {spec.effect} action"], layer="policy")
    if session_risk and flagged_sources and spec.effect != "read":
        return Decision(Verdict.ASK_CONFIRM, layer="session_risk",
                        reasons=[f"{spec.effect} action after flagged content from {', '.join(flagged_sources)}"])
    return Decision(Verdict.ALLOW, layer="policy")
