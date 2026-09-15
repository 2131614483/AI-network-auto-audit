from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"


def _schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_topology_service_schemas_are_valid() -> None:
    for name in (
        "topology-upsert.schema.json",
        "topology-release.schema.json",
        "topology-recycle.schema.json",
        "topology-plan.schema.json",
    ):
        Draft202012Validator.check_schema(_schema(name))


def test_topology_upsert_accepts_all_six_kinds_and_requires_idempotency_key() -> None:
    schema = _schema("topology-upsert.schema.json")
    validator = Draft202012Validator(schema)

    samples = [
        {
            "kind": "cluster",
            "idempotency_key": "cls-1",
            "payload": {
                "key": "business-financial-audit",
                "name": "财务审计业务域",
                "axis": "business_domain",
                "layer": "L0",
                "domains": ["audit"],
                "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            },
        },
        {
            "kind": "blueprint",
            "idempotency_key": "bp-1",
            "payload": {
                "key": "ledger-quality-slot",
                "name": "总账质量槽位",
                "blueprint_type": "capability",
                "primary_cluster_key": "business-financial-audit",
                "lifecycle": "planned",
                "capability_contract": {"capability": "audit.ledger.validate", "version": "1.0.0", "outputs": ["audit-quality-candidates"]},
                "source_refs": [{"kind": "design_document", "uri": "reference://TA-01"}],
            },
        },
        {
            "kind": "membership",
            "idempotency_key": "mem-1",
            "payload": {"blueprint_key": "ledger-quality-slot", "cluster_key": "capability-audit-quality", "axis": "capability"},
        },
        {
            "kind": "edge",
            "idempotency_key": "edge-1",
            "payload": {"source_blueprint_key": "ledger-quality-slot", "target_blueprint_key": "finding-draft-slot", "relation_type": "depends_on"},
        },
        {
            "kind": "contract",
            "idempotency_key": "ct-1",
            "payload": {
                "contract_id": "incident-proposal",
                "contract_version": "1.0.0",
                "kind": "input",
                "format": "json",
                "classification": "internal",
                "schema_ref": "contracts/jsonschema/incident-proposal.schema.json",
            },
        },
        {
            "kind": "bridge",
            "idempotency_key": "br-1",
            "payload": {
                "blueprint_key": "ledger-quality-slot",
                "ref_kind": "capability_contract",
                "bridge_ref": "contracts/jsonschema/audit-quality-candidates@1",
            },
        },
    ]
    for sample in samples:
        validator.validate(sample)

    errors = list(validator.iter_errors({**samples[0], "idempotency_key": ""}))
    assert any("should be non-empty" in error.message for error in errors)


def test_topology_upsert_rejects_unknown_bridge_ref_kind_and_missing_ref() -> None:
    schema = _schema("topology-upsert.schema.json")
    validator = Draft202012Validator(schema)

    unknown_kind = {
        "kind": "bridge",
        "idempotency_key": "br-bad-1",
        "payload": {
            "blueprint_key": "ledger-quality-slot",
            "ref_kind": "domain_private_table",
            "bridge_ref": "schema://domain.private",
        },
    }
    errors = list(validator.iter_errors(unknown_kind))
    assert errors
    assert any("'domain_private_table' is not one of" in error.message for error in errors)

    missing_ref = {
        "kind": "bridge",
        "idempotency_key": "br-bad-2",
        "payload": {"blueprint_key": "ledger-quality-slot", "ref_kind": "artifact_ref"},
    }
    errors = list(validator.iter_errors(missing_ref))
    assert errors
    assert any("bridge_ref" in error.message for error in errors)


def test_topology_upsert_rejects_blueprint_with_execution_fields() -> None:
    schema = _schema("topology-upsert.schema.json")
    validator = Draft202012Validator(schema)

    invalid = {
        "kind": "blueprint",
        "idempotency_key": "bp-bad",
        "payload": {
            "key": "forbidden-slot",
            "name": "带执行字段的槽位",
            "blueprint_type": "capability",
            "lifecycle": "planned",
            "capability_contract": {"capability": "audit.ledger.validate", "version": "1.0.0"},
            "source_refs": [{"kind": "design_document", "uri": "reference://TA-01"}],
            "runtime": "isolated-process",
        },
    }
    errors = list(validator.iter_errors(invalid))
    assert errors
    assert any("runtime" in error.message for error in errors)


def test_topology_release_requires_checksum_on_publish_and_version_on_rollback() -> None:
    schema = _schema("topology-release.schema.json")
    validator = Draft202012Validator(schema)

    validator.validate(
        {
            "action": "publish",
            "idempotency_key": "rel-1",
            "version": "1.0.0",
            "catalog_checksum": "a" * 64,
            "cluster_keys": ["business-financial-audit"],
            "blueprint_keys": ["ledger-quality-slot"],
        }
    )
    validator.validate(
        {"action": "rollback", "idempotency_key": "rel-2", "rollback_from_version": "1.0.0"}
    )

    publish_without_checksum = {
        "action": "publish",
        "idempotency_key": "rel-3",
        "version": "1.0.0",
        "cluster_keys": ["business-financial-audit"],
        "blueprint_keys": ["ledger-quality-slot"],
    }
    errors = list(validator.iter_errors(publish_without_checksum))
    assert errors
    assert any("catalog_checksum" in error.message for error in errors)

    rollback_without_version = {"action": "rollback", "idempotency_key": "rel-4"}
    errors = list(validator.iter_errors(rollback_without_version))
    assert errors
    assert any("rollback_from_version" in error.message for error in errors)


def test_topology_recycle_restore_requires_recycle_reference_and_rejects_hard_delete() -> None:
    schema = _schema("topology-recycle.schema.json")
    validator = Draft202012Validator(schema)

    validator.validate(
        {"recycle_type": "recycled", "entity_kind": "blueprint", "entity_key": "ledger-quality-slot", "idempotency_key": "rc-1"}
    )
    validator.validate(
        {
            "recycle_type": "restored",
            "entity_kind": "blueprint",
            "entity_key": "ledger-quality-slot",
            "idempotency_key": "rc-2",
            "restored_from_recycle_id": "7a3bb1c4-8c23-4b10-9a47-1f9a3d2c5e61",
        }
    )

    restore_without_ref = {
        "recycle_type": "restored",
        "entity_kind": "blueprint",
        "entity_key": "ledger-quality-slot",
        "idempotency_key": "rc-3",
    }
    errors = list(validator.iter_errors(restore_without_ref))
    assert errors
    assert any("restored_from_recycle_id" in error.message for error in errors)

    hard_delete = {"recycle_type": "delete", "entity_kind": "blueprint", "entity_key": "ledger-quality-slot", "idempotency_key": "rc-4"}
    errors = list(validator.iter_errors(hard_delete))
    assert errors
    assert any("'delete' is not one of" in error.message for error in errors)


def test_topology_plan_accepts_plan_only_and_rejects_execute_mode() -> None:
    schema = _schema("topology-plan.schema.json")
    validator = Draft202012Validator(schema)

    validator.validate(
        {
            "intent": "对总账执行质量校验并生成发现草稿",
            "idempotency_key": "plan-1",
            "mode": "plan_only",
            "capability_requirements": ["audit.ledger.validate", "audit.finding.draft"],
            "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            "planner_version": "1.0.0",
            "release_lock": {"release_id": "topology-20260907-001", "version": "1.0.0", "checksum_sha256": "a" * 64},
        }
    )

    executable = {
        "intent": "不应执行",
        "idempotency_key": "plan-2",
        "mode": "execute",
        "capability_requirements": ["audit.ledger.validate"],
        "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
    }
    errors = list(validator.iter_errors(executable))
    assert errors
    assert any("'plan_only' was expected" in error.message for error in errors)

    no_requirements = {
        "intent": "缺少能力需求",
        "idempotency_key": "plan-3",
        "mode": "plan_only",
        "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
    }
    errors = list(validator.iter_errors(no_requirements))
    assert errors
    assert any("capability_requirements" in error.message for error in errors)
