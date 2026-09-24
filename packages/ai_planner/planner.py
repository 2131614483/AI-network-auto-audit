"""CW5: AiPlanner — local-model drafting with deterministic gates.

Flow (方案 6.1): recall capabilities -> model emits a structured draft ->
strict validation (closed schema / capability whitelist / data boundary) ->
deterministic compile -> on failure feed stable issues back to the model for
a bounded number of revisions -> success yields an ExecutionPlan, exhaustion
yields an honest gap report.  A failed compile is never converted into a fake
successful run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from packages.plugin_topology.compiler import CompileError, compile_plan
from packages.plugin_topology.ports import PortContractError

from .catalog import PORT_CONTRACTS, CapabilityEntry, recall_snapshot
from .draft import _MAX_REVISION_ROUNDS, validate_draft
from .errors import CompileIssue, compile_issues, issues_to_dicts
from .templates import TEMPLATES, template_block

SYSTEM_PROMPT = (
    "你是审计工作流规划器。你只输出一个 JSON 对象，不含任何其他文字。"
    "你必须只使用给定的能力召回清单里的 capability；不得发明插件、端口或能力。"
    "你不得执行任何动作、不得调用工具、不得读取或修改文件。"
    "数据只能引用用户明确授权的数据源。若无法满足目标，输出 plan_key 为 plan- 开头的空缺口 JSON。"
)


class DraftLLM(Protocol):
    def complete_json(self, messages: list[dict[str, str]], *, temperature: float) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PlanningOutcome:
    status: str  # "draft_ready" | "gap_report"
    plan_key: str
    execution_plan: Any | None = None
    draft: dict[str, Any] | None = None
    revisions: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)
    revision_history: list[dict[str, Any]] = field(default_factory=list)


# Streaming progress events for the canvas chat (SSE): each event is a JSON
# object with a "stage" key — capability_recall / llm_draft / validate /
# compile / done.  Only emitted when a callback is injected; never on the
# planning result itself.
ProgressEvent = dict[str, Any]
ProgressCallback = Callable[[ProgressEvent], None] | None


def _build_prompt(
    goal: str,
    catalog: dict[str, CapabilityEntry],
    authorized_sources: set[tuple[str, str]],
    budget: dict[str, int],
    template_keys: tuple[str, ...],
    port_contracts: Mapping[str, dict[str, Any]] | None = None,
    prior: Mapping[str, float] | None = None,
) -> list[dict[str, str]]:
    """The initial prompt.  ``port_contracts`` is threaded into the recall
    block so the contracts the model is told to copy verbatim are the *same*
    ones ``validate_draft`` and ``compile_plan`` will judge it against — a
    prompt built from a different registry than the validator's is a draft
    that can only ever fail with ``contract_mismatch``.

    ``prior`` (ring 7) only **reorders** the recall list — the block still
    lists exactly the same capabilities and ports.  It never filters, so the
    set of things the model may use is unchanged; see
    ``packages/ai_planner/experience_prior.py``.
    """
    sources = "\n".join(sorted(f"{node}:{port}" for node, port in authorized_sources)) or "(none)"
    user_prompt = (
        f"目标：{goal}\n"
        f"能力召回清单（只能使用这些 capability）：\n{recall_snapshot(catalog, port_contracts, prior=prior)}\n"
        f"可用模板（完整 JSON 示例，可直接参考其节点/端口/边结构，只替换能力与端口名）：\n{template_block(template_keys)}\n"
        f"授权数据源（唯一可引用的 seed 来源，逐字符一致）：\n{sources}\n"
        f"预算：{budget}\n"
        "输出 JSON 硬性要求：\n"
        "1. 只输出一个 JSON 对象，无任何额外文字。\n"
        "2. node_instance_id 使用语义化小写短横线标识（如 ledger-validate-001、finding-draft-001），禁止 node1/consumer1 等占位名。\n"
        "3. \"seed_inputs\" 必须是双层数组 [[\"节点实例ID\",\"端口ID\"]]，其中每个配对的节点实例ID和端口ID必须与「授权数据源」列表中的条目逐字符相同（那是画布上已存在的数据出口，不必出现在本次草稿的 nodes 里）；禁止把本次新建节点的 ID 当作 seed 来源；没有需要注入的数据就省略该字段。\n"
        "4. 端口对象必须逐字段复制能力清单中登记的端口契约，不得省略、修改或新增字段。\n"
        "5. \"nodes\": [{\"node_instance_id\", \"capability\", \"plugin_id\", \"input_ports\": [{\"port_id\", \"direction\", "
        "\"schema_ref\", \"schema_version\", \"schema_sha256\", \"media_type\", \"required\", \"cardinality\", "
        "\"classification\", \"transport\"}], \"output_ports\": [...]}]；\"edges\": [{\"edge_id\", \"source_instance\", "
        "\"source_port\", \"target_instance\", \"target_port\"}]；\"budget\": {...}；\"plan_key\": \"plan-...\"。"
        "direction 字段只允许字符串 \"input\" 或 \"output\"，且必须与所在数组一致（input_ports 数组内为 \"input\"，"
        "output_ports 数组内为 \"output\"）。\n"
        "6. 输出端口的 schema_ref 与目标输入端口的 schema_ref 相同时才可直连成边；否则不要画该边。\n"
        "7. 若某节点的 required 输入端口（required=true）没有来自其他节点的入边，必须通过 seed_inputs 从授权数据源注入该输入，否则编译器会拒绝该草稿。\n"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _revise_prompt(previous_draft: dict[str, Any], issues: list[CompileIssue], revision: int) -> dict[str, str]:
    issue_lines = "\n".join(
        f"- code={issue.code} node={issue.node_id or '-'} port={issue.port_id or '-'} "
        f"edge={issue.edge_id or '-'} message={issue.message}"
        for issue in issues
    )
    return {
        "role": "user",
        "content": (
            f"第 {revision} 次修订。上一版草稿：\n{previous_draft}\n"
            f"未通过确定性编译/校验，错误：\n{issue_lines}\n"
            "请只输出修正后的完整 JSON 草稿（同一 schema）。编译器不可绕过，不要关闭任何校验。"
        ),
    }


def _auto_seed_inputs(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    authorized_sources: set[tuple[str, str]],
    port_contracts: Mapping[str, dict[str, Any]] | None = None,
) -> set[tuple[str, str]]:
    """Deterministically bind canvas data outlets to dangling required inputs.

    The canvas owns data files (an authorized source ``(canvas_node, port)``);
    a new flow consumes them as its inputs.  Rather than asking the model to
    restate that binding (it repeatedly omits ``seed_inputs``), when a node's
    required input port has no incoming edge and an authorized source exposes
    the *same* registered port (hence the same ``schema_ref``), that outlet is
    bound as the seed — only ever from the authorized set, never beyond the
    data boundary.
    """
    # The registry decides which ports may be injected from an authorized outlet.
    # It must be the *same* one `validate_draft` was given, or a draft that
    # validates is then failed by the compiler for a dangling input the seeder
    # refused to bind — the port was simply not in the hand-written seven.
    registry: Mapping[str, dict[str, Any]] = (
        port_contracts if port_contracts is not None else PORT_CONTRACTS
    )
    consumed: set[tuple[str, str]] = set()
    for edge in edges:
        consumed.add((str(edge.get("target_instance")), str(edge.get("target_port"))))
    seeds: set[tuple[str, str]] = set()
    for node in nodes:
        node_id = str(node.get("node_instance_id") or "")
        for port in node.get("input_ports") or ():
            port_id = str(port.get("port_id") or "")
            if not port.get("required", True):
                continue
            if (node_id, port_id) in consumed:
                continue
            if port_id not in registry:
                continue
            for _source_node, source_port in sorted(authorized_sources):
                if source_port == port_id:
                    # The compiler's seed is expressed on THIS flow node's port;
                    # the canvas outlet (authorized_sources) is the provenance of
                    # that injection, kept in the intent evidence.
                    seeds.add((node_id, port_id))
                    break
    return seeds


class AiPlanner:
    """Local-model planner.  ``llm`` is injectable (Protocol) for tests."""

    def __init__(self, llm: DraftLLM, *, max_revision_rounds: int = _MAX_REVISION_ROUNDS) -> None:
        self.llm = llm
        self.max_revision_rounds = int(max_revision_rounds)

    def plan(
        self,
        *,
        goal: str,
        catalog: dict[str, CapabilityEntry],
        authorized_sources: set[tuple[str, str]],
        budget: dict[str, int] | None = None,
        template_keys: tuple[str, ...] = tuple(TEMPLATES),
        seed_inputs: set[tuple[str, str]] | None = None,
        on_progress: ProgressCallback = None,
        port_contracts: Mapping[str, dict[str, Any]] | None = None,
        max_draft_nodes: int | None = None,
        capability_prior: Mapping[str, float] | None = None,
    ) -> PlanningOutcome:
        effective_budget = dict(budget or {"max_chain_length": 8, "max_candidates": 8, "max_latency_ms": 5000})
        messages = _build_prompt(
            goal, catalog, authorized_sources, effective_budget, template_keys, port_contracts,
            capability_prior,
        )

        def emit(event: ProgressEvent) -> None:
            if on_progress is not None:
                on_progress(event)

        emit({"stage": "capability_recall", "capabilities": len(catalog), "goal": goal[:120]})
        for revision in range(1, self.max_revision_rounds + 1):
            emit({"stage": "llm_draft", "round": revision, "max_rounds": self.max_revision_rounds})
            raw = self.llm.complete_json(messages, temperature=0.0)
            validation = validate_draft(
            raw, catalog, authorized_sources, port_contracts=port_contracts,
            **({"max_nodes": max_draft_nodes} if max_draft_nodes else {}),
        )
            emit({
                "stage": "validate",
                "round": revision,
                "ok": validation.ok,
                "issues": issues_to_dicts([validation.issue]) if validation.issue else [],
            })
            if not validation.ok:
                issue = validation.issue
                assert issue is not None
                messages.append(_revise_prompt(raw, [issue], revision))
                if revision == self.max_revision_rounds:
                    outcome = self._gap(raw, [issue], revision)
                    emit({"stage": "done", "status": outcome.status, "revisions": outcome.revisions})
                    return outcome
                continue
            draft = validation.draft
            assert draft is not None
            try:
                declared_seeds = set(tuple(pair) for pair in (draft.get("seed_inputs") or []))
                auto_seeds = _auto_seed_inputs(
                    draft["nodes"], draft["edges"], authorized_sources,
                    port_contracts=port_contracts,
                )
                plan = compile_plan(
                    nodes=draft["nodes"],
                    edges=draft["edges"],
                    budget=draft.get("budget") or None,
                    plan_key=draft["plan_key"],
                    seed_inputs=declared_seeds | auto_seeds,
                )
            except CompileError as exc:
                issues = compile_issues(exc)
                emit({"stage": "compile", "round": revision, "ok": False, "issues": issues_to_dicts(issues)})
                messages.append(_revise_prompt(draft, issues, revision))
                if revision == self.max_revision_rounds:
                    outcome = self._gap(draft, issues, revision)
                    emit({"stage": "done", "status": outcome.status, "revisions": outcome.revisions})
                    return outcome
                continue
            except PortContractError as exc:
                issue = CompileIssue(code="contract_mismatch", message=str(exc))
                emit({"stage": "compile", "round": revision, "ok": False, "issues": issues_to_dicts([issue])})
                messages.append(_revise_prompt(draft, [issue], revision))
                if revision == self.max_revision_rounds:
                    outcome = self._gap(draft, [issue], revision)
                    emit({"stage": "done", "status": outcome.status, "revisions": outcome.revisions})
                    return outcome
                continue
            emit({"stage": "compile", "round": revision, "ok": True, "issues": []})
            outcome = PlanningOutcome(
                status="draft_ready",
                plan_key=plan.plan_key,
                execution_plan=plan,
                draft=draft,
                revisions=revision,
            )
            emit({"stage": "done", "status": outcome.status, "revisions": outcome.revisions})
            return outcome

        # Unreachable: loop always returns on the last round.
        outcome = self._gap(None, [CompileIssue(code="unsupported_feature", message="revision loop exhausted")], 0)
        emit({"stage": "done", "status": outcome.status, "revisions": outcome.revisions})
        return outcome

    def _gap(self, draft: dict[str, Any] | None, issues: list[CompileIssue], revisions: int) -> PlanningOutcome:
        plan_key = draft.get("plan_key") if isinstance(draft, dict) else None
        return PlanningOutcome(
            status="gap_report",
            plan_key=str(plan_key or "plan-unresolved"),
            draft=draft,
            revisions=revisions,
            issues=issues_to_dicts(issues),
        )


def _default_llm() -> DraftLLM:
    """Default model backend: the unified AI gateway (``packages.ai``).

    Provider, endpoint, model and key resolve through the shared configuration
    (``AI_*`` variables, falling back to the legacy ``OPENAI_COMPAT_*`` /
    ``OLLAMA_*`` names, then built-in defaults) at *call* time — so a change
    saved in the desktop AI settings page applies without a restart.

    Fail-closed: a missing credential or an unreachable backend surfaces as an
    ``AIClientError`` from the call itself — never a faked draft.
    """
    from packages.ai import get_chat_client

    return get_chat_client()
