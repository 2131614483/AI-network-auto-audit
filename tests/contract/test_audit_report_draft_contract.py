from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("audit.report-draft")
CONTRACT_DIR = PLUGIN_DIR / "contract"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict[str, object]:
    return _load(SCHEMA_DIR / name)


def test_protocol_is_schema_valid_and_stays_a_read_only_contract() -> None:
    schema = _schema("unified-plugin-protocol.schema.json")
    protocol = _load(PLUGIN_DIR / "plugin.protocol.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(protocol)
    assert protocol["id"] == "audit.report-draft"
    assert protocol["version"] == "0.1.0"
    assert protocol["lifecycle"] == "contract_only"
    assert protocol["governance"]["permissions"]["network"] == "none"
    assert protocol["governance"]["permissions"]["data_write"] == []
    assert "entrypoint" not in protocol
    assert "command" not in protocol

    capability = protocol["capabilities"][0]
    assert capability["id"] == "audit.report.draft"
    assert capability["side_effects"] == "read_only"
    assert capability["policy"]["approval_required"] is True
    assert capability["inputs"][0]["contract_id"] == "finding-set"
    assert capability["outputs"][0]["contract_id"] == "report-draft"


def test_manifest_is_schema_valid_and_declares_no_write_permission() -> None:
    schema = _schema("plugin-manifest.schema.json")
    manifest = _load(PLUGIN_DIR / "plugin.manifest.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    assert manifest["id"] == "audit.report-draft"
    assert manifest["capabilities"] == ["audit.report.draft"]
    assert manifest["side_effects"] == "read_only"
    assert manifest["permissions"]["data_write"] == []
    assert manifest["permissions"]["network"] == "none"


def test_finding_set_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("finding-set.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    positive = _load(CONTRACT_DIR / "finding-set@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "finding-set@1.negative.json")
    messages = [error.message for error in validator.iter_errors(negative)]
    assert messages
    assert any("'explosive'" in message for message in messages)
    assert any("'confirmed' was expected" in message for message in messages)


def test_report_draft_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("report-draft.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    positive = _load(CONTRACT_DIR / "report-draft@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "report-draft@1.negative.json")
    messages = [error.message for error in validator.iter_errors(negative)]
    assert messages
    assert any("'draft' was expected" in message for message in messages)
    assert any("True was expected" in message for message in messages)
    assert any("False was expected" in message for message in messages)
