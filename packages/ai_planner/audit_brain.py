"""Knowledge-grounded audit task handoff, before any plugin composition."""
from __future__ import annotations

from .evidence import AuditTaskBrief, EvidenceItem, PlanningRequest, evidence_hash, fingerprint


def knowledge_hash(evidence: list[EvidenceItem]) -> str:
    return evidence_hash([item for item in evidence if item.source_kind == "knowledge"])


def audit_brief_issues(request: PlanningRequest, brief: AuditTaskBrief, evidence: list[EvidenceItem]) -> list[str]:
    issues = []
    knowledge_ids = {item.evidence_id for item in evidence if item.source_kind == "knowledge"}
    if brief.goal_hash != fingerprint(request):
        issues.append("audit_brief_goal_mismatch")
    if brief.knowledge_hash != knowledge_hash(evidence):
        issues.append("audit_brief_knowledge_mismatch")
    if any(query.source_kind != "knowledge" or query.capabilities for query in brief.followup_queries):
        issues.append("audit_brain_requires_knowledge_queries_only")
    ids = [procedure.procedure_id for procedure in brief.procedures]
    if len(ids) != len(set(ids)):
        issues.append("audit_procedure_ids_must_be_unique")
    keys = {item.key for item in request.requirements}
    covered = set()
    for procedure in brief.procedures:
        covered.update(procedure.requirement_keys)
        if not procedure.requirement_keys or set(procedure.requirement_keys) - keys:
            issues.append(f"audit_procedure_unknown_goal:{procedure.procedure_id}")
        if set(procedure.knowledge_evidence_ids) - knowledge_ids:
            issues.append(f"audit_procedure_unverified_knowledge:{procedure.procedure_id}")
    if not brief.blockers:
        if not knowledge_ids:
            issues.append("audit_knowledge_required")
        if not brief.procedures or covered != keys:
            issues.append("audit_procedures_must_cover_every_goal")
    return issues
