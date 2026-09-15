"""Topology registration, release, recycle and plan-only routing service (M1).

Every write path carries a tenant, a trace id and an idempotency key, passes
the Policy gateway, and persists to versioned/recyclable topology tables.
Releases are frozen by catalog checksum and are never overwritten in place.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import Json, register_uuid

from packages.plugin_runtime.runner import IsolatedPluginRuntime
from packages.policy.engine import PolicyEngine

from .approvals import approval_ref_for, transition_status
from .chain import ChainResolver, IntentGenerator
from .compiler import compile_plan
from .contracts import (
    ApprovalRequest,
    ChainExecute,
    ChainMaterialize,
    ChainRunRequest,
    RemediationProposalRequest,
    RunVerificationRequest,
    TopologyPlan,
    TopologyRecycle,
    TopologyRelease,
    TopologyUpsert,
)
from .dag_persistence import AttemptStore
from .evidence import (
    EVIDENCE_SCOPES,
    anchor_by_key,
    chain_status,
)
from .evidence import (
    anchor_evidence as _anchor_evidence,
)
from .evidence import (
    export_evidence as _export_evidence,
)
from .evidence import (
    verify_evidence_chain as _verify_evidence_chain,
)
from .executor import EXECUTION_MODE, gate_blockers, node_output_checksum
from .isolated import ISOLATED_MODE, IsolatedChainExecutor
from .planner import RoutingPlan, TopologyPlanner
from .ports_executor import PortBoundExecutor
from .remediation import (
    RemediationConflictError,
    create_proposal,
    decide_proposal,
    link_remediation_run,
    list_proposal_rows,
    proposal_by_key,
    proposal_projection,
)
from .runs import (
    begin_run,
    finalize_run,
    list_run_rows,
    load_chain_context,
    preflight,
    run_entries,
    run_projection,
)
from .verification import (
    list_verification_rows,
    verification_by_key,
    verification_projection,
)
from .verification import (
    verify_run as _verify_run,
)

register_uuid()  # type: ignore[no-untyped-call]

_CAPABILITY_BY_KIND = {
    "cluster": "topology.cluster.write",
    "blueprint": "topology.blueprint.write",
    "membership": "topology.membership.write",
    "edge": "topology.edge.write",
    "contract": "topology.contract.write",
    "bridge": "topology.bridge.write",
}

# Cross-domain bridges expose only public references.  The ref_kind dictates
# the shape of the ref so a caller cannot smuggle a domain-private read.
_BRIDGE_REF_PREFIX = {
    "artifact_ref": "artifact://",
    "capability_contract": "contracts/jsonschema/",
    "released_graph_ref": "graph://release/",
    "health_signal": "health://",
}


class TopologyService:
    """Tenant-scoped topology governance behind the Policy gateway."""

    def __init__(self, database_url: str, policy: PolicyEngine | None = None, tenant_slug: str = "local-dev") -> None:
        self.database_url = database_url
        self.policy = policy or PolicyEngine()
        self.tenant_slug = tenant_slug
        self.planner = TopologyPlanner()
        self.resolver = ChainResolver()
        self.generator = IntentGenerator()

    # -- helpers -------------------------------------------------------------

    def _tenant(self, cur: Any) -> UUID:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (self.tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {self.tenant_slug}")
        tenant_id: UUID = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        cur.fetchone()
        return tenant_id

    def _gate(self, capability: str, arguments: dict[str, Any], *, risk_class: str = "medium") -> None:
        decision = self.policy.evaluate(capability, arguments, risk_class, side_effects="write_data")
        if decision.decision != "allow":
            raise PermissionError(f"policy denied {capability}: {decision.reason}")

    @staticmethod
    def _idempotent(cur: Any, tenant_id: UUID, idempotency_key: str) -> dict[str, Any] | None:
        """Return the recorded business response for a replay, or None for a fresh key.

        Only rows that the topology service itself wrote (response_status=201)
        are ever replayed.  The API-level Policy gateway records its own
        decision under the same key with response_status=200; replaying that
        gateway projection as a business response would be a type confusion.
        """
        cur.execute(
            "SELECT request_hash,response_json FROM control.idempotency_records "
            "WHERE tenant_id=%s AND idempotency_key=%s AND response_status=201",
            (tenant_id, idempotency_key),
        )
        existing = cur.fetchone()
        if existing is None:
            return None
        response = existing[1] if isinstance(existing[1], dict) else {}
        return {**response, "idempotent": True}

    @staticmethod
    def _record_idempotent(
        cur: Any, tenant_id: UUID, idempotency_key: str, request_hash: str, response: dict[str, Any]
    ) -> None:
        cur.execute(
            """INSERT INTO control.idempotency_records(tenant_id,idempotency_key,request_hash,response_status,response_json)
            VALUES(%s,%s,%s,201,%s)
            ON CONFLICT(tenant_id,idempotency_key) DO UPDATE
              SET response_status=201,response_json=EXCLUDED.response_json""",
            (tenant_id, idempotency_key, request_hash, Json(response)),
        )

    @staticmethod
    def _request_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()

    # -- registration --------------------------------------------------------

    def upsert(self, request: TopologyUpsert | dict[str, Any]) -> dict[str, Any]:
        """Register or update one topology entity under an idempotency key."""
        if isinstance(request, dict):
            request = TopologyUpsert.parse(request)
        self._validate_upsert(request)
        arguments = {"kind": request.kind, "entity_key": request.payload.get("key") or request.payload.get("contract_id") or ""}
        self._gate(_CAPABILITY_BY_KIND[request.kind], arguments)
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                response = self._apply_upsert(cur, tenant_id, request)
                self._record_idempotent(
                    cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response
                )
                return response

    def _validate_upsert(self, request: TopologyUpsert) -> None:
        if request.kind == "cluster":
            key = str(request.payload["key"])
            axis = str(request.payload["axis"])
            budget = request.payload["routing_budget"]
            if axis not in {"business_domain", "capability_family", "runtime_pool", "governance_zone"}:
                raise ValueError("invalid cluster axis")
            if int(budget["max_candidates"]) < 1 or int(budget["max_chain_length"]) < 1:
                raise ValueError("routing budget must be at least 1")
            if key in {"business-financial-audit", "capability-audit-quality", "pool-local-cpu", "governance-local-dev-approved"}:
                raise ValueError("seed cluster keys are reserved")
        elif request.kind == "blueprint":
            if request.payload.get("lifecycle") != "planned":
                raise ValueError("blueprint lifecycle must be planned (no execution fields)")
        elif request.kind == "membership":
            if request.payload["axis"] not in {"business", "capability", "resource", "governance"}:
                raise ValueError("invalid membership axis")
        elif request.kind == "edge":
            if request.payload["relation_type"] not in {"depends_on", "data_flow", "fallback", "bridge"}:
                raise ValueError("invalid relation type")
            if request.payload["source_blueprint_key"] == request.payload["target_blueprint_key"]:
                raise ValueError("an edge cannot connect a blueprint to itself")
        elif request.kind == "contract":
            if request.payload["kind"] not in {"input", "output", "event"}:
                raise ValueError("invalid contract kind")
        elif request.kind == "bridge":
            ref_kind = request.payload.get("ref_kind")
            if ref_kind not in {"artifact_ref", "capability_contract", "released_graph_ref", "health_signal"}:
                raise ValueError(f"invalid bridge ref_kind: {ref_kind}")
            bridge_ref = str(request.payload.get("bridge_ref") or "")
            if not bridge_ref:
                raise ValueError("bridge_ref is required for bridge upsert")
            if not bridge_ref.startswith(_BRIDGE_REF_PREFIX[ref_kind]):
                raise ValueError(
                    f"bridge_ref does not match ref_kind={ref_kind}, expected prefix {_BRIDGE_REF_PREFIX[ref_kind]}"
                )
        else:
            raise ValueError(f"unsupported topology kind: {request.kind}")

    def _apply_upsert(self, cur: Any, tenant_id: UUID, request: TopologyUpsert) -> dict[str, Any]:
        payload = request.payload
        if request.kind == "cluster":
            parent_id = None
            if payload.get("parent_cluster_key"):
                cur.execute(
                    "SELECT id FROM topology.plugin_clusters WHERE tenant_id=%s AND key=%s",
                    (tenant_id, payload["parent_cluster_key"]),
                )
                parent = cur.fetchone()
                if parent is None:
                    raise ValueError(f"parent cluster not found: {payload['parent_cluster_key']}")
                parent_id = parent[0]
            cur.execute(
                """INSERT INTO topology.plugin_clusters
                (tenant_id,key,name,parent_cluster_id,cluster_kind,description,routing_budget,allowed_bridge_kinds,status)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'draft')
                ON CONFLICT(tenant_id,key) DO UPDATE SET
                  name=EXCLUDED.name,parent_cluster_id=EXCLUDED.parent_cluster_id,
                  cluster_kind=EXCLUDED.cluster_kind,description=EXCLUDED.description,
                  routing_budget=EXCLUDED.routing_budget,allowed_bridge_kinds=EXCLUDED.allowed_bridge_kinds,
                  updated_at=now()
                RETURNING id""",
                (
                    tenant_id, payload["key"], payload["name"], parent_id, payload["axis"],
                    payload.get("description", ""), Json(payload["routing_budget"]),
                    Json(payload.get("allowed_bridge_kinds", [])),
                ),
            )
            entity_id = cur.fetchone()
            if entity_id is None:
                raise RuntimeError("cluster upsert returned no id")
            return {"kind": "cluster", "entity_key": payload["key"], "entity_id": str(entity_id[0]), "idempotent": False}
        if request.kind == "blueprint":
            primary_cluster_id = None
            if payload.get("primary_cluster_key"):
                cur.execute(
                    "SELECT id FROM topology.plugin_clusters WHERE tenant_id=%s AND key=%s",
                    (tenant_id, payload["primary_cluster_key"]),
                )
                cluster = cur.fetchone()
                if cluster is None:
                    raise ValueError(f"primary cluster not found: {payload['primary_cluster_key']}")
                primary_cluster_id = cluster[0]
            cur.execute(
                """INSERT INTO topology.plugin_blueprints
                (tenant_id,primary_cluster_id,key,name,blueprint_type,capability_contract,source_refs,status)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'planned')
                ON CONFLICT(tenant_id,key) DO UPDATE SET
                  primary_cluster_id=EXCLUDED.primary_cluster_id,name=EXCLUDED.name,
                  blueprint_type=EXCLUDED.blueprint_type,capability_contract=EXCLUDED.capability_contract,
                  source_refs=EXCLUDED.source_refs,updated_at=now()
                RETURNING id""",
                (
                    tenant_id, primary_cluster_id, payload["key"], payload["name"], payload["blueprint_type"],
                    Json(payload["capability_contract"]), Json(payload["source_refs"]),
                ),
            )
            entity_id = cur.fetchone()
            if entity_id is None:
                raise RuntimeError("blueprint upsert returned no id")
            return {"kind": "blueprint", "entity_key": payload["key"], "entity_id": str(entity_id[0]), "idempotent": False}
        if request.kind == "membership":
            blueprint_id = self._blueprint_id(cur, tenant_id, str(payload["blueprint_key"]))
            cluster_id = self._cluster_id(cur, tenant_id, str(payload["cluster_key"]))
            cur.execute(
                """INSERT INTO topology.cluster_memberships(tenant_id,blueprint_id,cluster_id,axis)
                VALUES(%s,%s,%s,%s)
                ON CONFLICT(tenant_id,blueprint_id,cluster_id) DO UPDATE SET axis=EXCLUDED.axis
                RETURNING id""",
                (tenant_id, blueprint_id, cluster_id, payload["axis"]),
            )
            entity_id = cur.fetchone()
            if entity_id is None:
                raise RuntimeError("membership upsert returned no id")
            return {
                "kind": "membership",
                "entity_key": f"{payload['blueprint_key']}@{payload['cluster_key']}",
                "entity_id": str(entity_id[0]),
                "idempotent": False,
            }
        if request.kind == "edge":
            source_id = self._blueprint_id(cur, tenant_id, str(payload["source_blueprint_key"]))
            target_id = self._blueprint_id(cur, tenant_id, str(payload["target_blueprint_key"]))
            if payload["relation_type"] == "bridge":
                cur.execute(
                    "SELECT 1 FROM topology.domain_bridges "
                    "WHERE tenant_id=%s AND blueprint_id=%s AND status='active' LIMIT 1",
                    (tenant_id, source_id),
                )
                if cur.fetchone() is None:
                    raise ValueError(
                        f"bridge edge requires a registered public bridge on source blueprint: "
                        f"{payload['source_blueprint_key']}"
                    )
            cur.execute(
                """INSERT INTO topology.topology_edges
                (tenant_id,source_blueprint_id,target_blueprint_id,relation_type,condition_ref,weight,status)
                VALUES(%s,%s,%s,%s,%s,%s,'active')
                ON CONFLICT(tenant_id,source_blueprint_id,target_blueprint_id,relation_type) DO UPDATE SET
                  condition_ref=EXCLUDED.condition_ref,weight=EXCLUDED.weight,updated_at=now()
                RETURNING id""",
                (
                    tenant_id, source_id, target_id, payload["relation_type"],
                    payload.get("condition_ref"), float(payload.get("weight", 1)),
                ),
            )
            entity_id = cur.fetchone()
            if entity_id is None:
                raise RuntimeError("edge upsert returned no id")
            return {
                "kind": "edge",
                "entity_key": f"{payload['source_blueprint_key']}->{payload['target_blueprint_key']}",
                "entity_id": str(entity_id[0]),
                "idempotent": False,
            }
        if request.kind == "contract":
            cur.execute(
                """INSERT INTO topology.interface_contracts
                (tenant_id,contract_id,contract_version,kind,format,classification,schema_ref)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(tenant_id,contract_id,contract_version,kind) DO UPDATE SET
                  format=EXCLUDED.format,classification=EXCLUDED.classification,schema_ref=EXCLUDED.schema_ref
                RETURNING id""",
                (
                    tenant_id, payload["contract_id"], payload["contract_version"], payload["kind"],
                    payload["format"], payload["classification"], payload["schema_ref"],
                ),
            )
            entity_id = cur.fetchone()
            if entity_id is None:
                raise RuntimeError("contract upsert returned no id")
            return {
                "kind": "contract",
                "entity_key": payload["contract_id"],
                "entity_id": str(entity_id[0]),
                "idempotent": False,
            }
        if request.kind == "bridge":
            blueprint_id = self._blueprint_id(cur, tenant_id, str(payload["blueprint_key"]))
            ref_kind = str(payload["ref_kind"])
            bridge_ref = str(payload["bridge_ref"])
            status = str(payload.get("status", "active"))
            cur.execute(
                """INSERT INTO topology.domain_bridges
                (tenant_id,blueprint_id,ref_kind,bridge_ref,status)
                VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(tenant_id,blueprint_id,ref_kind) DO UPDATE SET
                  bridge_ref=EXCLUDED.bridge_ref,status=EXCLUDED.status
                RETURNING id""",
                (tenant_id, blueprint_id, ref_kind, bridge_ref, status),
            )
            bridge_id = cur.fetchone()
            if bridge_id is None:
                raise RuntimeError("bridge upsert returned no id")
            return {
                "kind": "bridge",
                "entity_key": f"{payload['blueprint_key']}:{ref_kind}",
                "entity_id": str(bridge_id[0]),
                "idempotent": False,
            }
        raise ValueError(f"unsupported topology kind: {request.kind}")

    # -- release / rollback ---------------------------------------------------

    def release(self, request: TopologyRelease | dict[str, Any]) -> dict[str, Any]:
        """Publish a frozen catalog (checksum-protected) or roll back to a version."""
        if isinstance(request, dict):
            request = TopologyRelease.parse(request)
        if request.action == "publish":
            return self._publish(request)
        if request.action == "rollback":
            return self._rollback(request)
        raise ValueError("release action must be publish or rollback")

    def _publish(self, request: TopologyRelease) -> dict[str, Any]:
        self._gate("topology.release.publish", {"version": request.version or ""})
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                version = request.version or ""
                checksum = request.catalog_checksum or ""
                if len(checksum) != 64 or any(c not in "0123456789abcdefABCDEF" for c in checksum):
                    raise ValueError("catalog_checksum must be a SHA256 hex digest")
                blueprint_snapshots: dict[str, Any] = {}
                for key in request.blueprint_keys:
                    cur.execute(
                        """SELECT capability_contract,name,blueprint_type
                        FROM topology.plugin_blueprints WHERE tenant_id=%s AND key=%s AND status='planned'""",
                        (tenant_id, key),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise ValueError(f"planned blueprint not found: {key}")
                    blueprint_snapshots[key] = {"name": row[1], "blueprint_type": row[2], "capability_contract": row[0]}
                edge_snapshots: dict[str, Any] = {}
                if blueprint_snapshots:
                    cur.execute(
                        """SELECT s.key,tt.key,e.relation_type
                        FROM topology.topology_edges e
                        JOIN topology.plugin_blueprints s ON s.id=e.source_blueprint_id AND s.tenant_id=e.tenant_id
                        JOIN topology.plugin_blueprints tt ON tt.id=e.target_blueprint_id AND tt.tenant_id=e.tenant_id
                        WHERE e.tenant_id=%s AND e.status='active' AND s.key=ANY(%s) AND tt.key=ANY(%s)""",
                        (tenant_id, list(blueprint_snapshots.keys()), list(blueprint_snapshots.keys())),
                    )
                    for source_key, target_key, relation in cur.fetchall():
                        edge_snapshots[f"{source_key}->{target_key}"] = relation
                release_key = f"catalog-{version.replace('.', '-')}"
                cur.execute(
                    """INSERT INTO topology.topology_releases
                    (tenant_id,release_key,version,catalog_checksum,blueprint_snapshots,edge_snapshots,status,created_by,trace_id)
                    VALUES(%s,%s,%s,%s,%s,%s,'published',%s,%s) RETURNING id""",
                    (
                        tenant_id, release_key, version, checksum,
                        Json(blueprint_snapshots), Json(edge_snapshots),
                        request.created_by, request.trace_id,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("release insert returned no id")
                release_id = row[0]
                for blueprint_key in request.blueprint_keys:
                    cur.execute(
                        """INSERT INTO topology.blueprint_versions
                        (tenant_id,blueprint_id,version,contract_snapshot,checksum,status,created_by)
                        SELECT %s,id,%s,capability_contract,%s,'published',%s
                        FROM topology.plugin_blueprints WHERE tenant_id=%s AND key=%s
                        ON CONFLICT(tenant_id,blueprint_id,version) DO NOTHING""",
                        (tenant_id, version, checksum, request.created_by, tenant_id, blueprint_key),
                    )
                response = {
                    "action": "publish", "release_id": str(release_id), "version": version,
                    "release_key": release_key, "catalog_checksum": checksum,
                    "blueprint_count": len(blueprint_snapshots), "edge_count": len(edge_snapshots),
                    "idempotent": False,
                }
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    def _rollback(self, request: TopologyRelease) -> dict[str, Any]:
        self._gate("topology.release.rollback", {"rollback_from_version": request.rollback_from_version or ""})
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                from_version = request.rollback_from_version or ""
                cur.execute(
                    """SELECT id,version,blueprint_snapshots,edge_snapshots FROM topology.topology_releases
                    WHERE tenant_id=%s AND version=%s AND status='published' ORDER BY created_at DESC LIMIT 1""",
                    (tenant_id, from_version),
                )
                target = cur.fetchone()
                if target is None:
                    raise ValueError(f"published release version not found: {from_version}")
                target_id, blueprint_snapshots, edge_snapshots = target[0], target[2] or {}, target[3] or {}
                cur.execute(
                    """UPDATE topology.topology_releases SET status='superseded' WHERE tenant_id=%s AND status='published'""",
                    (tenant_id,),
                )
                release_key = f"catalog-rollback-{from_version.replace('.', '-')}"
                cur.execute(
                    """INSERT INTO topology.topology_releases
                    (tenant_id,release_key,version,catalog_checksum,blueprint_snapshots,edge_snapshots,status,created_by,trace_id)
                    VALUES(%s,%s,%s,%s,%s,%s,'rolled_back',%s,%s) RETURNING id""",
                    (
                        tenant_id, release_key, from_version,
                        hashlib.sha256(json.dumps(blueprint_snapshots, sort_keys=True).encode("utf-8")).hexdigest(),
                        Json(blueprint_snapshots), Json(edge_snapshots),
                        request.created_by, request.trace_id,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("rollback insert returned no id")
                response = {
                    "action": "rollback", "release_id": str(row[0]), "version": from_version,
                    "rolled_back_from": str(target_id), "idempotent": False,
                }
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    # -- recycle / restore ----------------------------------------------------

    def recycle(self, request: TopologyRecycle | dict[str, Any]) -> dict[str, Any]:
        """Soft-delete an entity into the recycle bin, or restore a snapshot."""
        if isinstance(request, dict):
            request = TopologyRecycle.parse(request)
        if request.recycle_type == "recycled":
            return self._recycle(request)
        return self._restore(request)

    def _recycle(self, request: TopologyRecycle) -> dict[str, Any]:
        self._gate("topology.recycle.write", {"entity_kind": request.entity_kind, "entity_key": request.entity_key})
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                snapshot = self._fetch_entity(cur, tenant_id, request.entity_kind, request.entity_key)
                cur.execute(
                    """INSERT INTO topology.topology_recycle_bin
                    (tenant_id,entity_kind,entity_id,snapshot,reason,recycle_type,created_by)
                    VALUES(%s,%s,%s,%s,%s,'recycled',%s) RETURNING id""",
                    (tenant_id, request.entity_kind, snapshot["entity_id"], Json(snapshot["payload"]), request.reason, request.created_by),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("recycle insert returned no id")
                self._deactivate_entity(cur, tenant_id, request.entity_kind, request.entity_key)
                response = {
                    "recycle_type": "recycled", "recycle_id": str(row[0]),
                    "entity_kind": request.entity_kind, "entity_key": request.entity_key,
                    "snapshot_sha256": hashlib.sha256(
                        json.dumps(snapshot["payload"], sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest(),
                    "idempotent": False,
                }
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    def _restore(self, request: TopologyRecycle) -> dict[str, Any]:
        self._gate("topology.recycle.restore", {"entity_kind": request.entity_kind, "entity_key": request.entity_key})
        if request.restored_from_recycle_id is None:
            raise ValueError("restore requires restored_from_recycle_id")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                cur.execute(
                    """SELECT entity_kind,entity_id,snapshot FROM topology.topology_recycle_bin
                    WHERE tenant_id=%s AND id=%s AND recycle_type='recycled'""",
                    (tenant_id, request.restored_from_recycle_id),
                )
                recycled = cur.fetchone()
                if recycled is None:
                    raise ValueError("recycled record not found")
                self._restore_entity(cur, tenant_id, request.entity_kind, request.entity_key, recycled[2])
                cur.execute(
                    """INSERT INTO topology.topology_recycle_bin
                    (tenant_id,entity_kind,entity_id,snapshot,reason,recycle_type,restored_from,created_by)
                    VALUES(%s,%s,%s,%s,%s,'restored',%s,%s) RETURNING id""",
                    (tenant_id, request.entity_kind, recycled[1], Json(recycled[2]), request.reason, request.restored_from_recycle_id, request.created_by),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("restore insert returned no id")
                response = {
                    "recycle_type": "restored", "restore_id": str(row[0]),
                    "entity_kind": request.entity_kind, "entity_key": request.entity_key,
                    "restored_from_recycle_id": str(request.restored_from_recycle_id),
                    "idempotent": False,
                }
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    # -- plan ---------------------------------------------------------------

    def plan(self, request: TopologyPlan | dict[str, Any]) -> dict[str, Any]:
        """Build and persist a plan_only routing plan under the caller's budget."""
        if isinstance(request, dict):
            request = TopologyPlan.parse(request)
        self._gate("topology.plan.read", {"mode": request.mode, "intent": request.intent[:80]}, risk_class="read_only")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                blueprints, memberships = self._load_catalog(cur, tenant_id)
                edges = self._load_edges(cur, tenant_id)
                locked_release = self._locked_release(cur, tenant_id, request.release_lock)
                if locked_release is not None:
                    # CW0: a release-locked plan must be routed inside the frozen
                    # release snapshot, never against the live catalog.  Otherwise
                    # the planner could pick a live blueprint the release snapshot
                    # does not contain, and materialization would fail.
                    blueprints, edges = self._load_release_catalog(cur, tenant_id, locked_release["release_id"])
                routing_plan = self.planner.plan(
                    capability_requirements=list(request.capability_requirements),
                    budget=request.budget,
                    blueprints=blueprints,
                    cluster_memberships=memberships,
                    edges=edges,
                    locked_release=locked_release,
                    planner_version=request.planner_version or "1.0.0",
                )
                release_id = self._release_id_for_plan(cur, tenant_id, request.release_lock)
                cur.execute(
                    """INSERT INTO topology.routing_plans
                    (tenant_id,plan_key,mission_key,mode,plan_json,topology_release_id,planner_version,budget,checksum,trace_id)
                    VALUES(%s,%s,%s,'plan_only',%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,plan_key) DO NOTHING RETURNING id""",
                    (
                        tenant_id, routing_plan.plan_key, routing_plan.mission_key,
                        Json(self._plan_json(routing_plan)), release_id, routing_plan.planner_version,
                        Json(request.budget), routing_plan.checksum, str(request.trace_id or uuid4()),
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    # Idempotent replay: plan_key is a deterministic hash of the
                    # capability requirements, so the same logical plan is
                    # already persisted. Return the existing plan instead of
                    # surfacing a UniqueViolation - required for order-independent
                    # suites and unattended 24x7 replays.
                    cur.execute(
                        """SELECT id, plan_json, topology_release_id FROM topology.routing_plans
                        WHERE tenant_id=%s AND plan_key=%s""",
                        (tenant_id, routing_plan.plan_key),
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        raise RuntimeError("routing plan insert returned no id")
                    plan_json = existing[1]
                    response = {
                        "plan_key": routing_plan.plan_key,
                        "mode": plan_json.get("mode", "plan_only"),
                        "checksum": plan_json.get("checksum") or routing_plan.checksum,
                        "release_locked": bool(plan_json.get("release_locked")),
                        "node_count": len(plan_json.get("nodes", [])),
                        "edge_count": len(plan_json.get("edges", [])),
                        "plan_id": str(existing[0]),
                        "idempotent": True,
                        "release_id": str(existing[2]) if existing[2] is not None else None,
                    }
                    self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                    return response
                plan_id = row[0]
                for node in routing_plan.nodes:
                    blueprint_id = self._blueprint_id(cur, tenant_id, node.blueprint_key)
                    cur.execute(
                        """INSERT INTO topology.routing_plan_nodes(tenant_id,plan_id,blueprint_id,slot_key,alternatives)
                        VALUES(%s,%s,%s,%s,%s)""",
                        (tenant_id, plan_id, blueprint_id, node.slot_key, Json(list(node.alternatives))),
                    )
                for edge in routing_plan.edges:
                    cur.execute(
                        """INSERT INTO topology.routing_plan_edges(tenant_id,plan_id,source_node,target_node,relation_type)
                        VALUES(%s,%s,%s,%s,%s)""",
                        (tenant_id, plan_id, edge.source_node, edge.target_node, edge.relation_type),
                    )
                response = {
                    "plan_key": routing_plan.plan_key,
                    "mode": routing_plan.mode,
                    "checksum": routing_plan.checksum,
                    "release_locked": bool(locked_release),
                    "node_count": len(routing_plan.nodes),
                    "edge_count": len(routing_plan.edges),
                    "plan_id": str(plan_id),
                    "idempotent": False,
                }
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    # -- queries (read-only, no policy write) --------------------------------

    def list_clusters(self, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                self._tenant(cur)
                cur.execute(
                    "SELECT key,name,cluster_kind,status FROM topology.plugin_clusters ORDER BY key LIMIT %s",
                    (limit,),
                )
                return [
                    {"key": str(row[0]), "name": row[1], "axis": row[2], "status": row[3]}
                    for row in cur.fetchall()
                ]

    def list_blueprints(self, limit: int = 200) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                self._tenant(cur)
                cur.execute(
                    """SELECT key,name,blueprint_type,capability_contract->>'capability',status
                    FROM topology.plugin_blueprints ORDER BY key LIMIT %s""",
                    (limit,),
                )
                return [
                    {"key": str(row[0]), "name": row[1], "blueprint_type": row[2], "capability": row[3], "status": row[4]}
                    for row in cur.fetchall()
                ]

    def list_releases(self, limit: int = 50) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                self._tenant(cur)
                cur.execute(
                    "SELECT release_key,version,status,catalog_checksum FROM topology.topology_releases ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
                return [
                    {"release_key": str(row[0]), "version": row[1], "status": row[2], "catalog_checksum": row[3]}
                    for row in cur.fetchall()
                ]

    def list_plans(self, limit: int = 50) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                self._tenant(cur)
                cur.execute(
                    """SELECT plan_key,mission_key,mode,checksum,planner_version,created_at
                    FROM topology.routing_plans ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                )
                return [
                    {
                        "plan_key": str(row[0]),
                        "mission_key": str(row[1]),
                        "mode": row[2],
                        "checksum": row[3],
                        "planner_version": row[4],
                        "created_at": row[5].isoformat(),
                    }
                    for row in cur.fetchall()
                ]

    def list_bridges(self, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT b.key,d.ref_kind,d.bridge_ref,d.status
                    FROM topology.domain_bridges d
                    JOIN topology.plugin_blueprints b ON b.id=d.blueprint_id AND b.tenant_id=d.tenant_id
                    WHERE d.tenant_id=%s ORDER BY b.key,d.ref_kind LIMIT %s""",
                    (tenant_id, limit),
                )
                return [
                    {"blueprint_key": str(row[0]), "ref_kind": row[1], "bridge_ref": row[2], "status": row[3]}
                    for row in cur.fetchall()
                ]

    # -- invocation-chain materialization (M3) --------------------------------

    def materialize_chain(self, request: ChainMaterialize | dict[str, Any], *, trace_id: UUID | None = None) -> dict[str, Any]:
        """Materialize one deterministic invocation chain from a plan_only plan.

        The chain is a pure projection of the locked plan: same plan + same
        release lock yields the same chain_key and checksum.  Nothing here
        invokes a plugin; intents carry only Policy decisions (status
        projection), and every write is idempotency-keyed.
        """
        if isinstance(request, dict):
            request = ChainMaterialize.parse(request)
        self._gate("topology.chain.write", {"plan_key": request.plan_key}, risk_class="low")
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                plan = self._load_plan(cur, tenant_id, request.plan_key)
                existing = self._existing_chain_summary(cur, tenant_id, request.plan_key)
                if existing is not None:
                    return existing
                chain = self.resolver.materialize(
                    plan_key=request.plan_key,
                    plan_nodes=plan["nodes"],
                    plan_edges=plan["edges"],
                    blueprints=plan["blueprints"],
                    bridges=plan["bridges"],
                    planner_version=plan["planner_version"],
                    release_locked=plan["release_locked"],
                    trace_id=trace,
                )
                intents = self.generator.intents(
                    chain, plan["blueprints"], trace_id=trace, decisions=self._intent_decisions(chain)
                )
                cur.execute(
                    """INSERT INTO topology.invocation_chains
                    (tenant_id,chain_key,plan_id,mode,chain_json,chain_checksum,planner_version,reason,trace_id)
                    VALUES(%s,%s,%s,'plan_only',%s,%s,%s,%s,%s) RETURNING id""",
                    (
                        tenant_id, chain.chain_key, plan["plan_id"], Json(chain.to_json()),
                        chain.checksum, chain.planner_version, request.reason, trace,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("invocation chain insert returned no id")
                chain_id = row[0]
                for node in chain.chain_order:
                    blueprint_id = self._blueprint_id(cur, tenant_id, node.blueprint_key)
                    contract = plan["blueprints"].get(node.blueprint_key) or {}
                    cur.execute(
                        """INSERT INTO topology.invocation_chain_nodes
                        (tenant_id,chain_id,plan_node_slot_key,blueprint_id,ordinal,role,input_bindings,expected_output)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            tenant_id, chain_id, node.slot_key, blueprint_id, node.ordinal, node.role,
                            Json([str(item) for item in contract.get("inputs") or ()]),
                            Json([str(item) for item in contract.get("outputs") or ()]),
                        ),
                    )
                for intent in intents:
                    cur.execute(
                        """INSERT INTO topology.invocation_intents
                        (tenant_id,chain_id,plan_node_slot_key,role,capability,intent_json,policy_decision,approval_ref,status,idempotency_key,trace_id)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            tenant_id, chain_id, intent.slot_key, intent.role, intent.capability,
                            Json(intent.to_json()), intent.policy_decision, intent.approval_ref or None,
                            intent.status, request.idempotency_key, trace,
                        ),
                    )
                response = {
                    "chain_key": chain.chain_key,
                    "plan_key": chain.plan_key,
                    "mode": chain.mode,
                    "planner_version": chain.planner_version,
                    "checksum": chain.checksum,
                    "release_locked": chain.release_locked,
                    "node_count": len(chain.chain_order),
                    "binding_count": len(chain.port_bindings),
                    "intent_count": len(intents),
                    "chain_id": str(chain_id),
                    "idempotent": False,
                }
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    def list_chains(self, limit: int = 50) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                self._tenant(cur)
                cur.execute(
                    """SELECT c.chain_key,p.plan_key,c.mode,c.chain_checksum,c.planner_version,c.created_at
                    FROM topology.invocation_chains c
                    JOIN topology.routing_plans p ON p.id=c.plan_id AND p.tenant_id=c.tenant_id
                    ORDER BY c.created_at DESC LIMIT %s""",
                    (limit,),
                )
                return [
                    {
                        "chain_key": str(row[0]),
                        "plan_key": str(row[1]),
                        "mode": row[2],
                        "checksum": row[3],
                        "planner_version": row[4],
                        "created_at": row[5].isoformat(),
                    }
                    for row in cur.fetchall()
                ]

    def list_intents(self, chain_key: str, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT i.plan_node_slot_key,i.role,i.capability,i.intent_json,i.policy_decision,i.status,i.approval_ref
                    FROM topology.invocation_intents i
                    JOIN topology.invocation_chains c ON c.id=i.chain_id AND c.tenant_id=i.tenant_id
                    WHERE i.tenant_id=%s AND c.chain_key=%s
                    ORDER BY i.plan_node_slot_key LIMIT %s""",
                    (tenant_id, chain_key, limit),
                )
                items: list[dict[str, Any]] = []
                for slot_key, role, capability, intent_json, decision, status, approval_ref in cur.fetchall():
                    projection = intent_json if isinstance(intent_json, dict) else {}
                    # DB columns are the current truth and must win over the
                    # materialize-time snapshot carried inside intent_json.
                    items.append(
                        {
                            **projection,
                            "slot_key": str(slot_key),
                            "role": str(role),
                            "capability": str(capability),
                            "policy_decision": str(decision),
                            "status": str(status),
                            "approval_ref": str(approval_ref) if approval_ref else None,
                        }
                    )
                return items

    # -- M4 approval workflow & shadow execution ----------------------------

    def approve_intent(
        self, request: ApprovalRequest | dict[str, Any], *, trace_id: UUID | None = None
    ) -> dict[str, Any]:
        """Approve or reject one requires_approval intent (human gate, idempotent)."""
        if isinstance(request, dict):
            request = ApprovalRequest.parse(request)
        self._gate("topology.chain.approve", {"chain_key": request.chain_key, "slot_key": request.slot_key}, risk_class="low")
        trace = str(trace_id or uuid4())
        approver = str(request.created_by) if request.created_by else "desktop-user"
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                cur.execute(
                    """SELECT i.id, i.policy_decision, i.status, c.id
                    FROM topology.invocation_intents i
                    JOIN topology.invocation_chains c ON c.id = i.chain_id AND c.tenant_id = i.tenant_id
                    WHERE i.tenant_id = %s AND c.chain_key = %s AND i.plan_node_slot_key = %s""",
                    (tenant_id, request.chain_key, request.slot_key),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"intent not found: {request.chain_key}/{request.slot_key}")
                intent_id, policy_decision, status, chain_id = row
                new_status = transition_status(str(policy_decision), str(status), request.decision)
                approval_ref = approval_ref_for(
                    chain_key=request.chain_key, slot_key=request.slot_key, decision=request.decision, approver=approver
                )
                if new_status == "denied":
                    set_sql = "SET policy_decision='denied', status='denied', approval_ref=%s"
                    params: tuple[Any, ...] = (approval_ref, intent_id)
                else:
                    set_sql = "SET status=%s, approval_ref=%s"
                    params = (new_status, approval_ref, intent_id)
                cur.execute(
                    f"""UPDATE topology.invocation_intents {set_sql}
                    WHERE id=%s AND status='materialized' AND policy_decision='requires_approval'
                    RETURNING id""",
                    params,
                )
                if cur.fetchone() is None:
                    raise ValueError("intent was concurrently finalized; approval rejected")
                cur.execute(
                    """INSERT INTO topology.invocation_approvals
                    (tenant_id,chain_id,intent_id,slot_key,decision,approver,reason,idempotency_key,trace_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id, chain_id, slot_key, decision) DO NOTHING""",
                    (
                        tenant_id, chain_id, intent_id, request.slot_key, request.decision, approver,
                        request.reason, request.idempotency_key, trace,
                    ),
                )
                response = {
                    "chain_key": request.chain_key,
                    "slot_key": request.slot_key,
                    "decision": request.decision,
                    "intent_status": new_status,
                    "approval_ref": approval_ref,
                    "approver": approver,
                    "idempotent": False,
                }
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    def list_approvals(self, chain_key: str, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT a.slot_key,a.decision,a.approver,a.reason,a.created_at
                    FROM topology.invocation_approvals a
                    JOIN topology.invocation_chains c ON c.id=a.chain_id AND c.tenant_id=a.tenant_id
                    WHERE a.tenant_id=%s AND c.chain_key=%s
                    ORDER BY a.created_at DESC LIMIT %s""",
                    (tenant_id, chain_key, limit),
                )
                return [
                    {
                        "slot_key": str(row[0]),
                        "decision": str(row[1]),
                        "approver": str(row[2]),
                        "reason": str(row[3]),
                        "created_at": row[4].isoformat(),
                    }
                    for row in cur.fetchall()
                ]

    def execute_chain(
        self, request: ChainExecute | dict[str, Any], *, trace_id: UUID | None = None
    ) -> dict[str, Any]:
        """Simulated whole-chain execution: fail-closed gate, deterministic ledger."""
        if isinstance(request, dict):
            request = ChainExecute.parse(request)
        self._gate("topology.chain.execute", {"chain_key": request.chain_key}, risk_class="medium")
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                cur.execute(
                    """SELECT id,chain_checksum,planner_version,chain_json
                    FROM topology.invocation_chains WHERE tenant_id=%s AND chain_key=%s""",
                    (tenant_id, request.chain_key),
                )
                chain_row = cur.fetchone()
                if chain_row is None:
                    raise ValueError(f"chain not found: {request.chain_key}")
                chain_id, chain_checksum, planner_version, chain_json = chain_row
                payload = chain_json if isinstance(chain_json, dict) else {}
                cur.execute(
                    """SELECT plan_node_slot_key,policy_decision,status
                    FROM topology.invocation_intents WHERE tenant_id=%s AND chain_id=%s
                    ORDER BY plan_node_slot_key""",
                    (tenant_id, chain_id),
                )
                intents = [
                    {"slot_key": str(r[0]), "policy_decision": str(r[1]), "status": str(r[2])}
                    for r in cur.fetchall()
                ]
                blockers = gate_blockers(intents)
                if blockers:
                    raise PermissionError(f"chain execution fail-closed: {blockers}")
                cur.execute(
                    """SELECT plan_node_slot_key, ordinal, expected_output
                    FROM topology.invocation_chain_nodes WHERE tenant_id=%s AND chain_id=%s
                    ORDER BY ordinal""",
                    (tenant_id, chain_id),
                )
                nodes = cur.fetchall()
                if not nodes:
                    raise ValueError("chain has no nodes; cannot execute")
                cur.execute(
                    """SELECT COUNT(*) FROM topology.execution_ledger
                    WHERE tenant_id=%s AND chain_id=%s AND mode=%s""",
                    (tenant_id, chain_id, EXECUTION_MODE),
                )
                row = cur.fetchone()
                if row is not None and row[0] > 0:
                    return self._existing_execution_summary(cur, tenant_id, chain_id, chain_checksum)
                input_by_consumer: dict[str, list[str]] = {}
                for binding in payload.get("port_bindings") or []:
                    consumer = str(binding.get("consumer_slot") or "")
                    ref = str(binding.get("contract_ref") or "")
                    if consumer:
                        input_by_consumer.setdefault(consumer, []).append(ref)
                entries: list[dict[str, Any]] = []
                started = datetime.now(timezone.utc).isoformat()
                for slot_key, ordinal, expected_output in nodes:
                    slot = str(slot_key)
                    input_refs = sorted(set(input_by_consumer.get(slot, [])))
                    out_raw = expected_output if isinstance(expected_output, (list, tuple)) else []
                    output_refs = sorted({str(item) for item in out_raw})
                    output_contract = output_refs[0] if output_refs else ""
                    checksum = node_output_checksum(
                        slot_key=slot, ordinal=int(ordinal), version=str(planner_version or "1.0.0"),
                        input_refs=input_refs, output_refs=output_refs,
                    )
                    cur.execute(
                        """INSERT INTO topology.execution_ledger
                        (tenant_id,chain_id,plan_node_slot_key,ordinal,mode,input_refs,output_refs,
                         output_contract,output_checksum,status,policy_ref,idempotency_key,trace_id,
                         started_at,finished_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'succeeded',%s,%s,%s,%s,%s)""",
                        (
                            tenant_id, chain_id, slot, int(ordinal), EXECUTION_MODE,
                            Json(input_refs), Json(output_refs), output_contract, checksum,
                            "policy_allowed", request.idempotency_key, trace, started, started,
                        ),
                    )
                    entries.append(
                        {
                            "execution_id": "exec-" + hashlib.sha256(f"{chain_id}:{slot}:0".encode("utf-8")).hexdigest()[:16],
                            "chain_key": request.chain_key,
                            "slot_key": slot,
                            "ordinal": int(ordinal),
                            "mode": EXECUTION_MODE,
                            "input_refs": input_refs,
                            "output_refs": output_refs,
                            "output_contract": output_contract,
                            "output_checksum": checksum,
                            "status": "succeeded",
                            "policy_ref": "policy_allowed",
                            "trace_id": trace,
                            "started_at": started,
                            "finished_at": started,
                        }
                    )
                response = {
                    "chain_key": request.chain_key,
                    "mode": EXECUTION_MODE,
                    "status": "succeeded",
                    "planner_version": str(planner_version or "1.0.0"),
                    "chain_checksum": str(chain_checksum),
                    "node_count": len(entries),
                    "entries": entries,
                    "idempotent": False,
                }
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    def execute_chain_isolated(
        self,
        request: ChainExecute | dict[str, Any],
        *,
        trace_id: UUID | None = None,
        input_sources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """M5 isolated whole-chain execution: ISO gate -> per-node child -> ledger.

        ``IsolatedChainExecutor.iso_gate`` runs before any subprocess may start
        and re-validates every intent plus both policies
        (``topology.chain.execute`` and ``topology.chain.execute.isolated``).
        A failed node writes a ``failed`` ledger row and stops every later node;
        a real run is never silently downgraded to ``simulated``.
        ``input_sources`` lets a governed caller lock the first node's input
        artifact(s); the API and GUI never pass it (fail-closed to DB/bridge
        materialization or a ``failed`` node).
        """
        if isinstance(request, dict):
            request = ChainExecute.parse(request)
        if request.mode != ISOLATED_MODE:
            raise ValueError("isolated execution requires mode=isolated")
        if not (request.reason or "").strip():
            raise ValueError("isolated execution requires a reason")
        for capability in ("topology.chain.execute", "topology.chain.execute.isolated"):
            self._gate(capability, {"chain_key": request.chain_key}, risk_class="medium")
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                # Session-level RLS (not transaction-local): CW0 short-transaction
                # commits would otherwise wipe the tenant context before the
                # executor writes ledger rows.
                cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
                cur.fetchone()
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                cur.execute(
                    """SELECT id,chain_checksum,planner_version,chain_json
                    FROM topology.invocation_chains WHERE tenant_id=%s AND chain_key=%s""",
                    (tenant_id, request.chain_key),
                )
                chain_row = cur.fetchone()
                if chain_row is None:
                    raise ValueError(f"chain not found: {request.chain_key}")
                chain_id, chain_checksum, planner_version, chain_json = chain_row
                payload = chain_json if isinstance(chain_json, dict) else {}
                cur.execute(
                    """SELECT plan_node_slot_key,capability,intent_json,policy_decision,status
                    FROM topology.invocation_intents WHERE tenant_id=%s AND chain_id=%s
                    ORDER BY plan_node_slot_key""",
                    (tenant_id, chain_id),
                )
                intents: list[dict[str, Any]] = []
                for slot_key, capability, intent_json, decision, status in cur.fetchall():
                    projection = intent_json if isinstance(intent_json, dict) else {}
                    # DB columns are current truth and win over the materialize-time snapshot.
                    intents.append(
                        {
                            **projection,
                            "slot_key": str(slot_key),
                            "capability": str(capability),
                            "policy_decision": str(decision),
                            "status": str(status),
                        }
                    )
                cur.execute(
                    """SELECT plan_node_slot_key, ordinal, expected_output
                    FROM topology.invocation_chain_nodes WHERE tenant_id=%s AND chain_id=%s
                    ORDER BY ordinal""",
                    (tenant_id, chain_id),
                )
                nodes = cur.fetchall()
                if not nodes:
                    raise ValueError("chain has no nodes; cannot execute")
                cur.execute(
                    """SELECT COUNT(*) FROM topology.execution_ledger
                    WHERE tenant_id=%s AND chain_id=%s AND mode=%s""",
                    (tenant_id, chain_id, ISOLATED_MODE),
                )
                count_row = cur.fetchone()
                if count_row is not None and int(count_row[0]) > 0:
                    return {
                        "chain_key": request.chain_key,
                        "mode": ISOLATED_MODE,
                        "status": "already_executed",
                        "planner_version": str(planner_version or "1.0.0"),
                        "chain_checksum": str(chain_checksum),
                        "node_count": 0,
                        "entries": [],
                        "idempotent": False,
                    }
                port_bindings = [
                    dict(binding) for binding in (payload.get("port_bindings") or []) if isinstance(binding, dict)
                ]
                staging_root = Path(__file__).resolve().parents[2] / ".data" / "isolated"
                executor = IsolatedChainExecutor(
                    chain_key=request.chain_key,
                    chain_id=UUID(str(chain_id)),
                    tenant_id=tenant_id,
                    trace_id=trace,
                    planner_version=str(planner_version or "1.0.0"),
                    chain_checksum=str(chain_checksum),
                    policy=self.policy,
                    nodes=[
                        {
                            "slot_key": str(slot_key),
                            "ordinal": int(ordinal),
                            "expected_output": [
                                str(item) for item in (expected_output if isinstance(expected_output, (list, tuple)) else [])
                            ],
                        }
                        for slot_key, ordinal, expected_output in nodes
                    ],
                    intents=intents,
                    port_bindings=port_bindings,
                    runtime=IsolatedPluginRuntime(allowed_roots=(staging_root,)),
                    staging_root=staging_root,
                    idempotency_key=request.idempotency_key,
                    input_sources=dict(input_sources or {}),
                )
                response = executor.execute(connection, cur)
                response["planner_version"] = str(planner_version or "1.0.0")
                response["chain_checksum"] = str(chain_checksum)
                self._record_idempotent(cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response)
                return response

    def list_executions(self, chain_key: str, limit: int = 200) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                cur.execute(
                    """SELECT e.id,e.plan_node_slot_key,e.ordinal,e.mode,e.input_refs,e.output_refs,
                            e.output_contract,e.output_checksum,e.status,e.policy_ref,e.started_at,e.finished_at,
                            e.plugin_id,e.plugin_version,e.runtime_code_sha256,e.input_sha256,e.output_artifact_refs
                    FROM topology.execution_ledger e
                    JOIN topology.invocation_chains c ON c.id=e.chain_id AND c.tenant_id=e.tenant_id
                    WHERE e.tenant_id=%s AND c.chain_key=%s
                    ORDER BY e.started_at DESC, e.ordinal LIMIT %s""",
                    (tenant_id, chain_key, limit),
                )
                return [
                    {
                        "execution_id": "exec-" + hashlib.sha256(str(row[0]).encode("utf-8")).hexdigest()[:16],
                        "chain_key": chain_key,
                        "slot_key": str(row[1]),
                        "ordinal": int(row[2]),
                        "mode": str(row[3]),
                        "input_refs": row[4] if isinstance(row[4], (list, tuple)) else [],
                        "output_refs": row[5] if isinstance(row[5], (list, tuple)) else [],
                        "output_contract": str(row[6] or ""),
                        "output_checksum": str(row[7]),
                        "status": str(row[8]),
                        "policy_ref": str(row[9] or ""),
                        "started_at": row[10].isoformat(),
                        "finished_at": row[11].isoformat() if row[11] else None,
                        "plugin_id": str(row[12] or ""),
                        "plugin_version": str(row[13] or ""),
                        "runtime_code_sha256": str(row[14] or ""),
                        "input_sha256": str(row[15] or ""),
                        "output_artifact_refs": row[16] if isinstance(row[16], (list, tuple)) else [],
                    }
                    for row in cur.fetchall()
                ]

    def start_run(
        self,
        request: ChainRunRequest | dict[str, Any],
        *,
        trace_id: UUID | None = None,
        input_sources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """M6 chain-level run: preflight -> running -> isolated execute -> success|failed.

        Fail-closed preflight: the chain must be release-locked, every intent
        must be past its approval gate, and the tenant policy must freshly
        grant both ``topology.chain.execute`` and
        ``topology.chain.execute.isolated``.  The run is exclusive per chain
        (a second ``running`` run is a 409) and the ledger rows are grouped by
        ``run_id``, so the same chain can be re-drilled with a fresh key.
        """
        if isinstance(request, dict):
            request = ChainRunRequest.parse(request)
        if request.mode != ISOLATED_MODE:
            raise ValueError("isolated run requires mode=isolated")
        if not (request.reason or "").strip():
            raise ValueError("isolated run requires a reason")
        for capability in ("topology.chain.execute", "topology.chain.execute.isolated"):
            self._gate(capability, {"chain_key": request.chain_key}, risk_class="medium")
        trace = str(trace_id or uuid4())
        connection = psycopg2.connect(self.database_url)
        try:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                # Session-level RLS (not transaction-local): CW0 short-transaction
                # commits would otherwise wipe the tenant context before the
                # executor writes ledger rows.
                cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
                cur.fetchone()
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                # DB-level replay: a crashed run row is still the truth for
                # this idempotency key (UNIQUE(tenant_id, idempotency_key)).
                cur.execute(
                    """SELECT id FROM topology.execution_runs
                    WHERE tenant_id=%s AND idempotency_key=%s""",
                    (tenant_id, request.idempotency_key),
                )
                existing = cur.fetchone()
                if existing is not None:
                    return {
                        **run_projection(cur, existing[0], tenant_id),
                        "entries": run_entries(cur, tenant_id, existing[0]),
                        "idempotent": True,
                    }
                context = load_chain_context(cur, tenant_id, request.chain_key)
                preflight(context)
                run_id = begin_run(
                    cur,
                    tenant_id=tenant_id,
                    context=context,
                    reason=request.reason,
                    idempotency_key=request.idempotency_key,
                    trace_id=trace,
                )
                # CW0 short transaction (1/3): the running run row is committed
                # before any child starts, so a crash mid-chain never loses the
                # run itself (begin_run also holds the same-chain concurrency lock).
                connection.commit()
                staging_root = Path(__file__).resolve().parents[2] / ".data" / "isolated"
                executor = IsolatedChainExecutor(
                    chain_key=context.chain_key,
                    chain_id=context.chain_id,
                    tenant_id=tenant_id,
                    trace_id=trace,
                    planner_version=context.planner_version,
                    chain_checksum=context.chain_checksum,
                    policy=self.policy,
                    nodes=context.nodes,
                    intents=context.intents,
                    port_bindings=context.port_bindings,
                    runtime=IsolatedPluginRuntime(allowed_roots=(staging_root,)),
                    staging_root=staging_root,
                    idempotency_key=request.idempotency_key,
                    input_sources=dict(input_sources or {}),
                    run_id=run_id,
                )
                try:
                    outcome = executor.execute(connection, cur)
                except Exception:
                    # Fail-closed: whatever was already written stays as the
                    # evidence; the run itself is recorded as failed.  CW0: the
                    # per-node commits above mean the evidence is durable, and
                    # the finalize below is committed on its own short txn.
                    #
                    # Deliberately catches *Exception*, not a three-type tuple.
                    # It used to list (ValueError, PermissionError, OSError), so
                    # a PluginRuntimeError -- raised when a plugin fails to load
                    # or its binding no longer verifies -- sailed straight past
                    # this handler.  `finalize_run` then never ran and the row
                    # sat at status='running' forever, which the partial unique
                    # index `topology_execution_runs_one_running` turned into a
                    # cascade: every later run of the same chain failed with
                    # "another isolated run is already running for this chain".
                    # The exception is re-raised below, so nothing is masked --
                    # the only difference is that the run is left finalised.
                    finished = run_entries(cur, tenant_id, run_id)
                    node_succeeded = sum(1 for item in finished if item.get("status") == "succeeded")
                    finalize_run(
                        cur,
                        run_id=run_id,
                        tenant_id=tenant_id,
                        status="failed",
                        node_succeeded=node_succeeded,
                        node_failed=context.node_total - node_succeeded,
                    )
                    connection.commit()
                    # Experience accumulation (design 5.1): separate short
                    # transaction after commit; never blocks/fails the business run.
                    from packages.experience.projector import project_run_best_effort

                    project_run_best_effort(
                        self.database_url, tenant_id=tenant_id, run_id=run_id, trace_id=trace
                    )
                    raise
                node_succeeded = sum(1 for item in outcome["entries"] if item.get("status") == "succeeded")
                node_failed = max(0, context.node_total - node_succeeded)
                finalize_run(
                    cur,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    status="success" if outcome["status"] == "succeeded" else "failed",
                    node_succeeded=node_succeeded,
                    node_failed=node_failed,
                )
                projection = run_projection(cur, run_id, tenant_id)
                entries = run_entries(cur, tenant_id, run_id)
                response = {**projection, "entries": entries, "idempotent": False}
                self._record_idempotent(
                    cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response
                )
                connection.commit()
                # Experience accumulation (design 5.1): best-effort projection in
                # a separate short transaction after the business run committed.
                from packages.experience.projector import project_run_best_effort

                project_run_best_effort(
                    self.database_url, tenant_id=tenant_id, run_id=run_id, trace_id=trace
                )
                return response
        finally:
            connection.close()

    def start_plan_run(
        self,
        plan: Any,
        *,
        trace_id: UUID | None = None,
        seed_inputs: dict[Any, Any] | None = None,
        worker_id: str | None = None,
        max_output_bytes: int | None = None,
        staging_root: Path | None = None,
    ) -> dict[str, Any]:
        """CW3 unified DAG execution entry: compile -> gate -> short-transaction run.

        Every node commits its attempt + outbox event in its own short
        transaction (AttemptStore), so a crash never loses committed node
        evidence and a stale worker's late write is fenced.  Returns the run
        projection with every attempt and every data edge traceable.
        """
        if isinstance(plan, dict):
            plan = compile_plan(**plan)
        for capability in ("topology.chain.execute", "topology.chain.execute.isolated"):
            self._gate(capability, {"plan_key": plan.plan_key}, risk_class="medium")
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
        run_id = str(uuid4())
        staging_root = staging_root or (Path(__file__).resolve().parents[2] / ".data" / "isolated")
        run_trace_id = str(trace_id or uuid4())
        store = AttemptStore(self.database_url, worker_id=worker_id)
        executor = PortBoundExecutor(
            plan=plan,
            runtime=IsolatedPluginRuntime(allowed_roots=(staging_root,)),
            policy=self.policy,
            tenant_id=tenant_id,
            trace_id=run_trace_id,
            staging_root=staging_root,
            idempotency_key=f"plan:{plan.plan_key}:{run_id}",
            seed_inputs=seed_inputs,
            persistence=store,
            run_id=run_id,
            worker_id=worker_id,
            max_output_bytes=max_output_bytes,
        )
        # Experience accumulation (design 5.1): the M6 chain surface has projected
        # finished runs since 0055, but this DAG surface never did — so a run that
        # went through here accumulated nothing, and the knowledge graph's
        # "which projects has this plugin been proven in?" stayed empty for it.
        # Best-effort, in its own short transaction, on **both** the success and
        # the exception path; a projection failure can never fail or roll back a
        # real plugin run (``project_run_best_effort`` swallows and logs).
        from packages.experience.projector import project_run_best_effort

        try:
            outcome = executor.execute()
        except Exception:
            project_run_best_effort(
                self.database_url, tenant_id=tenant_id, run_id=run_id, trace_id=run_trace_id
            )
            raise
        project_run_best_effort(
            self.database_url, tenant_id=tenant_id, run_id=run_id, trace_id=run_trace_id
        )
        return {
            "run_id": run_id,
            "tenant_id": str(tenant_id),
            "plan_key": plan.plan_key,
            "execution_hash": plan.execution_hash,
            "status": outcome["status"],
            "trace_id": outcome["trace_id"],
            "attempts": store.query_attempts(tenant_id=tenant_id, run_id=run_id),
            "edges": store.query_edges(tenant_id=tenant_id, run_id=run_id),
        }

    def list_plan_runs(self, tenant_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
        """CW4 runs feed: aggregate attempt state per run for one tenant."""
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.fetchone()
            cur.execute(
                """SELECT run_id, plan_key, execution_hash,
                          MIN(created_at) AS created_at,
                          MAX(trace_id) AS trace_id,
                          COUNT(*) AS attempt_total,
                          COUNT(*) FILTER (WHERE status='succeeded') AS attempt_succeeded,
                          COUNT(*) FILTER (WHERE status='failed') AS attempt_failed,
                          CASE WHEN COUNT(*) FILTER (WHERE status IN ('pending','running','retry_wait')) > 0 THEN 'running'
                               WHEN COUNT(*) FILTER (WHERE status='failed') > 0 THEN 'failed'
                               ELSE 'succeeded' END AS status
                   FROM control.node_attempts
                   WHERE tenant_id=%s
                   GROUP BY run_id, plan_key, execution_hash
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (str(tenant_id), limit),
            )
            return [
                {
                    "run_id": str(row[0]), "plan_key": row[1], "execution_hash": row[2],
                    "created_at": row[3].isoformat() if row[3] else None,
                    "trace_id": row[4], "attempt_total": row[5],
                    "attempt_succeeded": row[6], "attempt_failed": row[7],
                    "status": row[8],
                }
                for row in cur.fetchall()
            ]

    def canvas_projection(self, tenant_id: UUID, run_id: str) -> dict[str, Any]:
        """CW4 canvas definition: nodes (attempts) + edges (data flow).

        Projection is read from the same persisted store the executor wrote
        (AttemptStore), so the 2-D canvas can never drift from run truth.
        Raises ValueError when the run does not exist for this tenant.
        """
        store = AttemptStore(self.database_url)
        attempts = store.query_attempts(tenant_id=tenant_id, run_id=run_id)
        if not attempts:
            raise ValueError(f"run not found: {run_id}")
        first = attempts[0]
        edges = store.query_edges(tenant_id=tenant_id, run_id=run_id)
        return {
            "run_id": run_id,
            "plan_key": first.get("plan_key"),
            "execution_hash": first.get("execution_hash"),
            "status": "failed" if any(a["status"] == "failed" for a in attempts) else "succeeded",
            "trace_id": first["trace_id"],
            "created_at": first["created_at"],
            "nodes": [
                {
                    "node_instance_id": a["node_instance_id"],
                    "capability": a["capability"],
                    "plugin_id": a["plugin_id"],
                    # Which implementation ran.  Empty for a node that failed
                    # before reaching the runtime; never a fabricated value.
                    "plugin_version": a.get("plugin_version", ""),
                    "runtime_code_sha256": a.get("runtime_code_sha256", ""),
                    "attempt_seq": a["attempt_seq"],
                    "attempt_id": a["attempt_id"],
                    "status": a["status"],
                    "error_kind": a["error_kind"],
                    "error_message": a["error_message"],
                    "worker_id": a["worker_id"],
                    "input_bindings": a["input_bindings"],
                    "output_refs": a["output_refs"],
                    "trace_id": a["trace_id"],
                    "created_at": a["created_at"],
                    "finished_at": a["finished_at"],
                }
                for a in attempts
            ],
            "edges": edges,
        }

    def list_runs(self, chain_key: str, limit: int = 50) -> list[dict[str, Any]]:
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                return list_run_rows(cur, tenant_id, chain_key, limit=limit)

    def get_run(self, run_id: UUID | str) -> dict[str, Any]:
        """One run projection plus its grouped per-node ledger entries."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                projection = run_projection(cur, UUID(str(run_id)), tenant_id)
                entries = run_entries(cur, tenant_id, UUID(str(run_id)))
                return {**projection, "entries": entries}

    def verify_chain_run(
        self,
        request: RunVerificationRequest | dict[str, Any],
        *,
        trace_id: UUID | None = None,
    ) -> dict[str, Any]:
        """M7 run verification: pure DB reproducibility compare + verdict row.

        Fail-closed: the run must be ``success``; an explicit reference must
        exist, be success, belong to the same tenant and the same chain; the
        ``topology.chain.verify`` capability is freshly granted by the policy
        gateway.  The comparator spawns no child process and performs no
        rollback - ``rollback_verdict`` is advisory only.
        """
        if isinstance(request, dict):
            request = RunVerificationRequest.parse(request)
        self._gate("topology.chain.verify", {"run_id": str(request.run_id)}, risk_class="medium")
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                run_projection(cur, request.run_id, tenant_id)  # tenant/state precheck
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                existing = verification_by_key(cur, tenant_id, request.idempotency_key)
                if existing is not None:
                    return {**existing, "idempotent": True}
                response = _verify_run(
                    cur,
                    tenant_id=tenant_id,
                    run_id=request.run_id,
                    reference_run_id=request.reference_run_id,
                    reason=request.reason,
                    idempotency_key=request.idempotency_key,
                    trace_id=trace,
                )
                self._record_idempotent(
                    cur, tenant_id, request.idempotency_key, self._request_hash(asdict(request)), response
                )
                return {**response, "idempotent": False}

    def list_chain_verifications(self, run_id: UUID | str, limit: int = 50) -> list[dict[str, Any]]:
        """Every verification recorded for one run (newest first)."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                run_projection(cur, UUID(str(run_id)), tenant_id)  # tenant precheck
                return list_verification_rows(cur, tenant_id, UUID(str(run_id)), limit=limit)

    def get_run_verification(self, verification_id: UUID | str) -> dict[str, Any]:
        """One verification projection, tenant-scoped."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                return verification_projection(cur, UUID(str(verification_id)), tenant_id)

    # -- M8 drift disposition: remediation proposal governance -----------------

    def create_remediation_proposal(
        self,
        request: RemediationProposalRequest | dict[str, Any],
        *,
        trace_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Open a remediation proposal for one drifted verification (M8).

        Fail-closed: the ``topology.chain.remediate`` capability must be
        freshly granted (else 403 with zero writes), the verification must
        exist in the tenant and be ``drifted`` with a non-empty rollback
        verdict (else fail-closed).  The proposal row is append-only
        (``status`` stays ``pending_approval`` forever); terminal state is
        derived from the immutable decision ledger, never stored by UPDATE.
        """
        if isinstance(request, dict):
            request = RemediationProposalRequest.parse(request)
        self._gate(
            "topology.chain.remediate",
            {"verification_id": str(request.verification_id)},
            risk_class="medium",
        )
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                verification_projection(cur, request.verification_id, tenant_id)  # tenant precheck
                replay = self._idempotent(cur, tenant_id, request.idempotency_key)
                if replay is not None:
                    return replay
                existing = proposal_by_key(cur, tenant_id, request.idempotency_key)
                if existing is not None:
                    return {**existing, "idempotent": True}
                response = create_proposal(
                    cur,
                    tenant_id=tenant_id,
                    verification_id=request.verification_id,
                    action=request.action,
                    reason=request.reason,
                    idempotency_key=request.idempotency_key,
                    trace_id=trace,
                )
                self._record_idempotent(
                    cur,
                    tenant_id,
                    request.idempotency_key,
                    self._request_hash(asdict(request)),
                    response,
                )
                return {**response, "idempotent": False}

    def decide_remediation(
        self,
        proposal_id: UUID | str,
        *,
        decision: str,
        approver: str,
        reason: str,
        idempotency_key: str,
        trace_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Record one terminal approve/reject/close decision (append-only)."""
        self._gate(
            "topology.chain.remediate",
            {"proposal_id": str(proposal_id)},
            risk_class="medium",
        )
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, idempotency_key)
                if replay is not None:
                    return replay
                proposal = proposal_projection(cur, UUID(str(proposal_id)), tenant_id)
                if proposal["status"] != "pending_approval":
                    # A repeated decision on a live proposal conflicts; an
                    # identical decision already on the ledger is a replay.
                    cur.execute(
                        """SELECT id FROM topology.remediation_decisions
                        WHERE tenant_id=%s AND proposal_id=%s AND decision=%s""",
                        (tenant_id, UUID(str(proposal_id)), decision),
                    )
                    if cur.fetchone() is not None:
                        return {**proposal, "decision": decision, "idempotent": True}
                    raise RemediationConflictError(
                        "proposal already finalized as "
                        f"{proposal['status']}; decisions are irreversible"
                    )
                response = decide_proposal(
                    cur,
                    tenant_id=tenant_id,
                    proposal_id=UUID(str(proposal_id)),
                    decision=decision,
                    approver=approver,
                    reason=reason,
                    idempotency_key=idempotency_key,
                    trace_id=trace,
                )
                self._record_idempotent(
                    cur,
                    tenant_id,
                    idempotency_key,
                    self._request_hash(
                        {
                            "proposal_id": str(proposal_id),
                            "decision": decision,
                            "approver": approver,
                            "reason": reason,
                        }
                    ),
                    response,
                )
                return {**response, "decision": decision, "idempotent": False}

    def remediate_run(
        self,
        proposal_id: UUID | str,
        *,
        reason: str,
        idempotency_key: str,
        trace_id: UUID | None = None,
        input_sources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """M8 governed re-run: dispatch a run only for an approved re-run-locked-release proposal.

        Fail-closed: the proposal must be ``approved`` with action
        ``re-run-locked-release`` (everything else rejects with zero writes),
        and the tenant policy must freshly grant ``topology.chain.remediate``
        plus the M6 execute pair.  The run itself reuses the entire M6
        ``start_run`` machinery (preflight fail-closed, exclusive begin,
        per-node ``python -I`` read-only isolation, CAS finalize) unchanged;
        this method only adds the proposal -> run lineage row in
        ``remediation_run_links``.  ``re-verify`` / ``escalate-human``
        proposals never trigger execution.
        """
        if not (reason or "").strip():
            raise ValueError("governed re-run requires a reason")
        for capability in ("topology.chain.execute", "topology.chain.execute.isolated"):
            self._gate(capability, {"proposal_id": str(proposal_id)}, risk_class="medium")
        trace = str(trace_id or uuid4())
        typed_pid = UUID(str(proposal_id))
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, idempotency_key)
                if replay is not None:
                    return replay
                proposal = proposal_projection(cur, typed_pid, tenant_id)
                if proposal["status"] != "approved":
                    raise RemediationConflictError(
                        "only approved proposals can dispatch a governed re-run; "
                        f"got {proposal['status']}"
                    )
                if proposal["action"] != "re-run-locked-release":
                    raise RemediationConflictError(
                        "governed re-run requires action=re-run-locked-release; "
                        f"got {proposal['action']}"
                    )
            run_response = self.start_run(
                ChainRunRequest(
                    chain_key=proposal["chain_key"],
                    mode=ISOLATED_MODE,
                    idempotency_key=idempotency_key,
                    reason=reason,
                ),
                trace_id=UUID(trace),
                input_sources=dict(input_sources or {}),
            )
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                run_id = UUID(str(run_response["run_id"]))
                link_remediation_run(
                    cur,
                    tenant_id=tenant_id,
                    proposal_id=typed_pid,
                    run_id=run_id,
                    idempotency_key=idempotency_key,
                    trace_id=trace,
                )
                response = {
                    **run_response,
                    "proposal_id": str(typed_pid),
                    "remediation_run_id": str(run_id),
                }
                self._record_idempotent(
                    cur,
                    tenant_id,
                    idempotency_key,
                    self._request_hash(
                        {"proposal_id": str(typed_pid), "reason": reason}
                    ),
                    response,
                )
                return response

    def list_remediation_proposals(
        self,
        *,
        run_id: UUID | str | None = None,
        verification_id: UUID | str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Proposals filtered by run/verification (newest first), read-only."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                if run_id is not None:
                    run_projection(cur, UUID(str(run_id)), tenant_id)  # tenant precheck
                if verification_id is not None:
                    verification_projection(cur, UUID(str(verification_id)), tenant_id)
                return list_proposal_rows(
                    cur,
                    tenant_id,
                    run_id=UUID(str(run_id)) if run_id is not None else None,
                    verification_id=(
                        UUID(str(verification_id)) if verification_id is not None else None
                    ),
                    limit=limit,
                )

    def get_remediation_proposal(self, proposal_id: UUID | str) -> dict[str, Any]:
        """One remediation proposal projection, tenant-scoped."""
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                return proposal_projection(cur, UUID(str(proposal_id)), tenant_id)

    # -- M9 evidence chain: anchor / verify / export --------------------------

    def anchor_evidence(
        self,
        scope: str,
        *,
        idempotency_key: str,
        trace_id: UUID | None = None,
    ) -> dict[str, Any]:
        """M9: anchor the M1-M8 evidence ledgers into the per-tenant hash chain.

        Fail-closed: the ``topology.evidence.anchor`` capability must be
        freshly granted (else 403 with zero writes), the scope must be one of
        the locked enum (else 400), and the batch is append-only in
        ``topology.evidence_chain_anchors``.  A repeated idempotency key
        replays the recorded projection (or the DB-level existing batch) with
        ``idempotent=True``; nothing is ever overwritten.
        """
        if scope not in EVIDENCE_SCOPES:
            raise ValueError(f"invalid evidence scope: {scope}")
        self._gate("topology.evidence.anchor", {"scope": scope}, risk_class="medium")
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                replay = self._idempotent(cur, tenant_id, idempotency_key)
                if replay is not None:
                    return replay
                existing = anchor_by_key(cur, tenant_id, idempotency_key, scope)
                if existing is not None:
                    return {"entries": existing, "scope": scope, "idempotent": True}
                anchors = _anchor_evidence(
                    cur,
                    tenant_id=tenant_id,
                    scope=scope,
                    idempotency_key=idempotency_key,
                    trace_id=trace,
                )
                response = {"entries": anchors, "scope": scope, "idempotent": False}
                self._record_idempotent(
                    cur,
                    tenant_id,
                    idempotency_key,
                    self._request_hash({"scope": scope}),
                    {"entries": anchors, "scope": scope},
                )
                return response

    def verify_evidence_chain(
        self,
        scope: str = "full",
        *,
        trace_id: UUID | None = None,
    ) -> dict[str, Any]:
        """M9: recompute row hashes + replay predecessor links (read-only).

        The ``topology.evidence.verify`` capability is fail-closed; the caller
        needs a fresh grant for every tenant.  Returns a strict-schema proof:
        ``verified`` + ``tail_hash``, or the first mismatching link for precise
        tamper localization.  No row is ever written.
        """
        if scope not in EVIDENCE_SCOPES:
            raise ValueError(f"invalid evidence scope: {scope}")
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                return _verify_evidence_chain(
                    cur, tenant_id=tenant_id, trace_id=trace, scope=scope
                )

    def export_evidence(
        self,
        scope: str = "full",
        *,
        trace_id: UUID | None = None,
    ) -> dict[str, Any]:
        """M9: read-only audit export of anchor metadata + overall sha256.

        The ``topology.evidence.export`` capability is fail-closed.  Export is
        an API response body only — no file is ever created on disk.
        """
        if scope not in EVIDENCE_SCOPES:
            raise ValueError(f"invalid evidence scope: {scope}")
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                return _export_evidence(cur, tenant_id=tenant_id, trace_id=trace, scope=scope)

    def evidence_chain_status(
        self,
        *,
        trace_id: UUID | None = None,
    ) -> dict[str, Any]:
        """M9: lightweight chain status (anchor count, tail hash, last anchor)."""
        trace = str(trace_id or uuid4())
        with psycopg2.connect(self.database_url) as connection:
            with connection.cursor() as cur:
                tenant_id = self._tenant(cur)
                return chain_status(cur, tenant_id=tenant_id, trace_id=trace)

    def _existing_execution_summary(self, cur: Any, tenant_id: UUID, chain_id: UUID, chain_checksum: str) -> dict[str, Any]:
        """Same chain + same simulated mode never re-executes (UNIQUE-safe)."""
        return {
            "chain_key": self._chain_key_for(cur, tenant_id, chain_id),
            "mode": EXECUTION_MODE,
            "status": "already_executed",
            "chain_checksum": str(chain_checksum),
            "idempotent": False,
        }

    def _chain_key_for(self, cur: Any, tenant_id: UUID, chain_id: UUID) -> str:
        cur.execute("SELECT chain_key FROM topology.invocation_chains WHERE tenant_id=%s AND id=%s", (tenant_id, chain_id))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"chain not found: {chain_id}")
        return str(row[0])

    # -- internal entity helpers ---------------------------------------------

    def _blueprint_id(self, cur: Any, tenant_id: UUID, key: str) -> UUID:
        cur.execute("SELECT id FROM topology.plugin_blueprints WHERE tenant_id=%s AND key=%s", (tenant_id, key))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"blueprint not found: {key}")
        return UUID(str(row[0]))

    def _cluster_id(self, cur: Any, tenant_id: UUID, key: str) -> UUID:
        cur.execute("SELECT id FROM topology.plugin_clusters WHERE tenant_id=%s AND key=%s", (tenant_id, key))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"cluster not found: {key}")
        return UUID(str(row[0]))

    def _fetch_entity(self, cur: Any, tenant_id: UUID, entity_kind: str, entity_key: str) -> dict[str, Any]:
        if entity_kind == "cluster":
            cur.execute(
                "SELECT id,key,name,cluster_kind,status,routing_budget,allowed_bridge_kinds FROM topology.plugin_clusters WHERE tenant_id=%s AND key=%s",
                (tenant_id, entity_key),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"cluster not found: {entity_key}")
            return {
                "entity_id": row[0],
                "payload": {"key": row[1], "name": row[2], "cluster_kind": row[3], "status": row[4], "routing_budget": row[5], "allowed_bridge_kinds": row[6]},
            }
        if entity_kind == "blueprint":
            cur.execute(
                "SELECT id,key,name,blueprint_type,capability_contract,source_refs,status FROM topology.plugin_blueprints WHERE tenant_id=%s AND key=%s",
                (tenant_id, entity_key),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"blueprint not found: {entity_key}")
            return {
                "entity_id": row[0],
                "payload": {"key": row[1], "name": row[2], "blueprint_type": row[3], "capability_contract": row[4], "source_refs": row[5], "status": row[6]},
            }
        if entity_kind == "edge":
            cur.execute(
                """SELECT e.id,s.key,tt.key,e.relation_type,e.status
                FROM topology.topology_edges e
                JOIN topology.plugin_blueprints s ON s.id=e.source_blueprint_id AND s.tenant_id=e.tenant_id
                JOIN topology.plugin_blueprints tt ON tt.id=e.target_blueprint_id AND tt.tenant_id=e.tenant_id
                WHERE e.tenant_id=%s AND s.key=%s AND tt.key=%s""",
                (tenant_id, entity_key.split("->")[0], entity_key.split("->")[1] if "->" in entity_key else entity_key),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"edge not found: {entity_key}")
            return {"entity_id": row[0], "payload": {"source": row[1], "target": row[2], "relation_type": row[3], "status": row[4]}}
        raise ValueError(f"unsupported recycle entity_kind: {entity_kind}")

    def _deactivate_entity(self, cur: Any, tenant_id: UUID, entity_kind: str, entity_key: str) -> None:
        if entity_kind == "cluster":
            cur.execute(
                "UPDATE topology.plugin_clusters SET status='retired' WHERE tenant_id=%s AND key=%s",
                (tenant_id, entity_key),
            )
        elif entity_kind == "blueprint":
            cur.execute(
                "UPDATE topology.plugin_blueprints SET status='archived' WHERE tenant_id=%s AND key=%s",
                (tenant_id, entity_key),
            )
        elif entity_kind == "edge":
            cur.execute(
                """UPDATE topology.topology_edges e SET status='inactive'
                FROM topology.plugin_blueprints s JOIN topology.plugin_blueprints tt ON tt.tenant_id=s.tenant_id
                WHERE e.tenant_id=%s AND e.source_blueprint_id=s.id AND e.target_blueprint_id=tt.id
                  AND s.key=%s AND tt.key=%s""",
                (tenant_id, entity_key.split("->")[0], entity_key.split("->")[1] if "->" in entity_key else entity_key),
            )
        else:
            raise ValueError(f"entity_kind cannot be recycled: {entity_kind}")

    def _restore_entity(self, cur: Any, tenant_id: UUID, entity_kind: str, entity_key: str, snapshot: dict[str, Any]) -> None:
        if entity_kind == "cluster":
            cur.execute(
                """UPDATE topology.plugin_clusters SET status='active'
                WHERE tenant_id=%s AND key=%s""",
                (tenant_id, entity_key),
            )
        elif entity_kind == "blueprint":
            cur.execute(
                """UPDATE topology.plugin_blueprints SET status='planned'
                WHERE tenant_id=%s AND key=%s""",
                (tenant_id, entity_key),
            )
        elif entity_kind == "edge":
            source, target = entity_key.split("->")
            cur.execute(
                """UPDATE topology.topology_edges e SET status='active'
                FROM topology.plugin_blueprints s JOIN topology.plugin_blueprints tt ON tt.tenant_id=s.tenant_id
                WHERE e.tenant_id=%s AND e.source_blueprint_id=s.id AND e.target_blueprint_id=tt.id
                  AND s.key=%s AND tt.key=%s""",
                (tenant_id, source, target),
            )
        else:
            raise ValueError(f"entity_kind cannot be restored: {entity_kind}")

    def _load_catalog(self, cur: Any, tenant_id: UUID) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
        cur.execute(
            """SELECT b.key,b.capability_contract FROM topology.plugin_blueprints b
            WHERE b.tenant_id=%s AND b.status IN ('planned','released')""",
            (tenant_id,),
        )
        blueprints: dict[str, dict[str, Any]] = {}
        for key, contract in cur.fetchall():
            blueprints[str(key)] = contract if isinstance(contract, dict) else {}
        cur.execute(
            """SELECT b.key,c.key FROM topology.cluster_memberships m
            JOIN topology.plugin_blueprints b ON b.id=m.blueprint_id AND b.tenant_id=m.tenant_id
            JOIN topology.plugin_clusters c ON c.id=m.cluster_id AND c.tenant_id=m.tenant_id
            WHERE m.tenant_id=%s""",
            (tenant_id,),
        )
        memberships: dict[str, list[str]] = {}
        for blueprint_key, cluster_key in cur.fetchall():
            memberships.setdefault(str(blueprint_key), []).append(str(cluster_key))
        return blueprints, memberships

    def _load_edges(self, cur: Any, tenant_id: UUID) -> list[dict[str, str]]:
        cur.execute(
            """SELECT s.key,tt.key,e.relation_type FROM topology.topology_edges e
            JOIN topology.plugin_blueprints s ON s.id=e.source_blueprint_id AND s.tenant_id=e.tenant_id
            JOIN topology.plugin_blueprints tt ON tt.id=e.target_blueprint_id AND tt.tenant_id=e.tenant_id
            WHERE e.tenant_id=%s AND e.status='active'""",
            (tenant_id,),
        )
        return [
            {"source_blueprint_key": str(row[0]), "target_blueprint_key": str(row[1]), "relation_type": str(row[2])}
            for row in cur.fetchall()
        ]

    def _load_release_catalog(
        self, cur: Any, tenant_id: UUID, release_id: str,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
        """Return blueprint contracts + edges from the frozen release snapshot."""
        cur.execute(
            """SELECT blueprint_snapshots, edge_snapshots FROM topology.topology_releases
            WHERE tenant_id=%s AND id=%s""",
            (tenant_id, release_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("release snapshot not found for planning")
        blueprints: dict[str, dict[str, Any]] = {}
        for key, snapshot in (row[0] or {}).items():
            contract = snapshot.get("capability_contract") if isinstance(snapshot, dict) else None
            if isinstance(contract, dict):
                blueprints[str(key)] = contract
        edges: list[dict[str, str]] = []
        for label, relation in (row[1] or {}).items():
            if not isinstance(relation, str) or "->" not in label:
                continue
            source_key, target_key = label.split("->", 1)
            edges.append(
                {"source_blueprint_key": source_key, "target_blueprint_key": target_key, "relation_type": relation}
            )
        return blueprints, edges

    def _locked_release(self, cur: Any, tenant_id: UUID, release_lock: dict[str, str] | None) -> dict[str, str] | None:
        if release_lock is None:
            return None
        release_id = release_lock.get("release_id")
        version = release_lock.get("version")
        checksum = release_lock.get("checksum_sha256")
        cur.execute(
            """SELECT release_key,version,catalog_checksum FROM topology.topology_releases
            WHERE tenant_id=%s AND id=%s AND status='published'""",
            (tenant_id, release_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("locked release not found or not published")
        if version is None or checksum is None:
            raise ValueError("release lock requires version and checksum_sha256")
        if str(row[1]) != version or str(row[2]).lower() != checksum.lower():
            raise ValueError("release lock mismatch: version or checksum does not match")
        return {"release_id": str(release_id), "version": version, "checksum_sha256": checksum}

    def _release_id_for_plan(self, cur: Any, tenant_id: UUID, release_lock: dict[str, str] | None) -> UUID | None:
        if release_lock is None:
            cur.execute(
                """SELECT id FROM topology.topology_releases WHERE tenant_id=%s AND status='published'
                ORDER BY created_at DESC LIMIT 1""",
                (tenant_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        cur.execute(
            "SELECT id FROM topology.topology_releases WHERE tenant_id=%s AND id=%s AND status='published'",
            (tenant_id, release_lock.get("release_id")),
        )
        row = cur.fetchone()
        return row[0] if row else None

    @staticmethod
    def _plan_json(routing_plan: RoutingPlan) -> dict[str, Any]:
        return {
            "kind": "plugin_routing_plan",
            "mode": routing_plan.mode,
            "plan_key": routing_plan.plan_key,
            "checksum": routing_plan.checksum,
            "nodes": [
                {
                    "slot_key": node.slot_key,
                    "capability": node.capability,
                    "blueprint_key": node.blueprint_key,
                    "alternatives": list(node.alternatives),
                }
                for node in routing_plan.nodes
            ],
            "edges": [
                {
                    "source_node": edge.source_node,
                    "target_node": edge.target_node,
                    "relation_type": edge.relation_type,
                }
                for edge in routing_plan.edges
            ],
            "release_locked": bool(routing_plan.locked_release),
            "planner_version": routing_plan.planner_version,
        }

    # -- M3 chain helpers -----------------------------------------------------

    def _load_plan(self, cur: Any, tenant_id: UUID, plan_key: str) -> dict[str, Any]:
        """Load a plan_only routing plan plus its blueprint/bridge projections."""
        cur.execute(
            "SELECT id,plan_json,planner_version,topology_release_id "
            "FROM topology.routing_plans WHERE tenant_id=%s AND plan_key=%s",
            (tenant_id, plan_key),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"routing plan not found: {plan_key}")
        plan_id, plan_json, planner_version, release_id = row
        payload = plan_json if isinstance(plan_json, dict) else {}
        nodes = list(payload.get("nodes") or [])
        edges = list(payload.get("edges") or [])
        if not nodes:
            raise ValueError("plan has no nodes; cannot materialize a chain")
        blueprint_keys = sorted({str(node["blueprint_key"]) for node in nodes})
        blueprints: dict[str, Any] = {}
        if blueprint_keys:
            if release_id is not None and bool(payload.get("release_locked")):
                # CW0: an explicitly release-locked plan must interpret the
                # frozen release snapshot, never the live catalog.  A later
                # catalog edit must not change how an already-published release
                # is materialized.  Plans without an explicit lock keep reading
                # the current catalog (the implicit latest-release binding).
                cur.execute(
                    """SELECT blueprint_snapshots FROM topology.topology_releases
                    WHERE tenant_id=%s AND id=%s""",
                    (tenant_id, release_id),
                )
                release_row = cur.fetchone()
                if release_row is None or not isinstance(release_row[0], dict):
                    raise ValueError(f"release snapshot not found for plan: {plan_key}")
                for key in blueprint_keys:
                    snapshot = (release_row[0] or {}).get(key)
                    if not isinstance(snapshot, dict):
                        raise ValueError(f"release snapshot missing blueprint: {key}")
                    contract = snapshot.get("capability_contract")
                    blueprints[key] = contract if isinstance(contract, dict) else {}
            else:
                cur.execute(
                    """SELECT key,capability_contract FROM topology.plugin_blueprints
                    WHERE tenant_id=%s AND key=ANY(%s)""",
                    (tenant_id, blueprint_keys),
                )
                for key, contract in cur.fetchall():
                    blueprints[str(key)] = contract if isinstance(contract, dict) else {}
        bridge_producers = {str(edge["source_node"]) for edge in edges if str(edge.get("relation_type") or "") == "bridge"}
        bridges: dict[str, dict[str, str]] = {}
        if bridge_producers:
            cur.execute(
                """SELECT b.key,d.ref_kind,d.bridge_ref
                FROM topology.domain_bridges d
                JOIN topology.plugin_blueprints b ON b.id=d.blueprint_id AND b.tenant_id=d.tenant_id
                WHERE d.tenant_id=%s AND d.status='active' AND b.key=ANY(%s)""",
                (tenant_id, list(bridge_producers)),
            )
            for key, ref_kind, bridge_ref in cur.fetchall():
                bridges[str(key)] = {"ref_kind": str(ref_kind), "bridge_ref": str(bridge_ref)}
        for producer in sorted(bridge_producers):
            if producer not in bridges:
                raise ValueError(f"bridge edge requires a registered active bridge on producer: {producer}")
        if len(nodes) > 32:
            raise ValueError("plan exceeds the invocation-chain node budget (max 32)")
        return {
            "plan_id": row[0],
            "nodes": nodes,
            "edges": edges,
            "blueprints": blueprints,
            "bridges": bridges,
            "planner_version": str(planner_version or "1.0.0"),
            "release_locked": bool(payload.get("release_locked")) or release_id is not None,
        }

    def _existing_chain_summary(self, cur: Any, tenant_id: UUID, plan_key: str) -> dict[str, Any] | None:
        """Return the already-materialized chain for this plan (chains are deterministic)."""
        cur.execute(
            """SELECT chain_key,mode,chain_checksum,planner_version,chain_json->>'release_locked'
            FROM topology.invocation_chains
            WHERE tenant_id=%s AND plan_id=(SELECT id FROM topology.routing_plans
              WHERE tenant_id=%s AND plan_key=%s)""",
            (tenant_id, tenant_id, plan_key),
        )
        row = cur.fetchone()
        if row is None:
            return None
        chain_key, mode, checksum, planner_version, release_locked = row[0], row[1], row[2], row[3], row[4]
        return {
            "chain_key": str(chain_key),
            "plan_key": plan_key,
            "mode": str(mode),
            "planner_version": str(planner_version),
            "checksum": str(checksum),
            "release_locked": str(release_locked).lower() in {"true", "1"},
            "already_materialized": True,
            "idempotent": False,
        }

    def _intent_decisions(self, chain: Any) -> dict[str, tuple[str, str]]:
        """Map Policy gateway decisions onto the four-state intent projection."""
        decisions: dict[str, tuple[str, str]] = {}
        for node in chain.chain_order:
            result = self.policy.evaluate(node.capability, {}, "low", side_effects="read_only")
            if result.decision == "allow":
                decisions[node.slot_key] = ("allowed", "policy_allowed")
            elif result.decision in {"deny", "freeze"}:
                decisions[node.slot_key] = ("denied", "denied")
            else:
                decisions[node.slot_key] = ("requires_approval", "materialized")
        return decisions
