"""Rule scanner, the first layer of the input guard: fast, explainable regular expressions
for known prompt-injection phrasings (config/rules.yaml)."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "rules.yaml"
FLAG_THRESHOLD = 0.5  # rule-only baseline decision threshold


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    weight: float
    pattern: re.Pattern
    description: str


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    category: str
    weight: float
    start: int
    end: int
    text: str


class RuleScanner:
    def __init__(self, rules: list[Rule]):
        self.rules = rules

    @classmethod
    def from_yaml(cls, path: Path = RULES_PATH) -> "RuleScanner":
        with path.open(encoding="utf-8") as f:
            entries = yaml.safe_load(f)["rules"]
        rules = [
            Rule(
                id=e["id"], category=e["category"], weight=float(e["weight"]),
                pattern=re.compile(e["pattern"], re.IGNORECASE), description=e["description"],
            )
            for e in entries
        ]
        return cls(rules)

    def scan(self, text: str) -> list[RuleMatch]:
        """Every match of every rule, with character spans (used later for SANITIZE)."""
        return [
            RuleMatch(rule.id, rule.category, rule.weight, m.start(), m.end(), m.group(0))
            for rule in self.rules
            for m in rule.pattern.finditer(text)
        ]

    def score(self, text: str) -> float:
        """Noisy-OR of the weights of the distinct rules that matched (0 when none did)."""
        weights = {m.rule_id: m.weight for m in self.scan(text)}
        miss = 1.0
        for weight in weights.values():
            miss *= 1.0 - weight
        return 1.0 - miss

    def predict(self, text: str) -> bool:
        return self.score(text) >= FLAG_THRESHOLD
