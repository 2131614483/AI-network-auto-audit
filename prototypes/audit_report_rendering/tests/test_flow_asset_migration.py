from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROTOTYPE_ROOT = REPOSITORY_ROOT / "prototypes" / "audit_report_rendering"


def _load_migrator():
    spec = importlib.util.spec_from_file_location(
        "audit_report_flow_migrator", PROTOTYPE_ROOT / "tools" / "migrate_flow_assets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_case_dags_migrate_without_losing_nodes_or_edges() -> None:
    migrator = _load_migrator()
    schema = json.loads(
        (PROTOTYPE_ROOT / "schemas" / "audit-report-render-bundle.schema.json").read_text(
            encoding="utf-8"
        )
    )

    bundles = migrator.build_case_bundles(REPOSITORY_ROOT)

    assert set(bundles) == {"qianling-2025-base", "qianling-2025-experiment", "qianling-2025-hard"}
    for report_id, bundle in bundles.items():
        Draft202012Validator(schema).validate(bundle)
        assert bundle["report"]["id"] == report_id
        assert bundle["sources"]
        assert bundle["graph"]["nodes"]
        assert bundle["graph"]["edges"]
        assert bundle["presentation"]["renderer"] == "legacy-dag@1.0.0"
        assert migrator.verify_content_sha256(bundle)

    assert len(bundles["qianling-2025-base"]["graph"]["nodes"]) == 21
    assert len(bundles["qianling-2025-experiment"]["graph"]["nodes"]) == 39
    assert len(bundles["qianling-2025-hard"]["graph"]["nodes"]) == 42

    legacy_builder = migrator._load_legacy_builder(REPOSITORY_ROOT)
    for report_id, factory_name in {
        "qianling-2025-base": "spec_base",
        "qianling-2025-experiment": "spec_exp",
        "qianling-2025-hard": "spec_hard",
    }.items():
        expected = migrator._legacy_page_payload(getattr(legacy_builder, factory_name)())
        assert bundles[report_id]["presentation"]["legacy_dag"] == expected
        assert expected["generated"] == "2026-09-15T00:00:00"
        assert all("from" in edge and "from_" not in edge for edge in expected["edges"])
