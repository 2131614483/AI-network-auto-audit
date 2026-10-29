"""Policy-recorded service for persistent, local, plan-only collaboration."""

from __future__ import annotations

import builtins
import hashlib
from dataclasses import asdict
from typing import Any
from uuid import UUID

import psycopg2

from packages.plugin_runtime.layout import runtime_dir
from packages.plugin_runtime.runner import PluginRuntimeError, load_verified_binding
from packages.plugin_topology.port_adapters import PORT_ADAPTER_CONTRACTS
from packages.plugin_topology.ports_executor import capability_parameters
from packages.policy.engine import PolicyEngine
from packages.policy.gateway import record_decision

from .collaborative import CollaborativePlanner
from .collaborative_agents import PlannerAgent, ReviewerAgent
from .composer import PluginSpec, discover_plugins, schema_sha256
from .evidence import PlanningContext, PlanningRequest, fingerprint
from .model_access import resolve_model_access
from .session_store import TERMINAL_STATUSES, PlanningSessionStore
from .source_adapters import SqlEvidenceSources

CAPABILITIES = frozenset({
    "topology.collaborative.plan", "topology.collaborative.read",
    "topology.collaborative.graph.read", "topology.collaborative.history.read",
    "topology.collaborative.knowledge.read", "topology.collaborative.input.read",
    "topology.collaborative.model.call",
})


class PlanningAccessDenied(PermissionError):
    def __init__(self, decision: str, decision_id: str) -> None:
        super().__init__(f"policy gateway blocked planning operation: {decision}")
        self.decision = decision
        self.decision_id = decision_id


class PlanningTenantNotFound(PermissionError):
    """An unknown workspace is not a database migration failure."""


def frozen_catalog(domain: str) -> tuple[dict[str, PluginSpec], str]:
    specs: dict[str, PluginSpec] = {}
    snapshot = []
    for plugin_id, spec in discover_plugins(domain=domain, lifecycle="verified").items():
        try:
            binding = load_verified_binding(plugin_id)
        except (ValueError, PluginRuntimeError):
            continue
        if binding.capability != spec.capability:
            continue
        specs[plugin_id] = spec
        snapshot.append({
            "spec": asdict(spec), "binding": asdict(binding),
            "runtime_sha256": hashlib.sha256((runtime_dir(plugin_id) / "runtime.py").read_bytes()).hexdigest(),
            "parameters": capability_parameters(spec.capability),
            "adapters": PORT_ADAPTER_CONTRACTS,
            "port_schemas": {port.schema_ref: schema_sha256(port.schema_ref) for port in (*spec.inputs, *spec.outputs)},
        })
    return specs, fingerprint(snapshot)


class CollaborativePlanningService:
    def __init__(self, database_url: str, *, sources: Any = None, model: Any = None) -> None:
        self.database_url = database_url
        self.store = PlanningSessionStore(database_url)
        self.sources = sources if sources is not None else SqlEvidenceSources(database_url)
        self.model = model

    def gate(self, context: PlanningContext, capability: str, arguments: dict[str, Any]) -> str:
        if capability not in CAPABILITIES:
            raise PermissionError("unknown collaborative capability")
        risk = "low" if capability in {"topology.collaborative.plan", "topology.collaborative.model.call"} else "read_only"
        effects = "write_data" if capability == "topology.collaborative.plan" else "read_only"
        if capability == "topology.collaborative.model.call" and arguments.get("model_mode") == "external":
            risk, effects = "medium", "external_network"
        args = {**arguments, "session_id": str(context.session_id), "round_no": context.round_no}
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(context.tenant_id),))
            cur.execute("SELECT id FROM iam.tenants WHERE id=%s", (str(context.tenant_id),))
            if cur.fetchone() is None:
                raise PlanningTenantNotFound("planning tenant not found")
            cur.execute("SELECT rules FROM policy.policy_sets WHERE tenant_id=%s AND status='active' ORDER BY created_at,id", (str(context.tenant_id),))
            rules = [rule for row in cur.fetchall() if isinstance(row[0], list) for rule in row[0] if isinstance(rule, dict)]
            args["policy_snapshot_sha256"] = fingerprint(rules)
            result = PolicyEngine(rules=rules).evaluate(capability, args, risk, side_effects=effects)
            decision = record_decision(cur, context.tenant_id, capability, args, result, context.trace_id)
        if result.decision != "allow":
            raise PlanningAccessDenied(decision.decision, str(decision.decision_id))
        return str(decision.decision_id)

    def create(self, context: PlanningContext, request: PlanningRequest) -> dict[str, Any]:
        model_access = resolve_model_access(self.model).snapshot(context)
        decision = self.gate(context, "topology.collaborative.plan", {
            "operation": "create", "request_hash": fingerprint(request), "domain": request.domain,
            "idempotency_key": request.idempotency_key,
            "model_access": model_access,
        })
        return self.store.create(context, request, UUID(decision), model_access=model_access)

    def get(self, context: PlanningContext) -> dict[str, Any]:
        self.gate(context, "topology.collaborative.read", {"operation": "get"})
        row = self.store.get(context.tenant_id, context.session_id)
        row["events"] = self.store.events(context.tenant_id, context.session_id)
        row.pop("lease_token", None)
        return row

    def list(self, context: PlanningContext, limit: int = 50) -> list[dict[str, Any]]:
        self.gate(context, "topology.collaborative.read", {"operation": "list", "limit": limit})
        rows = self.store.list(context.tenant_id, limit)
        return [{key: value for key, value in row.items() if key not in {"checkpoint_json", "lease_token"}} for row in rows]

    def catalog(self, context: PlanningContext, domain: str) -> builtins.list[dict[str, Any]]:
        self.gate(context, "topology.collaborative.graph.read", {"operation": "catalog", "domain": domain})
        specs, _ = frozen_catalog(domain)
        return [{"plugin_id": spec.plugin_id, "capability": spec.capability, "name": spec.name,
                 "inputs": [asdict(port) for port in spec.inputs], "outputs": [asdict(port) for port in spec.outputs]}
                for spec in specs.values()]

    def process(self, context: PlanningContext, *, worker_id: str) -> dict[str, Any]:
        self.gate(context, "topology.collaborative.read", {"operation": "process_lookup"})
        row = self.store.get(context.tenant_id, context.session_id)
        if row["status"] in TERMINAL_STATUSES:
            return dict(row["result_json"] or {"status": row["status"], "session_id": row["session_id"]})
        context = context.model_copy(update={"trace_id": UUID(row["trace_id"])})
        request = PlanningRequest.model_validate(row["request_json"])
        def write(operation: str) -> UUID:
            return UUID(self.gate(context, "topology.collaborative.plan", {
                "operation": operation, "domain": request.domain, "request_hash": fingerprint(request),
            }))

        claim = self.store.claim(context, worker_id, write("claim"), lease_seconds=max(300, request.budget.max_seconds + 120))
        if claim is None:
            return {"status": "running", "session_id": row["session_id"], "mode": "plan_only"}
        token = UUID(claim["lease_token"])
        checkpoint = dict(claim["checkpoint_json"])
        model_snapshot = checkpoint.pop("model_access", None)
        result: dict[str, Any]
        try:
            specs, catalog_hash = frozen_catalog(request.domain)
        except (OSError, ValueError, RuntimeError) as exc:
            result = {"status": "failed", "mode": "plan_only", "model_access": model_snapshot,
                      "detail": f"verified catalog unavailable: {type(exc).__name__}"}
            self.store.finish(context, "failed", result, write("finish"), token)
            return result
        if checkpoint.get("catalog_hash") and checkpoint["catalog_hash"] != catalog_hash:
            result = {"status": "failed", "mode": "plan_only", "detail": "capability directory changed; submit a new planning session"}
        else:
            def emit(key: str, kind: str, payload: dict[str, Any], state: dict[str, Any]) -> None:
                event_context = context.model_copy(update={"round_no": min(8, max(1, int(state.get("round_no", 1)) - (kind == "round_finished")))})
                decision = UUID(self.gate(event_context, "topology.collaborative.plan", {
                    "operation": "append", "event_key": key, "event_hash": fingerprint(payload), "domain": request.domain,
                }))
                self.store.append(event_context, key, kind, payload, decision, token, {
                    **state, "catalog_hash": catalog_hash, "model_access": model_snapshot,
                })

            try:
                access = resolve_model_access(self.model)
                if model_snapshot is None or access.snapshot(context) != model_snapshot:
                    raise ValueError("model configuration changed or missing; submit a new planning session")
                with access.connect(context, database_url=self.database_url, max_seconds=request.budget.max_seconds) as model:
                    engine = CollaborativePlanner(
                        sources=self.sources,
                        gateway=lambda capability, args: self.gate(engine.context, capability, args),
                        model=model, planner=PlannerAgent(), reviewer=ReviewerAgent(),
                        specs=specs, emit=emit,
                    )
                    result = engine.run(context, request, checkpoint=checkpoint or None)
            except (OSError, ValueError, RuntimeError) as exc:
                result = {"status": "failed", "mode": "plan_only", "detail": f"model access rejected: {exc}"}
            if result["status"] == "accepted":
                try:
                    catalog_current = frozen_catalog(request.domain)[1] == catalog_hash
                    detail = "capability directory changed during planning"
                except (OSError, ValueError, RuntimeError) as exc:
                    catalog_current = False
                    detail = f"final capability directory verification unavailable: {type(exc).__name__}"
                if not catalog_current:
                    assessment = {**result["assessment"], "passed": False, "plan": None,
                                  "hard_issues": [*result["assessment"]["hard_issues"], detail]}
                    result = {**result, "status": "failed", "detail": detail, "assessment": assessment,
                              "checkpoint": {**result["checkpoint"], "assessment": assessment}}
        finish_decision = write("finish")
        result["model_access"] = model_snapshot
        result["policy_snapshot_ref"] = f"policy://decisions/{finish_decision}"
        self.store.finish(context, result["status"], result, finish_decision, token)
        return result

    def process_next(self, tenant_id: UUID, *, worker_id: str) -> dict[str, Any] | None:
        from uuid import uuid4

        context = PlanningContext(tenant_id=tenant_id, trace_id=uuid4(), session_id=uuid4())
        self.gate(context, "topology.collaborative.read", {"operation": "pending"})
        pending = self.store.list_pending(tenant_id, limit=1)
        if not pending:
            return None
        row = pending[0]
        return self.process(PlanningContext(
            tenant_id=tenant_id, trace_id=UUID(row["trace_id"]), session_id=UUID(row["session_id"]),
        ), worker_id=worker_id)
