"""The guard over HTTP, behind the same interface as the in-process guard.

    in process:   guard = load_guard("full")
    over HTTP:    guard = RemoteGuard(httpx.Client(base_url="http://localhost:8000"))

Either way an agent calls open_session, check_input, check_action, record_outcome and
check_output (guardrail/interface.py), so the NovaMart agent, the scenario replays and the
tests run unchanged against the service. An application in another language makes the same
five requests itself; this file is the reference for what they are.
"""

from typing import Any

from guardrail.interface import Content, Decision, ToolCall
from guardrail.specs import Verdict


def _decision(body: dict[str, Any]) -> Decision:
    return Decision(Verdict(body["verdict"]), body["text"], body["reasons"], body["layer"])


class RemoteSession:
    def __init__(self, client, principal_id: str, session_id: str):
        self.client, self.principal_id = client, principal_id
        response = client.post("/sessions", json={"principal_id": principal_id, "session_id": session_id})
        response.raise_for_status()
        opened = response.json()
        self.session_id, self._addendum = opened["session_id"], opened["system_prompt_addendum"]
        self._tokens: dict[str, str] = {}   # call key -> the confirmation token issued for it

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(f"/sessions/{self.session_id}/{path}", json=body)
        response.raise_for_status()
        return response.json()

    def system_prompt_addendum(self) -> str:
        return self._addendum

    def check_input(self, item: Content) -> Decision:
        return _decision(self._post("input", {"text": item.text, "source": item.source, "trust": item.trust}))

    def check_action(self, call: ToolCall) -> Decision:
        body = self._post("action", {"tool": call.tool, "args": call.args})
        if body["confirmation"]:
            self._tokens[call.key()] = body["confirmation"]["token"]
        return _decision(body)

    def record_outcome(self, call: ToolCall, executed: bool) -> str | None:
        """The agent reaches this with executed=True only after the person confirmed, so
        presenting the token is the application's statement that this exact call was approved."""
        token = self._tokens.pop(call.key(), None) if executed else None
        return self._post("outcome", {"tool": call.tool, "args": call.args, "executed": executed,
                                      "confirmation_token": token})["violation"]

    def check_output(self, text: str) -> Decision:
        return _decision(self._post("output", {"text": text}))

    def close(self) -> None:
        self.client.delete(f"/sessions/{self.session_id}")


class RemoteGuard:
    name = "remote"

    def __init__(self, client):
        """`client`: an httpx.Client with base_url set to the service (or FastAPI's TestClient)."""
        self.client = client

    def open_session(self, principal_id: str, session_id: str) -> RemoteSession:
        return RemoteSession(self.client, principal_id, session_id)
