from __future__ import annotations

import os
from pathlib import Path

import psycopg2

from packages.audit.pipeline import AuditPipeline

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


def test_ledger_pipeline_creates_anomaly_candidates(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("account,amount\n6001,1200000\n6001,1200000\n6002,bad\n", encoding="utf-8")
    result = AuditPipeline(DB).run_ledger_csv(ledger)
    assert result.rows == 3
    assert result.duplicate_rows == 1
    assert result.missing_amounts == 1
    assert result.anomalies == 4
    assert result.artifact_id
    assert result.evidence_id


def test_confirmed_finding_has_original_evidence_slice(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("account,amount\n6001,bad\n", encoding="utf-8")
    pipeline = AuditPipeline(DB)
    result = pipeline.run_ledger_csv(ledger, engagement_name="Evidence lineage")
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            tenant_id = cur.fetchone()[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.execute("SELECT id FROM audit.anomaly_candidates WHERE engagement_id=%s ORDER BY created_at DESC LIMIT 1", (result.engagement_id,))
            candidate_id = cur.fetchone()[0]
    finding_id = pipeline.confirm_candidate(str(candidate_id), reviewer_label="independent-qa")
    with psycopg2.connect(DB) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT id FROM iam.tenants WHERE slug='local-dev'")
            tenant_id = cur.fetchone()[0]
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.execute("SELECT cardinality(evidence_artifact_ids) FROM audit.findings WHERE id=%s", (finding_id,))
            assert cur.fetchone()[0] == 1
