"""Mock NovaMart tools (P3). They act on a private copy of the mock world and log every side
effect, so an evaluation can tell whether an attack actually happened.

The tools enforce nothing beyond their JSON Schema: ownership, amount limits, recipient checks
and taint all belong to the guard (domains/novamart/tools.yaml, P4). An unguarded run therefore shows the
real harm of an obeyed injection, which is what the no-guard baseline needs to measure.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from agent.mock_world import KBDoc, MockDB, load_db, load_kb
from domains.novamart import DIR as DOMAIN_DIR
from guardrail.interface import Content, ToolCall
from guardrail.specs import ToolRegistry, load_tools

SEARCH_TOP_K = 3


@dataclass
class Effect:
    """One executed tool call. `ok` is the mock's own outcome (e.g. unknown order), which says
    nothing about whether the call should have been allowed."""
    tool: str
    args: dict[str, Any]
    ok: bool
    detail: str


@dataclass
class ToolEnvironment:
    customer_id: str
    db: MockDB = field(default_factory=load_db)
    kb: dict[str, KBDoc] = field(default_factory=load_kb)
    pinned_docs: list[str] | None = None  # scenario runs: search_kb returns exactly these documents
    registry: ToolRegistry = field(default_factory=lambda: load_tools(DOMAIN_DIR / "tools.yaml"))
    effects: list[Effect] = field(default_factory=list)

    def __post_init__(self):
        self.db = self.db.model_copy(deep=True)  # tools mutate the world; never the shared copy
        self.tickets: list[dict] = []
        self.outbox: list[dict] = []

    def definitions(self) -> list[dict]:
        """Tool list in the OpenAI function-calling format."""
        return [{"type": "function", "function": {"name": name, "description": spec.description,
                                                  "parameters": spec.parameters}}
                for name, spec in self.registry.tools.items()]

    def execute(self, call: ToolCall) -> list[Content]:
        spec = self.registry.tools.get(call.tool)
        if spec is None:
            return [self._error(call.tool, f"unknown tool {call.tool}")]
        try:
            jsonschema.validate(call.args, spec.parameters)
        except jsonschema.ValidationError as error:
            return [self._error(call.tool, f"invalid arguments: {error.message}")]
        if call.tool == "search_kb":
            return self._search_kb(**call.args)
        ok, result = getattr(self, f"_{call.tool}")(**call.args)
        self.effects.append(Effect(call.tool, call.args, ok, result if isinstance(result, str) else json.dumps(result)))
        return [Content(json.dumps(result, ensure_ascii=False), call.tool, spec.output_trust)]

    @staticmethod
    def _error(tool: str, message: str) -> Content:
        return Content(json.dumps({"error": message}), tool, "internal_data")

    def _search_kb(self, query: str) -> list[Content]:
        if self.pinned_docs is not None:
            docs = [self.kb[name] for name in self.pinned_docs]
        else:
            docs = list(self.kb.values())
            vectoriser = TfidfVectorizer().fit([f"{d.title} {d.body}" for d in docs] + [query])
            scores = cosine_similarity(vectoriser.transform([query]),
                                       vectoriser.transform([f"{d.title} {d.body}" for d in docs]))[0]
            docs = [docs[i] for i in scores.argsort()[::-1][:SEARCH_TOP_K] if scores[i] > 0]
        self.effects.append(Effect("search_kb", {"query": query}, True, ", ".join(d.name for d in docs)))
        # Each document is a separate item because each carries its own trust class.
        return [Content(f"[{d.title}] ({d.source})\n{d.body}", f"search_kb:{d.name}", d.trust) for d in docs]

    def _get_customer_record(self, customer_id: str) -> tuple[bool, Any]:
        customer = self.db.customers.get(customer_id)
        if customer is None:
            return False, {"error": f"no customer {customer_id}"}
        orders = {oid: o.model_dump(exclude={"customer_id"}) for oid, o in self.db.orders.items()
                  if o.customer_id == customer_id}
        return True, {"customer_id": customer_id, **customer.model_dump(), "orders": orders}

    def _create_ticket(self, subject: str, description: str, priority: str) -> tuple[bool, Any]:
        ticket_id = f"T-{len(self.tickets) + 1:04d}"
        self.tickets.append({"id": ticket_id, "subject": subject, "description": description, "priority": priority})
        return True, {"ticket_id": ticket_id, "status": "open"}

    def _issue_refund(self, order_id: str, amount: float) -> tuple[bool, Any]:
        if order_id not in self.db.orders:
            return False, {"error": f"order {order_id} not found"}
        return True, {"order_id": order_id, "refunded": amount, "status": "refund issued"}

    def _update_account(self, field: str, value: str) -> tuple[bool, Any]:
        customer = self.db.customers[self.customer_id]
        if field in type(customer).model_fields:  # the mock record holds no password
            setattr(customer, field, value)
        return True, {"field": field, "status": "updated"}

    def _send_email(self, to: str, subject: str, body: str) -> tuple[bool, Any]:
        self.outbox.append({"to": to, "subject": subject, "body": body})
        return True, {"to": to, "status": "sent"}
