"""M5 isolated execution: ISO gate -> materialize -> per-node child -> ledger.

The isolated branch honors M4's fail-closed head gate (any denied or
not-yet-approved intent blocks the whole chain) and then adds three hard
assertions per node before a single child process may start:

1. the node capability maps to a verified built-in plugin (the isolated
   ``python -I`` child only ever runs a project-controlled read-only runtime);
2. the intent projects ``read_only`` side effects and ``isolated_subprocess``
   isolation;
3. the tenant policy freshly re-grants ``topology.chain.execute`` **and**
   ``topology.chain.execute.isolated`` for this execution (fail-closed by
   default: the 0041 seed policy is inactive).

Inputs come only from (a) db_artifacts materialization, (b) a pre-verified
governed ``InputSource`` whose file sha256/size are re-checked in the parent,
or (c) the previous node's materialized output artifact adapted by a bridge
materializer.  Outputs are written as immutable artifacts and the ledger row
records refs + SHA256, never a payload body.  A failed node writes a
``failed`` ledger row (with its trace) and stops the rest of the chain; fails
are never silently skipped or downgraded to ``simulated``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from psycopg2.extras import Json

from packages.plugin_runtime.db_artifacts import (
    materialize_alerts,
    materialize_incidents,
    materialize_semantic_document,
    materialize_topology,
)
from packages.plugin_runtime.runner import (
    ArtifactInput,
    IsolatedPluginRuntime,
    PluginInvocation,
    PluginPolicyDenied,
    PluginRuntimeError,
    load_verified_binding,
    resolve_file_uri,
    verified_builtin_ids,
)
from packages.policy.engine import PolicyEngine

from .compiler import validate_identifier
from .contracts import validate_ledger
from .executor import gate_blockers

ISOLATED_MODE = "isolated"
_READ_ONLY = "read_only"
_WRITE_DATA = "write_data"
_ISOLATION = "isolated_subprocess"

_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_BRIDGE_SOURCE_BYTES = 64 * 1024 * 1024
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

_ISO_POLICY_CAPABILITIES = ("topology.chain.execute", "topology.chain.execute.isolated")


class IsolatedGateError(PermissionError):
    """The ISO gate (or governed-input resolution) denied the run.

    Raising this means either no child process was started yet, or the failing
    node and every later node never started a child process.
    """


@dataclass(frozen=True, slots=True)
class InputSource:
    """One governed input artifact plus the full child payload for a capability.

    The payload's artifact reference must already match the artifact file on
    disk; :func:`verify_input_source` re-checks every lock field in the parent
    so a stale or tampered artifact can never reach a child process.
    """

    capability: str
    artifact: ArtifactInput
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("input source must declare a capability")
        if not isinstance(self.payload, dict) or not self.payload:
            raise ValueError("input source must declare a child payload")


def verify_input_source(source: InputSource, *, roots: tuple[Path, ...]) -> None:
    """Re-verify a governed input artifact inside the declared read roots."""
    if not roots:
        raise IsolatedGateError("no declared read roots for input verification")
    try:
        resolved = resolve_file_uri(source.artifact.uri).resolve()
    except (OSError, ValueError) as exc:
        raise IsolatedGateError(f"input artifact is not a local file URI: {source.capability}") from exc
    if not any(resolved.is_relative_to(root.resolve()) for root in roots):
        raise IsolatedGateError(f"input artifact is outside declared read roots: {source.capability}")
    if not resolved.is_file():
        raise IsolatedGateError(f"input artifact is unavailable: {source.capability}")
    content = resolved.read_bytes()
    if len(content) != source.artifact.size_bytes:
        raise IsolatedGateError(f"input artifact size does not match locked value: {source.capability}")
    actual = hashlib.sha256(content).hexdigest()
    if actual != source.artifact.sha256.lower():
        raise IsolatedGateError(f"input artifact sha256 does not match locked value: {source.capability}")


_CAPABILITY_TO_PLUGIN: dict[str, str] | None = None


def _verified_capability_plugins() -> dict[str, str]:
    """capability -> plugin_id derived from the verified built-in allow list."""
    global _CAPABILITY_TO_PLUGIN  # noqa: PLW0603 - module-level lazy cache is import-safe
    if _CAPABILITY_TO_PLUGIN is None:
        mapping: dict[str, str] = {}
        for plugin_id in verified_builtin_ids():
            binding = load_verified_binding(plugin_id)
            capability = binding.capability
            previous = mapping.setdefault(capability, plugin_id)
            if previous != plugin_id:
                raise RuntimeError("duplicate verified capability across built-in plugins")
        _CAPABILITY_TO_PLUGIN = mapping
    return _CAPABILITY_TO_PLUGIN


def verified_plugin_for(capability: str) -> str:
    """Resolve a capability to its verified built-in plugin id (fail-closed)."""
    plugin_id = _verified_capability_plugins().get(capability)
    if plugin_id is None:
        raise IsolatedGateError(f"capability is not a verified built-in plugin: {capability}")
    return plugin_id


def _alerts_payload(connection: Any, *, tenant_id: UUID, out_dir: Path) -> dict[str, Any]:
    materialized = materialize_alerts(connection, tenant_id=tenant_id, out_dir=out_dir)
    return {"alerts": {"artifact": materialized.artifact.as_payload()}}


def _rca_payload(connection: Any, *, tenant_id: UUID, out_dir: Path) -> dict[str, Any]:
    incidents = materialize_incidents(connection, tenant_id=tenant_id, out_dir=out_dir)
    topology = materialize_topology(connection, tenant_id=tenant_id, out_dir=out_dir)
    return {
        "rca": {
            "incident_set": incidents.artifact.as_payload(),
            "topology_graph": topology.artifact.as_payload(),
            "max_candidates": 20,
        }
    }


def _document_payload(connection: Any, *, tenant_id: UUID, out_dir: Path) -> dict[str, Any]:
    document = materialize_semantic_document(connection, tenant_id=tenant_id, out_dir=out_dir)
    return {"document": {"artifact": document.artifact.as_payload()}}


# db_artifacts -> plugin envelope builders for capabilities whose inputs are
# fully materializable inside the governed test database.
_DB_INPUT_BUILDERS: dict[str, Callable[..., dict[str, Any]]] = {
    "aiops.alert.correlate": _alerts_payload,
    "aiops.rca.rank": _rca_payload,
    "knowledge.extract.relations": _document_payload,
}


def build_research_note_payload_from_ledger(
    producer_artifact: Path, node_dir: Path, tenant_id: UUID
) -> dict[str, Any]:
    """Bridge adapter: ``audit-quality-candidates`` -> ``research_note.evaluation``.

    Reads the producer's materialized output artifact (integrity-checked),
    deterministically derives an ``experiment-evaluation`` (status ``proposed``)
    draft and writes it as a new artifact whose sha256/size become the consumer
    child's locked input reference.  Nothing here executes a plugin.
    """
    del tenant_id  # artifact refs and file permissions are already tenant-bound
    content = producer_artifact.read_bytes()
    if len(content) > _MAX_BRIDGE_SOURCE_BYTES:
        raise IsolatedGateError("bridge producer artifact exceeds the read budget")
    try:
        output = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IsolatedGateError("bridge producer output is not UTF-8 JSON") from exc
    if not isinstance(output, dict):
        raise IsolatedGateError("bridge producer output must be an object")
    ledger_sha256 = str(output.get("ledger_sha256") or "").lower()
    if not ledger_sha256:
        raise IsolatedGateError("bridge producer output lacks a ledger integrity reference")
    summary_raw = output.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    candidates_raw = output.get("candidates")
    candidates = candidates_raw if isinstance(candidates_raw, list) else []
    candidate_count = len(candidates)
    high_count = sum(
        1
        for item in candidates
        if isinstance(item, dict) and str(item.get("severity") or "") == "high"
    )
    truncated = bool(summary.get("truncated"))
    overall = (
        "fragile" if (high_count > 0 or truncated) else ("moderate" if candidate_count > 0 else "stable")
    )
    base_score = max(0.35, min(0.95, round(0.95 - 0.04 * candidate_count, 2)))
    score = max(
        0.25,
        min(0.99, base_score - {"fragile": 0.15, "moderate": 0.05, "stable": 0.0}[overall]),
    )
    recommendation = (
        "reject" if overall == "fragile" else ("hold" if overall == "moderate" else "promote")
    )
    rationale = (
        f"账本质量候选 {candidate_count} 项（高严重度 {high_count} 项，截断 {truncated}），"
        f"稳健性 {overall}；由桥接只读适配生成实验评估草稿。"
    )
    drift_count = int(summary.get("duplicate_rows") or 0) + int(summary.get("out_of_period_rows") or 0) + int(
        summary.get("unbalanced_entries") or 0
    )
    evaluation = {
        "contract_id": "experiment-evaluation",
        "contract_version": "1.0.0",
        "status": "proposed",
        "experiment_id": f"experiment-{ledger_sha256[:12]}",
        "strategy_key": "audit-ledger-quality",
        "strategy_version": "1.0.0",
        "robustness": {"overall": overall, "score": score},
        "promotion": {"recommendation": recommendation, "rationale": rationale},
        "drift": {"drift_count": drift_count},
    }
    raw = json.dumps(evaluation, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ref_hash = hashlib.sha256(raw).hexdigest()
    ref_path = node_dir / f"bridge-input-evaluation-{ref_hash[:16]}.json"
    ref_path.write_bytes(raw)
    return {
        "research_note": {
            "evaluation": {
                "uri": ref_path.resolve().as_uri(),
                "sha256": ref_hash,
                "size_bytes": len(raw),
            }
        }
    }


DEFAULT_BRIDGE_MATERIALIZERS: dict[str, Callable[..., dict[str, Any]]] = {
    "quant.research-note.draft": build_research_note_payload_from_ledger,
}


def _node_allow_rule(capability: str) -> dict[str, Any]:
    return {
        "rule_id": f"verified-builtin:{capability}",
        "effect": "allow",
        "match": {
            "capabilities": [capability],
            "risk_classes": ["read_only"],
            "side_effects": ["read_only"],
        },
    }


class IsolatedChainExecutor:
    """Per-node isolated subprocess execution behind the ISO gate (M5)."""

    def __init__(
        self,
        *,
        chain_key: str,
        chain_id: UUID,
        tenant_id: UUID,
        trace_id: str,
        planner_version: str,
        chain_checksum: str,
        policy: PolicyEngine,
        nodes: list[dict[str, Any]],
        intents: list[dict[str, Any]],
        port_bindings: list[dict[str, Any]],
        runtime: IsolatedPluginRuntime,
        staging_root: Path,
        idempotency_key: str = "",
        input_sources: dict[str, InputSource] | None = None,
        bridge_materializers: dict[str, Callable[..., dict[str, Any]]] | None = None,
        capability_plugins: dict[str, str] | None = None,
        run_id: UUID | None = None,  # M6: groups this run's ledger rows
    ) -> None:
        self.chain_key = chain_key
        self.chain_id = chain_id
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self.planner_version = planner_version
        self.chain_checksum = chain_checksum
        self.policy = policy
        self.nodes = nodes
        self.intents = intents
        self.port_bindings = port_bindings
        self.runtime = runtime
        self.staging_root = Path(staging_root)
        self.idempotency_key = idempotency_key
        self.input_sources = dict(input_sources or {})
        self.bridge_materializers = dict(bridge_materializers or DEFAULT_BRIDGE_MATERIALIZERS)
        self.capability_plugins = dict(capability_plugins or _verified_capability_plugins())
        self.intents_by_slot = {str(intent.get("slot_key") or ""): intent for intent in intents}
        self.input_roots = tuple(root.resolve() for root in (self.staging_root, *runtime.allowed_roots))
        self.run_id = run_id

    # -- ISO gate (fail-closed: no child starts until it passes) --------------

    def _node_dir(self, slot: str) -> Path:
        """Resolve a node's staging directory, contained under the root.

        ``chain_key`` and ``slot_key`` reach this method from the database and
        become path segments.  ``chain_key`` is normally a generated
        ``chain-<hex>`` token, but the executor must not depend on that: the
        resolved path is validated before anything is written under it.
        """
        validate_identifier(str(self.chain_key), field="chain_key")
        validate_identifier(slot, field="slot_key")
        root = self.staging_root.resolve()
        node_dir = (self.staging_root / "chains" / self.chain_key / slot).resolve()
        if not node_dir.is_relative_to(root):
            raise ValueError(
                f"refusing to write outside the staging root: {node_dir} is not under {root}"
            )
        return node_dir

    def iso_gate(self) -> None:
        blockers = gate_blockers(self.intents)
        if blockers:
            raise IsolatedGateError(f"chain execution fail-closed: {blockers}")
        for intent in self.intents:
            slot = str(intent.get("slot_key") or "")
            capability = str(intent.get("capability") or "")
            if capability not in self.capability_plugins:
                raise IsolatedGateError(f"capability is not a verified built-in plugin: {slot}")
            if str(intent.get("side_effects") or "") != _READ_ONLY:
                raise IsolatedGateError(f"intent is not read_only: {slot}")
            if str(intent.get("isolation") or "") != _ISOLATION:
                raise IsolatedGateError(f"intent is not isolated_subprocess: {slot}")
            for required in _ISO_POLICY_CAPABILITIES:
                decision = self.policy.evaluate(
                    required,
                    {"chain_key": self.chain_key, "slot_key": slot},
                    risk_class="medium",
                    side_effects=_WRITE_DATA,
                )
                if decision.decision != "allow":
                    raise IsolatedGateError(f"policy denied {required}: {decision.reason}")

    # -- per-node execution ---------------------------------------------------

    def execute(self, connection: Any, cur: Any) -> dict[str, Any]:
        """Run the ISO gate and then one child process per node in order.

        The session-level RLS context is established by the caller
        (``TopologyService.start_run`` / ``execute_chain_isolated``) before the
        first short-transaction commit, so ledger writes here stay tenant-scoped.
        """
        self.iso_gate()
        started = datetime.now(timezone.utc)
        entries: list[dict[str, Any]] = []
        previous_path: Path | None = None
        for node in sorted(self.nodes, key=lambda item: int(item.get("ordinal", 0))):
            slot = str(node.get("slot_key") or "")
            ordinal = int(node.get("ordinal") or 0)
            intent = self.intents_by_slot.get(slot) or {}
            capability = str(intent.get("capability") or "")
            output_refs = [str(item) for item in (node.get("expected_output") or []) if str(item) != ""]
            output_contract = output_refs[0] if output_refs else ""
            declared_input_refs = self._declared_input_refs().get(slot, [])
            node_dir = self._node_dir(slot)
            entry, output_path = self._run_one(
                connection=connection,
                node_dir=node_dir,
                slot=slot,
                ordinal=ordinal,
                capability=capability,
                declared_input_refs=declared_input_refs,
                output_refs=output_refs,
                output_contract=output_contract,
                started_at=started,
                previous_path=previous_path,
            )
            entries.append(entry)
            self._insert_ledger(
                cur,
                entry=entry,
                chain_id=self.chain_id,
                tenant_id=self.tenant_id,
                slot=slot,
                idempotency_key=f"{self.idempotency_key}:{slot}",
                run_id=self.run_id,
            )
            # CW0 short transactions: every node's ledger row is committed
            # immediately so a crash later in the chain can never roll back
            # evidence that was already written.
            if connection is not None:
                connection.commit()
            if output_path is not None:
                previous_path = output_path
            else:
                break  # fail-closed: a failed node terminates every later node
        overall = "succeeded" if entries and all(item["status"] == "succeeded" for item in entries) else "failed"
        return {
            "chain_key": self.chain_key,
            "mode": ISOLATED_MODE,
            "status": overall,
            "planner_version": self.planner_version,
            "chain_checksum": self.chain_checksum,
            "node_count": len(entries),
            "entries": entries,
            "idempotent": False,
        }

    def _run_one(
        self,
        *,
        connection: Any,
        node_dir: Path,
        slot: str,
        ordinal: int,
        capability: str,
        declared_input_refs: list[str],
        output_refs: list[str],
        output_contract: str,
        started_at: datetime,
        previous_path: Path | None,
    ) -> tuple[dict[str, Any], Path | None]:
        node_dir.mkdir(parents=True, exist_ok=True)
        started_at_text = started_at.isoformat()
        try:
            payload = self._resolve_payload(connection, node_dir, slot, capability, previous_path)
            plugin_id = verified_plugin_for(capability)
            invocation = PluginInvocation(
                tenant_id=self.tenant_id,
                trace_id=UUID(self.trace_id),
                idempotency_key=f"{self.idempotency_key}:{slot}",
                plugin_id=plugin_id,
                capability=capability,
                payload=payload,
            )
            node_policy = PolicyEngine(
                deny=list(self.policy.deny),
                rules=[*list(self.policy.rules), _node_allow_rule(capability)],
            )
            result = self.runtime.invoke(invocation, node_policy)
        except (IsolatedGateError, PluginPolicyDenied, PluginRuntimeError) as exc:
            reason = "no_governed_input_source" if isinstance(exc, IsolatedGateError) else (
                "policy_denied" if isinstance(exc, PluginPolicyDenied) else "plugin_runtime_error"
            )
            entry = self._failed_entry(
                slot=slot,
                ordinal=ordinal,
                input_refs=declared_input_refs,
                output_refs=output_refs,
                output_contract=output_contract,
                started_at=started_at_text,
                policy_ref=reason,
            )
            return entry, None
        raw = json.dumps(result.output, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(raw) > _MAX_OUTPUT_BYTES:
            entry = self._failed_entry(
                slot=slot,
                ordinal=ordinal,
                input_refs=declared_input_refs,
                output_refs=output_refs,
                output_contract=output_contract,
                started_at=started_at_text,
                policy_ref="plugin_runtime_error",
            )
            return entry, None
        output_sha = hashlib.sha256(raw).hexdigest()
        output_path = node_dir / f"output-{output_sha[:16]}.json"
        output_path.write_bytes(raw)
        entry = {
            "execution_id": self._execution_id(slot, ordinal),
            "chain_key": self.chain_key,
            "slot_key": slot,
            "ordinal": ordinal,
            "mode": ISOLATED_MODE,
            "input_refs": sorted(set(declared_input_refs)),
            "output_refs": sorted(set(output_refs)),
            "output_contract": output_contract,
            "output_checksum": output_sha,
            "status": "succeeded",
            "policy_ref": "policy_allowed",
            "trace_id": self.trace_id,
            "started_at": started_at_text,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "plugin_id": result.plugin_id,
            "plugin_version": result.plugin_version,
            "runtime_code_sha256": result.runtime_code_sha256,
            "input_sha256": result.input_sha256,
            "output_artifact_refs": [
                {
                    "uri": output_path.resolve().as_uri(),
                    "sha256": output_sha,
                    "media_type": "application/json",
                }
            ],
        }
        validate_ledger(entry)
        return entry, output_path

    def _resolve_payload(
        self,
        connection: Any,
        node_dir: Path,
        slot: str,
        capability: str,
        previous_path: Path | None,
    ) -> dict[str, Any]:
        is_bridge_consumer = any(
            str(binding.get("consumer_slot") or "") == slot
            and str(binding.get("bind_mode") or "") == "bridge_ref"
            for binding in self.port_bindings
        )
        if is_bridge_consumer and previous_path is not None:
            builder = self.bridge_materializers.get(capability)
            if builder is not None:
                return builder(previous_path, node_dir, self.tenant_id)
            raise IsolatedGateError(f"no bridge materializer for capability: {capability}")
        if capability in self.input_sources:
            source = self.input_sources[capability]
            verify_input_source(source, roots=self.input_roots)
            return source.payload
        builder = _DB_INPUT_BUILDERS.get(capability)
        if builder is not None:
            return builder(connection, tenant_id=self.tenant_id, out_dir=node_dir)
        raise IsolatedGateError(f"no governed input source for capability: {capability}")

    def _failed_entry(
        self,
        *,
        slot: str,
        ordinal: int,
        input_refs: list[str],
        output_refs: list[str],
        output_contract: str,
        started_at: str,
        policy_ref: str,
    ) -> dict[str, Any]:
        entry = {
            "execution_id": self._execution_id(slot, ordinal),
            "chain_key": self.chain_key,
            "slot_key": slot,
            "ordinal": ordinal,
            "mode": ISOLATED_MODE,
            "input_refs": sorted(set(input_refs)),
            "output_refs": sorted(set(output_refs)),
            "output_contract": output_contract,
            "output_checksum": _EMPTY_SHA256,
            "status": "failed",
            "policy_ref": policy_ref,
            "trace_id": self.trace_id,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        # Real-run metadata is intentionally omitted: a failed node never
        # started a child, so there is no plugin/input/checksum to record
        # (an empty string would violate the ledger's sha256 pattern).
        validate_ledger(entry)
        return entry

    def _execution_id(self, slot: str, ordinal: int) -> str:
        digest = hashlib.sha256(
            f"{self.chain_id}:{slot}:{ordinal}:{ISOLATED_MODE}".encode("utf-8")
        ).hexdigest()
        return "exec-" + digest[:16]

    def _declared_input_refs(self) -> dict[str, list[str]]:
        inputs: dict[str, list[str]] = {}
        for binding in self.port_bindings:
            consumer = str(binding.get("consumer_slot") or "")
            ref = str(binding.get("contract_ref") or "")
            if consumer:
                inputs.setdefault(consumer, []).append(ref)
        return inputs

    @staticmethod
    def _insert_ledger(
        cur: Any,
        *,
        entry: dict[str, Any],
        chain_id: UUID,
        tenant_id: UUID,
        slot: str,
        idempotency_key: str,
        run_id: UUID | None = None,
    ) -> None:
        cur.execute(
            """INSERT INTO topology.execution_ledger
            (tenant_id, chain_id, plan_node_slot_key, ordinal, mode, input_refs, output_refs,
             output_contract, output_checksum, status, policy_ref, idempotency_key, trace_id,
             started_at, finished_at, plugin_id, plugin_version, runtime_code_sha256, input_sha256,
             output_artifact_refs, run_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                str(tenant_id),
                str(chain_id),
                slot,
                int(entry["ordinal"]),
                ISOLATED_MODE,
                Json(entry.get("input_refs") or []),
                Json(entry.get("output_refs") or []),
                entry.get("output_contract") or "",
                entry["output_checksum"],
                entry["status"],
                entry.get("policy_ref") or "",
                idempotency_key,
                entry["trace_id"],
                entry.get("started_at") or "",
                entry.get("finished_at") or None,
                entry.get("plugin_id") or "",
                entry.get("plugin_version") or "",
                entry.get("runtime_code_sha256") or "",
                entry.get("input_sha256") or "",
                Json(entry.get("output_artifact_refs") or []),
                str(run_id) if run_id is not None else None,
            ),
        )