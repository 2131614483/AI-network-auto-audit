from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("knowledge.graph-proposal-builder")
CONTRACT_DIR = PLUGIN_DIR / "contract"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict[str, object]:
    return _load(SCHEMA_DIR / name)


def _registry() -> Registry:
    return Registry().with_resources(
        [
            (f"https://audit.local/contracts/{name}", Resource.from_contents(_schema(name)))
            for name in ("graph-candidate-set.schema.json", "graph-snapshot.schema.json", "graph-proposal-draft.schema.json")
        ]
    )


def test_protocol_is_schema_valid_and_stays_a_read_only_contract() -> None:
    schema = _schema("unified-plugin-protocol.schema.json")
    protocol = _load(PLUGIN_DIR / "plugin.protocol.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(protocol)
    assert protocol["id"] == "knowledge.graph-proposal-builder"
    assert protocol["version"] == "0.1.0"
    assert protocol["lifecycle"] == "contract_only"
    assert protocol["governance"]["permissions"]["network"] == "none"
    assert protocol["governance"]["permissions"]["data_write"] == []
    assert "entrypoint" not in protocol
    assert "command" not in protocol

    capability = protocol["capabilities"][0]
    assert capability["id"] == "graph.proposal.build"
    assert capability["side_effects"] == "read_only"
    assert capability["inputs"][0]["contract_id"] == "graph-candidate-set"
    assert capability["inputs"][1]["contract_id"] == "graph-snapshot"
    assert capability["outputs"][0]["contract_id"] == "graph-proposal-draft"


def test_manifest_and_binding_declare_only_the_verified_read_only_capability() -> None:
    manifest = _load(PLUGIN_DIR / "plugin.manifest.json")
    binding = _load(PLUGIN_DIR / "plugin.runtime-binding.json")

    assert manifest["id"] == "knowledge.graph-proposal-builder"
    assert manifest["side_effects"] == "read_only"
    assert manifest["permissions"]["data_write"] == []
    assert manifest["capabilities"] == ["graph.proposal.build"]
    assert manifest["entrypoint"].endswith("knowledge_graph_proposal_builder.runtime:handle")

    assert binding["status"] == "verified"
    assert binding["execution_mode"] == "isolated_subprocess"
    assert binding["capabilities"] == ["graph.proposal.build"]
    assert binding["plugin"]["id"] == manifest["id"]
    assert binding["protocol"]["id"] == manifest["id"]


def test_graph_candidate_set_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("graph-candidate-set.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "graph-candidate-set@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "graph-candidate-set@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors
    assert any("owns_everything" in error.message for error in errors)


def test_graph_snapshot_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("graph-snapshot.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "graph-snapshot@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "graph-snapshot@1.negative.json")
    assert list(validator.iter_errors(negative))


def test_graph_proposal_draft_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("graph-proposal-draft.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "graph-proposal-draft@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "graph-proposal-draft@1.negative.json")
    assert list(validator.iter_errors(negative))