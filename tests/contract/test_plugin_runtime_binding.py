from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from packages.plugin_runtime.layout import declaration_dir

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"
PLUGIN_DIR = declaration_dir("knowledge.document-ingestion")


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_runtime_binding_is_schema_valid_and_fixed_to_an_isolated_process() -> None:
    schema = _load(SCHEMA_DIR / "plugin-runtime-binding.schema.json")
    binding = _load(PLUGIN_DIR / "plugin.runtime-binding.json")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(binding)
    assert binding["execution_mode"] == "isolated_subprocess"
    assert binding["status"] == "verified"
    assert "entrypoint" not in binding
    assert "command" not in binding


def test_runtime_binding_rejects_in_process_or_unverified_execution() -> None:
    schema = _load(SCHEMA_DIR / "plugin-runtime-binding.schema.json")
    invalid = {
        "schema_version": "1.0.0",
        "plugin": {"id": "knowledge.document-ingestion", "version": "0.1.0", "sha256": "a" * 64},
        "protocol": {"id": "knowledge.document-ingestion", "version": "0.1.0", "sha256": "b" * 64},
        "execution_mode": "in_process",
        "capabilities": ["knowledge.extract.document"],
        "status": "draft",
    }
    assert list(Draft202012Validator(schema).iter_errors(invalid))
