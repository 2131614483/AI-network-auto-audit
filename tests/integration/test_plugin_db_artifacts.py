"""Database-to-artifact materialization drives the verified plugins.

The adapter reads only ``audit_network_test`` (never the interactive work
database), turns tenant-scoped rows into immutable local artifacts and runs
the corresponding verified read-only plugin through the isolated subprocess
runtime — the same gate the API uses.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

import psycopg2
import pytest

from packages.plugin_runtime.db_artifacts import (
    materialize_alerts,
    materialize_incidents,
    materialize_semantic_document,
    materialize_topology,
)
from packages.plugin_runtime.runner import ArtifactInput, IsolatedPluginRuntime, PluginInvocation
from packages.policy.engine import PolicyEngine

DB = os.getenv("AUDIT_NETWORK_TEST_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network_test")


@pytest.fixture()
def isolated_tenant(tmp_path: Path) -> UUID:
    """Insert a throwaway tenant plus deterministic AIOps/knowledge rows."""
    connection = psycopg2.connect(DB)
    tenant_id = uuid4()
    alert_a = uuid4()
    alert_b = uuid4()
    incident_1 = uuid4()
    incident_2 = uuid4()
    document_id = uuid4()
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (str(tenant_id), f"db-artifacts-{tenant_id.hex[:8]}", "db artifacts test tenant"),
        )
        # Two alerts sharing a system, usable as an alert-event grouping key.
        cur.execute(
            """
            INSERT INTO aiops.alerts (id, tenant_id, source, fingerprint, severity, payload, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (str(alert_a), str(tenant_id), "pump-01", "fp-a", "high", json.dumps({"affected_system": "pump-01", "equipment_type": "centrifugal"}), "2026-08-01 01:00:00+00"),
        )
        cur.execute(
            """
            INSERT INTO aiops.alerts (id, tenant_id, source, fingerprint, severity, payload, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (str(alert_b), str(tenant_id), "pump-01", "fp-b", "medium", json.dumps({"affected_system": "pump-01", "equipment_type": "centrifugal"}), "2026-08-01 01:30:00+00"),
        )
        # An incident aggregating alert_a plus a second unrelated incident.
        cur.execute(
            """
            INSERT INTO aiops.incidents (id, tenant_id, title, status, root_cause, timeline, created_at)
            VALUES (%s, %s, %s, 'open', %s::jsonb, %s::jsonb, %s)
            """,
            (str(incident_1), str(tenant_id), "pump-01 surge", json.dumps({"text": "pump-01"}),
             json.dumps([{"alert_id": str(alert_a), "severity": "high"}]), "2026-08-01 01:45:00+00"),
        )
        cur.execute(
            """
            INSERT INTO aiops.incidents (id, tenant_id, title, status, root_cause, timeline, created_at)
            VALUES (%s, %s, %s, 'open', NULL, %s::jsonb, %s)
            """,
            (str(incident_2), str(tenant_id), "compressor vibration",
             json.dumps([]), "2026-08-01 02:00:00+00"),
        )
        # One knowledge document whose chunks mention relations.
        cur.execute(
            """
            INSERT INTO semantic.documents (id, tenant_id, source_uri, title, status, current_version, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'staged', 1, %s, %s)
            """,
            (str(document_id), str(tenant_id), "db://memo.md", "memo", "2026-08-01 00:00:00+00", "2026-08-01 00:00:00+00"),
        )
        cur.execute(
            """
            INSERT INTO semantic.chunks (id, tenant_id, document_id, ordinal, content, token_count, metadata, document_version)
            VALUES (%s, %s, %s, 1, %s, 4, '{}'::jsonb, 1)
            """,
            (str(uuid4()), str(tenant_id), str(document_id), "甲公司与乙公司签订采购合同，乙公司参股丙公司。"),
        )
    connection.commit()
    connection.close()
    yield tenant_id
    connection = psycopg2.connect(DB)
    with connection.cursor() as cur:
        # audit_app intentionally has no DELETE on protected tables; the audit
        # network test database is disposable so cleanup is best-effort.
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        for statement in (
            "DELETE FROM aiops.alerts WHERE tenant_id = %s",
            "DELETE FROM aiops.incidents WHERE tenant_id = %s",
            "DELETE FROM semantic.chunks WHERE tenant_id = %s",
            "DELETE FROM semantic.documents WHERE tenant_id = %s",
            "DELETE FROM knowledge.ingest_batches WHERE tenant_id = %s",
            "DELETE FROM iam.tenants WHERE id = %s",
        ):
            try:
                cur.execute(statement, (str(tenant_id),))
            except psycopg2.Error:
                connection.rollback()
                cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    connection.commit()
    connection.close()


def _connect() -> Any:
    return psycopg2.connect(DB)


def _runtime(tmp_path: Path) -> IsolatedPluginRuntime:
    return IsolatedPluginRuntime(allowed_roots=(tmp_path.resolve(),))


def _invoke(runtime: IsolatedPluginRuntime, invocation: PluginInvocation, *, allow: list[str]) -> dict[str, object]:
    result = runtime.invoke(invocation, PolicyEngine(allow=allow))
    assert len(result.runtime_code_sha256) == 64
    return result.output


def _piece(artifact: ArtifactInput) -> dict[str, object]:
    return {
        "artifact_id": str(artifact.artifact_id),
        "tenant_id": str(artifact.tenant_id),
        "uri": artifact.uri,
        "media_type": artifact.media_type,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
        "classification": artifact.classification,
    }


def test_alert_correlation_driven_by_database_alerts(tmp_path: Path, isolated_tenant: UUID) -> None:
    materials = materialize_alerts(_connect(), tenant_id=isolated_tenant, out_dir=tmp_path)
    assert materials.row_count == 2
    artifact = materials.artifact
    parsed = urlparse(artifact.uri)
    raw = unquote(parsed.path)
    if raw.startswith("/") and len(raw) >= 3 and raw[2] == ":":
        raw = raw[1:]  # file:///C:/... -> C:/...
    assert artifact.sha256 == hashlib.sha256(Path(raw).read_bytes()).hexdigest()
    output = _invoke(
        _runtime(tmp_path),
        PluginInvocation(
            tenant_id=isolated_tenant,
            trace_id=uuid4(),
            idempotency_key="db-artifacts-alert-v1",
            plugin_id="aiops.alert-correlation",
            capability="aiops.alert.correlate",
            payload={
                "alerts": {
                    "artifact": _piece(artifact),
                    "window_minutes": 60,
                    "max_candidates": 10,
                    "grouping_keys": ["affected_system", "equipment_type"],
                }
            },
        ),
        allow=["aiops.alert.correlate"],
    )
    assert output["contract_id"] == "incident-candidate-set"
    assert output["alert_set_sha256"] == artifact.sha256
    assert output["summary"]["alerts_processed"] == 2
    assert output["candidates"]
    assert output["candidates"][0]["correlation_key"] == "affected_system=pump-01|equipment_type=centrifugal"
    assert output["candidates"][0]["count"] == 2


def test_rca_ranker_driven_by_database_incidents_and_topology(tmp_path: Path, isolated_tenant: UUID) -> None:
    incidents = materialize_incidents(_connect(), tenant_id=isolated_tenant, out_dir=tmp_path)
    assert incidents.row_count == 2
    topology = materialize_topology(_connect(), tenant_id=isolated_tenant, out_dir=tmp_path)
    assert topology.row_count == 3  # two incidents + one referenced alert
    output = _invoke(
        _runtime(tmp_path),
        PluginInvocation(
            tenant_id=isolated_tenant,
            trace_id=uuid4(),
            idempotency_key="db-artifacts-rca-v1",
            plugin_id="aiops.rca-ranker",
            capability="aiops.rca.rank",
            payload={
                "rca": {
                    "incident_set": _piece(incidents.artifact),
                    "topology_graph": _piece(topology.artifact),
                    "max_candidates": 10,
                    "max_hops": 1,
                }
            },
        ),
        allow=["aiops.rca.rank"],
    )
    assert output["contract_id"] == "rca-candidates"
    assert output["incident_set_sha256"] == incidents.artifact.sha256
    assert output["topology_sha256"] == topology.artifact.sha256
    assert output["summary"]["incidents_processed"] == 2
    assert output["summary"]["nodes_considered"] >= 3
    assert output["candidates"]


def test_knowledge_entity_relation_driven_by_database_chunks(tmp_path: Path, isolated_tenant: UUID) -> None:
    materials = materialize_semantic_document(_connect(), tenant_id=isolated_tenant, out_dir=tmp_path)
    assert materials.row_count == 1
    output = _invoke(
        _runtime(tmp_path),
        PluginInvocation(
            tenant_id=isolated_tenant,
            trace_id=uuid4(),
            idempotency_key="db-artifacts-knowledge-v1",
            plugin_id="knowledge.entity-relation-candidate",
            capability="knowledge.extract.relations",
            payload={"document": {"artifact": _piece(materials.artifact), "language": "zh"}},
        ),
        allow=["knowledge.extract.relations"],
    )
    assert output["contract_id"] == "graph-candidate-set"
    assert output["document_sha256"] == materials.artifact.sha256
    assert any(entity["entity_type"] != "document" for entity in output["entity_candidates"])
    assert any(edge["relation_type"] in ("transacts_with", "invests_in") for edge in output["relation_candidates"])


def test_document_ingestion_driven_by_database_markdown(tmp_path: Path, isolated_tenant: UUID) -> None:
    materials = materialize_semantic_document(_connect(), tenant_id=isolated_tenant, out_dir=tmp_path)
    output = _invoke(
        _runtime(tmp_path),
        PluginInvocation(
            tenant_id=isolated_tenant,
            trace_id=uuid4(),
            idempotency_key="db-artifacts-ingestion-v1",
            plugin_id="knowledge.document-ingestion",
            capability="knowledge.extract.document",
            payload={"artifact": _piece(materials.artifact), "max_characters": 20_000},
        ),
        allow=["knowledge.extract.document"],
    )
    assert output["contract_id"] == "document-content"
    assert output["source_sha256"] == materials.artifact.sha256