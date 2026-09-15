from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, register_uuid

register_uuid()  # type: ignore[no-untyped-call]


@dataclass(frozen=True, slots=True)
class AuditRunResult:
    engagement_id: str
    artifact_id: str
    evidence_id: str
    rows: int
    anomalies: int
    duplicate_rows: int
    missing_amounts: int


class AuditPipeline:
    def __init__(self, database_url: str, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug

    def run_ledger_csv(self, csv_path: str | Path, engagement_name: str = "Ledger Review", amount_threshold: float = 1_000_000) -> AuditRunResult:
        path = Path(csv_path).resolve()
        if path.suffix.lower() != ".csv" or not path.is_file():
            raise ValueError("audit input must be an existing CSV file")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise ValueError("ledger CSV has no data rows")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
                tenant_row = cur.fetchone()
                if tenant_row is None:
                    raise ValueError(f"tenant not found: {self.tenant_slug}")
                tenant_id = tenant_row[0]
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT id FROM iam.projects WHERE tenant_id=%s ORDER BY created_at LIMIT 1", (tenant_id,))
                project_row = cur.fetchone()
                if project_row is None:
                    raise ValueError("tenant has no project")
                file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                cur.execute(
                    """INSERT INTO artifact.blobs(sha256,byte_size,media_type,storage_uri)
                    VALUES(%s,%s,'text/csv',%s) ON CONFLICT(sha256) DO NOTHING""",
                    (file_sha256, path.stat().st_size, path.as_uri()),
                )
                cur.execute(
                    """INSERT INTO artifact.artifacts(tenant_id,blob_sha256,artifact_type,classification,metadata)
                    VALUES(%s,%s,'audit_ledger_source','confidential',%s) RETURNING id""",
                    (tenant_id, file_sha256, Json({"filename": path.name, "rows": len(rows)})),
                )
                artifact_row = cur.fetchone()
                if artifact_row is None:
                    raise RuntimeError("source artifact insert returned no id")
                artifact_id = artifact_row[0]
                cur.execute(
                    """INSERT INTO audit.engagements(tenant_id,project_id,name,status) VALUES(%s,%s,%s,'running')
                    RETURNING id""",
                    (tenant_id, project_row[0], engagement_name),
                )
                engagement_row = cur.fetchone()
                if engagement_row is None:
                    raise RuntimeError("engagement insert returned no id")
                engagement_id = engagement_row[0]
                cur.execute(
                    """INSERT INTO audit.evidence(tenant_id,engagement_id,artifact_id,evidence_type,description,metadata)
                    VALUES(%s,%s,%s,'ledger_source','Original ledger CSV',%s) RETURNING id""",
                    (tenant_id, engagement_id, artifact_id, Json({"sha256": file_sha256, "rows": len(rows)})),
                )
                evidence_row = cur.fetchone()
                if evidence_row is None:
                    raise RuntimeError("audit evidence insert returned no id")
                evidence_id = evidence_row[0]
                seen: set[str] = set()
                duplicate_rows = 0
                missing_amounts = 0
                anomalies = 0
                for index, row in enumerate(rows, start=1):
                    normalized = "|".join(f"{key}={row.get(key, '')}" for key in sorted(row))
                    row_hash = hashlib.sha256(normalized.encode()).hexdigest()
                    if row_hash in seen:
                        duplicate_rows += 1
                        self._candidate(cur, tenant_id, engagement_id, str(index), "duplicate_row", 0.8, row)
                        anomalies += 1
                    seen.add(row_hash)
                    raw_amount = row.get("amount") or row.get("金额") or ""
                    try:
                        amount = float(str(raw_amount).replace(",", ""))
                    except ValueError:
                        amount = None
                    if amount is None:
                        missing_amounts += 1
                        self._candidate(cur, tenant_id, engagement_id, str(index), "missing_or_invalid_amount", 0.7, row)
                        anomalies += 1
                    elif abs(amount) >= amount_threshold:
                        self._candidate(cur, tenant_id, engagement_id, str(index), "large_amount", 0.6, row)
                        anomalies += 1
                cur.execute("UPDATE audit.engagements SET status='completed' WHERE id=%s", (engagement_id,))
                cur.execute(
                    """INSERT INTO audit.workpapers(tenant_id,engagement_id,workpaper_key,status,content,artifact_ids)
                    VALUES(%s,%s,'ledger-quality','draft',%s,%s)""",
                    (
                        tenant_id,
                        engagement_id,
                        Json({"rows": len(rows), "anomalies": anomalies, "duplicate_rows": duplicate_rows, "missing_amounts": missing_amounts}),
                        [artifact_id],
                    ),
                )
                return AuditRunResult(str(engagement_id), str(artifact_id), str(evidence_id), len(rows), anomalies, duplicate_rows, missing_amounts)

    def confirm_candidate(self, candidate_id: str, reviewer_label: str) -> str:
        """Create a reportable finding only after evidence-backed QA confirmation.

        Idempotent per candidate: confirming an already-confirmed candidate returns
        the existing finding id instead of creating a duplicate claim/finding.
        """
        if not reviewer_label.strip():
            raise ValueError("reviewer_label is required")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT engagement_id,source_ref,rule_key,score,payload,status FROM audit.anomaly_candidates
                    WHERE tenant_id=%s AND id=%s FOR UPDATE""",
                    (tenant_id, candidate_id),
                )
                candidate = cur.fetchone()
                if candidate is None:
                    raise ValueError("anomaly candidate not found")
                engagement_id, source_ref, rule_key, score, _payload, status = candidate
                if status == "confirmed":
                    cur.execute(
                        """SELECT f.id FROM audit.finding_claims fc
                        JOIN audit.findings f ON f.id=fc.finding_id
                        WHERE fc.tenant_id=%s AND f.tenant_id=%s AND f.engagement_id=%s
                        ORDER BY fc.created_at DESC LIMIT 1""",
                        (tenant_id, tenant_id, engagement_id),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        return str(existing[0])
                    raise ValueError("anomaly candidate already confirmed")
                cur.execute(
                    "SELECT id,artifact_id FROM audit.evidence WHERE tenant_id=%s AND engagement_id=%s ORDER BY created_at LIMIT 1",
                    (tenant_id, engagement_id),
                )
                evidence = cur.fetchone()
                if evidence is None or evidence[1] is None:
                    raise ValueError("cannot confirm finding without an original evidence slice")
                claim_text = f"{rule_key} detected at ledger row {source_ref}"
                cur.execute(
                    """INSERT INTO belief.claims(tenant_id,subject,predicate,object,confidence,status)
                    VALUES(%s,%s,'has_anomaly',%s,%s,'confirmed') RETURNING id""",
                    (tenant_id, f"audit_engagement:{engagement_id}", claim_text, score),
                )
                claim_row = cur.fetchone()
                if claim_row is None:
                    raise RuntimeError("claim insert returned no id")
                claim_id = claim_row[0]
                cur.execute(
                    """INSERT INTO belief.evidence_links(tenant_id,claim_id,artifact_id,audit_evidence_id)
                    VALUES(%s,%s,%s,%s)""",
                    (tenant_id, claim_id, evidence[1], evidence[0]),
                )
                cur.execute(
                    """INSERT INTO audit.findings(tenant_id,engagement_id,title,severity,status,evidence_artifact_ids)
                    VALUES(%s,%s,%s,%s,'confirmed',%s) RETURNING id""",
                    (tenant_id, engagement_id, claim_text, "medium", [evidence[1]]),
                )
                finding_row = cur.fetchone()
                if finding_row is None:
                    raise RuntimeError("finding insert returned no id")
                finding_id = finding_row[0]
                cur.execute(
                    """INSERT INTO audit.finding_claims(tenant_id,finding_id,claim_id,audit_evidence_id,reviewer_label)
                    VALUES(%s,%s,%s,%s,%s)""",
                    (tenant_id, finding_id, claim_id, evidence[0], reviewer_label),
                )
                cur.execute("UPDATE audit.anomaly_candidates SET status='confirmed' WHERE id=%s AND tenant_id=%s", (candidate_id, tenant_id))
                return str(finding_id)

    def list_engagements(self, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT e.id,e.name,e.status,e.created_at,
                    (SELECT count(*) FROM audit.evidence ev WHERE ev.engagement_id=e.id AND ev.tenant_id=%s),
                    (SELECT count(*) FROM audit.anomaly_candidates ac WHERE ac.engagement_id=e.id AND ac.tenant_id=%s AND ac.status='open'),
                    (SELECT count(*) FROM audit.findings f WHERE f.engagement_id=e.id AND f.tenant_id=%s)
                    FROM audit.engagements e WHERE e.tenant_id=%s ORDER BY e.created_at DESC LIMIT %s""",
                    (tenant_id, tenant_id, tenant_id, tenant_id, limit),
                )
                return [
                    {
                        "id": str(row[0]), "name": row[1], "status": row[2], "created_at": row[3],
                        "evidence_count": int(row[4]), "open_anomaly_count": int(row[5]),
                        "finding_count": int(row[6]),
                    }
                    for row in cur.fetchall()
                ]

    def get_engagement_lineage(self, engagement_id: str, limit: int = 100) -> dict[str, Any]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT id,name,status,created_at FROM audit.engagements WHERE tenant_id=%s AND id=%s",
                    (tenant_id, engagement_id),
                )
                eng = cur.fetchone()
                if eng is None:
                    raise ValueError("audit engagement not found")
                cur.execute(
                    """SELECT id,evidence_type,artifact_id,metadata FROM audit.evidence
                    WHERE tenant_id=%s AND engagement_id=%s ORDER BY created_at LIMIT %s""",
                    (tenant_id, engagement_id, limit),
                )
                evidence = [
                    {"id": str(r[0]), "evidence_type": r[1], "artifact_id": str(r[2]) if r[2] else None, "metadata": r[3]}
                    for r in cur.fetchall()
                ]
                cur.execute(
                    """SELECT id,source_ref,rule_key,score,status FROM audit.anomaly_candidates
                    WHERE tenant_id=%s AND engagement_id=%s ORDER BY created_at LIMIT %s""",
                    (tenant_id, engagement_id, limit),
                )
                anomalies = [
                    {"id": str(r[0]), "source_ref": r[1], "rule_key": r[2], "score": float(r[3]), "status": r[4]}
                    for r in cur.fetchall()
                ]
                cur.execute(
                    """SELECT f.id,f.title,f.severity,f.status,
                    COALESCE(json_agg(json_build_object('claim_id',fc.claim_id,'reviewer_label',fc.reviewer_label))
                             FILTER (WHERE fc.claim_id IS NOT NULL),'[]')
                    FROM audit.findings f LEFT JOIN audit.finding_claims fc ON fc.finding_id=f.id AND fc.tenant_id=f.tenant_id
                    WHERE f.tenant_id=%s AND f.engagement_id=%s
                    GROUP BY f.id,f.title,f.severity,f.status ORDER BY f.created_at LIMIT %s""",
                    (tenant_id, engagement_id, limit),
                )
                findings = [
                    {
                        "id": str(r[0]), "title": r[1], "severity": r[2], "status": r[3],
                        "claims": r[4] or [],
                    }
                    for r in cur.fetchall()
                ]
                return {
                    "engagement_id": str(engagement_id),
                    "engagement": {"id": str(eng[0]), "name": eng[1], "status": eng[2], "created_at": eng[3]},
                    "evidence": evidence,
                    "anomalies": anomalies,
                    "findings": findings,
                }

    def _tenant(self, cur: Any) -> Any:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        tenant_row = cur.fetchone()
        if tenant_row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id = tenant_row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return tenant_id

    @staticmethod
    def _candidate(cur: Any, tenant_id: Any, engagement_id: Any, source_ref: str, rule_key: str, score: float, payload: dict[str, str]) -> None:
        cur.execute(
            """INSERT INTO audit.anomaly_candidates(tenant_id,engagement_id,source_ref,rule_key,score,payload)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(engagement_id,source_ref,rule_key) DO NOTHING""",
            (tenant_id, engagement_id, source_ref, rule_key, score, Json(payload)),
        )
