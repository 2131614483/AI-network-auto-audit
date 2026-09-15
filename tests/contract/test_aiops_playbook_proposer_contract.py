from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("aiops.playbook-proposer")
CONTRACT_DIR = PLUGIN_DIR / "contract"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict[str, object]:
    return _load(SCHEMA_DIR / name)


def _registry() -> Registry:
    return Registry().with_resources(
        [
            (f"https://audit.local/contracts/{name}", Resource.from_contents(_schema(name)))
            for name in (
                "rca-candidates.schema.json",
                "remediation-proposal.schema.json",
            )
        ]
    )


def test_protocol_is_schema_valid_and_stays_a_read_only_contract() -> None:
    schema = _schema("unified-plugin-protocol.schema.json")
    protocol = _load(PLUGIN_DIR / "plugin.protocol.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(protocol)
    assert protocol["id"] == "aiops.playbook-proposer"
    assert protocol["version"] == "0.1.0"
    assert protocol["lifecycle"] == "contract_only"
    assert protocol["governance"]["permissions"]["network"] == "none"
    assert protocol["governance"]["permissions"]["data_write"] == []
    assert "entrypoint" not in protocol
    assert "command" not in protocol

    capability = protocol["capabilities"][0]
    assert capability["id"] == "aiops.remediation.propose"
    assert capability["side_effects"] == "read_only"
    assert capability["inputs"][0]["contract_id"] == "rca-candidates"
    assert capability["outputs"][0]["contract_id"] == "remediation-proposal"


def test_manifest_and_binding_declare_only_the_verified_read_only_capability() -> None:
    manifest = _load(PLUGIN_DIR / "plugin.manifest.json")
    binding = _load(PLUGIN_DIR / "plugin.runtime-binding.json")

    assert manifest["id"] == "aiops.playbook-proposer"
    assert manifest["side_effects"] == "read_only"
    assert manifest["permissions"]["data_write"] == []
    assert manifest["capabilities"] == ["aiops.remediation.propose"]
    assert manifest["entrypoint"].endswith("aiops_playbook_proposer.runtime:handle")

    assert binding["status"] == "verified"
    assert binding["execution_mode"] == "isolated_subprocess"
    assert binding["capabilities"] == ["aiops.remediation.propose"]
    assert binding["plugin"]["id"] == manifest["id"]
    assert binding["protocol"]["id"] == manifest["id"]


def test_rca_candidates_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("rca-candidates.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "rca-candidates@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "rca-candidates@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors


def test_remediation_proposal_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("remediation-proposal.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "remediation-proposal@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "remediation-proposal@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors
    assert any("is not one of" in error.message for error in errors)
