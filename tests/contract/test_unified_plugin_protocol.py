from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
SAMPLE_DIR = ROOT / "plugins" / "builtin"


def _schema() -> dict[str, object]:
    return json.loads((SCHEMA_DIR / "unified-plugin-protocol.schema.json").read_text(encoding="utf-8"))


def test_unified_plugin_protocol_is_a_valid_contract_and_forbids_runtime_commands() -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    valid = {
        "protocol_version": "1.0.0",
        "id": "knowledge.document-ingestion",
        "version": "0.1.0",
        "name": "文档摄入协议插件",
        "kind": "ingestion",
        "domains": ["knowledge", "audit"],
        "lifecycle": "contract_only",
        "compatibility": {"host_protocol": ">=1.0.0 <2.0.0", "adapters": ["python", "node", "mcp"]},
        "capabilities": [{
            "id": "knowledge.extract.document",
            "side_effects": "read_only",
            "idempotency": "artifact_sha256",
            "inputs": [{"contract_id": "artifact-ref", "version": "1.0.0", "format": "artifact_ref", "delivery": "reference", "classification": "internal"}],
            "outputs": [{"contract_id": "document-content", "version": "1.0.0", "format": "json", "delivery": "artifact_ref", "classification": "internal"}],
            "invokes": ["knowledge.foundation.tokenize"],
        }],
        "governance": {
            "default_data_classification": "internal",
            "permissions": {"data_read": ["knowledge.documents"], "data_write": [], "network": "none", "secret_refs": []},
            "policy": {"gateway_required": True, "approval_required": False, "evidence_required": True},
        },
        "resources": {"cpu": 1, "memory_mb": 128, "gpu": "optional", "timeout_seconds": 60},
        "observability": {"trace_required": True, "emits": ["structured_log", "metric"], "health": "declared_only"},
        "provenance": {"source_refs": ["docs/plugin-protocol.md"]},
        "ui": {"mode": "declarative", "contribution_schema": "ui-contribution.schema.json"},
    }
    Draft202012Validator(schema).validate(valid)

    invalid = {**valid, "entrypoint": "plugin:run"}
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_builtin_protocol_plugins_cover_cross_domain_contracts_without_execution_binding() -> None:
    validator = Draft202012Validator(_schema())
    samples = sorted(SAMPLE_DIR.glob("*/plugin.protocol.json"))
    assert len(samples) >= 4
    domains: set[str] = set()
    for path in samples:
        sample = json.loads(path.read_text(encoding="utf-8"))
        validator.validate(sample)
        # protocol packages never carry an execution binding; verified plugins
        # declare the same contract shape (entrypoint lives in the manifest)
        assert sample["lifecycle"] in {"contract_only", "verified"}
        assert "entrypoint" not in sample
        assert "command" not in sample
        domains.update(sample["domains"])
    assert {"knowledge", "audit", "quant", "aiops"}.issubset(domains)


def test_financial_or_external_capabilities_declare_strict_policy_requirements() -> None:
    schema = _schema()
    capability = {
        "id": "financial.order.submit",
        "side_effects": "financial_execution",
        "idempotency": "client_order_id",
        "inputs": [{"contract_id": "order-request", "version": "1.0.0", "format": "json", "delivery": "request_response", "classification": "restricted"}],
        "outputs": [{"contract_id": "order-result", "version": "1.0.0", "format": "json", "delivery": "request_response", "classification": "restricted"}],
    }
    invalid = {
        "protocol_version": "1.0.0",
        "id": "financial.order-submission",
        "version": "0.1.0",
        "name": "金融订单协议包",
        "kind": "connector",
        "domains": ["quant"],
        "lifecycle": "contract_only",
        "compatibility": {"host_protocol": ">=1.0.0 <2.0.0", "adapters": ["http"]},
        "capabilities": [capability],
        "governance": {
            "default_data_classification": "restricted",
            "permissions": {"data_read": [], "data_write": [], "network": "none", "secret_refs": []},
            "policy": {"gateway_required": True, "approval_required": True, "evidence_required": True},
        },
        "resources": {"cpu": 1, "memory_mb": 128, "gpu": "none", "timeout_seconds": 60},
        "observability": {"trace_required": True, "emits": ["structured_log"], "health": "declared_only"},
        "provenance": {"source_refs": ["docs/plugin-protocol.md"]},
        "ui": {"mode": "none"},
    }
    errors = list(Draft202012Validator(schema).iter_errors(invalid))
    assert errors
