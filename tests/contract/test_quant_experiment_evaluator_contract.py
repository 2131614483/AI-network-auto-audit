from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("quant.experiment-evaluator")
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
                "backtest-report.schema.json",
                "experiment-evaluation.schema.json",
            )
        ]
    )


def test_protocol_is_schema_valid_and_stays_a_read_only_contract() -> None:
    schema = _schema("unified-plugin-protocol.schema.json")
    protocol = _load(PLUGIN_DIR / "plugin.protocol.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(protocol)
    assert protocol["id"] == "quant.experiment-evaluator"
    assert protocol["version"] == "0.1.0"
    assert protocol["kind"] == "quant"
    assert protocol["lifecycle"] == "contract_only"
    assert protocol["governance"]["permissions"]["network"] == "none"
    assert protocol["governance"]["permissions"]["data_write"] == []
    assert "entrypoint" not in protocol
    assert "command" not in protocol

    capability = protocol["capabilities"][0]
    assert capability["id"] == "quant.experiment.evaluate"
    assert capability["side_effects"] == "read_only"
    # Two distinct reports: the candidate and the baseline champion it is
    # compared against.  They previously shared the id `backtest-report`, which
    # the compiler rejects as a duplicate port — the contract could not be
    # compiled at all.  The runtime reads them as separate `candidate` /
    # `baseline` inputs, so distinct names are also what the plugin means.
    assert capability["inputs"][0]["contract_id"] == "backtest-report"
    assert capability["inputs"][1]["contract_id"] == "baseline-champion-report"
    assert capability["outputs"][0]["contract_id"] == "experiment-evaluation"
    _assert_port_ids_are_unique(capability)


def _assert_port_ids_are_unique(capability: dict) -> None:
    for direction in ("inputs", "outputs"):
        ids = [port["contract_id"] for port in capability[direction]]
        assert len(ids) == len(set(ids)), f"duplicate {direction} port id in {ids}"


def test_manifest_and_binding_declare_only_the_verified_read_only_capability() -> None:
    manifest = _load(PLUGIN_DIR / "plugin.manifest.json")
    binding = _load(PLUGIN_DIR / "plugin.runtime-binding.json")

    assert manifest["id"] == "quant.experiment-evaluator"
    assert manifest["side_effects"] == "read_only"
    assert manifest["permissions"]["data_write"] == []
    assert manifest["capabilities"] == ["quant.experiment.evaluate"]
    assert manifest["entrypoint"].endswith("quant_experiment_evaluator.runtime:handle")

    assert binding["status"] == "verified"
    assert binding["execution_mode"] == "isolated_subprocess"
    assert binding["capabilities"] == ["quant.experiment.evaluate"]
    assert binding["plugin"]["id"] == manifest["id"]
    assert binding["protocol"]["id"] == manifest["id"]


def test_backtest_report_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("backtest-report.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "backtest-report@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "backtest-report@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors
    assert any("expected" in error.message for error in errors)


def test_experiment_evaluation_contract_accepts_positive_sample_and_rejects_negative_sample() -> None:
    schema = _schema("experiment-evaluation.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=_registry())

    positive = _load(CONTRACT_DIR / "experiment-evaluation@1.positive.json")
    validator.validate(positive)

    negative = _load(CONTRACT_DIR / "experiment-evaluation@1.negative.json")
    errors = list(validator.iter_errors(negative))
    assert errors
    assert any("is not one of" in error.message for error in errors)
