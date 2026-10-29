import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from packages.ai_planner.evidence import AuditTaskBrief


def test_audit_brief_schema_matches_the_runtime_contract():
    schema = json.loads(Path("contracts/jsonschema/audit-task-brief.schema.json").read_text(encoding="utf-8"))
    assert schema == {"$schema": "https://json-schema.org/draft/2020-12/schema", **AuditTaskBrief.model_json_schema()}
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("extra", ["execute", "plugin_id", "tenant_id", "budget", "inputs"])
def test_brain_cannot_override_scope_or_execution_authority(extra):
    schema = json.loads(Path("contracts/jsonschema/audit-task-brief.schema.json").read_text(encoding="utf-8"))
    payload = {"goal_hash": "a" * 64, "knowledge_hash": "b" * 64, "objective": "goal", "scope": "plan only",
               "risk_hypotheses": ["missing basis"], "procedures": [], "blockers": ["need knowledge"], extra: True}
    assert list(Draft202012Validator(schema).iter_errors(payload))
