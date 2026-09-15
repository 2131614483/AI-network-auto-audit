"""M1 service payload models and jsonschema validation.

The four service schemas in ``contracts/jsonschema`` are the single source of
truth; these thin dataclasses keep payloads immutable once validated and let
the service layer raise clear errors before touching the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import referencing
from jsonschema import Draft202012Validator, FormatChecker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "contracts" / "jsonschema"

# Draft202012Validator skips ``format`` keywords unless a checker is supplied;
# ``uuid`` is not in the default checker set, so resolve it explicitly.
_format_checker = FormatChecker()


@_format_checker.checks("uuid")
def _check_uuid(value: object) -> bool:
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True

_SERVICE_SCHEMAS = {
    "upsert": "topology-upsert.schema.json",
    "release": "topology-release.schema.json",
    "recycle": "topology-recycle.schema.json",
    "plan": "topology-plan.schema.json",
    "chain": "topology-chain-request.schema.json",
    "approval": "topology-approval.schema.json",
    "execute": "chain-execution-request.schema.json",
    "ledger": "execution-ledger.schema.json",
    "run_request": "chain-run-request.schema.json",
    "run": "execution-run.schema.json",
    "verification_request": "run-verification-request.schema.json",
    "verification": "run-verification.schema.json",
    "proposal_request": "remediation-proposal-request.schema.json",
    "proposal": "topology-remediation-proposal.schema.json",
    "evidence_anchor_request": "topology-evidence-anchor-request.schema.json",
    "evidence_anchor": "topology-evidence-anchor.schema.json",
    "evidence_proof": "topology-evidence-proof.schema.json",
    "evidence_export": "topology-evidence-export.schema.json",
    "planning_intent": "topology-planning-intent.schema.json",
}

_validators: dict[str, Draft202012Validator] = {}


def _validator(name: str) -> Draft202012Validator:
    if name not in _validators:
        schema = json.loads((SCHEMA_DIR / _SERVICE_SCHEMAS[name]).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        _validators[name] = Draft202012Validator(schema, format_checker=_format_checker)
    return _validators[name]


def _first_message(schema_name: str, payload: dict[str, Any]) -> str | None:
    errors = sorted(_validator(schema_name).iter_errors(payload), key=lambda error: list(error.path))
    return errors[0].message if errors else None


@dataclass(frozen=True, slots=True)
class TopologyUpsert:
    """One registration/update for a cluster, blueprint, membership, edge or contract."""

    kind: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: UUID | None = None
    created_by: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "TopologyUpsert":
        message = _first_message("upsert", raw)
        if message is not None:
            raise ValueError(f"invalid topology upsert: {message}")
        payload = dict(raw.get("payload") or {})
        trace = raw.get("trace_id")
        created_by = raw.get("created_by")
        return cls(
            kind=str(raw["kind"]),
            idempotency_key=str(raw["idempotency_key"]),
            payload=payload,
            trace_id=UUID(trace) if trace else None,
            created_by=UUID(created_by) if created_by else None,
        )


@dataclass(frozen=True, slots=True)
class TopologyRelease:
    """Publish or rollback a frozen topology catalog."""

    action: str
    idempotency_key: str
    version: str | None = None
    catalog_checksum: str | None = None
    cluster_keys: tuple[str, ...] = ()
    blueprint_keys: tuple[str, ...] = ()
    rollback_from_version: str | None = None
    reason: str = ""
    trace_id: UUID | None = None
    created_by: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "TopologyRelease":
        message = _first_message("release", raw)
        if message is not None:
            raise ValueError(f"invalid topology release: {message}")
        trace = raw.get("trace_id")
        created_by = raw.get("created_by")
        return cls(
            action=str(raw["action"]),
            idempotency_key=str(raw["idempotency_key"]),
            version=raw.get("version"),
            catalog_checksum=raw.get("catalog_checksum"),
            cluster_keys=tuple(raw.get("cluster_keys") or ()),
            blueprint_keys=tuple(raw.get("blueprint_keys") or ()),
            rollback_from_version=raw.get("rollback_from_version"),
            reason=str(raw.get("reason") or ""),
            trace_id=UUID(trace) if trace else None,
            created_by=UUID(created_by) if created_by else None,
        )


@dataclass(frozen=True, slots=True)
class TopologyRecycle:
    """Soft-delete or restore a topology entity (evidence is never hard-deleted)."""

    recycle_type: str
    entity_kind: str
    entity_key: str
    idempotency_key: str
    restored_from_recycle_id: UUID | None = None
    reason: str = ""
    trace_id: UUID | None = None
    created_by: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "TopologyRecycle":
        message = _first_message("recycle", raw)
        if message is not None:
            raise ValueError(f"invalid topology recycle: {message}")
        trace = raw.get("trace_id")
        created_by = raw.get("created_by")
        restored_from = raw.get("restored_from_recycle_id")
        return cls(
            recycle_type=str(raw["recycle_type"]),
            entity_kind=str(raw["entity_kind"]),
            entity_key=str(raw["entity_key"]),
            idempotency_key=str(raw["idempotency_key"]),
            restored_from_recycle_id=UUID(restored_from) if restored_from else None,
            reason=str(raw.get("reason") or ""),
            trace_id=UUID(trace) if trace else None,
            created_by=UUID(created_by) if created_by else None,
        )


@dataclass(frozen=True, slots=True)
class TopologyPlan:
    """plan_only routing request: intent, capability requirements and budget."""

    intent: str
    idempotency_key: str
    mode: str
    capability_requirements: tuple[str, ...]
    budget: dict[str, int]
    release_lock: dict[str, str] | None = None
    planner_version: str | None = None
    trace_id: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "TopologyPlan":
        message = _first_message("plan", raw)
        if message is not None:
            raise ValueError(f"invalid topology plan: {message}")
        trace = raw.get("trace_id")
        return cls(
            intent=str(raw["intent"]),
            idempotency_key=str(raw["idempotency_key"]),
            mode=str(raw["mode"]),
            capability_requirements=tuple(raw["capability_requirements"]),
            budget=dict(raw["budget"]),
            release_lock=dict(raw["release_lock"]) if raw.get("release_lock") else None,
            planner_version=raw.get("planner_version"),
            trace_id=UUID(trace) if trace else None,
        )


@dataclass(frozen=True, slots=True)
class ChainMaterialize:
    """Materialize one invocation chain from a plan_only routing plan."""

    plan_key: str
    idempotency_key: str
    reason: str = ""

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "ChainMaterialize":
        message = _first_message("chain", raw)
        if message is not None:
            raise ValueError(f"invalid topology chain request: {message}")
        return cls(
            plan_key=str(raw["plan_key"]),
            idempotency_key=str(raw["idempotency_key"]),
            reason=str(raw.get("reason") or ""),
        )


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Human approve/reject of one requires_approval intent node (M4)."""

    chain_key: str
    slot_key: str
    decision: str
    idempotency_key: str
    reason: str = ""
    trace_id: UUID | None = None
    created_by: str | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "ApprovalRequest":
        # ``trace_id`` / ``created_by`` are service-internal metadata bound to the
        # request context (tenant identity); the public schema keeps rejecting any
        # client-supplied payload body beyond the four decision fields.
        public = {k: v for k, v in raw.items() if k not in {"trace_id", "created_by"}}
        message = _first_message("approval", public)
        if message is not None:
            raise ValueError(f"invalid topology approval: {message}")
        trace = raw.get("trace_id")
        created_by = raw.get("created_by")
        return cls(
            chain_key=str(raw["chain_key"]),
            slot_key=str(raw["slot_key"]),
            decision=str(raw["decision"]),
            idempotency_key=str(raw["idempotency_key"]),
            reason=str(raw.get("reason") or ""),
            trace_id=UUID(trace) if trace else None,
            created_by=str(created_by) if created_by else None,
        )


@dataclass(frozen=True, slots=True)
class ChainExecute:
    """Whole-chain shadow execution request (M4); mode is always simulated."""

    chain_key: str
    mode: str
    idempotency_key: str
    reason: str = ""
    trace_id: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "ChainExecute":
        message = _first_message("execute", raw)
        if message is not None:
            raise ValueError(f"invalid chain execution request: {message}")
        trace = raw.get("trace_id")
        return cls(
            chain_key=str(raw["chain_key"]),
            mode=str(raw["mode"]),
            idempotency_key=str(raw["idempotency_key"]),
            reason=str(raw.get("reason") or ""),
            trace_id=UUID(trace) if trace else None,
        )


@dataclass(frozen=True, slots=True)
class ChainRunRequest:
    """Chain-level execution run request (M6): one isolated read-only run."""

    chain_key: str
    mode: str
    reason: str
    idempotency_key: str
    trace_id: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "ChainRunRequest":
        message = _first_message("run_request", raw)
        if message is not None:
            raise ValueError(f"invalid chain run request: {message}")
        trace = raw.get("trace_id")
        return cls(
            chain_key=str(raw["chain_key"]),
            mode=str(raw["mode"]),
            reason=str(raw["reason"]),
            idempotency_key=str(raw["idempotency_key"]),
            trace_id=UUID(trace) if trace else None,
        )


@dataclass(frozen=True, slots=True)
class RunVerificationRequest:
    """Run verification request (M7): compare one success run to a reference run.

    The comparator is pure database reads (zero child processes): every node's
    ``output_checksum`` under ``run_id`` is matched against the same slot of the
    reference run.  ``reference_run_id`` may be omitted - the latest success
    run of the same chain is used as the baseline.
    """

    run_id: UUID
    reference_run_id: UUID | None
    reason: str
    idempotency_key: str
    trace_id: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "RunVerificationRequest":
        message = _first_message("verification_request", raw)
        if message is not None:
            raise ValueError(f"invalid run verification request: {message}")
        reference = raw.get("reference_run_id")
        run_id = UUID(str(raw["run_id"]))
        reference_id = UUID(str(reference)) if reference else None
        if reference_id is not None and reference_id == run_id:
            raise ValueError("reference_run_id must differ from run_id")
        trace = raw.get("trace_id")
        return cls(
            run_id=run_id,
            reference_run_id=reference_id,
            reason=str(raw["reason"]),
            idempotency_key=str(raw["idempotency_key"]),
            trace_id=UUID(trace) if trace else None,
        )


def validate_upsert(raw: dict[str, Any]) -> None:
    """Validate an upsert payload without constructing a model."""
    message = _first_message("upsert", raw)
    if message is not None:
        raise ValueError(f"invalid topology upsert: {message}")


def validate_release(raw: dict[str, Any]) -> None:
    message = _first_message("release", raw)
    if message is not None:
        raise ValueError(f"invalid topology release: {message}")


def validate_recycle(raw: dict[str, Any]) -> None:
    message = _first_message("recycle", raw)
    if message is not None:
        raise ValueError(f"invalid topology recycle: {message}")


def validate_plan(raw: dict[str, Any]) -> None:
    message = _first_message("plan", raw)
    if message is not None:
        raise ValueError(f"invalid topology plan: {message}")


def validate_chain(raw: dict[str, Any]) -> None:
    message = _first_message("chain", raw)
    if message is not None:
        raise ValueError(f"invalid topology chain request: {message}")


def validate_approval(raw: dict[str, Any]) -> None:
    message = _first_message("approval", raw)
    if message is not None:
        raise ValueError(f"invalid topology approval: {message}")


def validate_execute(raw: dict[str, Any]) -> None:
    message = _first_message("execute", raw)
    if message is not None:
        raise ValueError(f"invalid chain execution request: {message}")


def validate_ledger(raw: dict[str, Any]) -> None:
    """Validate an execution-ledger projection (used for service outputs)."""
    message = _first_message("ledger", raw)
    if message is not None:
        raise ValueError(f"invalid execution ledger entry: {message}")


def validate_run_request(raw: dict[str, Any]) -> None:
    message = _first_message("run_request", raw)
    if message is not None:
        raise ValueError(f"invalid chain run request: {message}")


def validate_run(raw: dict[str, Any]) -> None:
    """Validate an execution-run projection (used for service outputs)."""
    message = _first_message("run", raw)
    if message is not None:
        raise ValueError(f"invalid execution run: {message}")


def validate_verification_request(raw: dict[str, Any]) -> None:
    message = _first_message("verification_request", raw)
    if message is not None:
        raise ValueError(f"invalid run verification request: {message}")


def validate_verification(raw: dict[str, Any]) -> None:
    """Validate a run-verification projection and enforce count conservation.

    JSON Schema cannot express arithmetic, so the node count invariant
    ``matched + mismatched + ref_missing == node_total`` is checked here.
    """
    message = _first_message("verification", raw)
    if message is not None:
        raise ValueError(f"invalid run verification: {message}")
    counts = (
        int(raw["node_matched"]) + int(raw["node_mismatched"]) + int(raw["node_ref_missing"])
    )
    if counts != int(raw["node_total"]):
        raise ValueError(
            f"run verification node counts not conserved: "
            f"{raw['node_matched']}+{raw['node_mismatched']}+{raw['node_ref_missing']}"
            f" != {raw['node_total']}"
        )


@dataclass(frozen=True, slots=True)
class RemediationProposalRequest:
    """Remediation proposal request (M8): act on a drifted verification.

    The proposal is pure evidence projection (zero child processes): it records
    the recommended maintenance action (inherited from or overriding the M7
    rollback verdict) so a governed re-run can be approved and dispatched later
    through the existing M6 run machinery.
    """

    verification_id: UUID
    action: str
    reason: str
    idempotency_key: str
    trace_id: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "RemediationProposalRequest":
        message = _first_message("proposal_request", raw)
        if message is not None:
            raise ValueError(f"invalid remediation proposal request: {message}")
        trace = raw.get("trace_id")
        return cls(
            verification_id=UUID(str(raw["verification_id"])),
            action=str(raw["action"]),
            reason=str(raw["reason"]),
            idempotency_key=str(raw["idempotency_key"]),
            trace_id=UUID(trace) if trace else None,
        )


def validate_proposal_request(raw: dict[str, Any]) -> None:
    message = _first_message("proposal_request", raw)
    if message is not None:
        raise ValueError(f"invalid remediation proposal request: {message}")


def validate_proposal(raw: dict[str, Any]) -> None:
    """Validate a remediation-proposal projection (used for service outputs)."""
    message = _first_message("proposal", raw)
    if message is not None:
        raise ValueError(f"invalid remediation proposal: {message}")


@dataclass(frozen=True, slots=True)
class EvidenceAnchorRequest:
    """Evidence chain anchoring request (M9): anchor M1-M8 evidence ledgers.

    One explicit, user-triggered anchor appends a batch of row-hash anchors to
    the tenant evidence chain.  Scope selects the evidence-family table set
    (``full`` for all of them); the tenant comes from the request header, never
    from the request body.  Replaying the same idempotency key returns the
    existing anchor batch.
    """

    scope: str
    idempotency_key: str
    trace_id: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "EvidenceAnchorRequest":
        public = {k: v for k, v in raw.items() if k not in {"trace_id"}}
        message = _first_message("evidence_anchor_request", public)
        if message is not None:
            raise ValueError(f"invalid evidence anchor request: {message}")
        trace = raw.get("trace_id")
        return cls(
            scope=str(raw["scope"]),
            idempotency_key=str(raw["idempotency_key"]),
            trace_id=UUID(trace) if trace else None,
        )


def validate_evidence_anchor_request(raw: dict[str, Any]) -> None:
    message = _first_message("evidence_anchor_request", raw)
    if message is not None:
        raise ValueError(f"invalid evidence anchor request: {message}")


def validate_evidence_anchor(raw: dict[str, Any]) -> None:
    """Validate an evidence-anchor projection (used for service outputs)."""
    message = _first_message("evidence_anchor", raw)
    if message is not None:
        raise ValueError(f"invalid evidence anchor: {message}")


def validate_evidence_proof(raw: dict[str, Any]) -> None:
    """Validate an evidence-chain consistency proof."""
    message = _first_message("evidence_proof", raw)
    if message is not None:
        raise ValueError(f"invalid evidence proof: {message}")


def validate_evidence_export(raw: dict[str, Any]) -> None:
    """Validate a read-only evidence export."""
    message = _first_message("evidence_export", raw)
    if message is not None:
        raise ValueError(f"invalid evidence export: {message}")


@dataclass(frozen=True, slots=True)
class PlanningIntentRequest:
    """Graph-driven planning intent (M10): natural-language intent -> plan_only plan.

    The intent text is matched deterministically against the L2 capability /
    L3 domain spaces (pg_trgm + bounded graph traversal, zero LLM/network), the
    matched graph nodes are resolved back to blueprint capability tokens via
    ``topology.blueprint_graph_links``, and the resulting plan is generated by
    the existing ``TopologyService.plan()`` machinery.  ``budget.expand_hops``
    is 0 (no expansion) or 1 (one-hop bridge/internal expansion).
    """

    intent: str
    budget: dict[str, int]
    idempotency_key: str
    reason: str
    trace_id: UUID | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "PlanningIntentRequest":
        public = {k: v for k, v in raw.items() if k not in {"trace_id"}}
        message = _first_message("planning_intent", public)
        if message is not None:
            raise ValueError(f"invalid planning intent: {message}")
        trace = raw.get("trace_id")
        return cls(
            intent=str(raw["intent"]),
            budget=dict(raw["budget"]),
            idempotency_key=str(raw["idempotency_key"]),
            reason=str(raw["reason"]),
            trace_id=UUID(trace) if trace else None,
        )


def validate_planning_intent(raw: dict[str, Any]) -> None:
    message = _first_message("planning_intent", raw)
    if message is not None:
        raise ValueError(f"invalid planning intent: {message}")


def validate_planning_intent_result(raw: dict[str, Any]) -> None:
    """Validate a planning-intent projection against the schema's $defs.

    The request root and the append-only result projection share one schema
    file; the result is validated by resolving ``$defs/planningIntentResult``
    so the request payload and the evidence row stay contractually aligned.
    """
    schema = json.loads((SCHEMA_DIR / _SERVICE_SCHEMAS["planning_intent"]).read_text(encoding="utf-8"))
    registry = referencing.Registry().with_resource(
        str(schema["$id"]), referencing.Resource.from_contents(schema)
    )
    validator = Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/planningIntentResult"},
        registry=registry,
        format_checker=_format_checker,
    )
    errors = sorted(validator.iter_errors(raw), key=lambda error: list(error.path))
    if errors:
        raise ValueError(f"invalid planning intent result: {errors[0].message}")
