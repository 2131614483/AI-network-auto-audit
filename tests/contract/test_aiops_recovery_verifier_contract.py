from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("aiops.recovery-verifier")
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
                "metric-series.schema.json",
                "recovery-verification.schema.json",
                "remediation-proposal.schema.json",
            )
        ]
    )


def test_protocol_is_schema_valid_and_stays_a_read_only_contract() -> None:
    schema = _schema("unified-plugin-protocol.schema.json")
    protocol = _load(PLUGIN_DIR / "plugin.protocol.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(protocol)
    assert protocol["id"] == "aiops.recovery-verifier"
    assert protocol["version"] == "0.1.0"
    assert protocol["lifecycle"] == "contract_only"
    assert protocol["governance"]["permissions"]["network"] == "none"
    assert protocol["governance"]["permissions"]["data_write"] == []
    assert "entrypoint" not in protocol
    assert "command" not in protocol

    capability = protocol["capabilities"][0]
    assert capability["id"] == "aiops.recovery.verify"
    assert capability["side_effects"] == "read_only"
    # Baseline vs observed metric series: two inputs the runtime reads as
    # `baseline` / `observed`.  They previously shared the id `metric-series`,
    # which the compiler rejects as a duplicate port — the contract could not be
    # compiled at all.
    assert capability["inputs"][0]["contract_id"] == "baseline-metric-series"
    assert capability["inputs"][1]["contract_id"] == "observed-metric-series"
    assert capability["inputs"][2]["contract_id"] == "remediation-proposal"
    assert capability["outputs"][0]["contract_id"] == "recovery-verification"
    _assert_port_ids_are_unique(capability)


def _assert_port_ids_are_unique(capability: dict) -> None:
    for direction in ("inputs", "outputs"):
        ids = [port["contract_id"] for port in capability[direction]]
        assert len(ids) == len(set(ids)), f"duplicate {direction} port id in {ids}"


def test_manifest_and_binding_declare_only_the_verified_read_only_capability() -> None:
    manifest = _load(PLUGIN_DIR / "plugin.manifest.json")
    binding = _load(PLUGIN_DIR / "plugin.runtime-binding.json")

    assert manifest["id"] == "aiops.recovery-verifier"
    assert manifest["side_effects"] == "read_only"
    assert manifest["permissions"]["data_write"] == []
    assert manifest["capabilities"] == ["aiops.recovery.verify"]
    assert manifest["entrypoint"].endswith("aiops_recovery_verifier.runtime:handle")

    assert binding["status"] == "verified"
    assert binding["execution_mode"] == "isolated_subprocess"
    assert binding["capabilities"] == ["aiops.recovery.verify"]
    assert binding["plugin"]["id"] == manifest["id"]
    assert binding["protocol"]["id"] == manifest["id"]


def test_metric_series_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("metric-series.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "metric-series@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "metric-series@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors


def test_recovery_verification_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("recovery-verification.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "recovery-verification@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "recovery-verification@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors
    assert any("is not one of" in error.message for error in errors)
