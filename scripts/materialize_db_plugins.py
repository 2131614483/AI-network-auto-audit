"""Read-only materialization of the real main database into verified plugin artifacts.

The verified read-only plugins only accept immutable local ``file://`` artifacts,
so this script turns already-persisted tenant rows (``aiops.alerts``,
``aiops.incidents``, ``semantic.chunks``) into such artifacts and runs the four
verified plugins through the same isolated subprocess runtime the API uses.

Nothing is written back: the connection is used strictly for ``SELECT`` and the
RLS session variable.  Every invocation carries a fresh trace id and a fixed
per-plugin idempotency key, as the governance rules require.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg2

from packages.plugin_runtime.db_artifacts import (
    materialize_alerts,
    materialize_incidents,
    materialize_semantic_document,
    materialize_topology,
)
from packages.plugin_runtime.runner import ArtifactInput, IsolatedPluginRuntime, PluginInvocation
from packages.policy.engine import PolicyEngine

DEFAULT_URL = os.getenv("AUDIT_NETWORK_DATABASE_URL", "postgresql://audit_app:admin@localhost:5432/audit_network")


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


def _resolve_tenant(connection: Any, slug: str) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug = %s", (slug,))
        row = cur.fetchone()
    if row is None:
        raise SystemExit(f"tenant {slug!r} does not exist in the target database")
    return UUID(str(row[0]))


def _invoke(runtime: IsolatedPluginRuntime, invocation: PluginInvocation, allow: list[str]) -> dict[str, object]:
    return runtime.invoke(invocation, PolicyEngine(allow=allow)).output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="PostgreSQL URL (default: AUDIT_NETWORK_DATABASE_URL)")
    parser.add_argument("--tenant", default="local-dev", help="tenant slug to materialize")
    parser.add_argument("--out", default=None, help="artifact output directory (default: a fresh temp dir)")
    args = parser.parse_args()

    connection = psycopg2.connect(args.url)
    tenant_id = _resolve_tenant(connection, args.tenant)
    out_dir = Path(args.out) if args.out else Path(os.getenv("TEMP", ".")) / f"db-plugin-artifacts-{uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    runtime = IsolatedPluginRuntime(allowed_roots=(out_dir.resolve(),))
    connect = lambda: psycopg2.connect(args.url)  # noqa: E731 - fresh session per materialization

    trace_id = uuid4()
    results: list[dict[str, Any]] = []
    try:
        alerts = materialize_alerts(connect(), tenant_id=tenant_id, out_dir=out_dir)
        results.append(
            {
                "plugin_id": "aiops.alert-correlation",
                "description": alerts.description,
                "rows": alerts.row_count,
                "output": _invoke(
                    runtime,
                    PluginInvocation(
                        tenant_id=tenant_id,
                        trace_id=trace_id,
                        idempotency_key="db-materialize-alert-v1",
                        plugin_id="aiops.alert-correlation",
                        capability="aiops.alert.correlate",
                        payload={
                            "alerts": {
                                "artifact": _piece(alerts.artifact),
                                "window_minutes": 60,
                                "max_candidates": 20,
                                "grouping_keys": ["affected_system"],
                            }
                        },
                    ),
                    allow=["aiops.alert.correlate"],
                ),
            }
        )

        incidents = materialize_incidents(connect(), tenant_id=tenant_id, out_dir=out_dir)
        topology = materialize_topology(connect(), tenant_id=tenant_id, out_dir=out_dir)
        results.append(
            {
                "plugin_id": "aiops.rca-ranker",
                "description": topology.description,
                "rows": topology.row_count,
                "output": _invoke(
                    runtime,
                    PluginInvocation(
                        tenant_id=tenant_id,
                        trace_id=trace_id,
                        idempotency_key="db-materialize-rca-v1",
                        plugin_id="aiops.rca-ranker",
                        capability="aiops.rca.rank",
                        payload={
                            "rca": {
                                "incident_set": _piece(incidents.artifact),
                                "topology_graph": _piece(topology.artifact),
                                "max_candidates": 20,
                                "max_hops": 2,
                            }
                        },
                    ),
                    allow=["aiops.rca.rank"],
                ),
            }
        )

        document = materialize_semantic_document(connect(), tenant_id=tenant_id, out_dir=out_dir)
        results.append(
            {
                "plugin_id": "knowledge.entity-relation-candidate",
                "description": document.description,
                "rows": document.row_count,
                "output": _invoke(
                    runtime,
                    PluginInvocation(
                        tenant_id=tenant_id,
                        trace_id=trace_id,
                        idempotency_key="db-materialize-relations-v1",
                        plugin_id="knowledge.entity-relation-candidate",
                        capability="knowledge.extract.relations",
                        payload={"document": {"artifact": _piece(document.artifact), "language": "zh"}},
                    ),
                    allow=["knowledge.extract.relations"],
                ),
            }
        )
        results.append(
            {
                "plugin_id": "knowledge.document-ingestion",
                "description": document.description,
                "rows": document.row_count,
                "output": _invoke(
                    runtime,
                    PluginInvocation(
                        tenant_id=tenant_id,
                        trace_id=trace_id,
                        idempotency_key="db-materialize-ingestion-v1",
                        plugin_id="knowledge.document-ingestion",
                        capability="knowledge.extract.document",
                        payload={"artifact": _piece(document.artifact), "max_characters": 20_000},
                    ),
                    allow=["knowledge.extract.document"],
                ),
            }
        )
    except psycopg2.Error as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 2

    print(f"tenant: {args.tenant} ({tenant_id})  trace_id: {trace_id}  artifacts: {out_dir}")
    for entry in results:
        output = entry["output"]
        summary = output.get("summary", {})
        brief = ", ".join(f"{key}={value}" for key, value in summary.items() if isinstance(value, (int, bool, str)))
        print(f"- {entry['plugin_id']:38s} rows={entry['rows']:4d}  contract={output.get('contract_id')}  {brief}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())