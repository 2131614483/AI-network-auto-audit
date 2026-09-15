"""Unit tests for the audit foundation batch (quality-check / tag-manage / metric-compute)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput
from plugins.builtin.audit_foundation_metric_compute import runtime as metric_compute
from plugins.builtin.audit_foundation_quality_check import runtime as quality_check
from plugins.builtin.audit_foundation_tag_manage import runtime as tag_manage

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / ".data" / "audit-sim"


@pytest.fixture(autouse=True)
def _read_roots(tmp_path: Path) -> None:
    os.environ["AUDIT_PLUGIN_READ_ROOTS"] = json.dumps([str(SIM), str(SIM.parent), str(tmp_path)])


def _input(path: Path) -> ArtifactInput:
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="text/csv" if path.suffix == ".csv" else "application/json",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


def _envelope(plugin_id: str, capability: str, port_name: str, artifact: ArtifactInput) -> dict:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": capability,
        "trace_id": str(uuid4()),
        port_name: {
            "contract_id": "artifact-ref", "contract_version": "1.0.0",
            "artifact": {
                "uri": artifact.uri, "sha256": artifact.sha256, "size_bytes": artifact.size_bytes,
            },
        },
    }


def _call(runtime, plugin_id: str, capability: str, port_name: str, artifact: ArtifactInput) -> dict:
    return runtime.handle(_envelope(plugin_id, capability, port_name, artifact))


# ---------- quality-check ----------

def test_quality_check_reports_ledger_findings() -> None:
    result = _call(quality_check, "audit.foundation.quality-check", "audit.foundation.quality-check",
                   "quality-input", _input(SIM / "ledger.csv"))
    assert result["contract_id"] == "dataset-validation"
    summary = result["summary"]
    assert summary["checked_rows"] == 96
    assert summary["checked_columns"] == 6
    assert summary["missing_values"] == 0
    assert summary["valid"] is False
    by_id = {check["check_id"]: check for check in result["checks"]}
    assert by_id["csv_parse"]["status"] == "pass"
    assert by_id["columns"]["status"] == "pass"
    assert by_id["missing_rate"]["status"] == "pass"
    assert by_id["duplicate_timestamps"]["status"] == "fail"
    assert by_id["point_in_time"]["status"] == "fail"
    assert by_id["freshness"]["status"] == "pass"
    messages = " ".join(v["message"] for v in result["violations"])
    assert "3 组重复主键" in messages
    assert "1 行日期无效" in messages


def test_quality_check_accepts_json_rows() -> None:
    payload = {"rows": [{"entry_id": "A1", "date": "2026-01-01", "amount": "1.00"}, {"entry_id": "A1", "date": "2026-13-01", "amount": ""}]}
    path = SIM.parent / "_qcheck_tmp.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        result = _call(quality_check, "audit.foundation.quality-check", "audit.foundation.quality-check",
                       "quality-input", _input(path))
    finally:
        path.unlink(missing_ok=True)
    assert result["summary"]["checked_rows"] == 2
    assert result["summary"]["missing_values"] == 1
    assert result["summary"]["duplicate_timestamps"] == 1
    by_id = {check["check_id"]: check for check in result["checks"]}
    assert by_id["point_in_time"]["status"] == "fail"


# ---------- tag-manage ----------

def test_tag_manage_buckets_rows_deterministically() -> None:
    payload = {
        "object_type": "ledger",
        "dimensions": ["amount-band", "entry-status"],
        "rows": [
            {"entry_id": "T1", "amount": "1200000", "status": "outlier"},
            {"entry_id": "T2", "amount": "50000", "status": "ok"},
            {"entry_id": "T3", "amount": "5000", "status": "normal"},
        ],
    }
    path = SIM.parent / "_tag_query_tmp.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        result = _call(tag_manage, "audit.foundation.tag-manage", "audit.foundation.tag-manage",
                       "tag-query", _input(path))
    finally:
        path.unlink(missing_ok=True)
    assert result["contract_id"] == "tag-tree"
    assert result["object_type"] == "ledger"
    assert result["total_rows"] == 3
    by_dim = {d["dimension"]: {t["tag"]: t["count"] for t in d["tags"]} for d in result["dimensions"]}
    assert by_dim["amount-band"] == {"小额": 1, "中额": 1, "大额": 1}
    assert by_dim["entry-status"] == {"正常": 2, "异常": 1}


def test_tag_manage_rejects_unknown_dimension() -> None:
    payload = {"object_type": "ledger", "dimensions": ["no-such-dim"], "rows": []}
    path = SIM.parent / "_tag_query_tmp.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        try:
            _call(tag_manage, "audit.foundation.tag-manage", "audit.foundation.tag-manage",
                  "tag-query", _input(path))
            raise AssertionError("expected InputRejected")
        except Exception as exc:  # noqa: BLE001
            assert "不支持的标签维度" in str(exc)
    finally:
        path.unlink(missing_ok=True)


# ---------- metric-compute ----------

def test_metric_compute_ledger_baseline() -> None:
    result = _call(metric_compute, "audit.foundation.metric-compute", "audit.foundation.metric-compute",
                   "metric-input", _input(SIM / "ledger.csv"))
    assert result["contract_id"] == "metric-output"
    assert result["row_count"] == 96
    metrics = result["metrics"]
    assert metrics["unbalanced_entries"] == 2
    assert metrics["duplicate_pairs"] == 3
    assert metrics["invalid_dates"] == 1
    assert metrics["outlier_amounts"] == 3
    assert metrics["total_debit"] > 0
    assert metrics["total_credit"] > 0
    assert metrics["balance_ratio"] is not None and 0.9 <= metrics["balance_ratio"] <= 1.1
    assert len(result["series"]) == 3
    for series in result["series"]:
        assert series["contract_id"] == "metric-series"
        assert series["points"][0]["value"] >= 0
