"""Independent, deterministic assessment of evidence-grounded plan proposals.

Model text supplies selections, never capabilities, contracts or permissions.
Only the trusted directory expands a selection into the compiler's input.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from packages.plugin_topology import port_adapters
from packages.plugin_topology.compiler import compile_plan
from packages.plugin_topology.ports_executor import ADAPTERS, capability_parameters

from .audit_brain import audit_brief_issues
from .composer import PluginSpec, PortSpec, schema_sha256
from .evidence import (
    AssessmentResult,
    AuditTaskBrief,
    CandidateDraft,
    EvidenceItem,
    PlanningRequest,
    ResolvedInput,
    ReviewResult,
    evidence_hash,
    fingerprint,
)
from .semantics import load_semantics


def _port(port: PortSpec) -> dict[str, Any]:
    digest = schema_sha256(port.schema_ref)
    if port.schema_sha256 is not None and port.schema_sha256 != digest:
        raise ValueError(f"directory port digest mismatch: {port.port_id}")
    return {
        "port_id": port.port_id, "direction": port.direction,
        "schema_ref": port.schema_ref, "schema_version": "1.0.0", "schema_sha256": digest,
        "media_type": "application/json", "required": port.required,
        "cardinality": port.cardinality, "classification": "internal", "transport": "artifact_ref",
    }


def _citation_issues(candidate: CandidateDraft, evidence: list[EvidenceItem]) -> list[str]:
    issues: list[str] = []
    counts = Counter(item.evidence_id for item in evidence)
    if any(count != 1 for count in counts.values()):
        issues.append("duplicate_evidence_id: retrieved evidence identities must be unambiguous")
    if len({item.tenant_id for item in evidence}) > 1:
        issues.append("evidence_tenant_mismatch: evidence from multiple tenants cannot be combined")
    known = set(counts)
    cited = set(candidate.evidence_ids)
    if not cited or not cited <= known:
        issues.append("unknown_evidence: the candidate must cite retrieved evidence")
    node_ids = {node.node_id for node in candidate.nodes}
    if set(candidate.decision_evidence) != node_ids:
        issues.append("decision_evidence_coverage: every selected node needs its own evidence references")
    for node_id, references in candidate.decision_evidence.items():
        if not references or not set(references) <= (known & cited):
            issues.append(f"decision_evidence_invalid: {node_id} cites missing or uncited evidence")
    return issues


def _review_issues(
    request: PlanningRequest, candidate: CandidateDraft, evidence: list[EvidenceItem], review: ReviewResult,
) -> list[str]:
    # Never replace this list with a field supplied by the planning role.
    issues = list(review.blockers)
    if review.goal_hash != fingerprint(request):
        issues.append("stale_goal_review: review does not cover this goal and its constraints")
    if review.candidate_hash != fingerprint(candidate):
        issues.append("stale_candidate_review: review does not cover this candidate")
    if review.evidence_hash != evidence_hash(evidence):
        issues.append("stale_evidence_review: review does not cover this evidence snapshot")
    expected = {requirement.key for requirement in request.requirements}
    keys = [item.key for item in review.requirements]
    if set(keys) != expected or len(keys) != len(expected):
        issues.append("review_requirement_coverage: review must cover every requirement exactly once")
    valid_ids = {item.evidence_id for item in evidence} & set(candidate.evidence_ids)
    for item in review.requirements:
        if not item.met:
            issues.append(f"review_requirement_not_met: {item.key}")
        if not item.evidence_ids or not set(item.evidence_ids) <= valid_ids:
            issues.append(f"review_evidence_invalid: {item.key} needs current candidate-cited evidence")
    return issues


def _parameter_type_matches(value: Any, declared: Any) -> bool:
    if isinstance(declared, bool):
        return isinstance(value, bool)
    if isinstance(declared, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return type(value) is type(declared)


def _nodes(
    candidate: CandidateDraft, specs: Mapping[str, PluginSpec], issues: list[str],
) -> tuple[list[dict[str, Any]], dict[str, PluginSpec]]:
    nodes: list[dict[str, Any]] = []
    chosen: dict[str, PluginSpec] = {}
    for selection in candidate.nodes:
        spec = specs.get(selection.plugin_id)
        if spec is None:
            issues.append(f"unknown_plugin: {selection.plugin_id}")
            continue
        if spec.plugin_id != selection.plugin_id:
            issues.append(f"plugin_binding_mismatch: {selection.plugin_id}")
            continue
        if selection.node_id in chosen:
            issues.append(f"duplicate_node: {selection.node_id}")
        chosen[selection.node_id] = spec
        declared_parameters = capability_parameters(spec.capability)
        if set(selection.parameters) - set(declared_parameters):
            issues.append(f"unsupported_parameter: {selection.node_id} uses undeclared parameters")
        for key, value in selection.parameters.items():
            if key in declared_parameters and not _parameter_type_matches(value, declared_parameters[key]):
                issues.append(f"parameter_type_mismatch: {selection.node_id}.{key}")
        try:
            inputs = [_port(port) for port in spec.inputs]
            outputs = [_port(port) for port in spec.outputs]
        except (OSError, ValueError) as exc:
            issues.append(f"invalid_directory_contract: {selection.node_id}: {exc}")
            continue
        node: dict[str, Any] = {
            "node_instance_id": selection.node_id, "plugin_id": spec.plugin_id,
            "capability": spec.capability, "input_ports": inputs, "output_ports": outputs,
        }
        if selection.parameters:
            node["parameters"] = dict(selection.parameters)
        nodes.append(node)
    return nodes, chosen


def _edges(
    candidate: CandidateDraft, chosen: Mapping[str, PluginSpec], issues: list[str],
) -> list[dict[str, Any]]:
    semantics = load_semantics()
    edges: list[dict[str, Any]] = []
    for index, selection in enumerate(candidate.edges):
        source, target = chosen.get(selection.source_node), chosen.get(selection.target_node)
        if source is None or target is None:
            issues.append(f"edge_unknown_node: edge-{index}")
            continue
        if selection.adapter is not None:
            if selection.adapter not in ADAPTERS:
                issues.append(f"unregistered_adapter: {selection.adapter}")
            else:
                conversion = port_adapters.PORT_ADAPTER_CONTRACTS.get(selection.adapter)
                source_schema = next((port.schema_ref for port in source.outputs if port.port_id == selection.source_port), None)
                target_schema = next((port.schema_ref for port in target.inputs if port.port_id == selection.target_port), None)
                if conversion is None:
                    issues.append(f"adapter_contract_unknown: {selection.adapter}")
                elif source_schema is None or target_schema is None or (
                    source_schema.removesuffix(".schema.json"), target_schema.removesuffix(".schema.json")
                ) != tuple(schema.removesuffix(".schema.json") for schema in conversion):
                    issues.append(f"adapter_contract_mismatch: {selection.adapter}")
        elif not semantics.same_object(selection.source_port, selection.target_port):
            issues.append(f"edge_semantic_mismatch: {selection.source_port} -> {selection.target_port}")
        edges.append({
            "edge_id": f"edge-{index}", "source_instance": selection.source_node,
            "source_port": selection.source_port, "target_instance": selection.target_node,
            "target_port": selection.target_port,
            **({"adapter": selection.adapter} if selection.adapter is not None else {}),
        })
    return edges


def _seeds(
    request: PlanningRequest, chosen: Mapping[str, PluginSpec], edges: list[dict[str, Any]],
    resolved_inputs: list[ResolvedInput], issues: list[str],
) -> set[tuple[str, str]]:
    authorized = {item.port_id: item for item in request.inputs}
    resolved: dict[str, ResolvedInput] = {}
    for item in resolved_inputs:
        if item.port_id in resolved:
            issues.append(f"ambiguous_input: {item.port_id}")
        resolved[item.port_id] = item
    connected = {(edge["target_instance"], edge["target_port"]) for edge in edges}
    seeds: set[tuple[str, str]] = set()
    for node_id, spec in chosen.items():
        for port in spec.inputs:
            if (node_id, port.port_id) in connected:
                continue
            permitted, actual = authorized.get(port.port_id), resolved.get(port.port_id)
            valid = (
                permitted is not None and actual is not None and actual.available
                and actual.artifact_id == permitted.artifact_id
                and actual.schema_ref == permitted.schema_ref == port.schema_ref
            )
            if valid:
                seeds.add((node_id, port.port_id))
            elif port.required:
                issues.append(f"required_input_unavailable: {node_id}.{port.port_id}")
    return seeds


def assess_candidate(
    request: PlanningRequest,
    candidate: CandidateDraft,
    evidence: list[EvidenceItem],
    review: ReviewResult,
    specs: Mapping[str, PluginSpec],
    resolved_inputs: list[ResolvedInput],
    *, audit_brief: AuditTaskBrief | None = None,
) -> AssessmentResult:
    """Rebuild and assess a proposal; a passed assessment grants no execution."""
    candidate_digest, evidence_digest = fingerprint(candidate), evidence_hash(evidence)
    hard_issues = _citation_issues(candidate, evidence)
    reviewer_issues = _review_issues(request, candidate, evidence, review)
    brief_hash = fingerprint(audit_brief) if audit_brief is not None else None
    if review.return_to_audit_brain:
        reviewer_issues.append("audit_brain_revision_required")
    if audit_brief is not None:
        hard_issues.extend(audit_brief_issues(request, audit_brief, evidence))
        hard_issues.extend(f"audit_brain_blocked:{issue}" for issue in audit_brief.blockers)
        procedures = {item.procedure_id for item in audit_brief.procedures}
        node_ids = {item.node_id for item in candidate.nodes}
        if set(candidate.procedure_nodes) != procedures:
            hard_issues.append("audit_procedure_mapping_coverage")
        mapped = set()
        for procedure_id, references in candidate.procedure_nodes.items():
            mapped.update(references)
            if not references or set(references) - node_ids:
                hard_issues.append(f"audit_procedure_mapping_invalid:{procedure_id}")
        if mapped != node_ids:
            hard_issues.append("audit_procedure_unmapped_nodes")
        cited = set(candidate.evidence_ids)
        for procedure in audit_brief.procedures:
            if set(procedure.knowledge_evidence_ids) - cited:
                hard_issues.append(f"audit_method_evidence_uncited:{procedure.procedure_id}")
        if review.audit_brief_hash != brief_hash:
            reviewer_issues.append("stale_audit_brief_review")
        reviewed = [item.procedure_id for item in review.procedures]
        if len(reviewed) != len(set(reviewed)) or set(reviewed) != procedures:
            reviewer_issues.append("audit_procedure_review_coverage")
        for item in review.procedures:
            if not item.met:
                reviewer_issues.append(f"audit_procedure_not_met:{item.procedure_id}")
            if not item.evidence_ids or not set(item.evidence_ids) <= cited:
                reviewer_issues.append(f"audit_procedure_review_evidence_invalid:{item.procedure_id}")
    if len(candidate.nodes) > request.budget.max_nodes:
        hard_issues.append("node_budget_exceeded: candidate exceeds the authorized node budget")
    nodes, chosen = _nodes(candidate, specs, hard_issues)
    edges = _edges(candidate, chosen, hard_issues)
    seeds = _seeds(request, chosen, edges, resolved_inputs, hard_issues)
    capabilities = {spec.capability for spec in chosen.values()}
    outputs = {port.port_id for spec in chosen.values() for port in spec.outputs}
    for requirement in request.requirements:
        if not set(requirement.capabilities) <= capabilities or not set(requirement.output_ports) <= outputs:
            hard_issues.append(f"requirement_uncovered: {requirement.key}")
    raw_plan: dict[str, Any] = {
        "plan_key": f"plan-collaborative-{candidate_digest[:16]}", "nodes": nodes, "edges": edges,
        "seed_inputs": [list(pair) for pair in sorted(seeds)],
        "budget": {
            "max_chain_length": request.budget.max_nodes, "max_candidates": 128,
            "max_latency_ms": request.budget.max_seconds * 1000,
        },
        # Compiler hashes historically exclude seed injection declarations.
        # Trusted resolver metadata binds this new plan to its actual inputs
        # without changing the identity of any existing stored execution plan.
        "parameters": {"input_snapshot_hash": fingerprint(sorted(
            (item.model_dump(mode="json") for item in resolved_inputs),
            key=lambda item: (str(item["port_id"]), str(item["artifact_id"])),
        ))},
    }
    compiled_hash: str | None = None
    if brief_hash is not None:
        raw_plan["parameters"]["audit_brief_hash"] = brief_hash
    if not hard_issues:
        try:
            compiled = compile_plan(
                nodes=nodes, edges=edges, plan_key=raw_plan["plan_key"],
                seed_inputs=seeds, budget=raw_plan["budget"],
                parameters=raw_plan["parameters"],
            )
            compiled_hash = compiled.execution_hash
        except (KeyError, TypeError, ValueError) as exc:
            hard_issues.append(f"compile_failed: {exc}")
    passed = not hard_issues and not reviewer_issues
    return AssessmentResult(
        passed=passed, hard_issues=hard_issues, reviewer_issues=reviewer_issues,
        goal_hash=fingerprint(request), candidate_hash=candidate_digest,
        evidence_hash=evidence_digest, compiled_plan_hash=compiled_hash,
        audit_brief_hash=brief_hash,
        plan=raw_plan if passed else None,
    )
