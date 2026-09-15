from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROTOTYPE_ROOT = REPOSITORY_ROOT / "prototypes" / "audit_report_rendering"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "audit_report_bundle_builder", PROTOTYPE_ROOT / "tools" / "build_bundle.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_high_difficulty_bundle_is_schema_valid_and_traceable(tmp_path: Path) -> None:
    builder = _load_builder()
    bundle = builder.build_high_difficulty_bundle(REPOSITORY_ROOT)
    schema = json.loads(
        (PROTOTYPE_ROOT / "schemas" / "audit-report-render-bundle.schema.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator(schema).validate(bundle)

    assert bundle["report"]["id"] == "qianling-2025-hard"
    assert len(bundle["sources"]) == 51
    assert len(bundle["evidence_index"]["findings"]) == 24
    assert len(bundle["evidence_index"]["noise_exclusions"]) == 5
    assert all(":" not in item["path"] for item in bundle["sources"])
    assert builder.verify_content_sha256(bundle)

    output = tmp_path / "qianling-2025-hard.render.json"
    builder.write_bundle(bundle, output)
    assert json.loads(output.read_text(encoding="utf-8"))["integrity"]["content_sha256"] == bundle[
        "integrity"
    ]["content_sha256"]
