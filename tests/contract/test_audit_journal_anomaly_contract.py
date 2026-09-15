from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("audit.journal-anomaly")
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
    assert protocol["id"] == "audit.journal-anomaly"
    assert protocol["version"] == "0.1.0"
    assert protocol["lifecycle"] == "contract_only"
    assert protocol["governance"]["permissions"]["network"] == "none"
    assert protocol["governance"]["permissions"]["data_write"] == []
    assert protocol["governance"]["policy"]["gateway_required"] is True
    assert "entrypoint" not in protocol
    assert "command" not in protocol

    capability = protocol["capabilities"][0]
    assert capability["id"] == "audit.journal.detect"
    assert capability["side_effects"] == "read_only"
    assert capability["idempotency"] == "ledger_sha256 + schema_mapping_version + zscore_threshold"
    assert capability["inputs"][0]["contract_id"] == "journal-artifact-ref"
    assert capability["outputs"][0]["contract_id"] == "anomaly-candidates"


def test_journal_artifact_ref_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("journal-artifact-ref.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "journal-artifact-ref@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "journal-artifact-ref@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors


def test_anomaly_candidates_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("anomaly-candidates.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    positive = _load(CONTRACT_DIR / "anomaly-candidates@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "anomaly-candidates@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors


def test_anomaly_candidates_rejects_finding_write_or_undeclared_properties() -> None:
    schema = _schema("anomaly-candidates.schema.json")
    validator = Draft202012Validator(schema)

    invalid = _load(CONTRACT_DIR / "anomaly-candidates@1.negative.json")
    messages = [error.message for error in validator.iter_errors(invalid)]
    assert any("self_confirm_finding" in message for message in messages)
    assert any("maximum" in message for message in messages)
