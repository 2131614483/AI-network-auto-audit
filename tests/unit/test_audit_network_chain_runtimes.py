"""Unit tests for the 6 audit-network chain runtimes (direct handle calls)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput
from plugins.builtin.audit_finding_issue_amount_compute import runtime as amount_compute
from plugins.builtin.audit_finding_issue_type_judge import runtime as issue_judge
from plugins.builtin.audit_foundation_finance_clean import runtime as finance_clean
from plugins.builtin.audit_report_issue_desc_write import runtime as desc_write
from plugins.builtin.audit_risk_finance_anomaly_alert import runtime as anomaly_alert
from plugins.builtin.audit_risk_risk_matrix_build import runtime as risk_matrix

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / ".data" / "audit-sim"


@pytest.fixture(autouse=True)
def _roots(tmp_path: Path) -> None:
    os.environ["AUDIT_PLUGIN_READ_ROOTS"] = json.dumps([str(SIM), str(tmp_path)])


def _input(path: Path) -> ArtifactInput:
    raw = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type="text/csv" if path.suffix == ".csv" else "application/json",
        sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), classification="audit_confidential",
    )


def _envelope(plugin_id: str, capability: str, payload: dict) -> dict:
    return {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": capability,
        "trace_id": str(uuid4()),
        **payload,
    }


def _ledger_port(path: Path) -> dict:
    ref = _input(path)
    return {
        "contract_id": "ledger-artifact-ref", "contract_version": "1.0.0",
        "artifact": ref.as_payload(), "schema_mapping_version": "1.0.0", "period": "2026-01",
    }


def _write_json(data: dict, tmp_path: Path, name: str) -> ArtifactInput:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return _input(path)


# ---------------------------------------------------------------- chain stage 1
def test_finance_clean_deduplicates_and_flags(tmp_path: Path) -> None:
    ledger = SIM / "ledger.csv"
    out = finance_clean.handle(_envelope(
        "audit.foundation.finance-clean", "audit.foundation.finance-clean",
        {"raw-finance-set": _ledger_port(ledger)},
    ))
    assert out["contract_id"] == "clean-finance-set"
    s = out["summary"]
    assert s["input_rows"] == 96
    assert s["duplicates_removed"] == 3
    assert s["kept_rows"] == 93
    assert s["invalid_dates"] == 1  # E9204 (2026-13-01)
    assert s["unbalanced_entries"] == 2  # E9101 / E9102
    assert s["missing_amounts"] == 0
    flags = [r["row_flags"] for r in out["rows"] if r["entry_id"] == "E9204"]
    assert flags and "invalid_date" in flags[0]


def test_finance_clean_rejects_unknown_inputs(tmp_path: Path) -> None:
    with pytest.raises(finance_clean.InputRejected):
        finance_clean.handle(_envelope(
            "audit.foundation.finance-clean", "audit.foundation.finance-clean",
            {"raw-finance-set": {"artifact": {"uri": "https://evil/x", "sha256": "a" * 64, "size_bytes": 1}}},
        ))


# ---------------------------------------------------------------- chain stage 2
def test_anomaly_alert_flags_seeded_anomalies(tmp_path: Path) -> None:
    ledger = SIM / "ledger.csv"
    clean = finance_clean.handle(_envelope(
        "audit.foundation.finance-clean", "audit.foundation.finance-clean",
        {"raw-finance-set": _ledger_port(ledger)},
    ))
    clean_ref = _write_json(clean, tmp_path, "clean.json")
    out = anomaly_alert.handle(_envelope(
        "audit.risk.finance-anomaly-alert", "audit.risk.finance-anomaly-alert",
        {"clean-finance-set": {"artifact": clean_ref.as_payload(), "schema_mapping_version": "1.0.0", "period": "2026-01"}},
    ))
    assert out["contract_id"] == "anomaly-candidates"
    by_rule: dict[str, int] = {}
    for c in out["candidates"]:
        by_rule[c["rule_key"]] = by_rule.get(c["rule_key"], 0) + 1
    assert by_rule.get("invalid_date") == 1
    assert by_rule.get("unbalanced_entry") == 2
    assert by_rule.get("outlier_amount") == 6  # 3 rows x debit+credit columns
    assert by_rule.get("round_amount", 0) >= 6


# ---------------------------------------------------------------- chain stage 3
def test_risk_matrix_aggregates_six_channels(tmp_path: Path) -> None:
    ledger = SIM / "ledger.csv"
    clean = finance_clean.handle(_envelope(
        "audit.foundation.finance-clean", "audit.foundation.finance-clean",
        {"raw-finance-set": _ledger_port(ledger)},
    ))
    clean_ref = _write_json(clean, tmp_path, "clean.json")
    anomaly = anomaly_alert.handle(_envelope(
        "audit.risk.finance-anomaly-alert", "audit.risk.finance-anomaly-alert",
        {"clean-finance-set": {"artifact": clean_ref.as_payload(), "schema_mapping_version": "1.0.0", "period": "2026-01"}},
    ))
    anomaly_ref = _write_json(anomaly, tmp_path, "anomaly.json")

    payload: dict = {"finance-anomaly-set": {"artifact": anomaly_ref.as_payload()}}
    for port_id in ("policy-risk-set", "industry-risk-set", "ic-risk-set", "process-gap-set", "fraud-risk-set"):
        seed = {
            "contract_id": "anomaly-candidates", "candidates": [
                {"rule_id": f"{port_id}:x", "rule_key": "control_gap", "severity": "medium",
                 "row_ref": "seed-1", "source_ref": port_id, "score": 0.6}
            ],
        }
        seed_ref = _write_json(seed, tmp_path, f"{port_id}.json")
        payload[port_id] = {"artifact": seed_ref.as_payload()}

    out = risk_matrix.handle(_envelope(
        "audit.risk.risk-matrix-build", "audit.risk.risk-matrix-build", payload,
    ))
    assert out["contract_id"] == "risk-matrix"
    assert len(out["channels"]) == 6
    assert len(out["matrix"]["points"]) > 0
    assert out["matrix"]["high"] + out["matrix"]["medium"] + out["matrix"]["low"] == len(out["matrix"]["points"])
    assert out["top_risks"] and out["top_risks"][0]["score"] >= out["top_risks"][-1]["score"]


# ---------------------------------------------------------------- chain stage 4-6
def test_issue_judge_amount_and_report_chain(tmp_path: Path) -> None:
    ledger = SIM / "ledger.csv"
    clean = finance_clean.handle(_envelope(
        "audit.foundation.finance-clean", "audit.foundation.finance-clean",
        {"raw-finance-set": _ledger_port(ledger)},
    ))
    clean_ref = _write_json(clean, tmp_path, "clean.json")
    anomaly = anomaly_alert.handle(_envelope(
        "audit.risk.finance-anomaly-alert", "audit.risk.finance-anomaly-alert",
        {"clean-finance-set": {"artifact": clean_ref.as_payload(), "schema_mapping_version": "1.0.0", "period": "2026-01"}},
    ))
    anomaly_ref = _write_json(anomaly, tmp_path, "anomaly.json")

    issues = issue_judge.handle(_envelope(
        "audit.finding.issue-type-judge", "audit.finding.issue-type-judge",
        {"merged-suspicion": {"artifact": anomaly_ref.as_payload()}},
    ))
    assert issues["contract_id"] == "finding-draft"
    assert issues["summary"]["issue_count"] >= 9
    cats = issues["summary"]["by_category"]
    assert cats.get("费用跨期") == 1
    assert cats.get("账务差错") == 2
    assert cats.get("大额交易", 0) >= 6

    issues_ref = _write_json(issues, tmp_path, "issues.json")
    quantified = amount_compute.handle(_envelope(
        "audit.finding.issue-amount-compute", "audit.finding.issue-amount-compute",
        {
            "issue-verify-input": {"artifact": issues_ref.as_payload()},
            "clean-finance-set": {"artifact": clean_ref.as_payload(), "schema_mapping_version": "1.0.0", "period": "2026-01"},
        },
    ))
    assert quantified["contract_id"] == "finding-draft"
    assert quantified["summary"]["quantified_total"] > 0
    assert all(f["amount"] > 0 for f in quantified["findings"] if f["amount_basis"] != "unquantified")
    # unbalanced entries are quantified via entry-level aggregation
    unbalanced = [f for f in quantified["findings"] if f["rule_key"] == "unbalanced_entry"]
    assert unbalanced and all(f["amount"] > 0 for f in unbalanced)

    quantified_ref = _write_json(quantified, tmp_path, "quantified.json")
    report = desc_write.handle(_envelope(
        "audit.report.issue-desc-write", "audit.report.issue-desc-write",
        {"final-issue-set": {"artifact": quantified_ref.as_payload()}},
    ))
    assert report["contract_id"] == "report-draft"
    assert "主要问题" in report["sections"]
    assert report["summary"]["issue_count"] >= 9
    assert all(desc["description"] for desc in report["issues"])
    assert report["summary"]["quantified_total"] > 0
