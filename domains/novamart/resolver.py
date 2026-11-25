"""NovaMart's answers to the engine's questions about its records (guardrail/domain.py,
ResourceResolver). Here the records are the mock database; a deployed application would query
its own system of record instead."""

from typing import Any

from agent.mock_world import MockDB, load_db
from guardrail.domain import ProtectedValue


class NovaMartResolver:
    def __init__(self, db: MockDB | None = None):
        self.db = db or load_db()

    def principal(self, principal_id: str) -> dict[str, Any] | None:
        customer = self.db.customers.get(principal_id)
        return customer.model_dump() if customer else None

    def resource(self, kind: str, resource_id: Any) -> dict[str, Any] | None:
        if kind != "order":
            return None
        order = self.db.orders.get(resource_id)
        return {"owner": order.customer_id, **order.model_dump(exclude={"customer_id"})} if order else None

    def protected_values(self, principal_id: str) -> list[ProtectedValue]:
        """Contact details of every other customer: a reply to this customer must not contain them."""
        values = []
        for customer_id, customer in self.db.customers.items():
            if customer_id != principal_id:
                values += [ProtectedValue("email", "email", customer.email),
                           ProtectedValue("phone", "phone", customer.phone),
                           ProtectedValue("text", "address", customer.address)]
        return values


def load_resolver() -> NovaMartResolver:
    return NovaMartResolver()
