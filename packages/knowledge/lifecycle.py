"""Minimal, auditable ChangeSet and knowledge release lifecycle.

The service intentionally supports graph-node changes only.  It provides the
durable governance seam needed by later extractors: proposed operations are
validated, explicitly approved, atomically activated, and compensated through
an inverse ChangeSet during rollback.  No historical row is deleted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, cast
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import Json

SUPPORTED_OPERATIONS = {"create", "update", "retire", "restore"}

# Graph nodes are materialised into graph.nodes on activation.  Experience
# relations are governance-only: they record that a human confirmed an
# observed plugin collaboration (design §5.5 exit 1) but never write to
# graph.nodes and never touch a plugin manifest / executor.
NODE_TARGET_KIND = "graph.node"
RELATION_TARGET_KIND = "experience.relation"
SUPPORTED_TARGET_KINDS = {NODE_TARGET_KIND, RELATION_TARGET_KIND}
RELATION_REQUIRED_PAYLOAD = ("suggestion_id", "source_plugin_id", "target_plugin_id", "contract_id")


@dataclass(frozen=True, slots=True)
class ChangeOperation:
    target_kind: str
    operation: str
    payload: dict[str, Any]
    target_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    changeset_id: UUID
    passed: bool
    violations: tuple[str, ...]
    checksum: str


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    import datetime as _dt

    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


def _checksum(value: Any) -> str:
    encoded = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class KnowledgeLifecycleService:
    def __init__(self, database_url: str, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.tenant_slug = tenant_slug

    def _tenant(self, cur: Any) -> UUID:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id = UUID(str(row[0]))
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return tenant_id

    @staticmethod
    def _operation_payload(operation: ChangeOperation) -> dict[str, Any]:
        payload = dict(operation.payload)
        if operation.target_id is not None:
            payload.setdefault("target_id", str(operation.target_id))
        return cast(dict[str, Any], _jsonable(payload))

    def create_changeset(
        self,
        title: str,
        reason: str,
        operations: Iterable[ChangeOperation],
        *,
        graph_space_key: str | None = None,
        risk_class: str = "low",
        source_batch_id: UUID | None = None,
    ) -> UUID:
        ops = tuple(operations)
        if not title.strip():
            raise ValueError("changeset title is required")
        if not ops:
            raise ValueError("changeset must contain at least one operation")
        kinds = {op.target_kind for op in ops}
        if not kinds <= SUPPORTED_TARGET_KINDS:
            raise ValueError("MVP changesets only support graph.node operations")
        if len(kinds) > 1:
            raise ValueError("a changeset must target the same target kind")
        is_relation = RELATION_TARGET_KIND in kinds
        if is_relation and any(op.operation != "create" for op in ops):
            raise ValueError("experience.relation changesets only support create operations")
        if any(op.operation not in SUPPORTED_OPERATIONS for op in ops):
            raise ValueError("unsupported changeset operation")
        change_type = "experience" if is_relation else "graph"
        checksum = _checksum([{"kind": op.target_kind, "operation": op.operation, "payload": self._operation_payload(op)} for op in ops])
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                graph_space_id: UUID | None = None
                if graph_space_key is not None:
                    cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, graph_space_key))
                    space = cur.fetchone()
                    if space is None:
                        raise ValueError(f"graph space not found: {graph_space_key}")
                    graph_space_id = UUID(str(space[0]))
                cur.execute(
                    """INSERT INTO knowledge.change_sets
                    (tenant_id,graph_space_id,change_type,title,reason,source_batch_id,risk_class,status,checksum,submitted_at,operations)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,'draft',%s,now(),%s) RETURNING id""",
                    (tenant_id, graph_space_id, change_type, title, reason, source_batch_id, risk_class, checksum,
                     Json([{"kind": op.target_kind, "operation": op.operation, "payload": self._operation_payload(op)} for op in ops])),
                )
                changeset_row = cur.fetchone()
                if changeset_row is None:
                    raise RuntimeError("changeset insert returned no id")
                changeset_id = UUID(str(changeset_row[0]))
                for order, operation in enumerate(ops):
                    target_id = operation.target_id
                    if operation.operation == "create" and target_id is None:
                        target_id = uuid4()
                    payload = self._operation_payload(ChangeOperation(operation.target_kind, operation.operation, operation.payload, target_id))
                    before: dict[str, Any] | None = None
                    if not is_relation and target_id is not None and operation.operation != "create":
                        cur.execute("SELECT canonical_key,node_type,label,properties,space_id,deleted_at FROM graph.nodes WHERE tenant_id=%s AND id=%s", (tenant_id, target_id))
                        current = cur.fetchone()
                        if current is not None:
                            before = {"canonical_key": current[0], "node_type": current[1], "label": current[2], "properties": current[3], "space_id": str(current[4]), "deleted_at": str(current[5]) if current[5] else None}
                    inverse: dict[str, Any] = {}
                    if is_relation:
                        # Governance-only confirmation: nothing is materialised to
                        # graph.nodes, so there is no graph-level inverse to apply.
                        inverse = {}
                    elif operation.operation == "create":
                        inverse = {"operation": "retire", "target_id": str(target_id)}
                    elif operation.operation == "retire":
                        inverse = {"operation": "restore", "target_id": str(target_id)}
                    elif operation.operation == "restore":
                        inverse = {"operation": "retire", "target_id": str(target_id)}
                    elif operation.operation == "update" and before is not None:
                        inverse = {"operation": "update", "target_id": str(target_id), "payload": before}
                    cur.execute(
                        """INSERT INTO knowledge.change_operations
                        (tenant_id,change_set_id,operation_type,target_kind,target_id,payload,operation_order,object_type,object_id,operation,before_json,after_json,inverse_json)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (tenant_id, changeset_id, operation.operation, operation.target_kind, target_id, Json(payload), order,
                         operation.target_kind, target_id, operation.operation, Json(before) if before is not None else None, Json(payload), Json(inverse)),
                    )
                return changeset_id

    def validate_changeset(self, changeset_id: UUID) -> ValidationResult:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute("SELECT status,checksum FROM knowledge.change_sets WHERE tenant_id=%s AND id=%s FOR UPDATE", (tenant_id, changeset_id))
                change = cur.fetchone()
                if change is None:
                    raise ValueError("changeset not found")
                cur.execute(
                    """SELECT operation,target_id,after_json,target_kind FROM knowledge.change_operations
                    WHERE tenant_id=%s AND change_set_id=%s ORDER BY operation_order,id""",
                    (tenant_id, changeset_id),
                )
                rows = cur.fetchall()
                violations: list[str] = []
                for operation, target_id, payload, target_kind in rows:
                    payload = payload or {}
                    if target_kind == RELATION_TARGET_KIND:
                        missing = [key for key in RELATION_REQUIRED_PAYLOAD if not payload.get(key)]
                        if missing:
                            violations.append(f"experience.relation create requires {', '.join(missing)}")
                            continue
                        cur.execute(
                            """SELECT status FROM experience.relation_suggestions
                               WHERE tenant_id=%s AND suggestion_id=%s""",
                            (tenant_id, payload["suggestion_id"]),
                        )
                        suggestion = cur.fetchone()
                        if suggestion is None:
                            violations.append(f"relation suggestion not found: {payload['suggestion_id']}")
                        elif suggestion[0] != "accepted":
                            violations.append(f"relation suggestion must be accepted, got: {suggestion[0]}")
                        continue
                    if operation == "create":
                        if not payload.get("canonical_key") or not payload.get("node_type") or not payload.get("label"):
                            violations.append("create requires canonical_key, node_type and label")
                        space_key = payload.get("space_key")
                        if not space_key:
                            violations.append("create requires space_key")
                        else:
                            cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, space_key))
                            space = cur.fetchone()
                            if space is None:
                                violations.append(f"graph space not found: {space_key}")
                            else:
                                cur.execute("SELECT 1 FROM graph.nodes WHERE tenant_id=%s AND space_id=%s AND canonical_key=%s", (tenant_id, space[0], payload["canonical_key"]))
                                if cur.fetchone() is not None:
                                    violations.append(f"active node already exists: {payload['canonical_key']}")
                    elif target_id is None:
                        violations.append(f"{operation} requires target_id")
                    else:
                        cur.execute("SELECT 1 FROM graph.nodes WHERE tenant_id=%s AND id=%s", (tenant_id, target_id))
                        if cur.fetchone() is None:
                            violations.append(f"node not found: {target_id}")
                passed = not violations
                status = "pending_review" if passed else "failed"
                cur.execute("UPDATE knowledge.change_sets SET status=%s WHERE tenant_id=%s AND id=%s", (status, tenant_id, changeset_id))
                cur.execute(
                    """INSERT INTO knowledge.validation_runs
                    (tenant_id,change_set_id,validator_key,validator_version,status,metrics,violations,finished_at)
                    VALUES(%s,%s,'knowledge.lifecycle','1.0',%s,%s,%s,now())""",
                    (tenant_id, changeset_id, "passed" if passed else "failed", Json({"operation_count": len(rows)}), Json(violations)),
                )
                return ValidationResult(changeset_id, passed, tuple(violations), str(change[1] or ""))

    def approve_changeset(self, changeset_id: UUID) -> None:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute("UPDATE knowledge.change_sets SET status='approved',approved_at=now() WHERE tenant_id=%s AND id=%s AND status='pending_review'", (tenant_id, changeset_id))
                if cur.rowcount != 1:
                    raise ValueError("changeset must pass validation before approval")

    def list_changesets(self, *, change_type: str = "graph", statuses: set[str] | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List ChangeSet governance records with per-candidate node summary.

        Read-only governance snapshot; never mutates state.  Only ``create``
        graph-node operations carrying a complete node description are counted
        as candidates, so unrelated lifecycle entries are not surfaced.
        """
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            if statuses:
                cur.execute(
                    """SELECT id,title,status,risk_class,graph_space_id,submitted_at,approved_at,applied_at
                    FROM knowledge.change_sets
                    WHERE tenant_id=%s AND change_type=%s AND status = ANY(%s) AND source_batch_id IS NULL
                    ORDER BY created_at DESC LIMIT %s""",
                    (tenant_id, change_type, list(statuses), limit),
                )
            else:
                cur.execute(
                    """SELECT id,title,status,risk_class,graph_space_id,submitted_at,approved_at,applied_at
                    FROM knowledge.change_sets
                    WHERE tenant_id=%s AND change_type=%s AND source_batch_id IS NULL
                    ORDER BY created_at DESC LIMIT %s""",
                    (tenant_id, change_type, limit),
                )
            rows = cur.fetchall()
            summaries: list[dict[str, Any]] = []
            for (changeset_id, title, status, risk, graph_space_id, submitted, approved, applied) in rows:
                cur.execute(
                    """SELECT after_json->>'space_key', after_json->>'canonical_key',
                           after_json->>'node_type', after_json->>'label', operation
                    FROM knowledge.change_operations
                    WHERE tenant_id=%s AND change_set_id=%s AND object_type='graph.node'
                      AND operation='create'
                      AND after_json ? 'space_key' AND after_json ? 'canonical_key'
                      AND after_json ? 'node_type' AND after_json ? 'label'
                    ORDER BY operation_order,id""",
                    (tenant_id, changeset_id),
                )
                node_rows = cur.fetchall()
                nodes: list[dict[str, Any]] = []
                for space_key, canonical_key, node_type, label, operation in node_rows:
                    nodes.append({
                        "node_key": canonical_key, "node_type": node_type,
                        "label": label, "space_key": space_key, "operation": operation,
                    })
                graph_space_key: str | None = None
                if graph_space_id is not None:
                    cur.execute("SELECT key FROM graph.spaces WHERE tenant_id=%s AND id=%s", (tenant_id, graph_space_id))
                    space = cur.fetchone()
                    graph_space_key = str(space[0]) if space else None
                summaries.append({
                    "id": str(changeset_id), "title": title, "status": status,
                    "risk_class": risk, "graph_space_key": graph_space_key,
                    "candidate_count": len(nodes), "nodes": nodes,
                    "submitted_at": _jsonable(submitted), "approved_at": _jsonable(approved),
                    "applied_at": _jsonable(applied),
                })
            return summaries

    def apply_changeset(self, changeset_id: UUID, version: str | None = None) -> UUID:
        """Governed 放行: validate → approve → release → activate (write to live graph)."""
        self.validate_changeset(changeset_id)
        self.approve_changeset(changeset_id)
        release_version = version or f"v-{uuid4().hex[:8]}"
        release_id = self.create_release(changeset_id, release_version)
        self.activate_release(release_id)
        return release_id

    def reject_changeset(self, changeset_id: UUID) -> None:
        """驳回 a staged proposal that has not yet been applied."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """UPDATE knowledge.change_sets SET status='rejected'
                    WHERE tenant_id=%s AND id=%s AND status IN ('draft','pending_review','approved')""",
                    (tenant_id, changeset_id),
                )
                if cur.rowcount != 1:
                    raise ValueError("changeset cannot be rejected from its current status")

    def create_release(self, changeset_id: UUID, version: str) -> UUID:
        if not version.strip():
            raise ValueError("release version is required")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute("SELECT status,checksum FROM knowledge.change_sets WHERE tenant_id=%s AND id=%s", (tenant_id, changeset_id))
                change = cur.fetchone()
                if change is None or change[0] != "approved":
                    raise ValueError("only an approved changeset can be released")
                cur.execute("SELECT id FROM knowledge.releases WHERE tenant_id=%s AND status='active' ORDER BY activated_at DESC LIMIT 1", (tenant_id,))
                parent = cur.fetchone()
                cur.execute(
                    """INSERT INTO knowledge.releases(tenant_id,version,status,change_set_ids,changeset_id,parent_release_id,checksum)
                    VALUES(%s,%s,'draft',ARRAY[%s]::uuid[],%s,%s,%s) RETURNING id""",
                    (tenant_id, version, changeset_id, changeset_id, parent[0] if parent else None, change[1]),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("release insert returned no id")
                return UUID(str(row[0]))

    def activate_release(self, release_id: UUID) -> None:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"knowledge-release:{tenant_id}",))
                cur.execute("SELECT status,changeset_id FROM knowledge.releases WHERE tenant_id=%s AND id=%s FOR UPDATE", (tenant_id, release_id))
                release = cur.fetchone()
                if release is None or release[0] not in {"draft", "active"}:
                    raise ValueError("release is not activatable")
                if release[1] is None:
                    raise ValueError("release has no changeset")
                cur.execute("SELECT status FROM knowledge.change_sets WHERE tenant_id=%s AND id=%s", (tenant_id, release[1]))
                change = cur.fetchone()
                if change is None or change[0] != "approved":
                    raise ValueError("release changeset is not approved")
                cur.execute("SELECT operation_order,operation,target_id,after_json,target_kind FROM knowledge.change_operations WHERE tenant_id=%s AND change_set_id=%s ORDER BY operation_order,id FOR UPDATE", (tenant_id, release[1]))
                operations = cur.fetchall()
                for operation_order, operation, target_id, payload, target_kind in operations:
                    payload = payload or {}
                    if target_kind == RELATION_TARGET_KIND:
                        # Governance-only confirmation: mark applied without
                        # materialising anything into graph.nodes (the relation
                        # endpoints are plugins, not knowledge-graph nodes).
                        inverse = {}
                    elif operation == "create":
                        cur.execute("SELECT id FROM graph.spaces WHERE tenant_id=%s AND key=%s", (tenant_id, payload.get("space_key"),))
                        space = cur.fetchone()
                        if space is None:
                            raise ValueError("create operation graph space not found")
                        cur.execute("INSERT INTO graph.nodes(id,tenant_id,space_id,canonical_key,node_type,label,properties) VALUES(%s,%s,%s,%s,%s,%s,%s)", (target_id, tenant_id, space[0], payload["canonical_key"], payload["node_type"], payload["label"], Json(payload.get("properties", {}))))
                        inverse = {"target_id": str(target_id)}
                    else:
                        if operation == "update":
                            cur.execute("UPDATE graph.nodes SET label=COALESCE(%s,label),node_type=COALESCE(%s,node_type),properties=COALESCE(%s,properties),row_version=row_version+1 WHERE tenant_id=%s AND id=%s", (payload.get("label"), payload.get("node_type"), Json(payload["properties"]) if "properties" in payload else None, tenant_id, target_id))
                        elif operation == "retire":
                            cur.execute("UPDATE graph.nodes SET deleted_at=now(),row_version=row_version+1 WHERE tenant_id=%s AND id=%s", (tenant_id, target_id))
                        elif operation == "restore":
                            cur.execute("UPDATE graph.nodes SET deleted_at=NULL,row_version=row_version+1 WHERE tenant_id=%s AND id=%s", (tenant_id, target_id))
                        if cur.rowcount != 1:
                            raise ValueError(f"operation target not found: {target_id}")
                        inverse = {}
                    cur.execute("UPDATE knowledge.change_operations SET status='applied',inverse_json=%s WHERE tenant_id=%s AND change_set_id=%s AND operation_order=%s", (Json(inverse), tenant_id, release[1], operation_order))
                cur.execute("UPDATE knowledge.releases SET status='inactive',deactivated_at=now() WHERE tenant_id=%s AND status='active'", (tenant_id,))
                cur.execute("UPDATE knowledge.releases SET status='active',activated_at=now() WHERE tenant_id=%s AND id=%s", (tenant_id, release_id))
                cur.execute("UPDATE knowledge.change_sets SET status='applied',applied_at=now() WHERE tenant_id=%s AND id=%s", (tenant_id, release[1]))

    def rollback_release(self, release_id: UUID, version: str) -> UUID:
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            self._lock_release(cur, tenant_id, release_id)
            cur.execute("SELECT status,changeset_id FROM knowledge.releases WHERE tenant_id=%s AND id=%s", (tenant_id, release_id))
            release = cur.fetchone()
            if release is None or release[0] != "active" or release[1] is None:
                raise ValueError("only the active release can be rolled back")
            cur.execute("SELECT operation,target_id,after_json,before_json,inverse_json,target_kind FROM knowledge.change_operations WHERE tenant_id=%s AND change_set_id=%s ORDER BY operation_order DESC,id DESC", (tenant_id, release[1]))
            rows = cur.fetchall()
        inverse_ops: list[ChangeOperation] = []
        for operation, target_id, payload, before, inverse, target_kind in rows:
            if target_kind == RELATION_TARGET_KIND:
                # Confirmed experience relations are retained, never auto-undone.
                continue
            if operation == "create":
                inverse_ops.append(ChangeOperation("graph.node", "retire", {}, UUID(str(target_id))))
            elif operation == "retire":
                inverse_ops.append(ChangeOperation("graph.node", "restore", {}, UUID(str(target_id))))
            elif operation == "restore":
                inverse_ops.append(ChangeOperation("graph.node", "retire", {}, UUID(str(target_id))))
            elif operation == "update":
                if before is None:
                    raise ValueError("update operation has no before snapshot")
                inverse_ops.append(ChangeOperation("graph.node", "update", dict(before), UUID(str(target_id))))
        if not inverse_ops:
            raise ValueError("release contains only experience.relation operations and is not auto-rolled back; evidence is retained")
        rollback_id = self.create_changeset(f"Rollback {release_id}", "Compensating rollback", inverse_ops, risk_class="medium")
        self.validate_changeset(rollback_id)
        self.approve_changeset(rollback_id)
        new_release = self.create_release(rollback_id, version)
        self.activate_release(new_release)
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            tenant_id = self._tenant(cur)
            cur.execute("UPDATE knowledge.change_sets SET rolled_back_from_id=%s WHERE tenant_id=%s AND id=%s", (release[1], tenant_id, rollback_id))
            cur.execute("UPDATE knowledge.change_sets SET status='rolled_back' WHERE tenant_id=%s AND id=%s", (tenant_id, release[1]))
        return new_release

    @staticmethod
    def _lock_release(cur: Any, tenant_id: UUID, release_id: UUID) -> None:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"knowledge-release:{tenant_id}",))
