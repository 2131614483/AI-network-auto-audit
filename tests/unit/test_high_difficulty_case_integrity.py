from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = ROOT / "审计项目案例" / "黔岭酒业2025年度财务报表审计_实验组_高难度"
TOOLS = CASE_ROOT / "_tools"


def _load_integrity_module():
    spec = importlib.util.spec_from_file_location(
        "high_difficulty_case_integrity", TOOLS / "case_integrity.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_case_source_inventory_and_evidence_binding_are_complete() -> None:
    integrity = _load_integrity_module()

    manifest = integrity.build_manifest(CASE_ROOT)

    assert manifest["source_file_count"] == 51
    assert len(manifest["source_files"]) == 51
    assert all(len(item["sha256"]) == 64 for item in manifest["source_files"])
    assert manifest["csv_key_scan_count"] > 0
    assert all(item["status"] == "pass" for item in manifest["csv_key_scans"])

    evidence_path = TOOLS / "audit_evidence.json"
    expected_evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    assert manifest["evidence_binding"]["audit_evidence_sha256"] == expected_evidence_hash
    assert manifest["evidence_binding"]["report_binds_current_evidence"] is True
