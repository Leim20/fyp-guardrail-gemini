"""Domain packs: how one engine protects different applications.

The engine (this package) knows nothing about any application. Everything specific to one is
declared in a domain pack, a directory under domains/:

    tools.yaml    the tools, and for each a list of deny rules built from the engine's generic
                  checks (guardrail/policy.py)
    trust.yaml    the content sources and their trust classes
    domain.yaml   the application in words: what it is and what its user is called
    resolver.py   load_resolver() returns a ResourceResolver

The resolver is the one piece of code an application writes. Policy cannot be decided from
text alone: "is O-6666 this customer's order?" needs the application's records. The engine asks
through this interface, so it holds no knowledge of orders, mailboxes or any other record type.
In a deployment the resolver would call the application's own service.

The engine reserves three trust-class names, which every trust.yaml must define: `system` (the
developer's prompt), `user` (the authenticated principal) and `external_untrusted` (what an
item with an unknown class is treated as).
"""

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from guardrail.specs import DomainProfile, ToolRegistry, TrustConfig, load_profile, load_tools, load_trust

DOMAINS_DIR = Path(__file__).resolve().parent.parent / "domains"
RESERVED_TRUST_CLASSES = {"system", "user", "external_untrusted"}


@dataclass(frozen=True)
class ProtectedValue:
    """A value that belongs to someone else and must not appear in a reply to this principal."""
    kind: Literal["email", "phone", "text"]  # how it is matched: address, digits, or wording
    label: str                               # for the reason, e.g. "address"
    value: str


class ResourceResolver(Protocol):
    def principal(self, principal_id: str) -> dict[str, Any] | None:
        """Attributes of the authenticated principal (e.g. their registered email), or None."""

    def resource(self, kind: str, resource_id: Any) -> dict[str, Any] | None:
        """A record of the given kind, with at least `owner` (a principal id), or None."""

    def protected_values(self, principal_id: str) -> list[ProtectedValue]:
        """Other principals' details that a reply to this principal must not contain."""


@dataclass
class DomainPack:
    name: str
    registry: ToolRegistry
    trust: TrustConfig
    profile: DomainProfile
    resolver: ResourceResolver


def load_domain(name: str) -> DomainPack:
    directory = DOMAINS_DIR / name
    if not directory.is_dir():
        raise ValueError(f"no domain pack named {name} in {DOMAINS_DIR}")
    trust = load_trust(directory / "trust.yaml")
    if missing := RESERVED_TRUST_CLASSES - trust.trust_classes.keys():
        raise ValueError(f"{name}/trust.yaml must define the trust classes {sorted(missing)}")
    resolver = importlib.import_module(f"domains.{name}.resolver").load_resolver()
    return DomainPack(name, load_tools(directory / "tools.yaml"), trust, load_profile(directory / "domain.yaml"),
                      resolver)
