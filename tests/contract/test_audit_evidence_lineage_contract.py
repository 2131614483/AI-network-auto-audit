from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("audit.evidence-lineage")
CONTRACT_DIR = PLUGIN_DIR / "contract"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict[str, object]:
    return _load(SCHEMA_DIR / name)


def _registry() -> Registry:
    return Registry().with_resources(
        [
            (f"https://audit.local/contracts/{name}", Resource.from_contents(_schema(name)))
            for name in ("artifact-ref.schema.json",)
        ]
    )


def test_protocol_is_schema_valid_and_stays_a_read_only_contract() -> None:
    schema = _schema("unified-plugin-protocol.schema.json")
    protocol = _load(PLUGIN_DIR / "plugin.protocol.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(protocol)
    assert protocol["id"] == "audit.evidence-lineage"
    assert protocol["version"] == "0.1.0"
    assert protocol["lifecycle"] == "contract_only"
    assert protocol["governance"]["permissions"]["network"] == "none"
    assert protocol["governance"]["permissions"]["data_write"] == []
    assert "entrypoint" not in protocol
    assert "command" not in protocol

    capability = protocol["capabilities"][0]
    assert capability["id"] == "audit.evidence.lineage"
    assert capability["side_effects"] == "read_only"
    assert capability["inputs"][0]["contract_id"] == "released-graph-ref"
    assert capability["outputs"][0]["contract_id"] == "evidence-lineage"


def test_manifest_is_schema_valid_and_declares_no_write_permission() -> None:
    schema = _schema("plugin-manifest.schema.json")
    manifest = _load(PLUGIN_DIR / "plugin.manifest.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    assert manifest["id"] == "audit.evidence-lineage"
    assert manifest["capabilities"] == ["audit.evidence.lineage"]
    assert manifest["side_effects"] == "read_only"
    assert manifest["permissions"]["data_write"] == []
    assert manifest["permissions"]["network"] == "none"


def test_released_graph_ref_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("released-graph-ref.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "released-graph-ref@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "released-graph-ref@1.negative.json")
    assert list(validator.iter_errors(negative))


def test_evidence_lineage_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("evidence-lineage.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "evidence-lineage@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "evidence-lineage@1.negative.json")
    messages = [error.message for error in validator.iter_errors(negative)]
    assert messages
    assert any("'explodes'" in message for message in messages)
    assert any("'execution_log'" in message for message in messages)
