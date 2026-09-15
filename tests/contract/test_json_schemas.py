from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"


def _load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_all_schemas_are_valid_draft_2020_12_documents() -> None:
    schemas = list(SCHEMA_DIR.glob("*.json"))
    assert len(schemas) >= 10
    for path in schemas:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def test_core_schema_contracts_have_required_fields() -> None:
    expected = {
        "artifact-ref.schema.json": {"artifact_id", "tenant_id", "media_type", "sha256", "size_bytes"},
        "mission-spec.schema.json": {"title", "objective", "autonomy_mode"},
        "plugin-manifest.schema.json": {"id", "version", "capabilities", "permissions"},
        "unified-plugin-protocol.schema.json": {"protocol_version", "id", "compatibility", "capabilities", "governance", "observability"},
        "plugin-cluster.schema.json": {"id", "axis", "layer", "domains", "routing_budget"},
        "plugin-blueprint.schema.json": {"id", "cluster_memberships", "lifecycle", "capabilities", "source_refs"},
        "plugin-topology-release.schema.json": {"release_id", "version", "checksum_sha256", "cluster_ids", "blueprint_ids"},
        "plugin-routing-plan.schema.json": {"plan_id", "mode", "planner_version", "catalog_release", "nodes", "budget"},
        "policy-decision.schema.json": {"decision", "risk_score"},
        "ui-contribution.schema.json": {"schema_version", "navigation", "views", "actions"},
        "ui-action.schema.json": {"id", "capability"},
        "ui-query.schema.json": {"query_id", "capability"},
        "knowledge-search.schema.json": {"query", "mode", "limit"},
        "quant-evidence-chain.schema.json": {"kind", "backtest_id", "backtest"},
        "aiops-governance-chain.schema.json": {"kind", "incident_id", "simulated_only", "incident", "executions", "verifications"},
    }
    for name, fields in expected.items():
        schema = _load(name)
        assert fields.issubset(set(schema.get("required", []))), name


def test_ui_contribution_resolves_local_contract_refs() -> None:
    schema = _load("ui-contribution.schema.json")
    registry = Registry().with_resources(
        [
            (f"https://audit.local/contracts/{name}", Resource.from_contents(_load(name)))
            for name in ("ui-action.schema.json", "ui-query.schema.json")
        ]
    )
    instance = {
        "schema_version": "1.0.0",
        "navigation": [],
        "views": [],
        "queries": [],
        "actions": [],
        "i18n": ["zh-CN"],
    }
    Draft202012Validator(schema, registry=registry).validate(instance)
