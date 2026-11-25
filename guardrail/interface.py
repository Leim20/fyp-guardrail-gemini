"""Contract between an agent and the guardrail (FR07).

The agent stops at three checkpoints and asks the guard; it contains no defence logic itself.
The guard is opened once per conversation for one authenticated principal (the person the
agent acts for: a customer, an employee, a mailbox owner), so it can keep per-session state such as the
untrusted content seen so far (taint tracking) or a canary token placed in the system prompt.

    checkpoint        agent calls                     guard returns
    content in        session.check_input(item)       verdict + the text to put in context
    tool call         session.check_action(call)      verdict (+ reasons)
    reply out         session.check_output(text)      verdict + the text to show the user
    after a tool      session.record_outcome(call, executed)   nothing

The guard decides; the agent enforces. A verdict of ASK_CONFIRM means the guard does not know
whether the call will run, so after each checked call the agent reports the outcome. Rules that
need memory, such as a cumulative limit, count only calls that were reported as executed.
The report also lets the guard notice when its decision was not enforced: record_outcome
returns a description of the violation (a call that ran after BLOCK, or was never checked),
or None.

Because the agent only depends on this contract, the same guard can protect any agent and
LLM, run in-process or behind the FastAPI middleware (api/), and be swapped for NoGuard to
measure the unguarded baseline.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from guardrail.specs import Verdict


@dataclass(frozen=True)
class Content:
    """One item entering the agent context."""
    text: str
    source: str  # "user", a tool name, or "search_kb:<document>"
    trust: str   # trust class from the domain's trust.yaml


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, Any]
    call_id: str = ""

    def key(self) -> str:
        """Identifies this exact call (tool and arguments), to match a decision, a confirmation
        and an outcome to one another."""
        return json.dumps([self.tool, self.args], sort_keys=True, default=str)


@dataclass
class Decision:
    verdict: Verdict
    text: str | None = None  # for inputs and outputs: what the agent should use instead
    reasons: list[str] = field(default_factory=list)
    layer: str | None = None  # which layer decided, for the audit log


class GuardSession(Protocol):
    principal_id: str

    def system_prompt_addendum(self) -> str: ...
    def check_input(self, item: Content) -> Decision: ...
    def check_action(self, call: ToolCall) -> Decision: ...
    def check_output(self, text: str) -> Decision: ...
    def record_outcome(self, call: ToolCall, executed: bool) -> str | None: ...


class Guard(Protocol):
    name: str

    def open_session(self, principal_id: str, session_id: str) -> GuardSession: ...


class NoGuardSession:
    def __init__(self, principal_id: str):
        self.principal_id = principal_id

    def system_prompt_addendum(self) -> str:
        return ""

    def check_input(self, item: Content) -> Decision:
        return Decision(Verdict.ALLOW, item.text)

    def check_action(self, call: ToolCall) -> Decision:
        return Decision(Verdict.ALLOW)

    def check_output(self, text: str) -> Decision:
        return Decision(Verdict.ALLOW, text)

    def record_outcome(self, call: ToolCall, executed: bool) -> str | None:
        return None


class NoGuard:
    """Lets everything through: the unguarded baseline."""
    name = "none"

    def open_session(self, principal_id: str, session_id: str) -> NoGuardSession:
        return NoGuardSession(principal_id)
