from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from apps.api.main import Settings, create_app
from packages.plugin_runtime.registration import (
    publish_alert_triage_allow_policy,
    publish_evidence_lineage_allow_policy,
    publish_finding_draft_allow_policy,
    publish_graph_proposal_allow_policy,
    publish_investigation_plan_allow_policy,
    publish_ledger_quality_allow_policy,
    publish_playbook_proposer_allow_policy,
    publish_postmortem_draft_allow_policy,
    publish_r1_plugins_allow_policy,
    publish_read_only_allow_policy,
    publish_recovery_verifier_allow_policy,
    publish_report_draft_allow_policy,
    publish_research_note_draft_allow_policy,
    publish_retention_recommendation_allow_policy,
    publish_simulated_backtest_allow_policy,
    publish_ticket_draft_allow_policy,
    publish_workpaper_export_allow_policy,
    register_verified_builtin,
)

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")
def _tenant_id() -> UUID:
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            row = cur.fetchone()
            assert row is not None
            return row[0]


def _register_verified_plugin_and_allow_capability(tenant_id: UUID) -> None:
    """Registration and policy publication are explicit setup steps."""

    register_verified_builtin(DB)
    assert publish_read_only_allow_policy(DB) == tenant_id
    assert publish_ledger_quality_allow_policy(DB) == tenant_id
    assert publish_investigation_plan_allow_policy(DB) == tenant_id
    assert publish_finding_draft_allow_policy(DB) == tenant_id
    assert publish_playbook_proposer_allow_policy(DB) == tenant_id
    assert publish_recovery_verifier_allow_policy(DB) == tenant_id
    assert publish_alert_triage_allow_policy(DB) == tenant_id
    assert publish_evidence_lineage_allow_policy(DB) == tenant_id
    assert publish_simulated_backtest_allow_policy(DB) == tenant_id
    assert publish_r1_plugins_allow_policy(DB) == tenant_id
    assert publish_workpaper_export_allow_policy(DB) == tenant_id
    assert publish_report_draft_allow_policy(DB) == tenant_id
    assert publish_ticket_draft_allow_policy(DB) == tenant_id
    assert publish_retention_recommendation_allow_policy(DB) == tenant_id
    assert publish_postmortem_draft_allow_policy(DB) == tenant_id
    assert publish_research_note_draft_allow_policy(DB) == tenant_id
    assert publish_graph_proposal_allow_policy(DB) == tenant_id


def test_verified_plugin_runtime_api_reads_a_local_artifact_after_policy_gate(tmp_path: Path) -> None:
    """The API invokes only the registered, verified, policy-approved builtin."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    source = tmp_path / "phase1-e2e.md"
    source.write_text("# Phase 1 验收样本\n\n插件只能读取这个受限文件。\n", encoding="utf-8")
    raw = source.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    status_response = client.get(
        "/api/v1/plugins/verified",
        headers={"X-Tenant-Id": str(tenant_id)},
    )
    assert status_response.status_code == 200, status_response.text
    trace = status_response.json()["trace_id"]
    assert status_response.json()["items"] == [
        {
            "plugin_id": plugin_id,
            "plugin_version": "0.1.0",
            "capability": capability,
            "execution_mode": "isolated_subprocess",
            "catalog_registered": True,
            "side_effects": "read_only",
            "trace_id": trace,
        }
        for plugin_id, capability in (
            ("aiops.alert-correlation", "aiops.alert.correlate"),
            ("aiops.alert-triage", "aiops.alert.triage"),
            ("aiops.playbook-proposer", "aiops.remediation.propose"),
            ("aiops.postmortem-draft", "aiops.postmortem.draft"),
            ("aiops.rca-ranker", "aiops.rca.rank"),
            ("aiops.recovery-verifier", "aiops.recovery.verify"),
            ("aiops.ticket-draft", "aiops.ticket.draft"),
            ("audit.evidence-lineage", "audit.evidence.lineage"),
            ("audit.evidence.e-signature", "audit.evidence.e-signature"),
            ("audit.evidence.evidence-archive", "audit.evidence.evidence-archive"),
            ("audit.evidence.evidence-index-link", "audit.evidence.evidence-index-link"),
            ("audit.evidence.evidence-verify", "audit.evidence.evidence-verify"),
            ("audit.evidence.workpaper-borrow-approve", "audit.evidence.workpaper-borrow-approve"),
            ("audit.evidence.workpaper-encrypt-store", "audit.evidence.workpaper-encrypt-store"),
            ("audit.evidence.workpaper-reconcile", "audit.evidence.workpaper-reconcile"),
            ("audit.evidence.workpaper-review3", "audit.evidence.workpaper-review3"),
            ("audit.evidence.workpaper-template-update", "audit.evidence.workpaper-template-update"),
            ("audit.evidence.workpaper-version-diff", "audit.evidence.workpaper-version-diff"),
            ("audit.field.asset-check", "audit.field.asset-check"),
            ("audit.field.audit-log", "audit.field.audit-log"),
            ("audit.field.confirm-letter", "audit.field.confirm-letter"),
            ("audit.field.cross-dept-inquiry", "audit.field.cross-dept-inquiry"),
            ("audit.field.evidence-photo", "audit.field.evidence-photo"),
            ("audit.field.extension-approve", "audit.field.extension-approve"),
            ("audit.field.interview-record", "audit.field.interview-record"),
            ("audit.field.inventory-count", "audit.field.inventory-count"),
            ("audit.field.meeting-minutes", "audit.field.meeting-minutes"),
            ("audit.field.progress-report", "audit.field.progress-report"),
            ("audit.field.site-checkin-track", "audit.field.site-checkin-track"),
            ("audit.field.suspicion-flag", "audit.field.suspicion-flag"),
            ("audit.field.voucher-drilldown", "audit.field.voucher-drilldown"),
            ("audit.field.workpaper-build", "audit.field.workpaper-build"),
            ("audit.finding-draft", "audit.finding.draft"),
            ("audit.finding.auditee-feedback", "audit.finding.auditee-feedback"),
            ("audit.finding.issue-amount-compute", "audit.finding.issue-amount-compute"),
            ("audit.finding.issue-final-review", "audit.finding.issue-final-review"),
            ("audit.finding.issue-grade", "audit.finding.issue-grade"),
            ("audit.finding.issue-type-judge", "audit.finding.issue-type-judge"),
            ("audit.finding.responsible-party-find", "audit.finding.responsible-party-find"),
            ("audit.finding.suspicion-merge", "audit.finding.suspicion-merge"),
            ("audit.finding.violation-clause-match", "audit.finding.violation-clause-match"),
            ("audit.foundation.biz-standardize", "audit.foundation.biz-standardize"),
            ("audit.foundation.data-encrypt", "audit.foundation.data-encrypt"),
            ("audit.foundation.data-mask", "audit.foundation.data-mask"),
            ("audit.foundation.finance-clean", "audit.foundation.finance-clean"),
            ("audit.foundation.fulltext-search", "audit.foundation.fulltext-search"),
            ("audit.foundation.lineage-track", "audit.foundation.lineage-track"),
            ("audit.foundation.master-mapping", "audit.foundation.master-mapping"),
            ("audit.foundation.metadata-manage", "audit.foundation.metadata-manage"),
            ("audit.foundation.metric-compute", "audit.foundation.metric-compute"),
            ("audit.foundation.multi-source-collect", "audit.foundation.multi-source-collect"),
            ("audit.foundation.nlp-process", "audit.foundation.nlp-process"),
            ("audit.foundation.ocr-extract", "audit.foundation.ocr-extract"),
            ("audit.foundation.permission-control", "audit.foundation.permission-control"),
            ("audit.foundation.quality-check", "audit.foundation.quality-check"),
            ("audit.foundation.rule-engine", "audit.foundation.rule-engine"),
            ("audit.foundation.tag-manage", "audit.foundation.tag-manage"),
            ("audit.foundation.viz-analysis", "audit.foundation.viz-analysis"),
            ("audit.foundation.workflow-engine", "audit.foundation.workflow-engine"),
            ("audit.govern.case-library-update", "audit.govern.case-library-update"),
            ("audit.govern.effect-evaluate", "audit.govern.effect-evaluate"),
            ("audit.govern.issue-trend-analysis", "audit.govern.issue-trend-analysis"),
            ("audit.govern.method-distill", "audit.govern.method-distill"),
            ("audit.govern.project-quality-score", "audit.govern.project-quality-score"),
            ("audit.govern.rule-iteration", "audit.govern.rule-iteration"),
            ("audit.investigation-plan", "audit.investigation.plan"),
            ("audit.journal-anomaly", "audit.journal.detect"),
            ("audit.ledger-quality", "audit.ledger.validate"),
            ("audit.mandate.annual-propose", "audit.mandate.annual-propose"),
            ("audit.mandate.demand-collect", "audit.mandate.demand-collect"),
            ("audit.mandate.material-precheck", "audit.mandate.material-precheck"),
            ("audit.mandate.material-submit", "audit.mandate.material-submit"),
            ("audit.mandate.notice-generate", "audit.mandate.notice-generate"),
            ("audit.mandate.priority-rank", "audit.mandate.priority-rank"),
            ("audit.mandate.project-library", "audit.mandate.project-library"),
            ("audit.mandate.proposal-score", "audit.mandate.proposal-score"),
            ("audit.mandate.strategy-align", "audit.mandate.strategy-align"),
            ("audit.mandate.team-forming", "audit.mandate.team-forming"),
            ("audit.plan.annual-plan-build", "audit.plan.annual-plan-build"),
            ("audit.plan.effort-budget", "audit.plan.effort-budget"),
            ("audit.plan.plan-version-control", "audit.plan.plan-version-control"),
            ("audit.plan.program-template-match", "audit.plan.program-template-match"),
            ("audit.plan.project-scheme-build", "audit.plan.project-scheme-build"),
            ("audit.plan.resource-conflict-detect", "audit.plan.resource-conflict-detect"),
            ("audit.plan.sample-size-compute", "audit.plan.sample-size-compute"),
            ("audit.plan.sampling-select", "audit.plan.sampling-select"),
            ("audit.plan.staff-schedule", "audit.plan.staff-schedule"),
            ("audit.remedy.remedy-close", "audit.remedy.remedy-close"),
            ("audit.remedy.remedy-dispatch", "audit.remedy.remedy-dispatch"),
            ("audit.remedy.remedy-effect-verify", "audit.remedy.remedy-effect-verify"),
            ("audit.remedy.remedy-overdue-alert", "audit.remedy.remedy-overdue-alert"),
            ("audit.remedy.remedy-plan-review", "audit.remedy.remedy-plan-review"),
            ("audit.remedy.remedy-progress-track", "audit.remedy.remedy-progress-track"),
            ("audit.remedy.remedy-publish", "audit.remedy.remedy-publish"),
            ("audit.report-draft", "audit.report.draft"),
            ("audit.report.advice-match", "audit.report.advice-match"),
            ("audit.report.issue-desc-write", "audit.report.issue-desc-write"),
            ("audit.report.notice-mask-publish", "audit.report.notice-mask-publish"),
            ("audit.report.report-data-check", "audit.report.report-data-check"),
            ("audit.report.report-frame-build", "audit.report.report-frame-build"),
            ("audit.report.report-multi-review", "audit.report.report-multi-review"),
            ("audit.report.result-distill", "audit.report.result-distill"),
            ("audit.risk.finance-anomaly-alert", "audit.risk.finance-anomaly-alert"),
            ("audit.risk.fraud-risk-match", "audit.risk.fraud-risk-match"),
            ("audit.risk.high-risk-area-locate", "audit.risk.high-risk-area-locate"),
            ("audit.risk.industry-risk-benchmark", "audit.risk.industry-risk-benchmark"),
            ("audit.risk.internal-control-risk-map", "audit.risk.internal-control-risk-map"),
            ("audit.risk.macro-policy-risk-scan", "audit.risk.macro-policy-risk-scan"),
            ("audit.risk.process-gap-detect", "audit.risk.process-gap-detect"),
            ("audit.risk.risk-advice-generate", "audit.risk.risk-advice-generate"),
            ("audit.risk.risk-heatmap-draw", "audit.risk.risk-heatmap-draw"),
            ("audit.risk.risk-level-assign", "audit.risk.risk-level-assign"),
            ("audit.risk.risk-matrix-build", "audit.risk.risk-matrix-build"),
            ("audit.workpaper-export", "audit.workpaper.export"),
            ("knowledge.document-ingestion", "knowledge.extract.document"),
            ("knowledge.entity-relation-candidate", "knowledge.extract.relations"),
            ("knowledge.graph-proposal-builder", "graph.proposal.build"),
            ("knowledge.retention-recommendation", "knowledge.retention.recommend"),
            ("quant.experiment-evaluator", "quant.experiment.evaluate"),
            ("quant.factor-compute", "quant.factor.compute"),
            ("quant.research-note-draft", "quant.research-note.draft"),
            ("quant.simulated-backtest", "quant.backtest.simulate"),
            ("quant.snapshot-guard", "quant.dataset.validate"),
        )
    ]

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "knowledge.document-ingestion",
            "capability": "knowledge.extract.document",
            "artifact": {
                "artifact_id": str(uuid4()),
                "tenant_id": str(tenant_id),
                "uri": source.resolve().as_uri(),
                "media_type": "text/markdown",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "classification": "internal",
            },
            "max_characters": 4096,
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-runtime-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "knowledge.document-ingestion"
    assert payload["capability"] == "knowledge.extract.document"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert payload["document"]["content"].startswith("# Phase 1 验收样本")
    assert len(payload["runtime_code_sha256"]) == 64

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id', tool_call.arguments->>'artifact_sha256'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='knowledge.extract.document'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "knowledge.document-ingestion", hashlib.sha256(raw).hexdigest())


def test_ledger_quality_plugin_api_validates_snapshot_after_policy_gate(tmp_path: Path) -> None:
    """The ledger-quality capability is reachable only through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    ledger = tmp_path / "ledger-2026-02.csv"
    ledger.write_text(
        "entry_id,date,account_code,description,debit_amount,credit_amount\n"
        "V-10,2026-02-03,1001,现金,500.00,0.00\n"
        "V-10,2026-02-03,8001,收入,0.00,500.00\n",
        encoding="utf-8",
    )
    raw = ledger.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "audit.ledger-quality",
            "capability": "audit.ledger.validate",
            "ledger": {
                "artifact": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": ledger.resolve().as_uri(),
                    "media_type": "text/csv",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "audit_confidential",
                },
                "schema_mapping_version": "1.0.0",
                "period": "2026-02",
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-ledger-quality-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "audit.ledger-quality"
    assert payload["capability"] == "audit.ledger.validate"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(payload["runtime_code_sha256"]) == 64
    output = payload["document"]
    assert output["contract_id"] == "audit-quality-candidates"
    assert set(output.keys()) == {
        "contract_id", "contract_version", "ledger_sha256", "schema_mapping_version",
        "period", "rule_pack_sha256", "rules", "summary", "candidates",
    }
    assert output["summary"]["total_rows"] == 2
    assert output["summary"]["candidate_count"] == 0
    assert output["candidates"] == []

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='audit.ledger.validate'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "audit.ledger-quality")


def test_verified_plugin_runtime_api_denial_returns_before_isolated_execution(tmp_path: Path) -> None:
    """A tenant deny rule takes precedence and the API must not return document output."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    policy_name = f"phase1-plugin-runtime-deny-{uuid4().hex}"
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                "INSERT INTO policy.policy_sets(tenant_id,name,version,status,rules) VALUES(%s,%s,1,'active',%s)",
                (
                    tenant_id,
                    policy_name,
                    Json(
                        [
                            {
                                "rule_id": str(uuid4()),
                                "effect": "deny",
                                "match": {"capabilities": ["knowledge.extract.document"]},
                            }
                        ]
                    ),
                ),
            )
    source = tmp_path / "denied.md"
    source.write_text("如果策略正确，隔离进程不能读取这段内容。", encoding="utf-8")
    raw = source.read_bytes()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))
    try:
        response = client.post(
            "/api/v1/plugins/invoke",
            json={
                "plugin_id": "knowledge.document-ingestion",
                "capability": "knowledge.extract.document",
                "artifact": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": source.resolve().as_uri(),
                    "media_type": "text/markdown",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "internal",
                },
            },
            headers={
                "X-Tenant-Id": str(tenant_id),
                "Idempotency-Key": f"phase1-runtime-deny-{uuid4().hex}",
            },
        )
    finally:
        with psycopg2.connect(DB) as connection:
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
                cur.execute(
                    "UPDATE policy.policy_sets SET status='inactive' WHERE tenant_id=%s AND name=%s",
                    (tenant_id, policy_name),
                )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["message"] == "policy gateway blocked capability"
    assert "document" not in response.json()


def test_journal_anomaly_plugin_api_after_policy_gate(tmp_path: Path) -> None:
    """The journal.detect capability is reachable only through the R1 policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    journal = tmp_path / "journal-2026-01.csv"
    rows = ["entry_id,date,account_code,description,amount"]
    for i in range(20):
        rows.append(f"V-{i:02d},2026-01-05,1001,采购,{1000 + i}.50")
    rows.append("V-99,2026-01-12,2001,大额,99999999.99")
    journal.write_text("\n".join(rows) + "\n", encoding="utf-8")
    raw = journal.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "audit.journal-anomaly",
            "capability": "audit.journal.detect",
            "journal": {
                "artifact": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": journal.resolve().as_uri(),
                    "media_type": "text/csv",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "audit_confidential",
                },
                "schema_mapping_version": "1.0.0",
                "period": "2026-01",
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-journal-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "audit.journal-anomaly"
    assert payload["capability"] == "audit.journal.detect"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    output = payload["document"]
    assert output["contract_id"] == "anomaly-candidates"
    keys = {candidate["rule_key"] for candidate in output["candidates"]}
    assert "outlier_amount" in keys
    assert output["ledger_sha256"] == hashlib.sha256(raw).hexdigest()


def test_rca_ranker_plugin_api_after_policy_gate(tmp_path: Path) -> None:
    """The rca.rank capability uses incident + topology artifacts through the R1 policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    incidents = tmp_path / "incidents.json"
    incidents.write_text(
        json.dumps(
            [
                {"ticket_id": "FT_001", "timestamp": "2025-08-18T15:28:50", "fault_description": "喷头堵塞"},
                {"ticket_id": "FT_002", "timestamp": "2025-08-19T09:00:00", "fault_description": "喷头堵塞"},
            ]
        ),
        encoding="utf-8",
    )
    topology = tmp_path / "topology.json"
    topology.write_text(
        json.dumps(
            {
                "nodes_by_type": {
                    "故障": [{"id": "n001", "text": "墨水粘度异常", "confidence": 0.9}],
                    "现象": [{"id": "n003", "text": "喷头堵塞", "confidence": 0.9}],
                },
                "edges": {"n001->n003": [{"source": "n001", "target": "n003", "confidence": 0.9, "strength": 0.8}]},
            }
        ),
        encoding="utf-8",
    )
    incidents_raw = incidents.read_bytes()
    topology_raw = topology.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "aiops.rca-ranker",
            "capability": "aiops.rca.rank",
            "rca": {
                "incident_set": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": incidents.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(incidents_raw).hexdigest(),
                    "size_bytes": len(incidents_raw),
                    "classification": "internal",
                },
                "topology_graph": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": topology.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(topology_raw).hexdigest(),
                    "size_bytes": len(topology_raw),
                    "classification": "internal",
                },
                "max_candidates": 10,
                "max_hops": 2,
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-rca-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "aiops.rca-ranker"
    assert payload["capability"] == "aiops.rca.rank"
    assert payload["trace_id"] == str(trace_id)
    output = payload["document"]
    assert output["contract_id"] == "rca-candidates"
    assert output["candidates"][0]["root_cause"] == "墨水粘度异常"
    assert output["summary"]["incidents_processed"] == 2


def test_graph_proposal_plugin_api_returns_shadow_draft_after_policy_gate(tmp_path: Path) -> None:
    """The graph.proposal.build capability reads candidate+snapshot and returns a non-mutating draft."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps(
            {
                "contract_id": "graph-candidate-set",
                "contract_version": "1.0.0",
                "entity_candidates": [
                    {
                        "candidate_id": "organization::审计局",
                        "entity_type": "organization",
                        "name": "审计局",
                        "span": "审计局",
                        "source_ref": "document:a1:span",
                        "confidence": 0.9,
                        "evidence": {},
                    },
                    {
                        "candidate_id": "document::《审计指引》",
                        "entity_type": "document",
                        "name": "《审计指引》",
                        "span": "《审计指引》",
                        "source_ref": "document:a1:line",
                        "confidence": 0.75,
                        "evidence": {},
                    },
                ],
                "relation_candidates": [
                    {
                        "candidate_id": "rel:cites",
                        "source_id": "organization::审计局",
                        "target_id": "document::《审计指引》",
                        "relation_type": "cites",
                        "span": "审计局依据《审计指引》",
                        "confidence": 0.7,
                        "evidence": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "contract_id": "graph-snapshot",
                "contract_version": "1.0.0",
                "space_level": "L1",
                "nodes_by_type": {
                    "organization": [],
                    "document": [],
                    "person": [],
                    "generic": [],
                },
            }
        ),
        encoding="utf-8",
    )
    candidates_raw = candidates.read_bytes()
    snapshot_raw = snapshot.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "knowledge.graph-proposal-builder",
            "capability": "graph.proposal.build",
            "proposal": {
                "candidate_set": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": candidates.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(candidates_raw).hexdigest(),
                    "size_bytes": len(candidates_raw),
                    "classification": "internal",
                },
                "graph_snapshot": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": snapshot.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(snapshot_raw).hexdigest(),
                    "size_bytes": len(snapshot_raw),
                    "classification": "internal",
                },
                "space_level": "L1",
                "max_proposals": 1000,
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-proposal-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "knowledge.graph-proposal-builder"
    assert payload["capability"] == "graph.proposal.build"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(candidates_raw).hexdigest()
    output = payload["document"]
    assert output["contract_id"] == "graph-proposal-draft"
    assert len(output["entity_proposals"]) == 2
    assert len(output["relation_proposals"]) == 1
    assert output["summary"]["entities_proposed"] == 2
    assert output["summary"]["relations_proposed"] == 1
    assert len(output["draft_id"]) == 16
    assert output["graph_snapshot_sha256"] == hashlib.sha256(snapshot_raw).hexdigest()

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='graph.proposal.build'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "knowledge.graph-proposal-builder")


def test_investigation_plan_plugin_api_returns_plan_draft_after_policy_gate(tmp_path: Path) -> None:
    """The audit.investigation.plan capability reads anomaly candidates and returns a non-mutating draft."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    anomalies = tmp_path / "anomalies.json"
    anomalies.write_text(
        json.dumps(
            {
                "contract_id": "anomaly-candidates",
                "contract_version": "1.0.0",
                "ledger_sha256": "a" * 64,
                "schema_mapping_version": "1.0.0",
                "period": "2026-03",
                "rule_pack_sha256": "b" * 64,
                "rules": ["invalid_date", "outlier_amount"],
                "summary": {"total_rows": 121, "candidate_count": 2, "invalid_dates": 1, "outliers": 1},
                "candidates": [
                    {
                        "rule_id": "invalid_date:60",
                        "rule_key": "invalid_date",
                        "severity": "high",
                        "row_ref": "60",
                        "source_ref": "journal:a1b2c3d4:row:60",
                        "score": 0.85,
                        "evidence": {"date": "2026-02-30"},
                    },
                    {
                        "rule_id": "outlier_amount:59",
                        "rule_key": "outlier_amount",
                        "severity": "high",
                        "row_ref": "59",
                        "source_ref": "journal:a1b2c3d4:row:59",
                        "score": 0.9,
                        "evidence": {"amount": 290.0, "zscore": 98.2},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "contract_id": "audit-evidence-index",
                "contract_version": "1.0.0",
                "period": "2026-03",
                "evidence": [
                    {"evidence_id": "ev:1", "evidence_type": "原始凭单", "source_ref": "evidence:bank:1"},
                ],
            }
        ),
        encoding="utf-8",
    )
    anomalies_raw = anomalies.read_bytes()
    evidence_raw = evidence.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "audit.investigation-plan",
            "capability": "audit.investigation.plan",
            "investigation": {
                "anomaly_candidates": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": anomalies.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(anomalies_raw).hexdigest(),
                    "size_bytes": len(anomalies_raw),
                    "classification": "audit_confidential",
                },
                "evidence_index": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": evidence.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(evidence_raw).hexdigest(),
                    "size_bytes": len(evidence_raw),
                    "classification": "audit_confidential",
                },
                "max_steps": 8,
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-investigation-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "audit.investigation-plan"
    assert payload["capability"] == "audit.investigation.plan"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(anomalies_raw).hexdigest()
    output = payload["document"]
    assert output["contract_id"] == "investigation-plan-draft"
    assert output["period"] == "2026-03"
    assert len(output["steps"]) == 2
    assert {step["rule_key"] for step in output["steps"]} == {"invalid_date", "outlier_amount"}
    assert len(output["counter_hypotheses"]) == 2
    # 已收集的证据类型（原始凭单）不再出现在待补证据中。
    gap_types = {item["evidence_type"] for item in output["evidence_gaps"]}
    assert "原始凭单" not in gap_types
    assert output["evidence_bundle_sha256"] == hashlib.sha256(evidence_raw).hexdigest()
    assert len(output["plan_id"]) == 16

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='audit.investigation.plan'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "audit.investigation-plan")


def test_finding_draft_plugin_api_returns_finding_draft_after_policy_gate(tmp_path: Path) -> None:
    """The audit.finding.draft capability returns a Claim/EvidenceRef shadow draft, never a confirmed Finding."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    anomalies = tmp_path / "anomalies.json"
    anomalies.write_text(
        json.dumps(
            {
                "contract_id": "anomaly-candidates",
                "contract_version": "1.0.0",
                "ledger_sha256": "a" * 64,
                "schema_mapping_version": "1.0.0",
                "period": "2026-03",
                "rule_pack_sha256": "b" * 64,
                "rules": ["invalid_date", "outlier_amount"],
                "summary": {"total_rows": 121, "candidate_count": 2, "invalid_dates": 1, "outliers": 1},
                "candidates": [
                    {
                        "rule_id": "invalid_date:60",
                        "rule_key": "invalid_date",
                        "severity": "high",
                        "row_ref": "60",
                        "source_ref": "journal:a1b2c3d4:row:60",
                        "reason_code": "INVALID_DATE",
                        "score": 0.85,
                        "evidence": {"source_sha256": "a" * 64, "date": "2026-02-30"},
                    },
                    {
                        "rule_id": "outlier_amount:59",
                        "rule_key": "outlier_amount",
                        "severity": "low",
                        "row_ref": "59",
                        "source_ref": "journal:a1b2c3d4:row:59",
                        "reason_code": "OUTLIER_AMOUNT",
                        "score": 0.55,
                        "evidence": {"source_sha256": "a" * 64, "amount": 290.0},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    anomalies_raw = anomalies.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "audit.finding-draft",
            "capability": "audit.finding.draft",
            "finding": {
                "anomaly_candidates": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": anomalies.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(anomalies_raw).hexdigest(),
                    "size_bytes": len(anomalies_raw),
                    "classification": "audit_confidential",
                }
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-finding-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "audit.finding-draft"
    assert payload["capability"] == "audit.finding.draft"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(anomalies_raw).hexdigest()
    output = payload["document"]
    assert output["contract_id"] == "finding-draft"
    assert output["period"] == "2026-03"
    assert len(output["findings"]) == 2
    assert all(finding["status"] == "proposed" for finding in output["findings"])
    assert all(finding["claims"] for finding in output["findings"])
    assert all(finding["counter_hypotheses"] for finding in output["findings"])
    assert len(output["draft_id"]) == 16

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='audit.finding.draft'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "audit.finding-draft")


def test_playbook_proposer_plugin_api_returns_remediation_proposal_after_policy_gate(tmp_path: Path) -> None:
    """The aiops.remediation.propose capability returns a shadow proposal, never a ChangeRequest or execution."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    rca = tmp_path / "rca-candidates.json"
    rca.write_text(
        json.dumps(
            {
                "contract_id": "rca-candidates",
                "contract_version": "1.0.0",
                "incident_set_sha256": "a" * 64,
                "topology_sha256": "b" * 64,
                "summary": {"incidents_processed": 3, "nodes_considered": 5, "candidate_count": 2, "truncated": False},
                "candidates": [
                    {
                        "rank": 1,
                        "candidate_id": "RC-0001",
                        "root_cause": "数据库连接池耗尽导致查询超时",
                        "node_ref": "n001",
                        "score": 0.85,
                        "confidence": 0.9,
                        "support_count": 12,
                        "supporting_incidents": ["INC-001", "INC-002"],
                        "evidence": {"node_type": "故障", "source_sha256": "c" * 64},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rca_raw = rca.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "aiops.playbook-proposer",
            "capability": "aiops.remediation.propose",
            "remediation": {
                "rca_candidates": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": rca.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(rca_raw).hexdigest(),
                    "size_bytes": len(rca_raw),
                    "classification": "internal",
                },
                "max_playbooks": 5,
                "canary_scope": "single_node",
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-remediation-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "aiops.playbook-proposer"
    assert payload["capability"] == "aiops.remediation.propose"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(rca_raw).hexdigest()
    output = payload["document"]
    assert output["contract_id"] == "remediation-proposal"
    assert output["status"] == "proposed"
    assert output["constraints"]["no_shell"] is True
    assert output["constraints"]["no_change_request"] is True
    assert output["constraints"]["requires_human_approval"] is True
    assert "db-connection-recovery" in [proposal["playbook_id"] for proposal in output["proposals"]]
    assert all(action["action_type"] != "shell_exec" for proposal in output["proposals"] for action in proposal["actions"])
    assert len(output["proposal_id"]) == 16

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='aiops.remediation.propose'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "aiops.playbook-proposer")


def test_recovery_verifier_plugin_api_returns_immutable_ledger_after_policy_gate(tmp_path: Path) -> None:
    """The aiops.recovery.verify capability returns a ledger entry, never an execution or circuit-breaker change."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    baseline = tmp_path / "baseline.json"
    observed = tmp_path / "observed.json"

    def _series(values: list[float]) -> str:
        return json.dumps(
            {
                "contract_id": "metric-series",
                "contract_version": "1.0.0",
                "series_id": "svc-order-error-rate",
                "metric": "http_error_rate",
                "unit": "ratio",
                "window_minutes": 60,
                "points": [{"at": f"2026-09-05T08:{index:02d}:00+00:00", "value": value} for index, value in enumerate(values)],
            }
        )

    baseline.write_text(_series([0.003, 0.002, 0.003]), encoding="utf-8")
    observed.write_text(_series([0.0031, 0.0032, 0.003]), encoding="utf-8")
    baseline_raw = baseline.read_bytes()
    observed_raw = observed.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "aiops.recovery-verifier",
            "capability": "aiops.recovery.verify",
            "recovery": {
                "baseline": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": baseline.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(baseline_raw).hexdigest(),
                    "size_bytes": len(baseline_raw),
                    "classification": "internal",
                },
                "observed": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": observed.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(observed_raw).hexdigest(),
                    "size_bytes": len(observed_raw),
                    "classification": "internal",
                },
                "availability_target": 0.99,
                "threshold_ratio": 1.5,
                "probes": [],
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-recovery-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "aiops.recovery-verifier"
    assert payload["capability"] == "aiops.recovery.verify"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(observed_raw).hexdigest()
    output = payload["document"]
    assert output["contract_id"] == "recovery-verification"
    assert output["status"] in {"recovered", "not_recovered", "insufficient_data"}
    assert output["constraints"]["no_execution"] is True
    assert output["constraints"]["no_circuit_breaker_change"] is True
    assert output["constraints"]["ledger_immutable"] is True
    assert len(output["verification_id"]) == 16
    assert output["baseline_sha256"] == hashlib.sha256(baseline_raw).hexdigest()
    assert output["observed_sha256"] == hashlib.sha256(observed_raw).hexdigest()

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='aiops.recovery.verify'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "aiops.recovery-verifier")


def test_alert_triage_plugin_api_after_policy_gate(tmp_path: Path) -> None:
    """The aiops.alert.triage capability is reachable only through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    event = tmp_path / "alert-event-01.json"
    event.write_text(
        json.dumps(
            {
                "contract_id": "alert-event",
                "contract_version": "1.0.0",
                "event_id": "fault-001-20260905-001",
                "source": "telemetry.equipment.sensor-12",
                "occurred_at": "2026-09-05T08:12:00+08:00",
                "severity": "critical",
                "fingerprint": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
                "summary": "BCSA 大案例 01 温度传感器越限，设备疑似过温停机",
                "grouping_keys": ["affected_system", "equipment_type"],
                "labels": {"case": "BCSA_big_01", "tenant_slug": "local-dev"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = event.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "aiops.alert-triage",
            "capability": "aiops.alert.triage",
            "triage": {
                "alert_event": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": event.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "internal",
                },
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-alert-triage-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "aiops.alert-triage"
    assert payload["capability"] == "aiops.alert.triage"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    output = payload["document"]
    assert output["contract_id"] == "incident-proposal"
    assert output["status"] == "proposed"
    assert output["constraints"]["no_execution"] is True
    assert output["constraints"]["no_playbook"] is True
    assert output["constraints"]["requires_human_approval"] is True
    assert len(output["proposal_id"]) == 16
    assert output["alert_fingerprint"] == "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890"
    assert output["triage"]["severity"] == "critical"

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='aiops.alert.triage'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "aiops.alert-triage")


def test_evidence_lineage_plugin_api_queries_a_released_graph_after_policy_gate(tmp_path: Path) -> None:
    """The audit.evidence.lineage capability is reachable only through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    release_sha256 = "c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890abc1"
    graph = tmp_path / "L2-evidence.graph.json"
    graph.write_text(
        json.dumps(
            {
                "contract_id": "released-graph",
                "contract_version": "1.0.0",
                "release_id": "rel-L2-001",
                "space_level": "L2",
                "release_sha256": release_sha256,
                "nodes": [
                    {"node_id": "doc-102", "node_type": "document", "label": "BCSA 大案例 01 故障工单"},
                    {"node_id": "ev-77", "node_type": "evidence", "label": "工单 102-7 温度越限记录"},
                    {"node_id": "cl-5", "node_type": "claim", "label": "设备 12 过温停机导致告警"},
                    {"node_id": "fi-1", "node_type": "finding", "label": "待复核：过温停机候选发现"},
                ],
                "edges": [
                    {"source_id": "ev-77", "target_id": "doc-102", "edge_type": "part_of"},
                    {"source_id": "cl-5", "target_id": "ev-77", "edge_type": "supports", "confidence": 0.91},
                    {"source_id": "fi-1", "target_id": "cl-5", "edge_type": "derived_from", "confidence": 0.8},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = graph.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "audit.evidence-lineage",
            "capability": "audit.evidence.lineage",
            "lineage": {
                "graph_ref": {
                    "artifact": {
                        "artifact_id": str(uuid4()),
                        "tenant_id": str(tenant_id),
                        "uri": graph.resolve().as_uri(),
                        "media_type": "application/json",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "size_bytes": len(raw),
                        "classification": "audit_confidential",
                    },
                    "space_level": "L2",
                    "release_sha256": release_sha256,
                    "release_time": "2026-09-05T02:00:00+08:00",
                    "budget": {"max_nodes": 10000, "max_edges": 50000, "max_hops": 3, "timeout_seconds": 60},
                },
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-evidence-lineage-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "audit.evidence-lineage"
    assert payload["capability"] == "audit.evidence.lineage"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    output = payload["document"]
    assert output["contract_id"] == "evidence-lineage"
    assert output["truncated"] is False
    assert output["node_count"] == 4
    assert output["edge_count"] == 3
    assert output["release_sha256"] == release_sha256.lower()
    assert output["summary"] == {"evidence_count": 1, "claim_count": 1, "finding_count": 1}
    assert len(output["lineage_id"]) == 16

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='audit.evidence.lineage'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "audit.evidence-lineage")


def test_simulated_backtest_plugin_api_after_policy_gate(tmp_path: Path) -> None:
    """The quant.backtest.simulate capability returns a simulated-only report through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    snapshot = tmp_path / "20260905_1559_market.csv"
    snapshot.write_text(
        "datetime,code,open,high,low,close,volume\n"
        "2026-09-05T09:30:00+08:00,600519,10.0,10.5,9.8,10.2,1000\n"
        "2026-09-05T09:30:00+08:00,000858,5.0,5.2,4.9,5.1,2000\n"
        "2026-09-05T09:31:00+08:00,600519,10.2,10.8,10.1,10.5,1100\n"
        "2026-09-05T09:31:00+08:00,000858,5.1,5.3,5.0,5.2,2100\n"
        "2026-09-05T09:32:00+08:00,600519,10.5,10.6,10.3,10.4,1200\n"
        "2026-09-05T09:32:00+08:00,000858,5.2,5.4,5.1,5.3,2200\n",
        encoding="utf-8",
    )
    raw = snapshot.read_bytes()
    code_sha256 = "ab12cd34ef567890ab12cd34ef567890ab12cd34ef567890ab12cd34ef567890"
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "quant.simulated-backtest",
            "capability": "quant.backtest.simulate",
            "backtest": {
                "snapshot_ref": {
                    "artifact": {
                        "artifact_id": str(uuid4()),
                        "tenant_id": str(tenant_id),
                        "uri": snapshot.resolve().as_uri(),
                        "media_type": "text/csv",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "size_bytes": len(raw),
                        "classification": "restricted",
                    },
                    "reference_time": "2026-09-05T15:59:00+08:00",
                    "max_freshness_hours": 6,
                    "point_in_time": True,
                    "data_frequency": "minute",
                    "expected_columns": ["datetime", "code", "open", "high", "low", "close", "volume"],
                },
                "strategy": {
                    "strategy_key": "momentum-reversion",
                    "strategy_version": "0.3.1",
                    "code_sha256": code_sha256,
                    "parameters": {"lookback_days": 20, "top_n": 10},
                },
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-simulated-backtest-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "quant.simulated-backtest"
    assert payload["capability"] == "quant.backtest.simulate"
    assert payload["trace_id"] == str(trace_id)
    assert len(payload["input_sha256"]) == 64
    assert len(payload["runtime_code_sha256"]) == 64
    output = payload["document"]
    assert output["contract_id"] == "backtest-report"
    assert output["contract_version"] == "1.0.0"
    assert len(output["backtest_id"]) == 16
    assert output["code_sha256"] == code_sha256
    assert output["snapshot_sha256"] == hashlib.sha256(raw).hexdigest()
    assert output["metrics"]["simulated_only"] is True
    assert output["metrics"]["n_periods"] == 2
    assert len(output["period_returns"]) == 2
    assert output["summary"] == {
        "simulated": True,
        "source_refs": [snapshot.resolve().as_uri()],
        "truncated": False,
    }

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='quant.backtest.simulate'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "quant.simulated-backtest")


def test_workpaper_export_plugin_api_returns_draft_after_policy_gate(tmp_path: Path) -> None:
    """The audit.workpaper.export capability returns a traceable draft through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    finding_set = tmp_path / "finding-set-confirmed.json"
    finding_set.write_text(
        json.dumps(
            {
                "contract_id": "finding-set",
                "contract_version": "1.0.0",
                "engagement_id": "d4e5f6a7-8b9c-4d0e-8f1a-2b3c4d5e6f70",
                "engagement_name": "BCSA 大案例 01 采购与付款审计",
                "findings": [
                    {
                        "finding_id": "0b3c4d5e-6f70-4a8b-9c0d-1e2f3a4b5c6d",
                        "title": "供应商准入未复核资质有效期",
                        "severity": "high",
                        "status": "confirmed",
                        "claim_id": "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
                        "claim": "采购流程未在付款前复核供应商资质到期日",
                        "reviewer_label": "审计二组-主管复核",
                        "evidence_refs": ["evidence://procurement/PO-102", "evidence://vendor/qual-77"],
                        "confirmed_at": "2026-09-05T14:30:00Z",
                    },
                    {
                        "finding_id": "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f60",
                        "title": "付款凭证缺少审批签章",
                        "severity": "medium",
                        "status": "confirmed",
                        "claim_id": "3e4f5a6b-7c8d-4e9f-0a1b-2c3d4e5f6071",
                        "claim": "两笔付款凭证未附审批签章影像",
                        "reviewer_label": "审计二组-经办复核",
                        "evidence_refs": ["evidence://ledger/V-77"],
                        "confirmed_at": "2026-09-05T15:10:00Z",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = finding_set.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "audit.workpaper-export",
            "capability": "audit.workpaper.export",
            "workpaper": {
                "finding_set": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": finding_set.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "audit_confidential",
                },
                "max_findings": 500,
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-workpaper-export-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "audit.workpaper-export"
    assert payload["capability"] == "audit.workpaper.export"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(payload["runtime_code_sha256"]) == 64
    output = payload["document"]
    assert output["contract_id"] == "workpaper-export"
    assert output["contract_version"] == "1.0.0"
    assert output["export_kind"] == "workpaper"
    assert output["status"] == "draft"
    assert len(output["export_id"]) == 16
    assert output["engagement_id"] == "d4e5f6a7-8b9c-4d0e-8f1a-2b3c4d5e6f70"
    assert len(output["sections"]) == 2
    assert output["summary"] == {
        "findings": 2, "high": 1, "medium": 1, "low": 0, "evidence_refs": 3, "truncated": False,
    }
    assert output["constraints"] == {
        "no_overwrite": True,
        "requires_human_approval": True,
        "immutable_source": True,
    }
    assert output["provenance"]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert output["provenance"]["plugin"] == "audit.workpaper-export@0.1.0"

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='audit.workpaper.export'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "audit.workpaper-export")


def test_report_draft_plugin_api_returns_unsigned_draft_after_policy_gate(tmp_path: Path) -> None:
    """The audit.report.draft capability returns a traceable unsigned report draft through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    finding_set = tmp_path / "finding-set-confirmed-report.json"
    finding_set.write_text(
        json.dumps(
            {
                "contract_id": "finding-set",
                "contract_version": "1.0.0",
                "engagement_id": "d4e5f6a7-8b9c-4d0e-8f1a-2b3c4d5e6f70",
                "engagement_name": "BCSA 大案例 01 采购与付款审计",
                "findings": [
                    {
                        "finding_id": "0b3c4d5e-6f70-4a8b-9c0d-1e2f3a4b5c6d",
                        "title": "供应商准入未复核资质有效期",
                        "severity": "high",
                        "status": "confirmed",
                        "claim_id": "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
                        "claim": "采购流程未在付款前复核供应商资质到期日",
                        "reviewer_label": "审计二组-主管复核",
                        "evidence_refs": ["evidence://procurement/PO-102", "evidence://vendor/qual-77"],
                        "confirmed_at": "2026-09-05T14:30:00Z",
                    },
                    {
                        "finding_id": "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f60",
                        "title": "付款凭证缺少审批签章",
                        "severity": "medium",
                        "status": "confirmed",
                        "claim_id": "3e4f5a6b-7c8d-4e9f-0a1b-2c3d4e5f6071",
                        "claim": "两笔付款凭证未附审批签章影像",
                        "reviewer_label": "审计二组-经办复核",
                        "evidence_refs": ["evidence://ledger/V-77"],
                        "confirmed_at": "2026-09-05T15:10:00Z",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = finding_set.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "audit.report-draft",
            "capability": "audit.report.draft",
            "report": {
                "finding_set": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": finding_set.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "audit_confidential",
                },
                "template_version": "1.0.0",
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-report-draft-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "audit.report-draft"
    assert payload["capability"] == "audit.report.draft"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(payload["runtime_code_sha256"]) == 64
    output = payload["document"]
    assert output["contract_id"] == "report-draft"
    assert output["contract_version"] == "1.0.0"
    assert output["report_kind"] == "report"
    assert output["status"] == "draft"
    assert len(output["report_id"]) == 16
    assert output["engagement_id"] == "d4e5f6a7-8b9c-4d0e-8f1a-2b3c4d5e6f70"
    assert output["template_version"] == "1.0.0"
    assert len(output["body"]["findings"]) == 2
    assert output["body"]["cover"]["severity_summary"] == {"high": 1, "medium": 1, "low": 0}
    assert output["signature"]["draft_by"] == "audit.report-draft@0.1.0"
    assert output["signature"]["signed"] is False
    assert output["summary"] == {
        "findings": 2, "high": 1, "medium": 1, "low": 0, "evidence_refs": 3, "truncated": False,
    }
    assert output["constraints"] == {
        "no_overwrite": True,
        "requires_human_approval": True,
        "immutable_source": True,
        "human_issuance_required": True,
    }
    assert output["provenance"]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert output["provenance"]["plugin"] == "audit.report-draft@0.1.0"

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='audit.report.draft'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "audit.report-draft")


def test_ticket_draft_plugin_api_returns_unsent_draft_after_policy_gate(tmp_path: Path) -> None:
    """The aiops.ticket.draft capability returns a traceable unsent ticket draft through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    proposal = tmp_path / "incident-proposal-confirmed.json"
    proposal.write_text(
        json.dumps(
            {
                "contract_id": "incident-proposal",
                "contract_version": "1.0.0",
                "proposal_id": "9a1b2c3d4e5f6789",
                "alert_fingerprint": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
                "status": "proposed",
                "triage": {
                    "grouping_key": "affected_system=BCSA_big_01",
                    "severity": "critical",
                    "affected_system": "BCSA_big_01/equipment-12",
                    "summary": "温度传感器越限且连续三分钟无心跳，建议人工确认是否过温停机",
                    "evidence_refs": ["file:///G:/数据/04-审计数据集与基准/PCCA-Benchmark/benchmark_cases/bigcase/BCSA/BCSA_big_01/fault_tickets.json"],
                    "review_reason": "单源告警，需结合故障工单与日志人工复核",
                },
                "constraints": {"no_execution": True, "no_playbook": True, "requires_human_approval": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = proposal.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "aiops.ticket-draft",
            "capability": "aiops.ticket.draft",
            "ticket": {
                "incident_proposal": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": proposal.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "internal",
                },
                "target_system": "jira",
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-ticket-draft-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "aiops.ticket-draft"
    assert payload["capability"] == "aiops.ticket.draft"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(payload["runtime_code_sha256"]) == 64
    output = payload["document"]
    assert output["contract_id"] == "ticket-draft"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "draft"
    assert len(output["ticket_id"]) == 16
    assert output["target_system"] == "jira"
    assert output["priority"] == "P1"
    assert output["incident_refs"] == ["incident:9a1b2c3d4e5f6789"]
    assert output["signature"] == {"drafted_by": "aiops.ticket-draft@0.1.0", "sent": False, "sender": None}
    assert output["summary"] == {
        "incidents": 1, "priority": "P1", "evidence_refs": 1, "truncated": False,
    }
    assert output["constraints"] == {
        "no_auto_send": True,
        "requires_target_whitelist": True,
        "requires_human_approval": True,
        "immutable_source": True,
    }
    assert output["provenance"]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert output["provenance"]["plugin"] == "aiops.ticket-draft@0.1.0"

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='aiops.ticket.draft'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "aiops.ticket-draft")


def test_retention_recommendation_plugin_api_returns_proposed_retention_after_policy_gate(tmp_path: Path) -> None:
    """The knowledge.retention.recommend capability returns a traceable retention recommendation through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    document = tmp_path / "knowledge-doc-retention.json"
    document.write_text(
        json.dumps(
            {
                "doc_id": "KD-2026-017",
                "domain": "BCSA_big_01",
                "kind": "benchmark_metadata",
                "rows": 24,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = document.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "knowledge.retention-recommendation",
            "capability": "knowledge.retention.recommend",
            "retention": {
                "document": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": document.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "internal",
                },
                "last_access_days": 400,
                "ref_count": 0,
                "classification": "internal",
                "archive_after_days": 365,
                "purge_candidate_after_days": 730,
                "min_refs_to_retain": 1,
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-retention-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "knowledge.retention-recommendation"
    assert payload["capability"] == "knowledge.retention.recommend"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(payload["runtime_code_sha256"]) == 64
    output = payload["document"]
    assert output["contract_id"] == "retention-recommendation"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "proposed"
    assert len(output["recommendation_id"]) == 16
    assert output["recommendation"] == "archive"
    assert output["document_ref"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert "idle_beyond_archive_threshold" in output["rationale"]["matched_rules"]
    assert output["summary"] == {"retain": 0, "archive": 1, "delete_candidate": 0}
    assert output["signature"] == {"recommended_by": "knowledge.retention-recommendation@0.1.0", "reviewed": False}
    assert output["constraints"] == {
        "no_delete": True,
        "evidence_immutable": True,
        "requires_human_approval": True,
    }
    assert output["provenance"]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert output["provenance"]["plugin"] == "knowledge.retention-recommendation@0.1.0"

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='knowledge.retention.recommend'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "knowledge.retention-recommendation")


def test_postmortem_draft_plugin_api_returns_unpublished_draft_after_policy_gate(tmp_path: Path) -> None:
    """The aiops.postmortem.draft capability returns a traceable postmortem draft through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    proposal = tmp_path / "incident-proposal-01.json"
    proposal.write_text(
        json.dumps(
            {
                "contract_id": "incident-proposal",
                "contract_version": "1.0.0",
                "proposal_id": "9a1b2c3d4e5f6789",
                "alert_fingerprint": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
                "status": "proposed",
                "triage": {
                    "grouping_key": "affected_system=BCSA_big_01",
                    "severity": "critical",
                    "affected_system": "BCSA_big_01/equipment-12",
                    "summary": "温度传感器越限且连续三分钟无心跳，建议人工确认是否过温停机",
                    "evidence_refs": ["file:///G:/数据/04-审计数据集与基准/PCCA-Benchmark/benchmark_cases/bigcase/BCSA/BCSA_big_01/fault_tickets.json"],
                    "review_reason": "单源告警，需结合故障工单与日志人工复核",
                },
                "constraints": {"no_execution": True, "no_playbook": True, "requires_human_approval": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = proposal.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "aiops.postmortem-draft",
            "capability": "aiops.postmortem.draft",
            "postmortem": {
                "incident_proposal": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": proposal.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "internal",
                }
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-postmortem-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "aiops.postmortem-draft"
    assert payload["capability"] == "aiops.postmortem.draft"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(payload["runtime_code_sha256"]) == 64
    output = payload["document"]
    assert output["contract_id"] == "postmortem-draft"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "draft"
    assert len(output["postmortem_id"]) == 16
    assert output["incident_refs"] == ["incident:9a1b2c3d4e5f6789"]
    assert output["root_cause_candidates"] == ["affected_system=BCSA_big_01"]
    assert output["action_items"][0]["priority"] == "high"
    assert output["signature"] == {"drafted_by": "aiops.postmortem-draft@0.1.0", "published": False, "publisher": None}
    assert output["constraints"] == {
        "no_auto_publish": True,
        "requires_human_approval": True,
        "immutable_source": True,
    }
    assert output["provenance"]["incident_sha256"] == hashlib.sha256(raw).hexdigest()
    assert output["provenance"]["plugin"] == "aiops.postmortem-draft@0.1.0"

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='aiops.postmortem.draft'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "aiops.postmortem-draft")


def test_research_note_draft_plugin_api_returns_unpublished_note_after_policy_gate(tmp_path: Path) -> None:
    """The quant.research-note.draft capability returns a traceable research note through the narrow policy."""

    tenant_id = _tenant_id()
    _register_verified_plugin_and_allow_capability(tenant_id)
    evaluation = tmp_path / "experiment-evaluation-01.json"
    evaluation.write_text(
        json.dumps(
            {
                "contract_id": "experiment-evaluation",
                "contract_version": "1.0.0",
                "experiment_id": "a1b2c3d4e5f67890",
                "report_sha256": "a" * 64,
                "baseline_sha256": "b" * 64,
                "strategy_key": "momentum",
                "strategy_version": "2.1.0",
                "snapshot_sha256": "a" * 64,
                "reference_time": "2026-03-31T00:00:00Z",
                "status": "proposed",
                "robustness": {
                    "overall": "stable",
                    "score": 0.72,
                    "basis": "period_returns",
                    "period_return_stability": 0.88,
                    "positive_period_ratio": 0.83,
                    "max_drawdown": -0.08,
                    "volatility": 0.11,
                    "notes": ["基于 12 个逐期收益计算稳健性。"],
                },
                "drift": {"available": True, "drift_count": 0, "worst_metric": None, "compared_metrics": []},
                "promotion": {
                    "recommendation": "promote",
                    "rationale": "挑战者在稳健性与风险调整收益上满足晋级门槛，可进入独立 Reviewer 复核。",
                    "blockers": [],
                },
                "evidence_refs": ["factor-pack:abc123", "snapshot:snap-2026-03"],
                "summary": {
                    "report_sha256": "a" * 64,
                    "baseline_included": True,
                    "recommendation": "promote",
                    "truncated": False,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = evaluation.read_bytes()
    trace_id = uuid4()
    client = TestClient(create_app(Settings(database_url=DB, plugin_read_roots=(tmp_path,))))

    response = client.post(
        "/api/v1/plugins/invoke",
        json={
            "plugin_id": "quant.research-note-draft",
            "capability": "quant.research-note.draft",
            "research_note": {
                "evaluation": {
                    "artifact_id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "uri": evaluation.resolve().as_uri(),
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "classification": "restricted",
                }
            },
        },
        headers={
            "X-Tenant-Id": str(tenant_id),
            "X-Trace-Id": str(trace_id),
            "Idempotency-Key": f"phase1-research-note-{uuid4().hex}",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plugin_id"] == "quant.research-note-draft"
    assert payload["capability"] == "quant.research-note.draft"
    assert payload["trace_id"] == str(trace_id)
    assert payload["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(payload["runtime_code_sha256"]) == 64
    output = payload["document"]
    assert output["contract_id"] == "research-note-draft"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "draft"
    assert len(output["note_id"]) == 16
    assert output["evaluation_refs"] == ["evaluation:a1b2c3d4e5f67890"]
    assert output["strategy"] == {"key": "momentum", "version": "2.1.0"}
    assert output["summary"] == {
        "evaluations": 1,
        "recommendation": "promote",
        "truncated": False,
    }
    assert output["signature"] == {"drafted_by": "quant.research-note-draft@0.1.0", "published": False, "publisher": None}
    assert output["constraints"] == {
        "no_auto_publish": True,
        "no_order_generation": True,
        "requires_human_approval": True,
        "immutable_source": True,
    }
    assert output["provenance"]["evaluation_sha256"] == hashlib.sha256(raw).hexdigest()
    assert output["provenance"]["plugin"] == "quant.research-note-draft@0.1.0"

    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id',%s,false)", (str(tenant_id),))
            cur.execute(
                """
                SELECT decision.decision, tool_call.arguments->>'plugin_id'
                FROM policy.decisions decision
                JOIN policy.tool_calls tool_call ON tool_call.id=decision.tool_call_id
                WHERE tool_call.trace_id=%s AND tool_call.capability='quant.research-note.draft'
                """,
                (str(trace_id),),
            )
            audit_row = cur.fetchone()
    assert audit_row == ("ALLOW", "quant.research-note-draft")
