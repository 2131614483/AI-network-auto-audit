from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from packages.ai_planner.evidence import PlanningRequest, SourceQuery


def request_data() -> dict:
    return {
        "goal": "生成抽样方案", "reason": "测试规划",
        "requirements": [{"key": "sampling", "description": "有抽样方案", "output_ports": ["sampling-plan"]}],
        "idempotency_key": "contract-collaborative-1",
    }


def test_published_schema_matches_closed_request() -> None:
    schema = json.loads(Path("contracts/jsonschema/collaborative-planning.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(PlanningRequest.model_validate(request_data()).model_dump(mode="json"))


@pytest.mark.parametrize("extra", [{"mode": "execute"}, {"tenant_id": "foreign"}, {"command": "anything"}])
def test_request_cannot_add_execution_or_tenant_override(extra: dict) -> None:
    with pytest.raises(ValidationError):
        PlanningRequest.model_validate({**request_data(), **extra})


@pytest.mark.parametrize("budget", [{"max_nodes": True}, {"max_rounds": 0}, {"max_queries": 2}])
def test_server_budget_is_closed_and_bounded(budget: dict) -> None:
    with pytest.raises(ValidationError):
        PlanningRequest.model_validate({**request_data(), "budget": budget})


def test_free_text_requirement_without_a_checkable_target_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PlanningRequest.model_validate({**request_data(), "requirements": [{"key": "vague", "description": "做得好"}]})


def test_retrieval_does_not_accept_arbitrary_sql_or_paths() -> None:
    with pytest.raises(ValidationError):
        SourceQuery.model_validate({"source_kind": "knowledge", "query": "抽样", "purpose": "找依据", "sql": "SELECT anything"})
