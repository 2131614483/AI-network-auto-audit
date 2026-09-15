"""Read-only materialization of database rows into verified plugin artifacts.

The verified plugins only accept immutable local ``file://`` artifact
references.  This module turns already-persisted tenant data (AIOps alerts,
incidents and knowledge chunks) into such artifacts without writing back to
the database.

Every query is scoped by ``tenant_id`` and uses the same RLS session variable
the rest of the project uses, so a caller can never see another tenant's rows.
The materialized files live under a caller-provided directory that must be
declared in the plugin runtime's ``AUDIT_PLUGIN_READ_ROOTS``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from packages.plugin_runtime.runner import ArtifactInput


@dataclass(frozen=True, slots=True)
class MaterializedArtifact:
    """A database slice turned into an immutable local artifact."""

    artifact: ArtifactInput
    row_count: int
    description: str


def _set_tenant(connection: Any, tenant_id: UUID) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))


def _write_artifact(
    out_dir: Path,
    *,
    name: str,
    media_type: str,
    content: bytes,
    tenant_id: UUID,
    classification: str = "internal",
) -> ArtifactInput:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(content)
    return ArtifactInput(
        artifact_id=uuid4(),
        tenant_id=tenant_id,
        uri=path.resolve().as_uri(),
        media_type=media_type,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        classification=classification,
    )


def materialize_alerts(
    connection: Any,
    *,
    tenant_id: UUID,
    out_dir: Path,
    limit: int = 5_000,
    classification: str = "internal",
) -> MaterializedArtifact:
    """Materialize ``aiops.alerts`` rows as an alert-event JSON array.

    The array items carry the immutable identity fields plus the JSON payload,
    which is what the alert-correlation plugin uses for grouping keys.
    """
    _set_tenant(connection, tenant_id)
    if not 1 <= limit <= 500_000:
        raise ValueError("alert materialization limit is outside the fixed budget")
    capacity = limit + 1  # detection of "more rows than the limit"
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT id, source, fingerprint, severity, payload, occurred_at
            FROM aiops.alerts
            WHERE tenant_id = %s
            ORDER BY occurred_at
            LIMIT %s
            """,
            (str(tenant_id), capacity),
        )
        rows = cur.fetchall()
    if len(rows) > limit:
        raise ValueError(f"alerts exceed the fixed materialization budget of {limit}")
    events: list[dict[str, Any]] = []
    for row in rows:
        alert_id, source, fingerprint, severity, payload, occurred_at = row
        event: dict[str, Any] = {
            "id": str(alert_id),
            "source": source,
            "fingerprint": fingerprint,
            "severity": severity,
        }
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key not in event or event[key] is None:
                    event[key] = value
        if isinstance(occurred_at, datetime):
            event["timestamp"] = occurred_at.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        events.append(event)
    payload_bytes = json.dumps(events, ensure_ascii=False, sort_keys=True).encode("utf-8")
    artifact = _write_artifact(
        out_dir,
        name=f"db-alerts-{tenant_id.hex[:8]}.json",
        media_type="application/json",
        content=payload_bytes,
        tenant_id=tenant_id,
        classification=classification,
    )
    return MaterializedArtifact(artifact=artifact, row_count=len(events), description="aiops.alerts")


def materialize_incidents(
    connection: Any,
    *,
    tenant_id: UUID,
    out_dir: Path,
    limit: int = 5_000,
    classification: str = "internal",
) -> MaterializedArtifact:
    """Materialize ``aiops.incidents`` rows as an incident-candidate JSON array."""
    _set_tenant(connection, tenant_id)
    if not 1 <= limit <= 100_000:
        raise ValueError("incident materialization limit is outside the fixed budget")
    capacity = limit + 1
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, status, root_cause, timeline, created_at
            FROM aiops.incidents
            WHERE tenant_id = %s
            ORDER BY created_at
            LIMIT %s
            """,
            (str(tenant_id), capacity),
        )
        rows = cur.fetchall()
    if len(rows) > limit:
        raise ValueError(f"incidents exceed the fixed materialization budget of {limit}")
    incidents: list[dict[str, Any]] = []
    for row in rows:
        incident_id, title, status, root_cause, timeline, created_at = row
        incident: dict[str, Any] = {
            "incident_id": str(incident_id),
            "title": title,
            "affected_system": title,
            "status": status,
            "root_cause": root_cause if isinstance(root_cause, str) else json.dumps(root_cause, ensure_ascii=False) if root_cause else None,
            "timeline": timeline if isinstance(timeline, list) else [],
        }
        if isinstance(created_at, datetime):
            incident["created_at"] = created_at.astimezone(timezone.utc).isoformat()
        incidents.append(incident)
    payload_bytes = json.dumps(incidents, ensure_ascii=False, sort_keys=True).encode("utf-8")
    artifact = _write_artifact(
        out_dir,
        name=f"db-incidents-{tenant_id.hex[:8]}.json",
        media_type="application/json",
        content=payload_bytes,
        tenant_id=tenant_id,
        classification=classification,
    )
    return MaterializedArtifact(artifact=artifact, row_count=len(incidents), description="aiops.incidents")


def materialize_topology(
    connection: Any,
    *,
    tenant_id: UUID,
    out_dir: Path,
    node_limit: int = 10_000,
    edge_limit: int = 50_000,
    classification: str = "internal",
) -> MaterializedArtifact:
    """Materialize a conservative causal topology from ``aiops.incidents``.

    The causal graph joins an incident to the alerts it aggregated.  Nodes are
    ``incident`` and ``alert`` entries; edges point from an incident to each of
    the alerts in its timeline.  The graph is deterministic (sorted ids) so the
    resulting artifact is reproducible for idempotency.
    """
    _set_tenant(connection, tenant_id)
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, root_cause, timeline
            FROM aiops.incidents
            WHERE tenant_id = %s
            ORDER BY created_at
            """,
            (str(tenant_id),),
        )
        incident_rows = cur.fetchall()
        alert_ids: set[str] = set()
        for _incident_id, _title, _root_cause, timeline in incident_rows:
            if not isinstance(timeline, list):
                continue
            for entry in timeline:
                if isinstance(entry, dict) and isinstance(entry.get("alert_id"), str):
                    alert_ids.add(entry["alert_id"])
        cur.execute(
            """
            SELECT id, source, severity
            FROM aiops.alerts
            WHERE tenant_id = %s AND id = ANY(%s::uuid[])
            ORDER BY occurred_at
            """,
            (str(tenant_id), sorted(alert_ids)),
        )
        alert_rows = cur.fetchall()
    if len(incident_rows) > node_limit or len(alert_rows) > node_limit:
        raise ValueError(f"topology exceeds the fixed materialization budget of {node_limit} nodes")

    graph: dict[str, Any] = {
        "nodes_by_type": {
            "incident": [
                {
                    "id": str(incident_id),
                    "text": title or "",
                    "node_type": "incident",
                    "confidence": 0.5,
                }
                for incident_id, title, _root_cause, _timeline in incident_rows
            ],
            "alert": [
                {
                    "id": str(alert_id),
                    "text": source or "",
                    "node_type": "alert",
                    "confidence": 0.5,
                }
                for alert_id, source, _severity in alert_rows
            ],
        },
        "edges": {"incident_aggregates_alert": []},
    }
    for incident_id, _title, _root_cause, timeline in incident_rows:
        if not isinstance(timeline, list):
            continue
        for entry in timeline:
            if not isinstance(entry, dict) or not isinstance(entry.get("alert_id"), str):
                continue
            if len(graph["edges"]["incident_aggregates_alert"]) >= edge_limit:
                raise ValueError(f"topology exceeds the fixed materialization budget of {edge_limit} edges")
            graph["edges"]["incident_aggregates_alert"].append(
                {"source": str(incident_id), "target": entry["alert_id"], "confidence": 0.5, "strength": 0.5}
            )
    graph_bytes = json.dumps(graph, ensure_ascii=False, sort_keys=True).encode("utf-8")
    artifact = _write_artifact(
        out_dir,
        name=f"db-topology-{tenant_id.hex[:8]}.json",
        media_type="application/json",
        content=graph_bytes,
        tenant_id=tenant_id,
        classification=classification,
    )
    return MaterializedArtifact(
        artifact=artifact,
        row_count=len(incident_rows) + len(alert_rows),
        description="aiops.incidents + aiops.alerts causal topology",
    )


def materialize_semantic_document(
    connection: Any,
    *,
    tenant_id: UUID,
    out_dir: Path,
    document_ref: str | None = None,
    max_chunks: int = 4_000,
    classification: str = "internal",
) -> MaterializedArtifact:
    """Materialize one knowledge document's chunks as a Markdown artifact.

    ``document_ref`` may be a ``semantic.documents.id`` or ``source_uri``;
    when omitted the most recently updated ingested document is chosen.
    """
    _set_tenant(connection, tenant_id)
    with connection.cursor() as cur:
        if document_ref:
            try:
                candidate = UUID(str(document_ref))
            except ValueError:
                cur.execute(
                    """
                    SELECT d.id FROM semantic.documents d
                    WHERE d.tenant_id = %s AND d.source_uri = %s AND d.status <> 'retired'
                    ORDER BY d.updated_at DESC LIMIT 1
                    """,
                    (str(tenant_id), str(document_ref)),
                )
            else:
                cur.execute(
                    """
                    SELECT d.id FROM semantic.documents d
                    WHERE d.tenant_id = %s AND d.id = %s AND d.status <> 'retired'
                    """,
                    (str(tenant_id), str(candidate)),
                )
        else:
            cur.execute(
                """
                SELECT d.id FROM semantic.documents d
                WHERE d.tenant_id = %s AND d.status <> 'retired'
                  AND EXISTS (SELECT 1 FROM semantic.chunks c WHERE c.document_id = d.id)
                ORDER BY d.updated_at DESC LIMIT 1
                """,
                (str(tenant_id),),
            )
        row = cur.fetchone()
        if row is None:
            raise ValueError("no non-retired chunked knowledge document is available to materialize")
        document_id = UUID(str(row[0]))  # psycopg2 may hand back str or uuid.UUID
        cur.execute(
            """
            SELECT ordinal, content
            FROM semantic.chunks
            WHERE tenant_id = %s AND document_id = %s
            ORDER BY ordinal
            LIMIT %s
            """,
            (str(tenant_id), str(document_id), max_chunks),
        )
        chunk_rows = cur.fetchall()
    if len(chunk_rows) >= max_chunks:
        raise ValueError(f"document chunks exceed the fixed materialization budget of {max_chunks}")
    markdown = "\n\n".join(content for _ordinal, content in chunk_rows) + "\n"
    artifact = _write_artifact(
        out_dir,
        name=f"db-document-{document_id.hex[:8]}.md",
        media_type="text/markdown",
        content=markdown.encode("utf-8"),
        tenant_id=tenant_id,
        classification=classification,
    )
    return MaterializedArtifact(
        artifact=artifact,
        row_count=len(chunk_rows),
        description=f"semantic.chunks of document {document_id}",
    )