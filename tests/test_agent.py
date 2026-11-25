"""Agent loop, mock tools and record/replay, with a scripted LLM instead of an API."""

import json

import pytest

from agent.llm import AssistantTurn, Recorded
from agent.loop import REFUSAL, Conversation
from agent.tools import ToolEnvironment
from guardrail.interface import Decision, NoGuardSession
from guardrail.specs import Verdict


class Scripted:
    """Returns the given turns in order and records what it was sent."""
    name, model = "scripted", "scripted-1"

    def __init__(self, *turns: AssistantTurn):
        self.turns, self.calls = list(turns), []

    def complete(self, messages, tools):
        self.calls.append(json.loads(json.dumps(messages)))
        return self.turns.pop(0)


def call(name: str, args: dict | str, call_id: str = "c1") -> AssistantTurn:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return AssistantTurn(None, [{"id": call_id, "name": name, "arguments": arguments}])


def text(content: str) -> AssistantTurn:
    return AssistantTurn(content)


class StubSession(NoGuardSession):
    """Returns a fixed verdict for one tool and lets everything else through."""

    def __init__(self, customer_id, tool, verdict):
        super().__init__(customer_id)
        self.tool, self.verdict = tool, verdict

    def check_action(self, call):
        return Decision(self.verdict if call.tool == self.tool else Verdict.ALLOW, reasons=["stub"])


class StubGuard:
    name = "stub"

    def __init__(self, tool, verdict):
        self.tool, self.verdict = tool, verdict

    def open_session(self, customer_id, session_id):
        return StubSession(customer_id, self.tool, self.verdict)


def conversation(backend, guard=None, **kwargs) -> Conversation:
    env = ToolEnvironment("C1001")
    return Conversation(backend, env, "test", **({"guard": guard} if guard else {}), **kwargs)


def test_unguarded_tool_call_runs_and_is_logged():
    convo = conversation(Scripted(call("issue_refund", {"order_id": "O-1001", "amount": 120}), text("Done.")))
    assert convo.send("Refund O-1001 please") == "Done."
    assert [(e.tool, e.args["order_id"], e.ok) for e in convo.env.effects] == [("issue_refund", "O-1001", True)]
    assert [e["type"] for e in convo.events] == ["input_check", "tool_call", "input_check", "tool_result",
                                                 "output_check", "reply"]


def test_blocked_call_never_reaches_the_tool():
    backend = Scripted(call("issue_refund", {"order_id": "O-6666", "amount": 500}), text("I can't do that."))
    convo = conversation(backend, StubGuard("issue_refund", Verdict.BLOCK))
    convo.send("refund")
    assert convo.env.effects == []
    tool_message = backend.calls[1][-1]
    assert tool_message["role"] == "tool" and "blocked by security policy" in tool_message["content"]


@pytest.mark.parametrize("approved, executed", [(False, 0), (True, 1)])
def test_ask_confirm_runs_only_when_the_customer_approves(approved, executed):
    backend = Scripted(call("update_account", {"field": "address", "value": "12 Jalan Ampang"}), text("ok"))
    convo = conversation(backend, StubGuard("update_account", Verdict.ASK_CONFIRM), confirm=lambda c: approved)
    convo.send("change my address")
    assert len(convo.env.effects) == executed
    assert convo.env.db.customers["C1001"].address == ("12 Jalan Ampang" if approved else "8 Jalan Bukit Bintang, 55100 Kuala Lumpur")


def test_malformed_arguments_are_rejected_by_the_schema():
    backend = Scripted(call("issue_refund", '{"order_id": "1001"'), call("issue_refund", {"order_id": "1001", "amount": 5}),
                       text("Sorry."))
    convo = conversation(backend)
    convo.send("refund")
    assert convo.env.effects == []
    assert "invalid arguments" in backend.calls[2][-1]["content"]


def test_tools_mutate_a_private_copy_of_the_world():
    first, second = ToolEnvironment("C1001"), ToolEnvironment("C1001")
    first._update_account("phone", "+60 1")
    assert second.db.customers["C1001"].phone != "+60 1"


def test_search_returns_one_item_per_document_with_its_trust_class():
    pinned = ToolEnvironment("C1001", pinned_docs=["forum_refund_tips.md"])._search_kb("anything")
    assert [(c.source, c.trust) for c in pinned] == [("search_kb:forum_refund_tips.md", "external_untrusted")]
    found = ToolEnvironment("C1001")._search_kb("refund policy")
    assert found and found[0].source == "search_kb:official_refund_policy.md"


def test_guard_can_block_the_user_message_before_the_llm_sees_it():
    class BlockInput(NoGuardSession):
        def check_input(self, item):
            return Decision(Verdict.BLOCK, reasons=["stub"])

    class Guard:
        name = "block-input"

        def open_session(self, customer_id, session_id):
            return BlockInput(customer_id)

    backend = Scripted()
    assert conversation(backend, Guard()).send("hello") == REFUSAL
    assert backend.calls == []


def test_recorded_backend_caches_and_replays(tmp_path):
    inner = Scripted(text("first"))
    recorded = Recorded(inner, "cache", tmp_path)
    messages = [{"role": "user", "content": "hi"}]
    assert recorded.complete(messages, []).content == "first"
    assert recorded.complete(messages, []).content == "first"  # served from the recording
    assert len(inner.calls) == 1
    replay = Recorded(Scripted(), "replay", tmp_path)
    assert replay.complete(messages, []).content == "first"
    with pytest.raises(LookupError):
        replay.complete([{"role": "user", "content": "never recorded"}], [])
