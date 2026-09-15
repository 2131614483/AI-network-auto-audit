"""End-to-end validation of the Phase 10 R1 plugins against real G:\\ data files.

Every test reads an actual file from ``G:\\数据`` and drives the plugin through
the isolated child-process runtime (the same gate the API uses).  When the
``G:\\数据`` tree is absent (for example on CI without the local dataset), the
whole module is skipped instead of failing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from packages.plugin_runtime.runner import ArtifactInput, IsolatedPluginRuntime, PluginInvocation
from packages.policy.engine import PolicyEngine

GDRIVE = Path("G:/数据")
PCCA = GDRIVE / "04-审计数据集与基准" / "PCCA-Benchmark" / "benchmark_cases" / "bigcase" / "BCSA" / "BCSA_big_01"
JOURNAL_CSV = GDRIVE / "03-AI审计技能包" / "financeskills" / "skills" / "ai-anomaly-detection" / "evals" / "files" / "expense_ledger.csv"
KNOWLEDGE_DOC = GDRIVE / "01-审计知识图谱项目" / "economic_audit_knowledge_graph" / "书籍" / "实体关系抽取.md"
SENSOR_CSV = PCCA / "sensor_data.csv"
FAULT_TICKETS = PCCA / "fault_tickets.json"
CAUSAL_GRAPH = PCCA / "causal_knowledge_graph.json"

_REQUIRED_FILES = (JOURNAL_CSV, KNOWLEDGE_DOC, SENSOR_CSV, FAULT_TICKETS, CAUSAL_GRAPH)

pytestmark = [
    pytest.mark.skipif(not GDRIVE.exists(), reason="G:\\数据 is not mounted on this host"),
    pytest.mark.skipif(any(not path.is_file() for path in _REQUIRED_FILES), reason="G:\\数据 real-data files are missing"),
]


def _artifact(path: Path, *, classification: str = "internal") -> ArtifactInput:
    content = path.read_bytes()
    return ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=uuid4(),
        uri=path.resolve().as_uri(),
        media_type="text/csv" if path.suffix.lower() == ".csv" else "text/markdown" if path.suffix.lower() == ".md" else "application/json",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        classification=classification,
    )


def _invoke(runtime: IsolatedPluginRuntime, invocation: PluginInvocation, *, allow: list[str]) -> dict[str, object]:
    result = runtime.invoke(invocation, PolicyEngine(allow=allow))
    assert len(result.runtime_code_sha256) == 64
    return result.output


def _runtime() -> IsolatedPluginRuntime:
    # The plugins only read; pointing the declared read roots at the user's own
    # G:\\数据 tree is exactly the local dataset scenario the operator intends.
    return IsolatedPluginRuntime(allowed_roots=(GDRIVE.resolve(),))


def test_journal_anomaly_real_expense_ledger() -> None:
    artifact = _artifact(JOURNAL_CSV, classification="audit_confidential")
    output = _invoke(
        _runtime(),
        PluginInvocation(
            tenant_id=artifact.tenant_id,
            trace_id=uuid4(),
            idempotency_key="gdrive-e2e-journal-v1",
            plugin_id="audit.journal-anomaly",
            capability="audit.journal.detect",
            payload={
                "journal": {
                    "artifact": artifact.as_payload(),
                    "schema_mapping_version": "1.0.0",
                    "period": "2026-01",
                }
            },
        ),
        allow=["audit.journal.detect"],
    )
    assert output["contract_id"] == "anomaly-candidates"
    assert output["ledger_sha256"] == artifact.sha256
    assert output["summary"]["total_rows"] == 120
    # The real expense_ledger.csv is an anomaly-detection fixture: it embeds two
    # impossible calendar dates (2026-02-29, 2026-02-30) and one 290.0 outlier.
    keys = {candidate["rule_key"] for candidate in output["candidates"]}
    assert output["summary"]["invalid_dates"] == 2
    assert "invalid_date" in keys
    assert "outlier_amount" in keys


def test_knowledge_entity_relation_real_audit_doc() -> None:
    artifact = _artifact(KNOWLEDGE_DOC)
    output = _invoke(
        _runtime(),
        PluginInvocation(
            tenant_id=artifact.tenant_id,
            trace_id=uuid4(),
            idempotency_key="gdrive-e2e-knowledge-v1",
            plugin_id="knowledge.entity-relation-candidate",
            capability="knowledge.extract.relations",
            payload={"document": {"artifact": artifact.as_payload(), "language": "zh"}},
        ),
        allow=["knowledge.extract.relations"],
    )
    assert output["contract_id"] == "graph-candidate-set"
    assert output["document_sha256"] == artifact.sha256
    assert output["entity_candidates"]
    assert any(entity["entity_type"] != "document" for entity in output["entity_candidates"])


def test_quant_snapshot_guard_real_sensor_series() -> None:
    artifact = _artifact(SENSOR_CSV, classification="restricted")
    output = _invoke(
        _runtime(),
        PluginInvocation(
            tenant_id=artifact.tenant_id,
            trace_id=uuid4(),
            idempotency_key="gdrive-e2e-snapshot-v1",
            plugin_id="quant.snapshot-guard",
            capability="quant.dataset.validate",
            payload={
                "snapshot": {
                    "artifact": artifact.as_payload(),
                    "reference_time": "2025-10-01 15:00:00",
                    "max_freshness_hours": 24,
                    "expected_columns": ["sensor_n001_温度漂移", "sensor_n021_喷头堵塞"],
                }
            },
        ),
        allow=["quant.dataset.validate"],
    )
    assert output["contract_id"] == "dataset-validation"
    assert output["summary"]["checked_rows"] == 6480
    checks = {check["check_id"]: check["status"] for check in output["checks"]}
    assert checks.get("columns") == "pass"
    assert checks.get("freshness") == "pass"


def test_quant_factor_compute_real_sensor_series() -> None:
    artifact = _artifact(SENSOR_CSV, classification="restricted")
    output = _invoke(
        _runtime(),
        PluginInvocation(
            tenant_id=artifact.tenant_id,
            trace_id=uuid4(),
            idempotency_key="gdrive-e2e-factor-v1",
            plugin_id="quant.factor-compute",
            capability="quant.factor.compute",
            payload={
                "dataset": {
                    "artifact": artifact.as_payload(),
                    "reference_time": "2025-10-01 15:00:00",
                    "factors": ["returns", "volatility", "momentum"],
                    "lookback": 252,
                }
            },
        ),
        allow=["quant.factor.compute"],
    )
    assert output["contract_id"] == "factor-artifact-ref"
    assert output["snapshot_sha256"] == artifact.sha256
    factor_ids = {factor["factor_id"] for factor in output["factors"]}
    assert {"returns", "volatility", "momentum"} <= factor_ids


def test_aiops_alert_correlation_real_fault_tickets() -> None:
    artifact = _artifact(FAULT_TICKETS, classification="internal")
    output = _invoke(
        _runtime(),
        PluginInvocation(
            tenant_id=artifact.tenant_id,
            trace_id=uuid4(),
            idempotency_key="gdrive-e2e-alert-v1",
            plugin_id="aiops.alert-correlation",
            capability="aiops.alert.correlate",
            payload={
                "alerts": {
                    "artifact": artifact.as_payload(),
                    "window_minutes": 10080,
                    "max_candidates": 100,
                    "grouping_keys": ["affected_system", "equipment_type"],
                }
            },
        ),
        allow=["aiops.alert.correlate"],
    )
    assert output["contract_id"] == "incident-candidate-set"
    assert output["alert_set_sha256"] == artifact.sha256
    assert output["summary"]["alerts_processed"] > 0
    assert output["candidates"]


def test_aiops_rca_ranker_real_fault_tickets_and_causal_graph() -> None:
    incidents = _artifact(FAULT_TICKETS, classification="internal")
    topology = _artifact(CAUSAL_GRAPH, classification="internal")
    output = _invoke(
        _runtime(),
        PluginInvocation(
            tenant_id=incidents.tenant_id,
            trace_id=uuid4(),
            idempotency_key="gdrive-e2e-rca-v1",
            plugin_id="aiops.rca-ranker",
            capability="aiops.rca.rank",
            payload={
                "rca": {
                    "incident_set": incidents.as_payload(),
                    "topology_graph": topology.as_payload(),
                    "max_candidates": 20,
                    "max_hops": 2,
                }
            },
        ),
        allow=["aiops.rca.rank"],
    )
    assert output["contract_id"] == "rca-candidates"
    assert output["incident_set_sha256"] == incidents.sha256
    assert output["topology_sha256"] == topology.sha256
    assert output["summary"]["incidents_processed"] > 0
    assert output["candidates"]
    assert output["candidates"][0]["rank"] == 1
