"""P1 consistency tests: tool policy, trust classes, mock world and scenarios must agree
with each other, so later iterations can rely on them."""

import jsonschema
import pytest

from agent.mock_world import load_db, load_kb
from evaluation.scenarios import SCENARIO_DIR, load_scenarios
from guardrail.domain import load_domain
from guardrail.specs import Verdict

PACK = load_domain("novamart")
TOOLS = PACK.registry.tools
TRUST = PACK.trust
DB = load_db()
KB = load_kb()
SCENARIOS = load_scenarios()


# --- tool policy (domains/novamart/tools.yaml) ---

@pytest.mark.parametrize("name", TOOLS)
def test_tool_parameters_are_valid_json_schema(name):
    schema = TOOLS[name].parameters
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["type"] == "object"
    assert set(schema["required"]) <= set(schema["properties"])


@pytest.mark.parametrize("name", TOOLS)
def test_tool_policy_is_well_formed(name):
    tool = TOOLS[name]
    rule_ids = [rule.id for rule in tool.policy.deny]
    assert len(rule_ids) == len(set(rule_ids)), "duplicate deny rule id"
    assert tool.rationale.strip()
    assert tool.output_trust in TRUST.trust_classes or tool.output_trust == "per_document"
    if tool.risk == "high":
        assert rule_ids or tool.policy.default == Verdict.ASK_CONFIRM, "high-risk tool with no control"


# --- trust classes (domains/novamart/trust.yaml) ---

def test_only_system_and_user_can_instruct():
    instructing = {name for name, tc in TRUST.trust_classes.items() if tc.can_instruct}
    assert instructing == {"system", "user"}


def test_trust_levels_are_strictly_ordered():
    levels = [TRUST.trust_classes[c].level for c in ("system", "user", "internal_data", "external_untrusted")]
    assert levels == sorted(levels, reverse=True) and len(set(levels)) == len(levels)


def test_taint_sources_taint_arguments():
    for source in TRUST.taint.sources:
        assert TRUST.trust_classes[source].taints_arguments


# --- mock world (data/mock_db, data/kb_docs) ---

def test_orders_belong_to_known_customers():
    for order in DB.orders.values():
        assert order.customer_id in DB.customers


@pytest.mark.parametrize("name", KB)
def test_kb_doc_metadata(name):
    doc = KB[name]
    expected_trust = "internal_data" if doc.source == "official" else "external_untrusted"
    assert doc.trust == expected_trust
    if doc.poisoned:
        assert doc.technique and doc.attack_goal
        assert doc.injection and doc.injection in doc.body, "injection must appear verbatim in the body"
    else:
        assert doc.injection is None and doc.technique is None


# --- scenarios (data/scenarios) ---

def test_scenario_ids_are_unique_and_match_filenames():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert sorted(ids) == sorted(p.stem for p in SCENARIO_DIR.glob("*.json"))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_references_exist(scenario):
    assert scenario.session.customer_id in DB.customers
    if scenario.retrieved_doc:
        doc = KB[scenario.retrieved_doc]
        # Indirect attacks must use a poisoned doc of the same kind; benign ones a clean doc.
        if scenario.category == "indirect":
            assert doc.poisoned
            assert (doc.technique, doc.attack_goal) == (scenario.technique, scenario.attack_goal)
        else:
            assert not doc.poisoned


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_tool_calls_match_tool_schemas(scenario):
    for call in (scenario.benign_goal_tool, scenario.attack_tool_call):
        if call is not None:
            assert call.tool in TOOLS
            jsonschema.validate(call.args, TOOLS[call.tool].parameters)


def test_every_attack_goal_and_technique_is_covered():
    attacks = [s for s in SCENARIOS if s.is_attack]
    goals = {s.attack_goal for s in attacks}
    techniques = {s.technique for s in attacks}
    assert goals == {"refund_fraud", "data_exfiltration", "account_takeover", "cross_customer_access", "prompt_leak"}
    assert techniques == {
        "instruction_override", "authority_claim", "role_play", "urgency", "delimiter_escape", "prompt_leak", "subtle",
    }
