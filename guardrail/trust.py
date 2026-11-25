"""Source trust and taint tracking (FR02; a domain's trust.yaml).

Every item that enters the agent context is recorded in a per-session ledger with its trust
class. At the action checkpoint the ledger answers one question: was this argument value copied
from content that may not give instructions?

Rule (trust.yaml `taint.match`): a value is tainted when it appears in content from a taint
source (external_untrusted) and the customer never typed it. Two refinements, both stated in the
report:
  - Only free-valued arguments are checked. An enum argument (update_account.field, ticket
    priority) is a choice among fixed options, not data copied from somewhere; otherwise the
    word "phone" in a forum post would taint every phone change.
  - Values with three or more digits are also matched on their digits alone, so reformatting
    "+60 11-6666 6666" as "+60116666666" does not wash the taint off. Shorter numbers are
    matched as whole tokens only, because "50" occurs in too many texts by chance.
  - A long number also matches when one form is the tail of the other: a post gives a phone
    number in national form (011 5555 0188) and the LLM writes it in international form
    (+60 11-5555 0188). Leading zeros are dropped and at least seven digits must agree.
  - A rule may name the arguments it checks. Taint is for arguments that pick the target or
    carry a new value (an order number, a phone number). A quantity that another rule already
    bounds, such as a refund amount capped by the order total, is left out: the same number
    turns up in forum posts by chance ("I got my RM 120 back"), and a lexical match cannot tell
    that the amount came from the order record.

This is a lexical approximation of data flow: the guard cannot see inside the LLM, so it treats
"the same value appears in untrusted content and nowhere the customer wrote" as evidence that the
value came from there. It cannot catch a value the LLM paraphrases beyond recognition (a known
limitation), and it never needs the text classifier to have noticed the injection.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from guardrail.interface import Content, ToolCall
from guardrail.specs import TrustConfig

MIN_DIGITS = 3
MIN_TAIL_DIGITS = 7  # shortest tail that identifies a phone number without its country code
DIGIT_RUN = re.compile(r"\+?\d[\d\s\-().]*\d|\d")


def normalise(text: str) -> str:
    return " ".join(str(text).lower().split())


def as_text(value: Any) -> str:
    """Argument value as it would be written: 500.0 -> "500"."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return normalise(value)


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def same_number(a: str, b: str) -> bool:
    """Two digit strings are the same number, allowing for a country code or trunk zero."""
    if a == b:
        return True
    short, long = sorted((a.lstrip("0"), b.lstrip("0")), key=len)
    return len(short) >= MIN_TAIL_DIGITS and long.endswith(short)


def appears_in(value: str, texts: list[str]) -> bool:
    if not value:
        return False
    token = re.compile(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])")
    if any(token.search(t) for t in texts):
        return True
    value_digits = digits(value)
    if len(value_digits) < MIN_DIGITS:
        return False
    return any(same_number(digits(run), value_digits) for t in texts for run in DIGIT_RUN.findall(t))


@dataclass
class Ledger:
    """What entered one session's context, by trust class."""
    trust: TrustConfig
    user_texts: list[str] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)  # as typed, for the alignment judge
    taint_texts: list[tuple[str, str]] = field(default_factory=list)  # (source, normalised text)
    flagged_sources: list[str] = field(default_factory=list)  # items the input guard flagged
    executed: list[ToolCall] = field(default_factory=list)  # calls the agent reported as run

    def record(self, item: Content, flagged: bool = False) -> None:
        if item.trust == "user":
            self.user_texts.append(normalise(item.text))
            self.user_messages.append(item.text)
        if item.trust in self.trust.taint.sources:
            # The raw text is kept even if the guard sanitised it: a value from a removed
            # sentence is just as untrusted if the LLM already saw it or guesses it.
            self.taint_texts.append((item.source, normalise(item.text)))
        if flagged:
            self.flagged_sources.append(item.source)

    def can_instruct(self, trust_class: str) -> bool:
        return self.trust.trust_classes[trust_class].can_instruct

    def tainted_arguments(self, args: dict[str, Any], schema: dict[str, Any],
                          only: list[str] | None = None) -> dict[str, str]:
        """{argument: source it was copied from} for every tainted free-valued argument, or for
        those named in `only`."""
        properties = schema.get("properties", {})
        found = {}
        for name, value in args.items():
            if only is not None and name not in only:
                continue
            if "enum" in properties.get(name, {}) or isinstance(value, (bool, dict, list)) or value is None:
                continue
            text = as_text(value)
            if appears_in(text, self.user_texts):
                continue
            for source, untrusted in self.taint_texts:
                if appears_in(text, [untrusted]):
                    found[name] = source
                    break
        return found
