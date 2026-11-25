"""Agent test scenarios (P1): schema, loader and coverage report.

Each scenario is one JSON file in data/scenarios/. The expected verdicts are the ground
truth for the evaluation harness (P5):
  - expected.user_message / expected.retrieved_doc  -> input guard (FR01)
  - expected.benign_tool   -> verdict for the legitimate tool call (benign utility)
  - expected.attack_tool   -> verdict for the attacker's intended tool call; also used in
                              the forced-compromise test, which sends attack_tool_call straight
                              to the action guard as if the LLM had fully obeyed the injection

Usage:
    uv run python -m evaluation.scenarios     # print the coverage table
"""

from pathlib import Path
from typing import Any, Literal, Self

import pandas as pd
from pydantic import BaseModel, ConfigDict, model_validator

from guardrail.specs import Verdict

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "data" / "scenarios"

Category = Literal["benign", "benign_hard", "direct", "indirect"]
AttackGoal = Literal[
    "refund_fraud", "data_exfiltration", "account_takeover", "cross_customer_access", "prompt_leak"
]
Technique = Literal[
    "instruction_override", "authority_claim", "role_play", "urgency", "delimiter_escape",
    "prompt_leak", "subtle",
]
ID_PREFIX = {"benign": "BEN", "benign_hard": "BEN", "direct": "DIR", "indirect": "IND"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(Strict):
    tool: str
    args: dict[str, Any]


class Session(Strict):
    customer_id: str


class Expected(Strict):
    user_message: Verdict
    retrieved_doc: Verdict | None = None
    benign_tool: Verdict | None = None
    attack_tool: Verdict | None = None


class Scenario(Strict):
    id: str
    category: Category
    attack_goal: AttackGoal | None = None
    technique: Technique | None = None
    session: Session
    user_message: str
    retrieved_doc: str | None = None
    benign_goal_tool: ToolCall | None = None
    attack_tool_call: ToolCall | None = None
    expected: Expected
    notes: str

    @property
    def is_attack(self) -> bool:
        return self.category in ("direct", "indirect")

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        if not self.id.startswith(ID_PREFIX[self.category] + "-"):
            raise ValueError(f"id {self.id} should start with {ID_PREFIX[self.category]}-")
        if self.is_attack:
            if self.attack_goal is None or self.technique is None:
                raise ValueError("attack scenarios need attack_goal and technique")
            # Prompt leaking has no tool call; it is judged on the agent's reply in P5.
            if self.attack_tool_call is None and self.attack_goal != "prompt_leak":
                raise ValueError("attack scenarios need attack_tool_call")
        elif self.attack_goal or self.technique or self.attack_tool_call:
            raise ValueError("benign scenarios must not set attack fields")
        if self.category == "indirect" and self.retrieved_doc is None:
            raise ValueError("indirect scenarios need retrieved_doc")
        pairs = [
            ("retrieved_doc", self.retrieved_doc, self.expected.retrieved_doc),
            ("benign_goal_tool", self.benign_goal_tool, self.expected.benign_tool),
            ("attack_tool_call", self.attack_tool_call, self.expected.attack_tool),
        ]
        for name, value, verdict in pairs:
            if (value is None) != (verdict is None):
                raise ValueError(f"{name} and its expected verdict must both be set or both be null")
        return self


def load_scenarios(scenario_dir: Path = SCENARIO_DIR) -> list[Scenario]:
    return [
        Scenario.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(scenario_dir.glob("*.json"))
    ]


def coverage_table(scenarios: list[Scenario]) -> tuple[pd.Series, pd.DataFrame]:
    """Scenario counts per category, and attack goal x technique for attack scenarios."""
    df = pd.DataFrame([s.model_dump(include={"category", "attack_goal", "technique"}) for s in scenarios])
    attacks = df[df["category"].isin(["direct", "indirect"])]
    return df["category"].value_counts(), pd.crosstab(attacks["attack_goal"], attacks["technique"])


if __name__ == "__main__":
    scenarios = load_scenarios()
    by_category, goal_by_technique = coverage_table(scenarios)
    print(f"{len(scenarios)} scenarios\n")
    print(by_category.to_string(), "\n")
    print(goal_by_technique.to_string())
