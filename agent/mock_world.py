"""Synthetic NovaMart world used by the mock tools: the customer database and the
help-centre / community documents returned by search_kb."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Customer(Record):
    name: str
    email: str
    phone: str
    address: str
    role: str


class Order(Record):
    customer_id: str
    item: str
    total: float
    status: str


class MockDB(Record):
    customers: dict[str, Customer]
    orders: dict[str, Order]


class KBDoc(Record):
    name: str
    title: str
    source: Literal["official", "forum", "review"]
    trust: str
    poisoned: bool
    technique: str | None = None
    attack_goal: str | None = None
    injection: str | None = None  # exact injected text, the ground truth for SANITIZE
    body: str


def load_db(path: Path = DATA_DIR / "mock_db" / "customers.json") -> MockDB:
    return MockDB.model_validate_json(path.read_text(encoding="utf-8"))


def parse_kb_doc(path: Path) -> KBDoc:
    """Split a markdown file into its YAML front-matter and body."""
    _, front_matter, body = path.read_text(encoding="utf-8").split("---\n", 2)
    return KBDoc(name=path.name, body=body.strip(), **yaml.safe_load(front_matter))


def load_kb(kb_dir: Path = DATA_DIR / "kb_docs") -> dict[str, KBDoc]:
    return {p.name: parse_kb_doc(p) for p in sorted(kb_dir.glob("*.md"))}
