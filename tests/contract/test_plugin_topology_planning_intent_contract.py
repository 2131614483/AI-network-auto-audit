"""Plugin Topology M10 contract tests: graph-driven planning intent schema.

The request root of ``topology-planning-intent.schema.json`` is the single
source of truth for the graph-driven planning entry point: intent text
(1-512 chars), a bounded budget (max_matches 1-16, expand_hops 0-1), a
mandatory idempotency key and a mandatory reason.  The result projection
(``$defs/planningIntentResult``) documents the append-only evidence row the
service persists.  These tests prove positive acceptance plus the three
fail-closed rejection samples: missing idempotency key, out-of-range intent,
and out-of-range budget.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"


def _schema() -> dict[str, object]:
    return json.loads((SCHEMA_DIR / "topology-planning-intent.schema.json").read_text(encoding="utf-8"))


def _result_validator() -> Draft202012Validator:
    schema = _schema()
    registry = Registry().with_resource(
        str(schema["$id"]), Resource.from_contents(schema)
    )
    return Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/planningIntentResult"},
        registry=registry,
    )


def test_planning_intent_schema_is_valid() -> None:
    Draft202012Validator.check_schema(_schema())


def test_planning_intent_positive_sample_passes() -> None:
    Draft202012Validator(_schema()).validate(
        {
            "intent": "总账质量校验并出具量化研究结论",
            "budget": {"max_matches": 4, "expand_hops": 1},
            "idempotency_key": "m10-plan-ledger-quant",
            "reason": "验收演示：图谱驱动组网规划（plan_only）",
        }
    )


def test_planning_intent_missing_idempotency_key_is_rejected() -> None:
    errors = list(
        Draft202012Validator(_schema()).iter_errors(
            {
                "intent": "总账质量校验",
                "budget": {"max_matches": 4, "expand_hops": 1},
                "reason": "缺少幂等键必须拒绝",
            }
        )
    )
    assert errors
    assert any(
        error.validator == "required" and "idempotency_key" in error.message
        for error in errors
    )


def test_planning_intent_out_of_range_intent_is_rejected() -> None:
    validator = Draft202012Validator(_schema())
    long_intent = "总账" * 300  # 600 chars > 512
    errors = list(
        validator.iter_errors(
            {
                "intent": long_intent,
                "budget": {"max_matches": 4, "expand_hops": 1},
                "idempotency_key": "m10-long-intent",
                "reason": "意图越界必须拒绝",
            }
        )
    )
    assert errors
    assert any(error.path[0] == "intent" for error in errors)


def test_planning_intent_out_of_range_budget_is_rejected() -> None:
    validator = Draft202012Validator(_schema())
    # max_matches > 16 and expand_hops > 1 are both rejected
    errors = list(
        validator.iter_errors(
            {
                "intent": "总账质量校验",
                "budget": {"max_matches": 32, "expand_hops": 2},
                "idempotency_key": "m10-budget-oob",
                "reason": "预算越界必须拒绝",
            }
        )
    )
    assert errors
    assert any(list(error.path) == ["budget", "max_matches"] for error in errors)
    assert any(list(error.path) == ["budget", "expand_hops"] for error in errors)


def test_planning_intent_result_projection_passes() -> None:
    _result_validator().validate(
        {
            "intent_id": "11111111-2222-3333-4444-555555555555",
            "intent_text": "总账质量校验并出具量化研究结论",
            "matched_nodes": [
                {
                    "node_key": "capability:audit.ledger.validate",
                    "space_key": "capability-l2",
                    "label": "总账质量校验",
                    "score": 0.42,
                    "match_kind": "direct",
                    "match_source": "trigram",
                },
                {
                    "node_key": "capability:quant.research-note.draft",
                    "space_key": "capability-l2",
                    "label": "量化研究结论",
                    "score": 0.31,
                    "match_kind": "expanded",
                    "match_source": "bridge",
                },
            ],
            "capability_requirements": ["audit.ledger.validate", "quant.research-note.draft"],
            "plan_key": "plan-" + "a" * 16,
            "mode": "plan_only",
            "plan_checksum": "b" * 64,
            "reused_plan": False,
            "node_count": 2,
            "edge_count": 1,
            "created_at": "2026-09-07T12:00:00Z",
            "trace_id": "11111111-2222-3333-4444-555555555555",
            "idempotent": False,
        }
    )


def test_planning_intent_result_rejects_executable_mode() -> None:
    payload = {
        "intent_id": "11111111-2222-3333-4444-555555555555",
        "intent_text": "总账质量校验",
        "matched_nodes": [],
        "capability_requirements": ["audit.ledger.validate"],
        "plan_key": "plan-" + "a" * 16,
        "mode": "execute",
        "plan_checksum": "b" * 64,
        "reused_plan": False,
        "node_count": 1,
        "edge_count": 0,
        "created_at": "2026-09-07T12:00:00Z",
        "trace_id": "11111111-2222-3333-4444-555555555555",
        "idempotent": False,
    }
    with pytest.raises(Exception):
        _result_validator().validate(payload)
