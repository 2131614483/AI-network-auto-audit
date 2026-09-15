from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("aiops.rca-ranker")
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
    assert protocol["id"] == "aiops.rca-ranker"
    assert protocol["version"] == "0.1.0"
    assert protocol["lifecycle"] == "contract_only"
    assert protocol["governance"]["permissions"]["network"] == "none"
    assert protocol["governance"]["permissions"]["data_write"] == []
    assert protocol["governance"]["policy"]["gateway_required"] is True
    assert "entrypoint" not in protocol
    assert "command" not in protocol

    capability = protocol["capabilities"][0]
    assert capability["id"] == "aiops.rca.rank"
    assert capability["side_effects"] == "read_only"
    assert capability["idempotency"] == "incident_set_sha256 + topology_sha256 + window_minutes + max_hops"
    assert capability["inputs"][0]["contract_id"] == "rca-input"
    assert capability["outputs"][0]["contract_id"] == "rca-candidates"


def test_rca_input_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("rca-input.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "rca-input@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "rca-input@1.negative.json")
    messages = [error.message for error in validator.iter_errors(negative)]
    assert messages
    assert any("required" in message or "max_candidates" in message for message in messages)


def test_rca_candidates_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("rca-candidates.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    positive = _load(CONTRACT_DIR / "rca-candidates@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "rca-candidates@1.negative.json")
    messages = [error.message for error in validator.iter_errors(negative)]
    assert messages
    assert any("maximum" in message for message in messages)
    assert any("minimum" in message for message in messages)


def test_rca_candidates_rejects_undeclared_properties() -> None:
    schema = _schema("rca-candidates.schema.json")
    validator = Draft202012Validator(schema)

    invalid = _load(CONTRACT_DIR / "rca-candidates@1.negative.json")
    messages = [error.message for error in validator.iter_errors(invalid)]
    assert any("confidence" in message or "minimum" in message or "maximum" in message for message in messages)
    assert messages
