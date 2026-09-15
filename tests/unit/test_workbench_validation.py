"""Workbench validation: two layers, and neither one needs a model.

Layer 1 is structural — every node must be a real plugin in the domain and every
port must be one it actually declares.  Layer 2 is the compiler's own gates.

Layer 1 exists because ``compile_plan`` validates a port against **the node's own
declared ports**, so a hand-edited draft that invents a port would pass the
compiler: the draft declares it, so the compiler sees it.  Comparing against the
plugin directory closes that hole.

The whole point of running validation without a model: a hand-edited draft is
checked immediately and offline.
"""
from __future__ import annotations

import json

import pytest

from packages.ai_planner.workbench import new_draft, validate_draft_full

ISSUE_KEYS = {"code", "message", "node_id", "port_id", "edge_id", "suggested_action"}


@pytest.fixture(scope="module")
def draft() -> dict:
    return new_draft("aiops", "告警处置闭环")


def _issue_codes(report) -> set[str]:
    return {i["code"] for i in report.issues}


# -- the happy path ---------------------------------------------------------

def test_a_composed_draft_validates_and_reports_its_identity(draft: dict) -> None:
    report = validate_draft_full(draft)
    assert report.ok
    assert report.execution_hash and len(report.execution_hash) == 64
    assert report.nodes == len(draft["nodes"])
    assert report.edges == len(draft["edges"])
    assert report.seeds == len(draft["seed_inputs"])


def test_issues_carry_the_stable_renderable_shape(draft: dict) -> None:
    """A UI renders this dict; the keys are the contract."""
    broken = json.loads(json.dumps(draft))
    broken["nodes"][0]["plugin_id"] = "aiops.ghost"
    report = validate_draft_full(broken)
    assert not report.ok
    for issue in report.issues:
        assert set(issue) == ISSUE_KEYS


def test_validation_needs_no_model(draft: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hand-edited draft must be checkable offline, immediately.

    Asserted by *forbidding* the model: the gateway raises if anything reaches
    for it, and validation still succeeds.  (An earlier version of this test
    inspected ``sys.modules``, which is global state another test can pollute —
    it passed alone and failed in the full suite.)
    """

    def _explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("validation must not need a model")

    monkeypatch.setattr("packages.ai.get_chat_client", _explode, raising=False)
    report = validate_draft_full(draft)
    assert report.ok


# -- layer 1: structural ----------------------------------------------------

def test_an_unknown_plugin_is_caught(draft: dict) -> None:
    broken = json.loads(json.dumps(draft))
    broken["nodes"][0]["plugin_id"] = "aiops.ghost"
    report = validate_draft_full(broken)
    assert _issue_codes(report) == {"capability_unavailable"}


def test_an_invented_port_is_caught_even_though_the_node_declares_it(draft: dict) -> None:
    """The case the compiler alone would miss: the draft declares a port no
    plugin has, so `compile_plan` sees it as legitimate."""
    broken = json.loads(json.dumps(draft))
    broken["nodes"][0]["input_ports"].append({
        "port_id": "invented-port", "direction": "input", "schema_ref": "x.schema.json",
        "schema_version": "1.0.0", "schema_sha256": "a" * 64,
        "media_type": "application/json", "required": False,
        "cardinality": "one", "classification": "internal", "transport": "artifact_ref",
    })
    report = validate_draft_full(broken)
    assert not report.ok
    assert _issue_codes(report) == {"contract_mismatch"}
    assert "invented-port" in report.issues[0]["message"]


def test_a_duplicate_node_id_is_caught(draft: dict) -> None:
    broken = json.loads(json.dumps(draft))
    broken["nodes"].append(json.loads(json.dumps(broken["nodes"][0])))
    report = validate_draft_full(broken)
    assert "duplicate_node_instance_id" in _issue_codes(report)


def test_an_unknown_domain_is_reported_not_raised() -> None:
    report = validate_draft_full({"domain": "nope", "nodes": [], "edges": []})
    assert not report.ok
    assert _issue_codes(report) == {"unsupported_feature"}


# -- layer 2: the compiler's gates ------------------------------------------

def test_a_contract_mismatch_edge_is_caught(draft: dict) -> None:
    broken = json.loads(json.dumps(draft))
    broken["edges"].append({
        "edge_id": "bad-001",
        "source_instance": "rca-ranker-001", "source_port": "rca-candidates",
        "target_instance": "ticket-draft-001", "target_port": "incident-proposal",
    })
    report = validate_draft_full(broken)
    assert not report.ok
    assert "contract_mismatch" in _issue_codes(report)


def test_an_unbound_required_input_is_caught(draft: dict) -> None:
    """Dropping a seed without adding an edge leaves a required input dangling —
    the compiler's own gate, reached through the workbench unchanged."""
    broken = json.loads(json.dumps(draft))
    node_id, port_id = broken["seed_inputs"][0]
    broken["seed_inputs"] = [s for s in broken["seed_inputs"] if s != [node_id, port_id]]
    assert not any(e["target_instance"] == node_id and e["target_port"] == port_id
                   for e in broken["edges"])
    report = validate_draft_full(broken)
    assert not report.ok
    assert report.issues


def test_a_cycle_is_caught(draft: dict) -> None:
    broken = json.loads(json.dumps(draft))
    # playbook-proposer -> recovery-verifier already exists; add the reverse so
    # the two close a loop
    broken["edges"].append({
        "edge_id": "cycle-001",
        "source_instance": "recovery-verifier-001", "source_port": "recovery-verification",
        "target_instance": "playbook-proposer-001", "target_port": "rca-candidates",
    })
    report = validate_draft_full(broken)
    assert not report.ok, "the compiler must refuse a cyclic plan"


def test_the_report_shape_is_json_serialisable(draft: dict) -> None:
    payload = validate_draft_full(draft).as_dict()
    assert json.loads(json.dumps(payload))["ok"] is True
