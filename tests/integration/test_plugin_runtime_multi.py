from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput, IsolatedPluginRuntime, PluginInvocation
from packages.policy.engine import PolicyEngine


def _artifact(path: Path, classification: str = "internal") -> ArtifactInput:
    content = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=uuid4(),
        uri=path.resolve().as_uri(),
        media_type="application/json" if content[:1] == b"[" or content[:1] == b"{" else "text/csv",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        classification=classification,
    )


def _invoke(runtime: IsolatedPluginRuntime, invocation: PluginInvocation, *, allow: list[str]) -> dict[str, object]:
    result = runtime.invoke(invocation, PolicyEngine(allow=allow))
    assert len(result.runtime_code_sha256) == 64
    assert result.trace_id
    return result.output


def _write(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.write_text(content, encoding="utf-8")
    return path


def test_journal_anomaly_runs_in_isolated_child_process(tmp_path: Path) -> None:
    rows = ["entry_id,date,account_code,description,amount"]
    for i in range(20):
        rows.append(f"V-{i:02d},2026-01-05,1001,采购,{1000 + i}.50")
    rows.append("V-99,2026-01-12,2001,大额,99999999.99")
    source = _write(tmp_path, "journal.csv", "\n".join(rows) + "\n")
    artifact = _artifact(source, classification="audit_confidential")
    invocation = PluginInvocation(
        tenant_id=artifact.tenant_id,
        trace_id=uuid4(),
        idempotency_key="e2e-journal-v1",
        plugin_id="audit.journal-anomaly",
        capability="audit.journal.detect",
        payload={
            "journal": {"artifact": artifact.as_payload(), "schema_mapping_version": "1.0.0", "period": "2026-01"}
        },
    )
    output = _invoke(IsolatedPluginRuntime(allowed_roots=(tmp_path,)), invocation, allow=["audit.journal.detect"])
    assert output["contract_id"] == "anomaly-candidates"
    keys = {candidate["rule_key"] for candidate in output["candidates"]}
    assert "outlier_amount" in keys
    assert output["ledger_sha256"] == artifact.sha256
    assert output["summary"]["total_rows"] == 21


def test_knowledge_entity_relation_runs_in_isolated_child_process(tmp_path: Path) -> None:
    doc = _write(tmp_path, "memo.md", "审计报告：甲公司向乙公司采购设备，乙公司参股丙公司。\n")
    artifact = _artifact(doc)
    invocation = PluginInvocation(
        tenant_id=artifact.tenant_id,
        trace_id=uuid4(),
        idempotency_key="e2e-knowledge-v1",
        plugin_id="knowledge.entity-relation-candidate",
        capability="knowledge.extract.relations",
        payload={"document": {"artifact": artifact.as_payload(), "language": "zh"}},
    )
    output = _invoke(IsolatedPluginRuntime(allowed_roots=(tmp_path,)), invocation, allow=["knowledge.extract.relations"])
    assert output["contract_id"] == "graph-candidate-set"
    assert output["document_sha256"] == artifact.sha256
    assert output["entity_candidates"]
    assert output["relation_candidates"]


def test_quant_snapshot_guard_runs_in_isolated_child_process(tmp_path: Path) -> None:
    csv_text = (
        "timestamp,open,close,volume\n"
        "2026-01-02T09:30:00,100.0,101.5,1200\n"
        "2026-01-02T09:31:00,101.5,102.0,980\n"
    )
    source = _write(tmp_path, "snapshot.csv", csv_text)
    artifact = _artifact(source, classification="restricted")
    invocation = PluginInvocation(
        tenant_id=artifact.tenant_id,
        trace_id=uuid4(),
        idempotency_key="e2e-snapshot-v1",
        plugin_id="quant.snapshot-guard",
        capability="quant.dataset.validate",
        payload={"snapshot": {"artifact": artifact.as_payload(), "reference_time": "2026-01-02T10:00:00"}},
    )
    output = _invoke(IsolatedPluginRuntime(allowed_roots=(tmp_path,)), invocation, allow=["quant.dataset.validate"])
    assert output["contract_id"] == "dataset-validation"
    assert output["snapshot_sha256"] == artifact.sha256
    assert output["summary"]["checked_rows"] == 2


def test_quant_factor_compute_runs_in_isolated_child_process(tmp_path: Path) -> None:
    csv_text = (
        "timestamp,close,volume\n"
        "2026-01-02T09:30:00,100.0,1200\n"
        "2026-01-02T09:31:00,101.0,980\n"
        "2026-01-02T09:32:00,100.5,1500\n"
        "2026-01-02T09:33:00,102.0,1100\n"
        "2026-01-02T09:34:00,103.0,900\n"
    )
    source = _write(tmp_path, "prices.csv", csv_text)
    artifact = _artifact(source, classification="restricted")
    invocation = PluginInvocation(
        tenant_id=artifact.tenant_id,
        trace_id=uuid4(),
        idempotency_key="e2e-factor-v1",
        plugin_id="quant.factor-compute",
        capability="quant.factor.compute",
        payload={
            "dataset": {
                "artifact": artifact.as_payload(),
                "reference_time": "2026-01-02T10:00:00",
                "factors": ["returns", "volatility"],
                "lookback": 4,
            }
        },
    )
    output = _invoke(IsolatedPluginRuntime(allowed_roots=(tmp_path,)), invocation, allow=["quant.factor.compute"])
    assert output["contract_id"] == "factor-artifact-ref"
    assert output["snapshot_sha256"] == artifact.sha256
    factor_ids = {factor["factor_id"] for factor in output["factors"]}
    assert {"returns", "volatility"}.issubset(factor_ids)


def test_alert_correlation_runs_in_isolated_child_process(tmp_path: Path) -> None:
    alerts = json.dumps(
        [
            {"ticket_id": "FT_001", "timestamp": "2025-08-18T15:28:50", "severity": "中", "affected_system": "溶剂浓度"},
            {"ticket_id": "FT_002", "timestamp": "2025-08-18T15:45:10", "severity": "高", "affected_system": "溶剂浓度"},
        ],
        ensure_ascii=False,
    )
    source = _write(tmp_path, "alerts.json", alerts)
    artifact = _artifact(source)
    invocation = PluginInvocation(
        tenant_id=artifact.tenant_id,
        trace_id=uuid4(),
        idempotency_key="e2e-alert-correlate-v1",
        plugin_id="aiops.alert-correlation",
        capability="aiops.alert.correlate",
        payload={"alerts": {"artifact": artifact.as_payload(), "window_minutes": 60, "max_candidates": 10}},
    )
    output = _invoke(IsolatedPluginRuntime(allowed_roots=(tmp_path,)), invocation, allow=["aiops.alert.correlate"])
    assert output["contract_id"] == "incident-candidate-set"
    assert output["summary"]["alerts_processed"] == 2
    assert output["candidates"][0]["count"] == 2


def test_rca_ranker_runs_in_isolated_child_process(tmp_path: Path) -> None:
    incidents = json.dumps(
        [
            {
                "ticket_id": "FT_001",
                "timestamp": "2025-08-18T15:28:50",
                "fault_description": "喷头堵塞",
                "affected_system": "环境湿度",
                "root_cause": "墨水粘度异常",
            },
            {
                "ticket_id": "FT_002",
                "timestamp": "2025-08-19T09:00:00",
                "fault_description": "喷头堵塞",
                "affected_system": "环境湿度",
                "root_cause": "墨水粘度异常",
            },
        ],
        ensure_ascii=False,
    )
    graph = json.dumps(
        {
            "nodes_by_type": {
                "故障": [{"id": "n001", "text": "墨水粘度异常", "node_type": "故障", "confidence": 0.9}],
                "现象": [{"id": "n003", "text": "喷头堵塞", "node_type": "现象", "confidence": 0.7}],
            },
            "edges": {"n001->n003": [{"source": "n001", "target": "n003", "confidence": 0.9, "strength": 0.8}]},
        },
        ensure_ascii=False,
    )
    incidents_file = _write(tmp_path, "incidents.json", incidents)
    graph_file = _write(tmp_path, "topology.json", graph)
    artifact_i = _artifact(incidents_file)
    artifact_g = _artifact(graph_file)
    invocation = PluginInvocation(
        tenant_id=artifact_i.tenant_id,
        trace_id=uuid4(),
        idempotency_key="e2e-rca-v1",
        plugin_id="aiops.rca-ranker",
        capability="aiops.rca.rank",
        payload={
            "rca": {
                "incident_set": artifact_i.as_payload(),
                "topology_graph": artifact_g.as_payload(),
                "window_minutes": 1440,
                "max_candidates": 10,
                "max_hops": 2,
            }
        },
    )
    output = _invoke(IsolatedPluginRuntime(allowed_roots=(tmp_path,)), invocation, allow=["aiops.rca.rank"])
    assert output["contract_id"] == "rca-candidates"
    assert output["summary"]["incidents_processed"] == 2
    assert output["candidates"][0]["root_cause"] == "墨水粘度异常"
    assert output["incident_set_sha256"] == artifact_i.sha256


def test_policy_denies_each_new_capability_without_starting_a_child(tmp_path: Path) -> None:
    source = _write(tmp_path, "alerts.json", json.dumps([], ensure_ascii=False))
    artifact = _artifact(source)
    invocation = PluginInvocation(
        tenant_id=artifact.tenant_id,
        trace_id=uuid4(),
        idempotency_key="e2e-denied-v1",
        plugin_id="aiops.alert-correlation",
        capability="aiops.alert.correlate",
        payload={"alerts": {"artifact": artifact.as_payload(), "window_minutes": 60, "max_candidates": 10}},
    )
    runtime = IsolatedPluginRuntime(allowed_roots=(tmp_path,))
    with pytest.raises(Exception, match="policy gateway blocked"):
        runtime.invoke(invocation, PolicyEngine(deny=["aiops.alert.correlate"]))
