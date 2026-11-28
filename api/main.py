"""The guard as an HTTP service (FR07).

An application runs its own agent, in any language or framework, and asks the guard at the
three checkpoints of guardrail/interface.py:

    POST   /sessions                    open a session for an authenticated principal
    POST   /sessions/{id}/input         check content before it enters the agent's context
    POST   /sessions/{id}/action        check a tool call the LLM proposed
    POST   /sessions/{id}/outcome       report whether that call ran
    POST   /sessions/{id}/output        check the reply before the user sees it
    DELETE /sessions/{id}               close the session
    GET    /policy                      the domain pack in force: tools, rules, trust classes
    GET    /health

The guard is stateful: what counts as tainted, the canary and the cumulative limits belong to
a session. Sessions live in this process's memory and expire after a period of inactivity.
Several instances behind a load balancer would need a shared store; that is out of scope.

Decision and enforcement are separate. This service decides (a policy decision point); the
application enforces (the enforcement point), and nothing here can stop an application that
ignores a verdict. Two things narrow that gap:

  - A verdict of ASK_CONFIRM comes with a confirmation token bound to the exact call (tool and
    arguments) and valid for a few minutes. Reporting that the call ran requires that token.
    An approval for one call cannot be spent on another, replayed, or used after it has gone
    stale (OWASP AI Agent Security Cheat Sheet: bind approvals to the exact action, with expiry).
  - The outcome report is checked against the decision. A call that ran after BLOCK, without
    having been checked, or without its confirmation is written to the audit log as a
    violation and returned to the caller.

If a check fails inside the guard, the answer is BLOCK (fail closed), not an HTTP error that
an application might treat as "no objection".

Not included: authentication of the calling application, rate limiting and TLS. The service is
meant to sit next to the application on a private network; who the principal is must come
from the application's own authentication, never from conversation text.

Usage:
    uv run uvicorn api.main:app --port 8000        # interactive documentation at /docs
"""

import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from guardrail.guard import HybridGuard, load_guard
from guardrail.interface import Content, Decision, Guard, GuardSession, ToolCall
from guardrail.specs import Verdict

SESSION_TTL = 30 * 60        # seconds without a request before a session is dropped
CONFIRMATION_TTL = 5 * 60    # seconds a confirmation token stays valid


# ---------- request and response bodies ----------

class OpenSession(BaseModel):
    principal_id: str = Field(description="The authenticated person the agent acts for, from the application's own login.")
    session_id: str | None = Field(default=None, description="Chosen by the caller; generated when omitted.")


class SessionOpened(BaseModel):
    session_id: str
    system_prompt_addendum: str = Field(description="Append this to the agent's system prompt (data markers, canary).")
    guard: str
    domain: str | None


class CheckInput(BaseModel):
    text: str
    source: str = Field(description='Where the text came from: "user", a tool name, or "search_kb:<document>".')
    trust: str = Field(description="A trust class of the domain pack, e.g. user, internal_data, external_untrusted.")


class CheckAction(BaseModel):
    tool: str
    args: dict[str, Any]


class Outcome(BaseModel):
    tool: str
    args: dict[str, Any]
    executed: bool
    confirmation_token: str | None = Field(default=None, description="Required when the call ran after ASK_CONFIRM.")


class CheckOutput(BaseModel):
    text: str


class Confirmation(BaseModel):
    token: str
    expires_in: int = Field(description="Seconds until the token is no longer accepted.")


class DecisionOut(BaseModel):
    verdict: Verdict
    text: str | None = Field(default=None, description="For input and output: the text to use instead of the original.")
    reasons: list[str]
    layer: str | None = Field(default=None, description="The layer that decided.")
    confirmation: Confirmation | None = Field(default=None, description="Present when the verdict is ASK_CONFIRM.")


class OutcomeRecorded(BaseModel):
    recorded: bool = True
    violation: str | None = Field(default=None, description="Set when the outcome contradicts the guard's decision.")


# ---------- sessions ----------

@dataclass
class Entry:
    session: GuardSession
    lock: threading.Lock = field(default_factory=threading.Lock)   # a guard session is not thread-safe
    last_used: float = field(default_factory=time.monotonic)
    verdicts: dict[str, Verdict] = field(default_factory=dict)     # call key -> the last verdict for it
    confirmations: dict[str, tuple[str, float]] = field(default_factory=dict)  # token -> (call key, expiry)


class Sessions:
    def __init__(self, ttl: float):
        self.ttl, self.entries, self.lock = ttl, {}, threading.Lock()

    def open(self, session_id: str, session: GuardSession) -> Entry:
        with self.lock:
            self._expire()
            if session_id in self.entries:
                raise HTTPException(409, f"session {session_id} already exists")
            self.entries[session_id] = Entry(session)
            return self.entries[session_id]

    def get(self, session_id: str) -> Entry:
        with self.lock:
            self._expire()
            if session_id not in self.entries:
                raise HTTPException(404, f"no session {session_id}: never opened, closed or expired")
            entry = self.entries[session_id]
            entry.last_used = time.monotonic()
            return entry

    def close(self, session_id: str) -> None:
        with self.lock:
            if self.entries.pop(session_id, None) is None:
                raise HTTPException(404, f"no session {session_id}")

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.ttl
        for session_id in [s for s, e in self.entries.items() if e.last_used < cutoff]:
            del self.entries[session_id]


# ---------- the application ----------

def create_app(guard: Guard | None = None, session_ttl: float = SESSION_TTL,
               confirmation_ttl: float = CONFIRMATION_TTL) -> FastAPI:
    """`guard`: the guard to serve. Left out, the full guard is loaded when the server starts
    (not at import, so importing this module is cheap and needs no model or API key)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.guard = guard or load_guard("full")
        yield

    app = FastAPI(title="Guardrail", summary="A hybrid guardrail against prompt injection, as middleware for LLM agents.",
                  version="0.1.0", lifespan=lifespan)
    sessions = Sessions(session_ttl)

    def checked(entry: Entry, checkpoint: str, check) -> Decision:
        """Run one check under the session's lock. A failure inside the guard becomes BLOCK."""
        with entry.lock:
            try:
                return check()
            except Exception as error:  # noqa: BLE001 - whatever went wrong, the safe answer is no
                reason = f"guard error: {type(error).__name__}"
                if isinstance(app.state.guard, HybridGuard):
                    app.state.guard.audit.write(guard=app.state.guard.name, session=getattr(entry.session, "session_id", None),
                                                principal=getattr(entry.session, "principal_id", None),
                                                checkpoint=checkpoint, verdict="BLOCK", layer="error", reasons=[reason],
                                                latency_ms=0.0)
                return Decision(Verdict.BLOCK, None, [reason], layer="error")

    def answer(decision: Decision, confirmation: Confirmation | None = None) -> DecisionOut:
        return DecisionOut(verdict=decision.verdict, text=decision.text, reasons=decision.reasons, layer=decision.layer,
                           confirmation=confirmation)

    @app.post("/sessions", status_code=201)
    def open_session(body: OpenSession) -> SessionOpened:
        session_id = body.session_id or uuid.uuid4().hex
        entry = sessions.open(session_id, app.state.guard.open_session(body.principal_id, session_id))
        domain = getattr(app.state.guard, "domain", None)
        return SessionOpened(session_id=session_id, system_prompt_addendum=entry.session.system_prompt_addendum(),
                             guard=app.state.guard.name, domain=domain.name if domain else None)

    @app.post("/sessions/{session_id}/input")
    def check_input(session_id: str, body: CheckInput) -> DecisionOut:
        entry = sessions.get(session_id)
        return answer(checked(entry, "input", lambda: entry.session.check_input(Content(body.text, body.source, body.trust))))

    @app.post("/sessions/{session_id}/action")
    def check_action(session_id: str, body: CheckAction) -> DecisionOut:
        entry, call = sessions.get(session_id), ToolCall(body.tool, body.args)
        decision = checked(entry, "action", lambda: entry.session.check_action(call))
        confirmation = None
        with entry.lock:
            entry.verdicts[call.key()] = decision.verdict
            if decision.verdict == Verdict.ASK_CONFIRM:
                token = secrets.token_urlsafe(16)
                entry.confirmations[token] = (call.key(), time.monotonic() + confirmation_ttl)
                confirmation = Confirmation(token=token, expires_in=int(confirmation_ttl))
        return answer(decision, confirmation)

    @app.post("/sessions/{session_id}/outcome")
    def record_outcome(session_id: str, body: Outcome) -> OutcomeRecorded:
        entry, call = sessions.get(session_id), ToolCall(body.tool, body.args)
        with entry.lock:
            violation = entry.session.record_outcome(call, body.executed)
            verdict = entry.verdicts.pop(call.key(), None)   # one check covers one execution
            if body.executed and verdict == Verdict.ASK_CONFIRM:
                key, expiry = entry.confirmations.pop(body.confirmation_token or "", (None, 0.0))   # single use
                if key != call.key() or expiry < time.monotonic():
                    violation = violation or "executed without a valid confirmation for this call"
                    if isinstance(app.state.guard, HybridGuard):
                        app.state.guard.audit.write(guard=app.state.guard.name, session=session_id, checkpoint="outcome",
                                                    verdict=None, layer="api", reasons=[violation], latency_ms=0.0,
                                                    tool=call.tool, executed=True)
        return OutcomeRecorded(violation=violation)

    @app.post("/sessions/{session_id}/output")
    def check_output(session_id: str, body: CheckOutput) -> DecisionOut:
        entry = sessions.get(session_id)
        return answer(checked(entry, "output", lambda: entry.session.check_output(body.text)))

    @app.delete("/sessions/{session_id}", status_code=204)
    def close_session(session_id: str) -> None:
        sessions.close(session_id)

    @app.get("/policy")
    def policy() -> dict[str, Any]:
        """What this deployment enforces, read from the domain pack, not from code."""
        domain = getattr(app.state.guard, "domain", None)
        if domain is None:
            return {"guard": app.state.guard.name, "domain": None}
        rule = lambda r: {"id": r.id, "check": r.check, "description": r.description}  # noqa: E731
        return {
            "guard": app.state.guard.name, "domain": domain.name,
            "global_deny": [rule(r) for r in domain.registry.global_deny],
            "tools": {name: {"description": tool.description, "effect": tool.effect, "risk": tool.risk,
                             "default": tool.policy.default, "deny": [rule(r) for r in tool.policy.deny],
                             "content_args": tool.content_args}
                      for name, tool in domain.registry.tools.items()},
            "trust_classes": {name: {"can_instruct": tc.can_instruct, "scan": tc.scan, "taints_arguments": tc.taints_arguments}
                              for name, tc in domain.trust.trust_classes.items()},
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "guard": app.state.guard.name, "sessions": len(sessions.entries)}

    return app


app = create_app()
