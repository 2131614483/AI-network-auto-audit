"""Closed contracts for Phase 10; these objects confer no execution authority."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SourceKind = Literal["graph", "history", "knowledge"]
SourceStatus = Literal["available", "empty", "denied", "degraded", "error"]


def fingerprint(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanningBudget(ClosedModel):
    max_rounds: int = Field(default=4, ge=1, le=8, strict=True)
    max_queries: int = Field(default=18, ge=3, le=48, strict=True)
    max_model_calls: int = Field(default=8, ge=2, le=16, strict=True)
    max_nodes: int = Field(default=16, ge=1, le=32, strict=True)
    max_evidence_chars: int = Field(default=24000, ge=1000, le=64000, strict=True)
    max_seconds: int = Field(default=180, ge=1, le=600, strict=True)


class GoalRequirement(ClosedModel):
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra={
        "anyOf": [
            {"required": ["capabilities"], "properties": {"capabilities": {"minItems": 1}}},
            {"required": ["output_ports"], "properties": {"output_ports": {"minItems": 1}}},
        ],
    })
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = Field(min_length=1, max_length=500)
    capabilities: list[str] = Field(default_factory=list, max_length=16)
    output_ports: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def objective_is_checkable(self) -> GoalRequirement:
        if not self.capabilities and not self.output_ports:
            raise ValueError("a requirement needs capabilities or output_ports")
        return self


class PlanningInput(ClosedModel):
    port_id: str = Field(min_length=1, max_length=128)
    schema_ref: str = Field(min_length=1, max_length=160)
    artifact_id: UUID


class PlanningRequest(ClosedModel):
    mode: Literal["plan_only"] = "plan_only"
    goal: str = Field(min_length=4, max_length=2000)
    domain: Literal["audit", "aiops", "knowledge", "quant"] = "audit"
    requirements: list[GoalRequirement] = Field(min_length=1, max_length=16)
    inputs: list[PlanningInput] = Field(default_factory=list, max_length=32)
    budget: PlanningBudget = Field(default_factory=PlanningBudget)
    idempotency_key: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_keys(self) -> PlanningRequest:
        keys = [item.key for item in self.requirements]
        ports = [item.port_id for item in self.inputs]
        if len(set(keys)) != len(keys) or len(set(ports)) != len(ports):
            raise ValueError("requirement keys and input port ids must be unique")
        return self


class PlanningContext(ClosedModel):
    tenant_id: UUID
    trace_id: UUID
    session_id: UUID
    round_no: int = Field(default=1, ge=1, le=8)


class SourceQuery(ClosedModel):
    source_kind: SourceKind
    query: str = Field(min_length=1, max_length=500)
    purpose: str = Field(min_length=1, max_length=500)
    capabilities: list[str] = Field(default_factory=list, max_length=16)
    limit: int = Field(default=6, ge=1, le=8, strict=True)


class EvidenceItem(ClosedModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    tenant_id: UUID
    source_kind: SourceKind
    source_ref: str = Field(min_length=1, max_length=500)
    version: str = Field(min_length=1, max_length=160)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(max_length=8000)
    facts: dict[str, Any] = Field(default_factory=dict)
    origin_refs: list[str] = Field(min_length=1, max_length=32)
    authority: Literal["declared", "observed", "published", "reference", "suggested"] = "reference"


class SourceResult(ClosedModel):
    source_kind: SourceKind
    status: SourceStatus
    items: list[EvidenceItem] = Field(default_factory=list, max_length=8)
    detail: str = Field(default="", max_length=1000)


AuditText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
AuditLongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class AuditProcedure(ClosedModel):
    procedure_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    requirement_keys: list[str] = Field(max_length=16)
    objective: AuditText
    method: AuditText
    required_evidence: list[AuditText] = Field(min_length=1, max_length=8)
    acceptance_criteria: list[AuditText] = Field(min_length=1, max_length=8)
    knowledge_evidence_ids: list[str] = Field(min_length=1, max_length=16)


class AuditTaskBrief(ClosedModel):
    goal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    knowledge_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    objective: AuditLongText
    scope: AuditLongText
    risk_hypotheses: list[AuditText] = Field(min_length=1, max_length=16)
    procedures: list[AuditProcedure] = Field(max_length=16)
    assumptions: list[AuditText] = Field(default_factory=list, max_length=16)
    blockers: list[AuditText] = Field(default_factory=list, max_length=16)
    followup_queries: list[SourceQuery] = Field(default_factory=list, max_length=6)


class NodeChoice(ClosedModel):
    node_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    plugin_id: str = Field(min_length=1, max_length=160)
    parameters: dict[str, Any] = Field(default_factory=dict)


class EdgeChoice(ClosedModel):
    source_node: str
    source_port: str
    target_node: str
    target_port: str
    adapter: str | None = None


class CandidateDraft(ClosedModel):
    nodes: list[NodeChoice] = Field(min_length=1, max_length=32)
    edges: list[EdgeChoice] = Field(default_factory=list, max_length=128)
    evidence_ids: list[str] = Field(default_factory=list, max_length=64)
    decision_evidence: dict[str, list[str]] = Field(default_factory=dict)
    procedure_nodes: dict[str, list[str]] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=3000)


class RequirementReview(ClosedModel):
    key: str
    met: bool = Field(strict=True)
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
    explanation: str = Field(min_length=1, max_length=1000)


class ProcedureReview(ClosedModel):
    procedure_id: str
    met: bool = Field(strict=True)
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
    explanation: str = Field(min_length=1, max_length=1000)


class ReviewResult(ClosedModel):
    goal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    requirements: list[RequirementReview] = Field(max_length=16)
    blockers: list[str] = Field(default_factory=list, max_length=32)
    followup_queries: list[SourceQuery] = Field(default_factory=list, max_length=6)
    summary: str = Field(min_length=1, max_length=2000)
    audit_brief_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    procedures: list[ProcedureReview] = Field(default_factory=list, max_length=16)
    return_to_audit_brain: bool = Field(default=False, strict=True)


class AssessmentResult(ClosedModel):
    passed: bool
    hard_issues: list[str] = Field(default_factory=list)
    reviewer_issues: list[str] = Field(default_factory=list)
    goal_hash: str
    candidate_hash: str
    evidence_hash: str
    compiled_plan_hash: str | None = None
    audit_brief_hash: str | None = None
    evaluator_version: str = "collaborative-v1"
    plan: dict[str, Any] | None = None


class ResolvedInput(ClosedModel):
    port_id: str
    schema_ref: str
    artifact_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    available: bool
    detail: str = ""


def evidence_hash(items: list[EvidenceItem]) -> str:
    return fingerprint(sorted((item.model_dump(mode="json") for item in items), key=lambda row: str(row["evidence_id"])))


def origin_groups(items: list[EvidenceItem]) -> list[list[str]]:
    """Group shared provenance without calling repeated evidence independent."""
    groups: list[tuple[set[str], set[str]]] = []
    for item in sorted(items, key=lambda value: value.evidence_id):
        origins, ids = set(item.origin_refs), {item.evidence_id}
        remaining = []
        for existing_origins, existing_ids in groups:
            if origins & existing_origins:
                origins.update(existing_origins)
                ids.update(existing_ids)
            else:
                remaining.append((existing_origins, existing_ids))
        groups = [*remaining, (origins, ids)]
    # A merged component can bridge two previously disjoint groups.
    changed = True
    while changed:
        changed = False
        for index, (origins, ids) in enumerate(groups):
            for other in range(index + 1, len(groups)):
                if origins & groups[other][0]:
                    origins.update(groups[other][0])
                    ids.update(groups[other][1])
                    groups.pop(other)
                    changed = True
                    break
            if changed:
                break
    return sorted((sorted(ids) for _, ids in groups), key=lambda values: values[0])
