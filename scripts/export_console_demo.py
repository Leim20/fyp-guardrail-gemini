"""Record real guard runs as events for the console prototype (console/data/demo_events.json).

The console shows every check the guard makes. Until the guard is served over HTTP (FR07) and
pushes its decisions live, the prototype plays back a recording, and this script makes it: the
16 scenarios and the review probes are replayed through five guard variants, and each check is
written as one event in the format the live stream will use later (docs/console_design.md).

Nothing here is simulated: verdicts, layers, reasons, scores and latencies are what the guard
returned. Two things are added for display only:
  - the checked text. The audit log deliberately keeps only a hash and a redacted preview; a
    demo needs the words. All data is synthetic, and the text goes into this file only.
  - a per-layer `trace`, derived from the fields of the audit record (scores, reasons, judge).
    The engine does not emit one yet; when it does, this derivation goes away.
  - `harm`: whether this check is the step an attack needs (the attacker's tool call, the
    leaked reply). It comes from the scenario or probe definition, so the console can show an
    attack going through when the guard is switched off.

Usage:
    uv run python scripts/export_console_demo.py
"""

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from evaluation import forced_compromise, review_baseline
from evaluation.scenarios import load_scenarios
from guardrail.detector import REMOVED, split_sentences
from guardrail.guard import VARIANTS, HybridGuard, load_settings
from guardrail.interface import Content, NoGuard, ToolCall

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "console" / "data" / "demo_events.json"
RUN_VARIANTS = ["none", "detectors", "policy", "no_judge", "full"]
GATES = {"input": ["trust", "rules", "classifier", "judge"],
         "action": ["schema", "policy", "taint", "alignment"],
         "output": ["leak", "links", "pii"]}
_WRAPPER = re.compile(r"^«DATA [0-9a-f]+ source=[^»]*»\n(.*)\n«/DATA [0-9a-f]+»$", re.DOTALL)
_COPIED = re.compile(r"(\w+) copied from (\S+)")


def plain(value):
    """JSON has no NaN or Infinity (a browser refuses the file), so such numbers become text."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return [plain(v) for v in value] if isinstance(value, list) else value


def step(layer: str, status: str, detail: str = "") -> dict:
    """status: pass | flag | block | clear | fail | skip | off"""
    return {"layer": layer, "status": status, "detail": detail}


class RecordingGuard:
    """Passes every call to the real guard and writes down what went in and what came back."""

    def __init__(self, guard, variant: str, events: list):
        self.guard, self.variant, self.events, self.name = guard, variant, events, guard.name
        self.config = VARIANTS.get(variant)  # None for the unguarded baseline
        settings = load_settings()
        self.rule_threshold = settings["rules"]["flag_threshold"]
        self.checks = {}
        if isinstance(guard, HybridGuard):
            rules = list(guard.registry.global_deny) + [r for t in guard.registry.tools.values() for r in t.policy.deny]
            self.checks = {r.id: r.check for r in rules}

    def open_session(self, principal_id: str, session_id: str) -> "RecordingSession":
        return RecordingSession(self, self.guard.open_session(principal_id, session_id), session_id.split("-", 1)[-1])

    def audit_record(self) -> dict:
        return self.guard.audit.records[-1] if isinstance(self.guard, HybridGuard) else {}

    def on(self, layer: str) -> bool:
        return bool(self.config) and getattr(self.config, layer)


class RecordingSession:
    def __init__(self, outer: RecordingGuard, inner, run: str):
        self.outer, self.inner, self.run = outer, inner, run
        self.principal_id = inner.principal_id

    def system_prompt_addendum(self) -> str:
        return self.inner.system_prompt_addendum()

    def _emit(self, checkpoint: str, decision, **fields) -> None:
        record = self.outer.audit_record()
        self.outer.events.append({
            "id": len(self.outer.events) + 1, "run": self.run, "variant": self.outer.variant,
            "checkpoint": checkpoint, "verdict": str(decision.verdict), "layer": decision.layer,
            "reasons": decision.reasons, "latency_ms": record.get("latency_ms", 0.0), **fields})

    # --- input ---
    def check_input(self, item: Content):
        decision = self.inner.check_input(item)
        if item.trust == "system":
            return decision  # the developer's prompt is registered, not screened: not shown
        record = self.outer.audit_record()
        wrapped = _WRAPPER.match(decision.text or "")
        result_text = wrapped.group(1) if wrapped else decision.text
        removed = ([s for s in split_sentences(item.text) if s not in (result_text or "")]
                   if REMOVED in (result_text or "") else [])
        self._emit("input", decision, source=item.source, trust=item.trust, text=item.text, result_text=result_text,
                   removed=removed, scores=record.get("scores", {}), judge=record.get("judge"),
                   trace=self._input_trace(item, decision, record))
        return decision

    def _input_trace(self, item: Content, decision, record: dict) -> list[dict]:
        outer = self.outer
        if not isinstance(outer.guard, HybridGuard) or outer.guard.detector is None:
            return [step(g, "off") for g in GATES["input"]]
        trust = outer.guard.trust.trust_classes.get(item.trust) or outer.guard.trust.trust_classes["external_untrusted"]
        trace = [step("trust", "pass", f"{item.trust}: scan {trust.scan}, {'may' if trust.can_instruct else 'may not'} instruct")]
        scores, judge = record.get("scores", {}), record.get("judge")
        rule_ids = [r.removeprefix("rule:") for r in decision.reasons if r.startswith("rule:")]
        if not outer.on("rules"):
            trace.append(step("rules", "off"))
        else:
            fired = bool(rule_ids) or scores.get("rules", 0) >= outer.rule_threshold
            trace.append(step("rules", "flag" if fired else "pass",
                              f"score {scores.get('rules', 0):.2f}" + (f" ({', '.join(rule_ids)})" if rule_ids else "")))
        if not outer.on("classifier"):
            trace.append(step("classifier", "off"))
        else:
            threshold = outer.guard.detector.thresholds[trust.scan]
            score = scores.get("classifier", 0)
            fired = "classifier" in decision.reasons or score >= threshold
            trace.append(step("classifier", "flag" if fired else "pass", f"score {score:.4f}, threshold {threshold:.4g}"))
        if not outer.on("judge"):
            trace.append(step("judge", "off"))
        elif judge is None:
            trace.append(step("judge", "skip", "not asked"))
        else:
            status = {True: "block", False: "clear", None: "fail"}[judge["injection"]]
            trace.append(step("judge", status, judge["reason"]))
        return trace

    # --- action ---
    def check_action(self, call: ToolCall):
        decision = self.inner.check_action(call)
        record = self.outer.audit_record()
        taint = None
        for reason in decision.reasons:
            if (found := _COPIED.search(reason)) and found.group(1) in call.args:
                taint = {"arg": found.group(1), "value": str(call.args[found.group(1)]), "source": found.group(2)}
        self._emit("action", decision, tool=call.tool, args=call.args, judge=record.get("judge"), taint=taint,
                   executed=None, trace=self._action_trace(decision, record))
        return decision

    def _action_trace(self, decision, record: dict) -> list[dict]:
        outer = self.outer
        if not outer.on("policy"):
            return [step(g, "off") for g in GATES["action"]]
        by_gate = {"schema": [], "policy": [], "taint": []}
        if str(decision.verdict) == "BLOCK" and decision.layer in ("policy", "taint"):
            for reason in decision.reasons:
                check = outer.checks.get(reason.split(":", 1)[0], "")
                gate = "schema" if check in ("known_tool", "schema") else "taint" if check == "not_tainted" else "policy"
                by_gate[gate].append(reason)
        trace = [step("schema", "block" if by_gate["schema"] else "pass", "; ".join(by_gate["schema"]))]
        stopped = bool(by_gate["schema"])
        trace.append(step("policy", "skip" if stopped else "block" if by_gate["policy"] else "pass", "; ".join(by_gate["policy"])))
        trace.append(step("taint", "off" if not outer.on("taint") else "skip" if stopped else
                          "block" if by_gate["taint"] else "pass", "; ".join(by_gate["taint"])))
        judge = record.get("judge")
        if not outer.on("judge"):
            trace.append(step("alignment", "off"))
        elif judge is None:
            trace.append(step("alignment", "skip", "not asked: read-only, or policy already decided"))
        else:
            status = {True: "pass", False: "block", None: "fail"}[judge["aligned"]]
            trace.append(step("alignment", status, judge["reason"]))
        return trace

    def record_outcome(self, call: ToolCall, executed: bool) -> None:
        self.inner.record_outcome(call, executed)
        for event in reversed(self.outer.events):
            if event["run"] == self.run and event["checkpoint"] == "action":
                event["executed"] = executed
                break

    # --- output ---
    def check_output(self, text: str):
        decision = self.inner.check_output(text)
        if not self.outer.on("output"):
            trace = [step(g, "off") for g in GATES["output"]]
        else:
            leak = [r for r in decision.reasons if r.startswith("prompt_leak")]
            links = [r for r in decision.reasons if r.startswith(("link", "contact"))]
            pii = [r for r in decision.reasons if r.startswith("pii")]
            trace = [step("leak", "block" if leak else "pass", "; ".join(leak)),
                     step("links", "skip" if leak else "flag" if links else "pass", "; ".join(links)),
                     step("pii", "skip" if leak else "flag" if pii else "pass", "; ".join(pii))]
        self._emit("output", decision, text=text, result_text=decision.text, trace=trace)
        return decision


def main() -> None:
    settings = load_settings()
    scenarios = load_scenarios()
    probes = json.loads(review_baseline.PROBES_PATH.read_text(encoding="utf-8"))["probes"]
    events, runs = [], []
    for variant in RUN_VARIANTS:
        guard = NoGuard() if variant == "none" else HybridGuard(VARIANTS[variant], settings)
        recorder = RecordingGuard(guard, variant, events)
        for scenario in scenarios:
            start = len(events)
            row = forced_compromise.replay(recorder, scenario)
            attack = scenario.attack_tool_call
            for event in events[start:]:
                event["harm"] = bool(
                    (event["checkpoint"] == "action" and attack and event["tool"] == attack.tool and event["args"] == attack.args)
                    or (event["checkpoint"] == "output" and scenario.attack_goal == "prompt_leak"))
            outcome = (("stopped" if row["stopped"] else "executed") if scenario.is_attack
                       else ("ok" if row["benign_ok"] else "refused"))
            runs.append({"run": scenario.id, "variant": variant, "set": "scenario", "outcome": outcome,
                         "kind": "attack" if scenario.is_attack else "benign",
                         "label": " · ".join(x.replace("_", " ") for x in (scenario.attack_goal, scenario.technique) if x)
                         or scenario.category.replace("_", " "),
                         "about": scenario.notes})
        for probe in probes:
            start = len(events)
            row = review_baseline.replay(recorder, probe)
            for event, probe_step in zip(events[start:], probe["steps"], strict=True):
                event["harm"] = bool(probe_step.get("harm"))
            runs.append({"run": probe["id"], "variant": variant, "set": "probe", "outcome": row["outcome"],
                         "kind": probe["kind"], "label": probe["family"].replace("_", " "),
                         "about": probe.get("hypothesis", "")})
    full = HybridGuard(VARIANTS["full"], settings)
    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "domain": full.domain.name, "organisation": full.domain.profile.organisation,
        "gates": GATES,
        "variants": {v: ({} if v == "none" else {k: getattr(VARIANTS[v], k) for k in
                                                 ("rules", "classifier", "judge", "policy", "taint", "output", "spotlight")})
                     for v in RUN_VARIANTS},
        "thresholds": {**{k: float(v) for k, v in full.detector.thresholds.items()},
                       "confident": full.confident_ml, "rules": settings["rules"]["flag_threshold"]},
        "tools": {name: {"effect": t.effect, "risk": t.risk} for name, t in full.registry.tools.items()},
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(plain({"meta": meta, "runs": runs, "events": events}), ensure_ascii=False,
                                   default=str, allow_nan=False))
    print(f"{len(events)} events from {len(runs)} runs ({len(scenarios)} scenarios + {len(probes)} probes, "
          f"{len(RUN_VARIANTS)} variants) -> {OUT_PATH.relative_to(ROOT)} ({OUT_PATH.stat().st_size / 1024:.0f} kB)")


if __name__ == "__main__":
    main()
