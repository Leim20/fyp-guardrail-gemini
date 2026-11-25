"""Tool policy enforcement, the deterministic core of the action guard (FR03, FR05).

Order, as a domain's tools.yaml specifies:
  1. global deny rules (unknown tool, invalid arguments)  -> BLOCK
  2. the tool's own deny rules                            -> BLOCK
  3. otherwise the tool's default verdict (ALLOW or ASK_CONFIRM)

The engine knows no application. It offers a small set of generic checks, and a domain builds
its rules from them in tools.yaml:

  known_tool             the tool is in the allow-list
  schema                 the arguments fit the tool's JSON Schema, and every number is finite
  owned_by_principal     `arg` names a `resource` that belongs to the authenticated principal
  equals_principal       `arg` is the principal's own id
  equals_principal_attr  `arg` equals the principal's attribute `attr` (e.g. registered email)
  forbidden_values       `arg` is none of `values`
  max_from_resource      `arg` is at most the attribute `attr` of the `resource` named by
                         `resource_arg`; with `cumulative`, together with the same argument
                         of the calls to this tool on this resource that already ran in the
                         session (the agent reports them, interface.record_outcome)
  not_tainted            no free-valued argument (or none of `args`) was copied from untrusted
                         content
  allowed_content        the tool's prose arguments (`content_args`) contain nothing the output
                         guard would remove from a reply: links and contacts off the
                         allowlist, other principals' details

NovaMart's "order_not_owned" is owned_by_principal with resource "order"; a mail assistant's
"draft_not_owned" would be the same check with resource "draft". The engine refuses to start if
a rule names a check that does not exist or leaves out a parameter the check needs, so a
domain's YAML and the engine cannot drift apart silently.

The checks use the authenticated session (the principal id) and the application's authoritative
records, reached through the domain's resolver, never what the LLM or retrieved text claims.
That is why they hold even when an injection fully convinces the model: "is O-6666 this
customer's order?" has the same answer however persuasive the forum post is.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from guardrail.domain import ResourceResolver
from guardrail.interface import ToolCall
from guardrail.output import OutputGuard
from guardrail.specs import PolicyRule, ToolRegistry, ToolSpec, Verdict
from guardrail.trust import Ledger, normalise


@dataclass
class RuleContext:
    call: ToolCall
    spec: ToolSpec | None
    rule: PolicyRule
    principal_id: str
    principal_label: str  # what the domain calls its principal, e.g. "customer"
    resolver: ResourceResolver
    ledger: Ledger
    content: OutputGuard | None = None

    def say(self, default: str, **fields: Any) -> str:
        """The rule's own message if the domain wrote one, else the generic wording."""
        return self.rule.message.format(arg=self.rule.arg, **fields) if self.rule.message else default


@dataclass
class PolicyResult:
    verdict: Verdict
    violations: list[str] = field(default_factory=list)  # rule ids, e.g. "order_not_owned"
    details: dict[str, Any] = field(default_factory=dict)
    taint_only: bool = False  # every violated rule is a not_tainted check


def _known_tool(ctx: RuleContext) -> str | None:
    return None if ctx.spec else f"no tool named {ctx.call.tool}"


def _schema(ctx: RuleContext) -> str | None:
    try:
        jsonschema.validate(ctx.call.args, ctx.spec.parameters, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as error:
        return error.message
    # JSON Schema has no notion of NaN or Infinity, but Python's JSON parser accepts both, and
    # every comparison with NaN is False, so "amount > limit" would let it through.
    if bad := sorted(path for path, value in _numbers(ctx.call.args) if not math.isfinite(value)):
        return f"{', '.join(bad)} is not a finite number"
    return None


def _numbers(value: Any, path: str = "") -> list[tuple[str, float]]:
    """Every number in the arguments, at any depth, with the path to it."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [(path, value)]
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else []
    return [found for key, item in items for found in _numbers(item, f"{path}.{key}" if path else str(key))]


def _owned_by_principal(ctx: RuleContext) -> str | None:
    resource = ctx.resolver.resource(ctx.rule.resource, ctx.call.args.get(ctx.rule.arg))
    if resource is None:
        return ctx.say(f"{ctx.rule.resource} does not exist", value=ctx.call.args.get(ctx.rule.arg))
    if resource.get("owner") == ctx.principal_id:
        return None
    return ctx.say(f"{ctx.rule.resource} belongs to another {ctx.principal_label}", value=ctx.call.args.get(ctx.rule.arg))


def _equals_principal(ctx: RuleContext) -> str | None:
    value = ctx.call.args.get(ctx.rule.arg)
    return None if value == ctx.principal_id else ctx.say(f"{value} is not the logged-in {ctx.principal_label}", value=value)


def _equals_principal_attr(ctx: RuleContext) -> str | None:
    registered = (ctx.resolver.principal(ctx.principal_id) or {}).get(ctx.rule.attr)
    value = ctx.call.args.get(ctx.rule.arg, "")
    if registered is not None and normalise(value) == normalise(registered):
        return None
    return ctx.say(f"{value} is not the {ctx.principal_label}'s registered {ctx.rule.attr}", value=value)


def _forbidden_values(ctx: RuleContext) -> str | None:
    value = ctx.call.args.get(ctx.rule.arg)
    return ctx.say(f"{ctx.rule.arg} may not be {value}", value=value) if value in ctx.rule.values else None


def _max_from_resource(ctx: RuleContext) -> str | None:
    resource = ctx.resolver.resource(ctx.rule.resource, ctx.call.args.get(ctx.rule.resource_arg))
    if resource is None:
        return None  # a missing resource is reported by the owned_by_principal rule
    value, limit = ctx.call.args.get(ctx.rule.arg, 0), resource[ctx.rule.attr]
    spent = 0
    if ctx.rule.cumulative:
        target = ctx.call.args.get(ctx.rule.resource_arg)
        spent = sum(c.args.get(ctx.rule.arg, 0) for c in ctx.ledger.executed
                    if c.tool == ctx.call.tool and c.args.get(ctx.rule.resource_arg) == target)
    if value + spent > limit:
        reason = ctx.say(f"{value} exceeds the {ctx.rule.resource}'s {ctx.rule.attr} ({limit})", value=value, limit=limit)
        return reason + (f" (already {spent} in this session)" if spent else "")
    return None


def _not_tainted(ctx: RuleContext) -> str | None:
    tainted = ctx.ledger.tainted_arguments(ctx.call.args, ctx.spec.parameters, ctx.rule.args)
    return ", ".join(f"{arg} copied from {source}" for arg, source in tainted.items()) or None


def _allowed_content(ctx: RuleContext) -> str | None:
    """A reply is checked before the principal sees it; text sent through a tool leaves the
    system without passing that check. This applies the same deterministic rules to it."""
    found = []
    for name in ctx.spec.content_args:
        if isinstance(text := ctx.call.args.get(name), str):
            found += [f"{name} has {reason}" for reason in ctx.content.check(text, ctx.principal_id, None, [], ctx.ledger.user_texts).reasons]
    return "; ".join(dict.fromkeys(found)) or None


# check name -> (implementation, the rule parameters it needs)
CHECKS: dict[str, tuple[Callable[[RuleContext], str | None], tuple[str, ...]]] = {
    "known_tool": (_known_tool, ()),
    "schema": (_schema, ()),
    "owned_by_principal": (_owned_by_principal, ("arg", "resource")),
    "equals_principal": (_equals_principal, ("arg",)),
    "equals_principal_attr": (_equals_principal_attr, ("arg", "attr")),
    "forbidden_values": (_forbidden_values, ("arg", "values")),
    "max_from_resource": (_max_from_resource, ("arg", "resource", "resource_arg", "attr")),
    "not_tainted": (_not_tainted, ()),
    "allowed_content": (_allowed_content, ()),
}
TAINT_CHECK = "not_tainted"


class PolicyEngine:
    def __init__(self, registry: ToolRegistry, resolver: ResourceResolver, principal_label: str = "user",
                 taint: bool = True, content: OutputGuard | None = None):
        self.registry, self.resolver, self.principal_label = registry, resolver, principal_label
        self.content = content  # the deterministic output checks, for allowed_content rules
        self.taint = taint  # off only for the ablation that removes taint tracking
        rules = list(registry.global_deny) + [r for t in registry.tools.values() for r in t.policy.deny]
        if missing := {r.check for r in rules} - CHECKS.keys():
            raise ValueError(f"rules that name a check the engine does not have: {sorted(missing)}")
        for rule in rules:
            if absent := [p for p in CHECKS[rule.check][1] if getattr(rule, p) is None]:
                raise ValueError(f"rule {rule.id} ({rule.check}) needs {', '.join(absent)}")
        for name, tool in registry.tools.items():
            if any(r.check == "allowed_content" for r in tool.policy.deny) and not (tool.content_args and content):
                raise ValueError(f"{name}: an allowed_content rule needs content_args and an output checker")

    def evaluate(self, call: ToolCall, principal_id: str, ledger: Ledger) -> PolicyResult:
        spec = self.registry.tools.get(call.tool)

        def run(rule: PolicyRule) -> str | None:
            return CHECKS[rule.check][0](RuleContext(call, spec, rule, principal_id, self.principal_label,
                                                     self.resolver, ledger, self.content))
        for rule in self.registry.global_deny:
            if reason := run(rule):
                return PolicyResult(Verdict.BLOCK, [rule.id], {rule.id: reason})
        violations, checks = {}, set()
        for rule in spec.policy.deny:
            if rule.check == TAINT_CHECK and not self.taint:
                continue
            if reason := run(rule):
                violations[rule.id] = reason
                checks.add(rule.check)
        if violations:
            return PolicyResult(Verdict.BLOCK, list(violations), violations, taint_only=checks == {TAINT_CHECK})
        return PolicyResult(spec.policy.default)
