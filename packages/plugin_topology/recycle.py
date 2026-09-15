"""Recycle / restore helpers for Plugin Topology (M1).

Recycling snapshots an entity into ``topology.topology_recycle_bin`` and only
soft-deactivates it; restoring requires an explicit ``restored_from_recycle_id``
and re-activates the snapshot.  Hard deletion is never performed.
"""

from __future__ import annotations

from typing import Any

import psycopg2
from psycopg2.extras import Json

from packages.policy.engine import PolicyEngine

from .contracts import TopologyRecycle


class RecycleService:
    """Tenant-scoped soft-delete/restore gateway for topology entities."""

    def __init__(self, database_url: str, policy: PolicyEngine | None = None, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.policy = policy or PolicyEngine()
        self.tenant_slug = tenant_slug

    def _tenant(self, cur: Any) -> Any:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.fetchone()
        return tenant_id

    def _gate(self, capability: str, arguments: dict[str, Any], *, risk_class: str = "medium") -> None:
        decision = self.policy.evaluate(capability, arguments, risk_class, side_effects="write_data")
        if decision.decision != "allow":
            raise PermissionError(f"policy denied {capability}: {decision.reason}")

    def recycle(self, request: TopologyRecycle | dict[str, Any]) -> dict[str, Any]:
        """Soft-delete one entity (snapshot kept, no hard delete)."""
        if isinstance(request, dict):
            request = TopologyRecycle.parse(request)
        if request.recycle_type != "recycled":
            raise ValueError("recycle() only accepts recycle_type='recycled'")
        self._gate("topology.recycle.write", {"entity_kind": request.entity_kind, "entity_key": request.entity_key})
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                snapshot = self._fetch_snapshot(cur, tenant_id, request)
                cur.execute(
                    """INSERT INTO topology.topology_recycle_bin
                    (tenant_id,entity_kind,entity_id,snapshot,reason,recycle_type,created_by)
                    VALUES(%s,%s,%s,%s,%s,'recycled',%s) RETURNING id""",
                    (tenant_id, request.entity_kind, snapshot["entity_id"], psycopg2.extras.Json(snapshot["payload"]), request.reason, request.created_by),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("recycle insert returned no id")
                self._deactivate(cur, tenant_id, request)
                return {
                    "recycle_type": "recycled",
                    "recycle_id": str(row[0]),
                    "entity_kind": request.entity_kind,
                    "entity_key": request.entity_key,
                    "snapshot_kept": True,
                }

    def restore(self, request: TopologyRecycle | dict[str, Any]) -> dict[str, Any]:
        """Restore a recycled entity from its recorded snapshot."""
        if isinstance(request, dict):
            request = TopologyRecycle.parse(request)
        if request.recycle_type != "restored":
            raise ValueError("restore() only accepts recycle_type='restored'")
        if request.restored_from_recycle_id is None:
            raise ValueError("restore requires restored_from_recycle_id")
        self._gate("topology.recycle.restore", {"entity_kind": request.entity_kind, "entity_key": request.entity_key})
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    "SELECT entity_kind,entity_id,snapshot FROM topology.topology_recycle_bin "
                    "WHERE tenant_id=%s AND id=%s AND recycle_type='recycled'",
                    (tenant_id, request.restored_from_recycle_id),
                )
                recycled = cur.fetchone()
                if recycled is None:
                    raise ValueError("recycled record not found")
                self._reactivate(cur, tenant_id, request)
                cur.execute(
                    """INSERT INTO topology.topology_recycle_bin
                    (tenant_id,entity_kind,entity_id,snapshot,reason,recycle_type,restored_from,created_by)
                    VALUES(%s,%s,%s,%s,%s,'restored',%s,%s) RETURNING id""",
                    (tenant_id, request.entity_kind, recycled[1], Json(recycled[2]), request.reason, request.restored_from_recycle_id, request.created_by),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("restore insert returned no id")
                return {
                    "recycle_type": "restored",
                    "restore_id": str(row[0]),
                    "entity_kind": request.entity_kind,
                    "entity_key": request.entity_key,
                    "restored_from_recycle_id": str(request.restored_from_recycle_id),
                }

    def _fetch_snapshot(self, cur: Any, tenant_id: Any, request: TopologyRecycle) -> dict[str, Any]:
        if request.entity_kind == "cluster":
            cur.execute(
                "SELECT id,key,name,cluster_kind,description,routing_budget,status FROM topology.plugin_clusters "
                "WHERE tenant_id=%s AND key=%s",
                (tenant_id, request.entity_key),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"cluster not found: {request.entity_key}")
            return {
                "entity_id": row[0],
                "payload": {"key": row[1], "name": row[2], "cluster_kind": row[3], "description": row[4], "routing_budget": row[5], "status": row[6]},
            }
        if request.entity_kind == "blueprint":
            cur.execute(
                "SELECT id,key,name,blueprint_type,capability_contract,source_refs,status FROM topology.plugin_blueprints "
                "WHERE tenant_id=%s AND key=%s",
                (tenant_id, request.entity_key),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"blueprint not found: {request.entity_key}")
            return {
                "entity_id": row[0],
                "payload": {"key": row[1], "name": row[2], "blueprint_type": row[3], "capability_contract": row[4], "source_refs": row[5], "status": row[6]},
            }
        if request.entity_kind == "edge":
            source_key, target_key = self._edge_keys(request.entity_key)
            cur.execute(
                """SELECT e.id,s.key,tt.key,e.relation_type,e.status
                FROM topology.topology_edges e
                JOIN topology.plugin_blueprints s ON s.id=e.source_blueprint_id AND s.tenant_id=e.tenant_id
                JOIN topology.plugin_blueprints tt ON tt.id=e.target_blueprint_id AND tt.tenant_id=e.tenant_id
                WHERE e.tenant_id=%s AND s.key=%s AND tt.key=%s""",
                (tenant_id, source_key, target_key),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"edge not found: {request.entity_key}")
            return {
                "entity_id": row[0],
                "payload": {"source": row[1], "target": row[2], "relation_type": row[3], "status": row[4]},
            }
        raise ValueError(f"unsupported recycle entity_kind: {request.entity_kind}")

    def _deactivate(self, cur: Any, tenant_id: Any, request: TopologyRecycle) -> None:
        if request.entity_kind == "cluster":
            cur.execute("UPDATE topology.plugin_clusters SET status='retired' WHERE tenant_id=%s AND key=%s", (tenant_id, request.entity_key))
        elif request.entity_kind == "blueprint":
            cur.execute("UPDATE topology.plugin_blueprints SET status='archived' WHERE tenant_id=%s AND key=%s", (tenant_id, request.entity_key))
        elif request.entity_kind == "edge":
            source_key, target_key = self._edge_keys(request.entity_key)
            cur.execute(
                """UPDATE topology.topology_edges e SET status='inactive'
                FROM topology.plugin_blueprints s JOIN topology.plugin_blueprints tt ON tt.tenant_id=s.tenant_id
                WHERE e.tenant_id=%s AND e.source_blueprint_id=s.id AND e.target_blueprint_id=tt.id
                  AND s.key=%s AND tt.key=%s""",
                (tenant_id, source_key, target_key),
            )
        else:
            raise ValueError(f"entity_kind cannot be recycled: {request.entity_kind}")

    def _reactivate(self, cur: Any, tenant_id: Any, request: TopologyRecycle) -> None:
        if request.entity_kind == "cluster":
            cur.execute("UPDATE topology.plugin_clusters SET status='active' WHERE tenant_id=%s AND key=%s", (tenant_id, request.entity_key))
        elif request.entity_kind == "blueprint":
            cur.execute("UPDATE topology.plugin_blueprints SET status='planned' WHERE tenant_id=%s AND key=%s", (tenant_id, request.entity_key))
        elif request.entity_kind == "edge":
            source_key, target_key = self._edge_keys(request.entity_key)
            cur.execute(
                """UPDATE topology.topology_edges e SET status='active'
                FROM topology.plugin_blueprints s JOIN topology.plugin_blueprints tt ON tt.tenant_id=s.tenant_id
                WHERE e.tenant_id=%s AND e.source_blueprint_id=s.id AND e.target_blueprint_id=tt.id
                  AND s.key=%s AND tt.key=%s""",
                (tenant_id, source_key, target_key),
            )
        else:
            raise ValueError(f"entity_kind cannot be restored: {request.entity_kind}")

    @staticmethod
    def _edge_keys(entity_key: str) -> tuple[str, str]:
        if "->" not in entity_key:
            raise ValueError("edge entity_key must use '<source>-><target>'")
        source, target = entity_key.split("->", 1)
        return source, target
