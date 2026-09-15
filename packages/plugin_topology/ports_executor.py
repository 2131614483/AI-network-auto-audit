"""CW1 port-bound executor: run an ExecutionPlan with per-port input binding.

The runtime output index is ``outputs[(node_instance_id, output_port_id)] =
ArtifactRef`` (方案 7.1).  A downstream node resolves every required input
from the compiled edges - the exact producer instance and port - never from
"the previous node in topological order" (the pre-CW1 defect).  Fan-out reads
the same producer port artifact; fan-in is only legal on a list input
(``cardinality='many'``), and each converted input keeps its provenance.

Every output artifact is written, size/SHA-locked and re-readable from disk,
so a caller can drill down to the data layer; each entry carries its trace id
for the log layer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import url2pathname
from uuid import UUID

from packages.plugin_runtime.runner import (
    ArtifactInput,
    IsolatedPluginRuntime,
    PluginInvocation,
)
from packages.policy.engine import PolicyEngine

from .compiler import topological_order, validate_identifier
from .ir import ExecutionPlan, IRNode
from .isolated import build_research_note_payload_from_ledger

_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# Registered explicit contract conversions (方案 5.1: an adapter must be
# explicit and registered; without one the compiler rejects the edge).  Each
# adapter returns the VALUE bound under the target port id inside the child
# payload (the port id itself is added by the executor).
ADAPTERS: dict[str, Callable[[Path, Path, UUID], dict[str, Any]]] = {
    "ledger-quality-to-research-note": lambda producer_path, node_dir, tenant_id: (
        build_research_note_payload_from_ledger(producer_path, node_dir, tenant_id)["research_note"]
    ),
}


def register_adapter(name: str, fn: Callable[[Path, Path, UUID], dict[str, Any]]) -> None:
    if not name.strip():
        raise ValueError("adapter name must not be empty")
    ADAPTERS[name] = fn


# Schema-ref aware payload builders for governed input ports.  Each builder
# returns the value bound under the port id inside the child payload.
def _ledger_artifact_value(ref: ArtifactInput) -> dict[str, object]:
    return {
        "contract_id": "ledger-artifact-ref",
        "contract_version": "1.0.0",
        "artifact": ref.as_payload(),
        "schema_mapping_version": "1.0.0",
        "period": "2026-01",
    }


_PAYLOAD_BUILDERS: dict[str, Callable[[ArtifactInput], dict[str, object]]] = {
    "ledger-artifact-ref": _ledger_artifact_value,
}

# Capability-level payload assemblies: a topology port (contract id) is not the
# same thing as the runtime child envelope key.  The default assembly binds the
# payload key = input port id; capabilities whose verified runtime expects a
# domain envelope (e.g. finding.draft -> {"finding": {"anomaly_candidates": ref}})
# register an explicit builder here so the same flow file compiles AND executes.
CAPABILITY_PAYLOAD_BUILDERS: dict[str, Callable[[IRNode, dict[str, Any], Path], dict[str, Any]]] = {}


def _finding_draft_payload(
    node: IRNode, bindings: dict[str, list[dict[str, Any]]], node_dir: Path
) -> dict[str, Any]:
    items = bindings.get("candidates") or []
    if not items:
        raise RuntimeError(
            f"required input 'candidates' of node {node.node_instance_id} "
            "has no bound producer artifact (edge or seed missing)"
        )
    ref = items[0]["ref"]
    return {"finding": {"anomaly_candidates": _ref_to_artifact_input(ref).as_payload()}}


CAPABILITY_PAYLOAD_BUILDERS["audit.finding.draft"] = _finding_draft_payload


# --------------------------------------------------------------------------
# declared capability parameters
# --------------------------------------------------------------------------
# Some verified runtimes require operational values (a window, a budget, a
# canary scope) that are *not* ports.  The executor never reads
# ``IRNode.parameters``, so there is nowhere else to carry them — and burying a
# threshold in code hides an operating policy.  They are declared here instead,
# with their ranges, so a reviewer can see and change them.

CAPABILITY_PARAMETERS_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "capability-parameters.json"
)
_CAPABILITY_PARAMETERS: dict[str, dict[str, Any]] | None = None


def capability_parameters(capability: str) -> dict[str, Any]:
    """Declared operational values for one capability (``{}`` when none)."""
    global _CAPABILITY_PARAMETERS
    if _CAPABILITY_PARAMETERS is None:
        try:
            raw = json.loads(CAPABILITY_PARAMETERS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        _CAPABILITY_PARAMETERS = {
            str(name): {
                str(k): v for k, v in (entry or {}).items() if not str(k).startswith("_")
            }
            for name, entry in (raw.get("parameters") or {}).items()
        }
    return dict(_CAPABILITY_PARAMETERS.get(capability, {}))


def _required_param(capability: str, name: str) -> Any:
    params = capability_parameters(capability)
    if name not in params:
        raise RuntimeError(
            f"{capability}: declared parameter {name!r} is missing from "
            f"{CAPABILITY_PARAMETERS_PATH.name}; the runtime rejects the call without it"
        )
    return params[name]


# --------------------------------------------------------------------------
# aiops capability payloads
# --------------------------------------------------------------------------
# Each aiops runtime reads a *namespaced* envelope key and expects every object
# input as a verified artifact reference (``uri``/``sha256``/``size_bytes``),
# not as inline data.  Two of the ports are bundle-typed: their artifact carries
# the real references plus the budget knobs, so the builder forwards what the
# bundle holds rather than inventing anything.


def _bound_ref(bindings: dict[str, list[dict[str, Any]]], port_id: str, node_id: str) -> ArtifactRef:
    items = bindings.get(port_id) or []
    if not items:
        raise RuntimeError(
            f"required input {port_id!r} of node {node_id} has no bound producer "
            "artifact (edge or seed missing)"
        )
    ref = items[0].get("ref")
    if not isinstance(ref, ArtifactRef):
        raise RuntimeError(f"binding for {port_id!r} of node {node_id} carries no artifact ref")
    return ref


def _payload_of(ref: ArtifactRef) -> dict[str, object]:
    return _ref_to_artifact_input(ref).as_payload()


def _bundle_of(ref: ArtifactRef) -> dict[str, Any]:
    """Parse the JSON a bundle-typed port points at."""
    path = _uri_to_path(ref.uri)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"bundle artifact is unreadable: {ref.uri}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"bundle artifact must be a JSON object: {ref.uri}")
    return data


def _forwarded_ref(bundle: dict[str, Any], key: str, capability: str) -> dict[str, Any]:
    """A reference the runtime must verify itself; forwarded, never rebuilt."""
    value = bundle.get(key)
    if not isinstance(value, dict) or not value.get("uri"):
        raise RuntimeError(
            f"{capability}: input bundle has no usable {key!r} artifact reference"
        )
    return value


def _aiops_triage_payload(
    node: IRNode, bindings: dict[str, list[dict[str, Any]]], node_dir: Path
) -> dict[str, Any]:
    del node_dir
    ref = _bound_ref(bindings, "alert-event", node.node_instance_id)
    return {"triage": {"alert_event": _payload_of(ref)}}


def _aiops_correlate_payload(
    node: IRNode, bindings: dict[str, list[dict[str, Any]]], node_dir: Path
) -> dict[str, Any]:
    del node_dir
    bundle = _bundle_of(_bound_ref(bindings, "alert-event-set", node.node_instance_id))
    return {"alerts": {
        "artifact": _forwarded_ref(bundle, "artifact", node.capability),
        "window_minutes": bundle.get("window_minutes", _required_param(node.capability, "window_minutes")),
        "max_candidates": bundle.get("max_candidates", _required_param(node.capability, "max_candidates")),
    }}


def _aiops_rca_payload(
    node: IRNode, bindings: dict[str, list[dict[str, Any]]], node_dir: Path
) -> dict[str, Any]:
    del node_dir
    bundle = _bundle_of(_bound_ref(bindings, "rca-input", node.node_instance_id))
    return {"rca": {
        "incident_set": _forwarded_ref(bundle, "incident_set", node.capability),
        "topology_graph": _forwarded_ref(bundle, "topology_graph", node.capability),
        "max_candidates": bundle.get("max_candidates", _required_param(node.capability, "max_candidates")),
        "max_hops": bundle.get("max_hops", _required_param(node.capability, "max_hops")),
    }}


def _aiops_remediation_payload(
    node: IRNode, bindings: dict[str, list[dict[str, Any]]], node_dir: Path
) -> dict[str, Any]:
    del node_dir
    ref = _bound_ref(bindings, "rca-candidates", node.node_instance_id)
    return {"remediation": {
        "rca_candidates": _payload_of(ref),
        "max_playbooks": _required_param(node.capability, "max_playbooks"),
        "canary_scope": _required_param(node.capability, "canary_scope"),
    }}


def _aiops_recovery_payload(
    node: IRNode, bindings: dict[str, list[dict[str, Any]]], node_dir: Path
) -> dict[str, Any]:
    del node_dir
    payload: dict[str, Any] = {
        "baseline": _payload_of(_bound_ref(bindings, "baseline-metric-series", node.node_instance_id)),
        "observed": _payload_of(_bound_ref(bindings, "observed-metric-series", node.node_instance_id)),
        "availability_target": _required_param(node.capability, "availability_target"),
        "threshold_ratio": _required_param(node.capability, "threshold_ratio"),
    }
    proposal = bindings.get("remediation-proposal") or []
    if proposal:
        payload["proposal"] = _payload_of(proposal[0]["ref"])
    return {"recovery": payload}


def _aiops_postmortem_payload(
    node: IRNode, bindings: dict[str, list[dict[str, Any]]], node_dir: Path
) -> dict[str, Any]:
    del node_dir
    payload: dict[str, Any] = {
        "incident_proposal": _payload_of(
            _bound_ref(bindings, "incident-proposal", node.node_instance_id)
        ),
    }
    verification = bindings.get("recovery-verification") or []
    if verification:
        payload["verification"] = _payload_of(verification[0]["ref"])
    return {"postmortem": payload}


def _aiops_ticket_payload(
    node: IRNode, bindings: dict[str, list[dict[str, Any]]], node_dir: Path
) -> dict[str, Any]:
    del node_dir
    ref = _bound_ref(bindings, "incident-proposal", node.node_instance_id)
    return {"ticket": {"incident_proposal": _payload_of(ref)}}


CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.triage"] = _aiops_triage_payload
CAPABILITY_PAYLOAD_BUILDERS["aiops.alert.correlate"] = _aiops_correlate_payload
CAPABILITY_PAYLOAD_BUILDERS["aiops.rca.rank"] = _aiops_rca_payload
CAPABILITY_PAYLOAD_BUILDERS["aiops.remediation.propose"] = _aiops_remediation_payload
CAPABILITY_PAYLOAD_BUILDERS["aiops.recovery.verify"] = _aiops_recovery_payload
CAPABILITY_PAYLOAD_BUILDERS["aiops.postmortem.draft"] = _aiops_postmortem_payload
CAPABILITY_PAYLOAD_BUILDERS["aiops.ticket.draft"] = _aiops_ticket_payload


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Immutable content reference for one produced artifact."""

    uri: str
    sha256: str
    size_bytes: int
    classification: str = "internal"

    def as_payload(self) -> dict[str, object]:
        return {
            "uri": self.uri,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "classification": self.classification,
        }


def _artifact_input_to_ref(seed: ArtifactInput) -> ArtifactRef:
    return ArtifactRef(
        uri=seed.uri,
        sha256=seed.sha256.lower(),
        size_bytes=seed.size_bytes,
        classification=seed.classification,
    )


def _uri_to_path(uri: str) -> Path:
    if uri.startswith("file://"):
        path = url2pathname(uri[len("file://"):])
        # Windows file URI: file:///C:/x -> /C:/x -> C:/x
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return Path(path)
    return Path(uri)


class PortBoundExecutor:
    """Deterministic executor over a compiled ExecutionPlan."""

    def __init__(
        self,
        *,
        plan: ExecutionPlan,
        runtime: IsolatedPluginRuntime,
        policy: PolicyEngine,
        tenant_id: UUID,
        trace_id: str,
        staging_root: Path,
        idempotency_key: str,
        seed_inputs: dict[tuple[str, str], ArtifactInput] | None = None,
        persistence: Any | None = None,
        run_id: str | None = None,
        worker_id: str | None = None,
        max_output_bytes: int | None = None,
    ) -> None:
        self.plan = plan
        self.runtime = runtime
        self.policy = policy
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self.staging_root = Path(staging_root)
        self.idempotency_key = idempotency_key
        self.seed_inputs = dict(seed_inputs or {})
        # CW3: optional short-transaction persistence (AttemptStore).
        self.persistence = persistence
        self.run_id = run_id
        self.worker_id = worker_id
        self.max_output_bytes = max_output_bytes

    # -- execution -----------------------------------------------------------

    def _node_dir(self, instance_id: str) -> Path:
        """Resolve this node's staging directory, contained under the root.

        Defence in depth behind :func:`~packages.plugin_topology.compiler.validate_identifier`:
        plans can also arrive from the database rather than through
        ``compile_plan``, so an executor must never trust an identifier it did
        not validate itself.  The resolved path is asserted to stay inside
        ``staging_root`` before any ``mkdir`` happens.
        """
        validate_identifier(self.plan.plan_key, field="plan_key")
        validate_identifier(instance_id, field="node_instance_id")
        root = self.staging_root.resolve()
        node_dir = (self.staging_root / "chains" / self.plan.plan_key / instance_id).resolve()
        if not node_dir.is_relative_to(root):
            raise ValueError(
                f"refusing to write outside the staging root: {node_dir} is not under {root}"
            )
        return node_dir

    def execute(self) -> dict[str, Any]:
        order = topological_order(self.plan.nodes, self.plan.edges)
        outputs: dict[tuple[str, str], ArtifactRef] = {}
        entries: list[dict[str, Any]] = []
        for instance_id in order:
            node = self.plan.node_by_id(instance_id)
            attempt = None
            if self.persistence is not None:
                attempt = self.persistence.begin_attempt(
                    tenant_id=self.tenant_id,
                    run_id=self.run_id,
                    plan_key=self.plan.plan_key,
                    execution_hash=self.plan.execution_hash,
                    node_instance_id=instance_id,
                    capability=node.capability,
                    plugin_id=node.plugin_id,
                    attempt_seq=1,
                    trace_id=self.trace_id,
                )
            started = datetime.now(timezone.utc).isoformat()
            try:
                node_dir = self._node_dir(instance_id)
                node_dir.mkdir(parents=True, exist_ok=True)
                entry, produced = self._run_one(node, node_dir, outputs)
            except Exception as exc:
                # Terminal guarantee: an attempt is opened before the node is
                # prepared, so *every* failure path — unsafe identifier, missing
                # upstream artifact, IO error — must still close it.  Letting an
                # exception escape here left the attempt stuck at
                # status='running'/finished_at=NULL forever, and the run
                # projection then looked like a live run.
                entry, produced = self._failed_entry(node, exc, {}, started), {}
            if self.persistence is not None and attempt is not None:
                entry = self._persist_node(entry, produced, attempt)
            entries.append(entry)
            for port_id, ref in produced.items():
                outputs[(instance_id, port_id)] = ref
        status = "succeeded" if entries and all(e["status"] == "succeeded" for e in entries) else "failed"
        return {
            "plan_key": self.plan.plan_key,
            "execution_hash": self.plan.execution_hash,
            "status": status,
            "trace_id": self.trace_id,
            "outputs": [
                {
                    "node_instance_id": instance_id,
                    "ports": [
                        {
                            "port_id": port_id,
                            "uri": ref.uri,
                            "sha256": ref.sha256,
                            "size_bytes": ref.size_bytes,
                        }
                        for (owner, port_id), ref in sorted(outputs.items())
                        if owner == instance_id
                    ],
                }
                for instance_id in order
            ],
            "entries": entries,
            "idempotent": False,
        }

    def _persist_node(
        self,
        entry: dict[str, Any],
        produced: dict[str, ArtifactRef],
        attempt: dict[str, Any],
    ) -> dict[str, Any]:
        """CW3 short transaction: fence-checked terminal write + outbox event."""
        assert self.persistence is not None  # only called when persistence is set
        bindings_dict: dict[str, Any] = {}
        for binding in entry.get("input_bindings") or []:
            port_id = binding.get("port_id")
            if port_id is not None:
                bindings_dict[port_id] = binding
        refs: dict[str, dict[str, Any]] = {
            port_id: {"uri": ref.uri, "sha256": ref.sha256, "size_bytes": ref.size_bytes}
            for port_id, ref in produced.items()
        }
        total_bytes = sum(int(ref.get("size_bytes", 0)) for ref in refs.values())
        if self.max_output_bytes is not None and total_bytes > self.max_output_bytes:
            entry = {
                **entry,
                "status": "failed",
                "error": "runtime budget exceeded: output too large",
            }
        status = "succeeded" if entry.get("status") == "succeeded" else "failed"
        error_kind = None if status == "succeeded" else (
            "budget_exceeded" if "budget exceeded" in (entry.get("error") or "") else "plugin_failed"
        )
        self.persistence.finish_attempt(
            tenant_id=self.tenant_id,
            attempt_id=attempt["attempt_id"],
            expected_lease_token=attempt.get("lease_token"),
            status=status,
            input_bindings=bindings_dict,
            output_refs=refs,
            error_kind=error_kind,
            error_message=entry.get("error"),
            plan_key=self.plan.plan_key,
            execution_hash=self.plan.execution_hash,
            # Which implementation ran, when the node reached the runtime at
            # all; both are absent for a node that failed during preparation.
            plugin_version=entry.get("plugin_version"),
            runtime_code_sha256=entry.get("runtime_code_sha256"),
        )
        return entry

    def _failed_entry(
        self,
        node: IRNode,
        exc: BaseException,
        bindings: dict[str, list[dict[str, Any]]],
        started: str,
    ) -> dict[str, Any]:
        """Fail-closed entry for a node that produced no outputs.

        Shared by the invoke path and the prepare path so a node that never
        reached the runtime is reported in exactly the same shape — the canvas
        projection reads these fields for every node, failed or not.
        """
        reason = "no_governed_input_source" if isinstance(exc, ValueError) else "plugin_runtime_error"
        return {
            "node_instance_id": node.node_instance_id,
            "capability": node.capability,
            "status": "failed",
            "policy_ref": reason,
            "error": f"{type(exc).__name__}: {exc}",
            "input_bindings": self._binding_report(bindings),
            "outputs": [],
            "output_checksum": _EMPTY_SHA256,
            "trace_id": self.trace_id,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    def _run_one(
        self,
        node: IRNode,
        node_dir: Path,
        outputs: dict[tuple[str, str], ArtifactRef],
    ) -> tuple[dict[str, Any], dict[str, ArtifactRef]]:
        started = datetime.now(timezone.utc).isoformat()
        bindings: dict[str, list[dict[str, Any]]] = {}
        try:
            # Binding is inside the guard on purpose: an upstream node that
            # failed produces no artifact, and the resulting RuntimeError used
            # to escape uncaught, aborting the loop and leaving this attempt
            # unfinished forever.
            bindings = self._bind_inputs(node, outputs)
            payload = self._build_payload(node, bindings, node_dir)
            invocation = PluginInvocation(
                tenant_id=self.tenant_id,
                trace_id=UUID(self.trace_id),
                idempotency_key=f"{self.idempotency_key}:{node.node_instance_id}",
                plugin_id=node.plugin_id,
                capability=node.capability,
                payload=payload,
            )
            node_policy = PolicyEngine(allow=[node.capability])
            result = self.runtime.invoke(invocation, node_policy)
        except Exception as exc:  # fail-closed: a failed node produces no outputs
            return self._failed_entry(node, exc, bindings, started), {}

        raw = json.dumps(result.output, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(raw) > _MAX_OUTPUT_BYTES:
            entry = {
                "node_instance_id": node.node_instance_id,
                "capability": node.capability,
                "status": "failed",
                "policy_ref": "plugin_runtime_error",
                "input_bindings": self._binding_report(bindings),
                "outputs": [],
                "output_checksum": _EMPTY_SHA256,
                "trace_id": self.trace_id,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            return entry, {}

        output_sha = hashlib.sha256(raw).hexdigest()
        produced: dict[str, ArtifactRef] = {}
        written: list[dict[str, object]] = []
        for port in node.output_ports:
            path = node_dir / f"output-{port.port_id}-{output_sha[:16]}.json"
            path.write_bytes(raw)
            ref = ArtifactRef(
                uri=path.resolve().as_uri(),
                sha256=output_sha,
                size_bytes=len(raw),
                classification=port.classification,
            )
            produced[port.port_id] = ref
            written.append(
                {
                    "port_id": port.port_id,
                    "uri": ref.uri,
                    "sha256": ref.sha256,
                    "size_bytes": ref.size_bytes,
                }
            )
        entry = {
            "node_instance_id": node.node_instance_id,
            "capability": node.capability,
            "status": "succeeded",
            "policy_ref": "policy_allowed",
            "input_bindings": self._binding_report(bindings),
            "outputs": written,
            "output_checksum": output_sha,
            "plugin_id": result.plugin_id,
            "plugin_version": result.plugin_version,
            "runtime_code_sha256": result.runtime_code_sha256,
            "input_sha256": result.input_sha256,
            "trace_id": self.trace_id,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        return entry, produced

    # -- per-port input resolution -------------------------------------------

    def _bind_inputs(
        self, node: IRNode, outputs: dict[tuple[str, str], ArtifactRef]
    ) -> dict[str, list[dict[str, Any]]]:
        bindings: dict[str, list[dict[str, Any]]] = {}
        for edge in self.plan.incoming_edges(node.node_instance_id):
            if not edge.is_data:
                # A call/control/bridge edge orders execution (the topological
                # walk already honours it) but carries no artifact, so there is
                # nothing to bind.  Looking it up would fail on a key nobody
                # ever produced.
                continue
            key = (edge.source_instance, edge.source_port)
            ref = outputs.get(key)
            if ref is None:
                raise RuntimeError(f"producer artifact not ready for edge {edge.edge_id}: {key}")
            bindings.setdefault(edge.target_port, []).append(
                {
                    "ref": ref,
                    "adapter": edge.adapter,
                    "source_instance": edge.source_instance,
                    "source_port": edge.source_port,
                }
            )
        for port in node.input_ports:
            key = (node.node_instance_id, port.port_id)
            if key in self.seed_inputs and port.port_id not in bindings:
                bindings[port.port_id] = [
                    {
                        "ref": _artifact_input_to_ref(self.seed_inputs[key]),
                        "adapter": None,
                        "source_instance": "seed",
                        "source_port": port.port_id,
                    }
                ]
        return bindings

    def _build_payload(
        self,
        node: IRNode,
        bindings: dict[str, list[dict[str, Any]]],
        node_dir: Path,
    ) -> dict[str, Any]:
        builder = CAPABILITY_PAYLOAD_BUILDERS.get(node.capability)
        if builder is not None:
            return builder(node, bindings, node_dir)
        payload: dict[str, Any] = {}
        for port in node.input_ports:
            items = bindings.get(port.port_id) or []
            if port.required and not items:
                raise RuntimeError(
                    f"required input {port.port_id!r} of node {node.node_instance_id} "
                    "has no bound producer artifact (edge or seed missing)"
                )
            if port.cardinality == "many":
                payload[port.port_id] = {
                    "artifacts": [self._port_value(item, node_dir, port.schema_ref) for item in items]
                }
            else:
                payload[port.port_id] = self._port_value(items[0], node_dir, port.schema_ref)
        return payload

    def _port_value(
        self, item: dict[str, Any], node_dir: Path, schema_ref: str
    ) -> dict[str, object]:
        if item["adapter"]:
            adapter = ADAPTERS.get(item["adapter"])
            if adapter is None:
                raise RuntimeError(f"adapter not registered: {item['adapter']}")
            converted = adapter(_uri_to_path(item["ref"].uri), node_dir, self.tenant_id)
            return converted
        builder = _PAYLOAD_BUILDERS.get(schema_ref)
        if builder is not None:
            return builder(_ref_to_artifact_input(item["ref"]))
        return {"artifact": item["ref"].as_payload()}

    def _binding_report(self, bindings: dict[str, list[dict[str, Any]]]) -> list[dict[str, object]]:
        report: list[dict[str, object]] = []
        for port_id in sorted(bindings):
            for item in bindings[port_id]:
                report.append(
                    {
                        "port_id": port_id,
                        "source_instance": item["source_instance"],
                        "source_port": item["source_port"],
                        "sha256": item["ref"].sha256,
                        "uri": item["ref"].uri,
                        "adapter": item["adapter"],
                    }
                )
        return report


def _ref_to_artifact_input(ref: ArtifactRef) -> ArtifactInput:
    return ArtifactInput(
        artifact_id=UUID(int=0),
        tenant_id=UUID(int=0),
        uri=ref.uri,
        media_type="application/json",
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        classification=ref.classification,
    )
