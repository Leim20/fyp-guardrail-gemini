"""Schemas for what a domain pack declares (guardrail/domain.py): the tool policy (tools.yaml),
the source-trust classes (trust.yaml) and the application profile (domain.yaml)."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    SANITIZE = "SANITIZE"
    ASK_CONFIRM = "ASK_CONFIRM"
    BLOCK = "BLOCK"


class Spec(BaseModel):
    # Reject unknown keys so a typo in a YAML file fails loudly instead of being ignored.
    model_config = ConfigDict(extra="forbid")


class PolicyRule(Spec):
    """One deny rule: the domain's name for it (`id`) and the generic check that implements it
    (`check`, one of the primitives in guardrail/policy.py) with that check's parameters."""
    id: str
    description: str
    check: str
    arg: str | None = None           # the tool argument the check looks at
    args: list[str] | None = None    # not_tainted: the arguments to check (default: all free-valued ones)
    resource: str | None = None      # resource kind the domain's resolver knows, e.g. "order"
    resource_arg: str | None = None  # the argument that names the resource, when it is not `arg`
    attr: str | None = None          # attribute of the principal or of the resource
    values: list[str] | None = None
    cumulative: bool = False         # max_from_resource: count what this session already executed
    message: str | None = None       # reason shown in the audit log; fields {value}, {limit}, {arg}


class ToolPolicy(Spec):
    deny: list[PolicyRule]
    default: Literal[Verdict.ALLOW, Verdict.ASK_CONFIRM]


class ToolSpec(Spec):
    description: str
    effect: Literal["read", "write", "external"]
    risk: Literal["low", "medium", "high"]
    parameters: dict[str, Any]  # JSON Schema, passed to the LLM as the function definition
    # Arguments that carry prose the LLM writes (an email body, a ticket description), as
    # opposed to values such as an order number. Prose that leaves the system is an output
    # channel, so the `allowed_content` check reads it like a reply.
    content_args: list[str] = []
    output_trust: str
    policy: ToolPolicy
    rationale: str


class ToolRegistry(Spec):
    global_deny: list[PolicyRule]
    tools: dict[str, ToolSpec]


class TrustClass(Spec):
    level: int
    can_instruct: bool
    scan: Literal["none", "normal", "strict"]
    taints_arguments: bool
    examples: list[str]
    rationale: str


class TaintSpec(Spec):
    sources: list[str]
    match: str


class TrustConfig(Spec):
    trust_classes: dict[str, TrustClass]
    taint: TaintSpec


class OutputProfile(Spec):
    allowed_domains: list[str]  # hosts a reply may link to, and email domains it may mention


class DomainProfile(Spec):
    """What the engine needs to know about the protected application in words."""
    organisation: str     # e.g. "NovaMart"
    principal: str        # what the authenticated person is called, e.g. "customer"
    assistant: str        # e.g. "NovaMart's customer-support AI assistant"
    app: str              # one-line description of the application for the injection judge
    benign_examples: str  # what ordinary requests and documents look like in this domain
    output: OutputProfile


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_tools(path: Path) -> ToolRegistry:
    return ToolRegistry.model_validate(_read_yaml(path))


def load_trust(path: Path) -> TrustConfig:
    return TrustConfig.model_validate(_read_yaml(path))


def load_profile(path: Path) -> DomainProfile:
    return DomainProfile.model_validate(_read_yaml(path))
