"""Bounded, resumable retrieval -> planning -> independent review loop.

This module has no executor, SQL, shell or network transport.  All access is
injected and passes the broker's per-operation policy gate.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Callable, Mapping, Protocol, cast

from packages.plugin_topology.port_adapters import PORT_ADAPTER_CONTRACTS
from packages.plugin_topology.ports_executor import capability_parameters

from .assessment import assess_candidate
from .audit_brain import audit_brief_issues, knowledge_hash
from .collaborative_agents import AuditBrainAgent, LocalOnlyJsonClient
from .composer import PluginSpec
from .evidence import (
    AuditTaskBrief,
    CandidateDraft,
    ClosedModel,
    EvidenceItem,
    PlanningContext,
    PlanningRequest,
    ResolvedInput,
    ReviewResult,
    SourceKind,
    SourceQuery,
    SourceResult,
    evidence_hash,
    fingerprint,
    origin_groups,
)

Gateway = Callable[[str, dict[str, Any]], str]
Emitter = Callable[[str, str, dict[str, Any], dict[str, Any]], None]


class SourcesPort(Protocol):
    def query(self, context: PlanningContext, query: SourceQuery) -> SourceResult: ...
    def resolve_inputs(self, context: PlanningContext, inputs: Any) -> list[ResolvedInput]: ...
    def validate_evidence(self, context: PlanningContext, items: list[EvidenceItem]) -> list[str]: ...


class DraftRole(Protocol):
    def plan(self, context: dict[str, Any], complete_json: Any) -> CandidateDraft: ...


class ReviewRole(Protocol):
    def review(self, context: dict[str, Any], complete_json: Any) -> ReviewResult: ...


class BrainRole(Protocol):
    def build_brief(self, context: dict[str, Any], complete_json: Any) -> AuditTaskBrief: ...


class BudgetExhausted(RuntimeError):
    pass


class SourceFailure(RuntimeError):
    pass


class CollaborativePlanner:
    def __init__(
        self, *, sources: SourcesPort, gateway: Gateway, model: Any,
        planner: DraftRole, reviewer: ReviewRole, specs: Mapping[str, PluginSpec],
        brain: BrainRole | None = None,
        emit: Emitter | None = None, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sources = sources
        self.gateway = gateway
        self.model = model
        self.planner = planner
        self.reviewer = reviewer
        self.brain = brain if brain is not None else AuditBrainAgent()
        self.specs = dict(specs)
        self.emit = emit
        self.clock = clock
        self.state: dict[str, Any] = {}
        self._started = 0.0
        self._elapsed = 0.0
        self.request: PlanningRequest
        self.context: PlanningContext

    def _checkpoint(self) -> dict[str, Any]:
        self.state["identity"] = {
            "tenant_id": str(self.context.tenant_id), "session_id": str(self.context.session_id),
            "request_hash": fingerprint(self.request),
        }
        self.state["elapsed_seconds"] = self._elapsed + max(0, self.clock() - self._started)
        return cast(dict[str, Any], json.loads(json.dumps(self.state, ensure_ascii=False)))

    def _event(self, key: str, kind: str, payload: dict[str, Any]) -> None:
        checkpoint = self._checkpoint()
        if self.emit:
            self.emit(key, kind, payload, checkpoint)

    def _time_budget(self) -> None:
        if self._elapsed + self.clock() - self._started >= self.request.budget.max_seconds:
            raise BudgetExhausted("planning time budget exhausted")

    def _gate(self, capability: str, arguments: dict[str, Any]) -> str:
        self._time_budget()
        return self.gateway(capability, {"domain": self.request.domain, **arguments})

    def _retrieve(self, query: SourceQuery) -> None:
        self._time_budget()
        if self.state["query_count"] >= self.request.budget.max_queries:
            raise BudgetExhausted("source query budget exhausted")
        allowed = {spec.capability for spec in self.specs.values()}
        if set(query.capabilities) - allowed:
            raise SourceFailure("query asks for capabilities outside the frozen domain catalog")
        self.state["query_count"] += 1
        number = self.state["query_count"]
        decision = self._gate(
            f"topology.collaborative.{query.source_kind}.read", query.model_dump(mode="json"),
        )
        key = fingerprint(query)
        cached = self.state["cache"].get(key)
        self._event(f"query-{number}-started", "query_started", {
            "query": query.model_dump(mode="json"), "decision_id": decision,
            "agent": f"{query.source_kind}scout", "cached": cached is not None,
        })
        result = SourceResult.model_validate(cached) if cached else self.sources.query(self.context, query)
        if result.source_kind != query.source_kind:
            raise SourceFailure("source kind mismatch")
        if len(result.items) > query.limit:
            raise SourceFailure("source exceeded its result budget")
        if result.status not in {"available", "empty"}:
            self.state["source_status"][query.source_kind] = result.status
            self._event(f"query-{number}-failed", "source_query", {
                "query": query.model_dump(mode="json"), "decision_id": decision,
                "result": {"source_kind": query.source_kind, "status": result.status, "items": [], "detail": result.detail},
            })
            if result.status == "denied":
                raise PermissionError("source denied access")
            raise SourceFailure(f"source is {result.status}; response evidence was not accepted")
        if result.status == "empty" and result.items:
            raise SourceFailure("empty source response contains evidence")
        for item in result.items:
            if item.tenant_id != self.context.tenant_id or item.source_kind != query.source_kind:
                raise SourceFailure("cross-tenant or wrong-source evidence rejected")
        self.state["source_status"][query.source_kind] = result.status
        self.state["cache"][key] = result.model_dump(mode="json")
        indexed = {item["evidence_id"]: item for item in self.state["evidence"]}
        for item in result.items:
            row = item.model_dump(mode="json")
            previous = indexed.get(item.evidence_id)
            if previous and previous != row:
                raise SourceFailure("evidence identity was reused for different content")
            indexed[item.evidence_id] = row
        evidence_rows = list(indexed.values())
        graph_nodes = set(self.state.get("graph_nodes", []))
        for item in result.items:
            if item.source_kind == "graph":
                graph_nodes.add(str(item.facts.get("node_id", item.source_ref)))
                for relation in item.facts.get("relations", []):
                    graph_nodes.update(str(relation[key]) for key in ("source_node_id", "target_node_id") if key in relation)
        if len(graph_nodes) > 64:
            raise BudgetExhausted("cumulative graph node budget exhausted")
        self.state["graph_nodes"] = sorted(graph_nodes)
        if len(json.dumps(evidence_rows, ensure_ascii=False)) > self.request.budget.max_evidence_chars:
            raise BudgetExhausted("evidence context budget exhausted")
        self.state["evidence"] = evidence_rows
        self._event(f"query-{number}-finished", "source_query", {
            "query": query.model_dump(mode="json"), "result": result.model_dump(mode="json"),
            "decision_id": decision, "cached": cached is not None,
        })
        self._time_budget()

    def _complete(self, role: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        if role not in {"audit_brain", "planner", "reviewer"}:
            raise ValueError("unknown model role")
        self._time_budget()
        if self.state["model_calls"] >= self.request.budget.max_model_calls:
            raise BudgetExhausted("model call budget exhausted")
        if len(json.dumps(messages, ensure_ascii=False)) > 64000:
            raise BudgetExhausted("model context exceeds 64000 characters")
        self.state["model_calls"] += 1
        number = self.state["model_calls"]
        access = {
            "model": getattr(self.model, "model", type(self.model).__name__),
            "model_mode": getattr(self.model, "mode", "injected"),
            "transport": getattr(self.model, "transport", "local_only" if isinstance(self.model, LocalOnlyJsonClient) else "injected"),
            "endpoint": getattr(self.model, "endpoint", "injected"),
            "model_config_hash": getattr(self.model, "config_hash", None),
        }
        decision = self._gate("topology.collaborative.model.call", {
            "role": role, "messages_hash": fingerprint(messages),
            **access,
        })
        self._event(f"model-{number}-started", "model_started", {
            "role": role, "decision_id": decision,
            **access,
        })
        if self.model is None:
            raise RuntimeError("local model is not configured")
        if isinstance(self.model, LocalOnlyJsonClient):
            models: dict[str, type[ClosedModel]] = {"audit_brain": AuditTaskBrief, "planner": CandidateDraft, "reviewer": ReviewResult}
            schema = models[role].model_json_schema()
            result = self.model.complete_json(messages, temperature=0.0, response_schema=schema)
        else:
            result = self.model.complete_json(messages, temperature=0.0)
        self._event(f"model-{number}-finished", "model_finished", {"role": role, "response_hash": fingerprint(result)})
        return result

    def _role_context(self, inputs: list[ResolvedInput]) -> dict[str, Any]:
        catalog = [{
            "plugin_id": spec.plugin_id, "capability": spec.capability,
            "description": spec.description[:300],
            "parameters": capability_parameters(spec.capability),
            "inputs": [{"port_id": p.port_id, "schema_ref": p.schema_ref, "required": p.required} for p in spec.inputs],
            "outputs": [{"port_id": p.port_id, "schema_ref": p.schema_ref} for p in spec.outputs],
        } for spec in self.specs.values()]
        context = {
            "request": self.request.model_dump(mode="json"), "round_no": self.context.round_no,
            "catalog": catalog, "evidence": self._model_evidence(self.state["evidence"]),
            "adapters": PORT_ADAPTER_CONTRACTS,
            "inputs": [item.model_dump(mode="json") for item in inputs],
            "previous_candidate": self.state.get("candidate"), "feedback": self.state.get("feedback", []),
            "source_status": self.state["source_status"],
            "audit_brief": self.state.get("audit_brief"), "audit_brief_hash": self.state.get("audit_brief_hash"),
            "evidence_origin_groups": origin_groups([EvidenceItem.model_validate(row) for row in self.state["evidence"]]),
        }
        return cast(dict[str, Any], json.loads(json.dumps(context, ensure_ascii=False)))

    @staticmethod
    def _model_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Graph/history content already serialises their facts. The full records
        # remain in state, hashes, events and final validation; the model sees
        # one content representation plus its immutable identity and provenance.
        return [{key: value for key, value in row.items() if key != "facts"} for row in rows]

    def _prepare_audit_brief(self) -> bool:
        evidence = [EvidenceItem.model_validate(row) for row in self.state["evidence"]]
        saved = self.state.get("audit_brief")
        if saved and not self.state.get("audit_feedback"):
            current = AuditTaskBrief.model_validate(saved)
            if not current.blockers and not current.followup_queries and not audit_brief_issues(self.request, current, evidence):
                self.state["audit_brief_hash"] = fingerprint(current)
                return True
        knowledge = [item for item in evidence if item.source_kind == "knowledge"]
        context = {
            "request": self.request.model_dump(mode="json"), "round_no": self.context.round_no,
            "goal_hash": fingerprint(self.request), "knowledge_hash": knowledge_hash(knowledge),
            "evidence": self._model_evidence([item.model_dump(mode="json") for item in knowledge]),
            "evidence_origin_groups": origin_groups(knowledge), "previous_brief": saved,
            "feedback": self.state.get("audit_feedback", []),
        }
        brief = AuditTaskBrief.model_validate(self.brain.build_brief(context, self._complete))
        issues = audit_brief_issues(self.request, brief, evidence)
        self.state["audit_brief"] = brief.model_dump(mode="json")
        self.state["audit_brief_hash"] = fingerprint(brief)
        self.state["audit_brief_issues"] = issues
        self.state["audit_feedback"] = []
        self._event(f"round-{self.context.round_no}-audit-brief", "audit_brief", {
            "brief": self.state["audit_brief"], "brief_hash": fingerprint(brief), "issues": issues,
        })
        self._time_budget()
        if issues:
            raise SourceFailure("invalid audit handoff: " + "; ".join(issues))
        if brief.blockers or brief.followup_queries:
            self.state["pending_queries"] = [q.model_dump(mode="json") for q in brief.followup_queries]
            self.state["feedback"] = list(brief.blockers)
            return False
        return True

    def _verify_sources(self, evidence: list[EvidenceItem]) -> list[str]:
        for kind in sorted({item.source_kind for item in evidence}):
            if self.state["query_count"] >= self.request.budget.max_queries:
                raise BudgetExhausted("source verification query budget exhausted")
            self.state["query_count"] += 1
            self._gate(f"topology.collaborative.{kind}.read", {"operation": "verify_snapshot"})
        issues = self.sources.validate_evidence(self.context, evidence)
        self._time_budget()
        return issues

    def _verify_inputs(self, inputs: list[ResolvedInput]) -> bool:
        self._gate("topology.collaborative.input.read", {"operation": "verify_inputs"})
        current = self.sources.resolve_inputs(self.context, self.request.inputs)
        self._time_budget()
        return fingerprint([item.model_dump(mode="json") for item in current]) == fingerprint([item.model_dump(mode="json") for item in inputs])

    def _check_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        expected = {"tenant_id": str(self.context.tenant_id), "session_id": str(self.context.session_id), "request_hash": fingerprint(self.request)}
        if checkpoint.get("identity") != expected:
            raise ValueError("checkpoint identity does not match tenant, session and request")
        for field in ("query_count", "model_calls", "round_no"):
            value = checkpoint.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if field == "round_no" else 0):
                raise ValueError("invalid checkpoint counters")
        elapsed = checkpoint.get("elapsed_seconds")
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid checkpoint elapsed time")
        if checkpoint["query_count"] > self.request.budget.max_queries or checkpoint["model_calls"] > self.request.budget.max_model_calls or checkpoint["round_no"] > self.request.budget.max_rounds + 1:
            raise ValueError("checkpoint exceeds the frozen planning budget")
        for item in checkpoint.get("evidence", []):
            if EvidenceItem.model_validate(item).tenant_id != self.context.tenant_id:
                raise ValueError("checkpoint contains cross-tenant evidence")

    def run(
        self, context: PlanningContext, request: PlanningRequest,
        *, checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.context, self.request = context, request
        self._started = self.clock()
        capabilities = list(dict.fromkeys(cap for req in request.requirements for cap in req.capabilities))
        target_ports = {port for req in request.requirements for port in req.output_ports}
        capabilities = list(dict.fromkeys([*capabilities, *(spec.capability for spec in self.specs.values() if target_ports & {port.port_id for port in spec.outputs})]))[:16]
        initial_kinds: tuple[SourceKind, ...] = ("knowledge",) if request.domain == "audit" else ("graph", "history", "knowledge")
        self.state = {
            "round_no": 1, "query_count": 0, "model_calls": 0, "evidence": [], "cache": {},
            "source_status": {}, "feedback": [], "elapsed_seconds": 0.0,
            "pending_queries": [SourceQuery(
                source_kind=kind, query=request.goal[:500], purpose="初始目标取证", capabilities=capabilities,
            ).model_dump(mode="json") for kind in initial_kinds],
        }
        try:
            if checkpoint:
                self._check_checkpoint(checkpoint)
                self.state = json.loads(json.dumps(checkpoint, allow_nan=False))
            self._elapsed = float(self.state.get("elapsed_seconds", 0.0))
            known_capabilities = {spec.capability for spec in self.specs.values()}
            known_outputs = {port.port_id for spec in self.specs.values() for port in spec.outputs}
            if any(set(item.capabilities) - known_capabilities or set(item.output_ports) - known_outputs for item in request.requirements):
                return self._result("blocked_capability", "requested capability or output is absent from the verified domain catalog")
            self._gate("topology.collaborative.input.read", {"artifact_ids": [str(item.artifact_id) for item in request.inputs]})
            inputs = self.sources.resolve_inputs(context, request.inputs)
            if len(inputs) != len(request.inputs) or any(not item.available for item in inputs):
                return self._result("blocked_input", "one or more authorized input artifacts are unavailable")
            saved_assessment = self.state.get("assessment") or {}
            if saved_assessment.get("passed") and self.state.get("candidate") and self.state.get("review"):
                saved_brief = None
                if request.domain == "audit":
                    if not self.state.get("audit_brief"):
                        return self._result("failed", "saved audit acceptance has no audit task brief; submit a new session")
                    saved_brief = AuditTaskBrief.model_validate(self.state["audit_brief"])
                candidate = CandidateDraft.model_validate(self.state["candidate"])
                evidence = [EvidenceItem.model_validate(row) for row in self.state["evidence"]]
                review = ReviewResult.model_validate(self.state["review"])
                assessed = assess_candidate(request, candidate, evidence, review, self.specs, inputs, audit_brief=saved_brief)
                changed = assessed.compiled_plan_hash != saved_assessment.get("compiled_plan_hash")
                source_issues = self._verify_sources(evidence) if assessed.passed and not changed else []
                if assessed.passed and not changed and self._verify_inputs(inputs) and not source_issues:
                    self.state["assessment"] = assessed.model_dump(mode="json")
                    return self._result("accepted", "recovered evaluated plan after rechecking current inputs and evidence")
                self.state["assessment"] = assessed.model_copy(update={
                    "passed": False, "plan": None,
                    "hard_issues": [*assessed.hard_issues, *source_issues, "saved plan or input snapshot is no longer valid"],
                }).model_dump(mode="json")
                return self._result("failed", "saved evaluation is no longer valid for current inputs or evidence")
            for round_no in range(int(self.state["round_no"]), request.budget.max_rounds + 1):
                self.context = context.model_copy(update={"round_no": round_no})
                self.state["round_no"] = round_no
                while self.state["pending_queries"]:
                    self._retrieve(SourceQuery.model_validate(self.state["pending_queries"][0]))
                    self.state["pending_queries"].pop(0)
                audit_brief = None
                if request.domain == "audit":
                    if not self._prepare_audit_brief():
                        progress = fingerprint(self.state["audit_brief"])
                        stalled = self.state.get("last_audit_progress") == progress
                        self.state["last_audit_progress"] = progress
                        self.state["round_no"] = round_no + 1
                        self._event(f"round-{round_no}-finished", "round_finished", {
                            "round_no": round_no, "stage": "audit_brain", "brief": self.state["audit_brief"],
                        })
                        if stalled or not self.state["pending_queries"]:
                            return self._result("stalled", "audit method basis is unresolved; plugin composition was not authorized")
                        continue
                    audit_brief = AuditTaskBrief.model_validate(self.state["audit_brief"])
                    for kind in ("graph", "history"):
                        if kind not in self.state["source_status"]:
                            self._retrieve(SourceQuery(source_kind=kind, query=request.goal[:500], purpose="按审计任务说明选择插件并参考经验", capabilities=capabilities))
                    while self.state.get("deferred_plugin_queries"):
                        self._retrieve(SourceQuery.model_validate(self.state["deferred_plugin_queries"][0]))
                        self.state["deferred_plugin_queries"].pop(0)
                if set(self.state["source_status"]) != {"graph", "history", "knowledge"}:
                    raise SourceFailure("all three sources must be queried before planning")
                role_context = self._role_context(inputs)
                candidate = CandidateDraft.model_validate(self.planner.plan(role_context, self._complete))
                evidence = [EvidenceItem.model_validate(row) for row in self.state["evidence"]]
                self._event(f"round-{round_no}-candidate", "candidate", candidate.model_dump(mode="json"))
                pending_review = ReviewResult(
                    goal_hash=fingerprint(request),
                    candidate_hash=fingerprint(candidate), evidence_hash=evidence_hash(evidence),
                    requirements=[], blockers=["independent review pending"], summary="pending",
                    audit_brief_hash=self.state.get("audit_brief_hash"),
                )
                preliminary = assess_candidate(request, candidate, evidence, pending_review, self.specs, inputs, audit_brief=audit_brief)
                if preliminary.hard_issues:
                    assessment = preliminary
                    review = pending_review
                else:
                    review_context = {**role_context, "candidate": candidate.model_dump(mode="json"),
                                      "goal_hash": fingerprint(request),
                                      "candidate_hash": fingerprint(candidate), "evidence_hash": evidence_hash(evidence)}
                    review = ReviewResult.model_validate(self.reviewer.review(review_context, self._complete))
                    assessment = assess_candidate(request, candidate, evidence, review, self.specs, inputs, audit_brief=audit_brief)
                source_issues = self._verify_sources(evidence) if assessment.passed else []
                if source_issues:
                    assessment = assessment.model_copy(update={"passed": False, "hard_issues": source_issues, "plan": None})
                if assessment.passed and not self._verify_inputs(inputs):
                    return self._result("blocked_input", "input artifact snapshot changed during planning")
                self.state["candidate"] = candidate.model_dump(mode="json")
                self.state["assessment"] = assessment.model_dump(mode="json")
                self.state["review"] = review.model_dump(mode="json")
                progress = fingerprint({"candidate": fingerprint(candidate), "evidence": evidence_hash(evidence),
                                        "audit_brief_hash": self.state.get("audit_brief_hash"),
                                        "issues": assessment.hard_issues + assessment.reviewer_issues})
                stalled = self.state.get("last_progress") == progress
                self.state["last_progress"] = progress
                self.state["feedback"] = assessment.hard_issues + assessment.reviewer_issues + review.blockers
                self.state["pending_queries"] = [q.model_dump(mode="json") for q in review.followup_queries]
                if request.domain == "audit" and (review.return_to_audit_brain or any(q.source_kind == "knowledge" for q in review.followup_queries)):
                    self.state["audit_feedback"] = list(dict.fromkeys([
                        *review.blockers, *(item.explanation for item in review.requirements if not item.met),
                        *(item.explanation for item in review.procedures if not item.met),
                        "Reviewer requested a revised audit task brief",
                    ]))
                    self.state["deferred_plugin_queries"] = [q.model_dump(mode="json") for q in review.followup_queries if q.source_kind != "knowledge"]
                    self.state["pending_queries"] = [q.model_dump(mode="json") for q in review.followup_queries if q.source_kind == "knowledge"]
                if source_issues:
                    self.state["cache"] = {}
                    self.state["evidence"] = []
                    self.state["source_status"] = {}
                    self.state.pop("audit_brief", None)
                    self.state.pop("audit_brief_hash", None)
                    self.state["deferred_plugin_queries"] = []
                    self.state["pending_queries"] = [SourceQuery(source_kind=kind, query=request.goal[:500], purpose="刷新已失效的来源快照", capabilities=capabilities).model_dump(mode="json") for kind in initial_kinds]
                if not self.state["pending_queries"] and not assessment.passed and not self.state.get("audit_feedback"):
                    self.state["pending_queries"] = [SourceQuery(
                        source_kind="graph", query=(request.goal + " " + " ".join(self.state["feedback"]))[:500],
                        purpose="解决方案校验缺口", capabilities=capabilities,
                    ).model_dump(mode="json")]
                self.state["round_no"] = round_no + 1
                self._event(f"round-{round_no}-finished", "round_finished", {
                    "round_no": round_no, "candidate": self.state["candidate"],
                    "assessment": self.state["assessment"], "review": self.state["review"],
                })
                self._time_budget()
                if assessment.passed:
                    return self._result("accepted", "static plan and independent evidence review passed")
                if stalled:
                    return self._result("stalled", "same candidate, evidence and unresolved issues")
            return self._result("budget_exhausted", "planning round budget exhausted")
        except PermissionError as exc:
            return self._result("blocked_policy", str(exc))
        except BudgetExhausted as exc:
            return self._result("budget_exhausted", str(exc))
        except (ValueError, RuntimeError) as exc:
            return self._result("failed", f"{type(exc).__name__}: {str(exc)[:800]}")

    def _result(self, status: str, detail: str) -> dict[str, Any]:
        assessed = self.state.get("assessment")
        if status != "accepted" and assessed and assessed.get("passed"):
            self.state["assessment"] = {**assessed, "passed": False, "plan": None,
                                        "hard_issues": [*assessed.get("hard_issues", []), detail]}
        return {
            "mode": "plan_only", "status": status, "detail": detail,
            "workflow_version": "audit-brain-v1" if self.request.domain == "audit" else "collaborative-v1",
            "session_id": str(self.context.session_id), "trace_id": str(self.context.trace_id),
            "rounds": max(0, int(self.state.get("round_no", 1)) - 1),
            "query_count": self.state.get("query_count", 0), "model_calls": self.state.get("model_calls", 0),
            "candidate": self.state.get("candidate"), "assessment": self.state.get("assessment"),
            "audit_brief": self.state.get("audit_brief"), "audit_brief_hash": self.state.get("audit_brief_hash"),
            "audit_brief_issues": self.state.get("audit_brief_issues", []),
            "review": self.state.get("review"),
            "source_status": self.state.get("source_status", {}), "evidence": self.state.get("evidence", []),
            "checkpoint": self._checkpoint(),
        }
