"""Contract tests for the deterministic AI workflow composer (CW7)."""
from __future__ import annotations

from packages.ai_planner.composer import (
    ComposeError,
    compile_flow,
    compose_and_compile,
    compose_flow,
    discover_plugins,
    recall_by_keywords,
)
from packages.plugin_topology.compiler import CompileError


def test_discover_full_contract_directory() -> None:
    specs = discover_plugins()
    assert len(specs) >= 100
    # the 100-plugin network root ids are all present
    assert "audit.foundation.nlp-process" in specs
    assert "audit.remedy.remedy-progress-track" in specs


def test_discover_verified_runtime_subset() -> None:
    verified = discover_plugins(lifecycle="verified")
    assert len(verified) == 100
    assert all(spec.lifecycle == "verified" for spec in verified.values())


def test_keyword_recall_hits() -> None:
    specs = discover_plugins()
    hit = recall_by_keywords("整改 跟踪 进度", specs, limit=10)
    assert "audit.remedy.remedy-progress-track" in hit
    hit2 = recall_by_keywords("底稿 证据 校验", specs, limit=10)
    assert "audit.evidence.evidence-verify" in hit2
    hit3 = recall_by_keywords("违规 条款 匹配", specs, limit=10)
    assert "audit.finding.violation-clause-match" in hit3


MAIN_CHAIN = [
    ("audit.foundation.finance-clean", "clean-finance-set", "audit.risk.finance-anomaly-alert", "clean-finance-set"),
    ("audit.risk.finance-anomaly-alert", "finance-anomaly-set", "audit.finding.suspicion-merge", "suspicion-set"),
    ("audit.finding.suspicion-merge", "merged-suspicion", "audit.finding.issue-type-judge", "merged-suspicion"),
    ("audit.finding.issue-type-judge", "issue-type-set", "audit.finding.violation-clause-match", "issue-type-set"),
    ("audit.finding.issue-type-judge", "issue-type-set", "audit.finding.issue-amount-compute", "issue-verify-input"),
    ("audit.finding.violation-clause-match", "violation-clause", "audit.finding.responsible-party-find", "issue-trace-input"),
    ("audit.finding.responsible-party-find", "responsible-party", "audit.finding.issue-grade", "responsible-party"),
    ("audit.finding.issue-amount-compute", "issue-amount", "audit.finding.issue-grade", "issue-amount"),
    ("audit.finding.issue-grade", "graded-issue", "audit.finding.auditee-feedback", "graded-issue"),
    ("audit.finding.auditee-feedback", "feedback-set", "audit.finding.issue-final-review", "feedback-set"),
    ("audit.finding.issue-final-review", "final-issue-set", "audit.report.issue-desc-write", "final-issue-set"),
    ("audit.risk.finance-anomaly-alert", "finance-anomaly-set", "audit.risk.risk-matrix-build", "finance-anomaly-set"),
    # batch E: mandate (立项) + plan (计划) start-of-chain
    ("audit.mandate.demand-collect", "demand-set", "audit.mandate.strategy-align", "demand-set"),
    ("audit.mandate.strategy-align", "aligned-demand", "audit.mandate.annual-propose", "aligned-demand"),
    ("audit.mandate.annual-propose", "proposal-set", "audit.mandate.proposal-score", "proposal-set"),
    ("audit.mandate.proposal-score", "scored-proposal", "audit.mandate.project-library", "scored-proposal"),
    ("audit.mandate.project-library", "project-snapshot", "audit.mandate.priority-rank", "project-snapshot"),
    ("audit.mandate.priority-rank", "priority-order", "audit.plan.annual-plan-build", "priority-order"),
    ("audit.mandate.project-library", "project-snapshot", "audit.mandate.notice-generate", "project-snapshot"),
    ("audit.mandate.material-submit", "submitted-material", "audit.mandate.material-precheck", "submitted-material"),
    ("audit.mandate.project-library", "project-snapshot", "audit.mandate.team-forming", "project-snapshot"),
    ("audit.mandate.team-forming", "team-scheme", "audit.plan.staff-schedule", "team-scheme"),
    ("audit.plan.staff-schedule", "schedule-plan", "audit.plan.effort-budget", "schedule-plan"),
    ("audit.plan.staff-schedule", "schedule-plan", "audit.plan.resource-conflict-detect", "schedule-plan"),
    ("audit.plan.project-scheme-build", "project-scheme", "audit.plan.program-template-match", "scheme-request"),
    ("audit.plan.sampling-select", "sampling-plan", "audit.plan.sample-size-compute", "sampling-plan"),
    ("audit.plan.program-template-match", "program-template", "audit.field.workpaper-build", "program-template"),
    # batch F: risk stage (风险扫描 → 矩阵 → 等级 → 领域 → 建议 → 计划)
    ("audit.risk.macro-policy-risk-scan", "policy-risk-set", "audit.risk.risk-matrix-build", "policy-risk-set"),
    ("audit.risk.industry-risk-benchmark", "industry-risk-set", "audit.risk.risk-matrix-build", "industry-risk-set"),
    ("audit.risk.internal-control-risk-map", "ic-risk-set", "audit.risk.risk-matrix-build", "ic-risk-set"),
    ("audit.risk.process-gap-detect", "process-gap-set", "audit.risk.risk-matrix-build", "process-gap-set"),
    ("audit.risk.fraud-risk-match", "fraud-risk-set", "audit.risk.risk-matrix-build", "fraud-risk-set"),
    ("audit.risk.risk-matrix-build", "risk-matrix", "audit.risk.risk-level-assign", "risk-matrix"),
    ("audit.risk.risk-level-assign", "risk-level", "audit.risk.high-risk-area-locate", "risk-level"),
    ("audit.risk.risk-level-assign", "risk-level", "audit.risk.risk-heatmap-draw", "risk-level"),
    ("audit.risk.high-risk-area-locate", "high-risk-area", "audit.risk.risk-advice-generate", "high-risk-area"),
    ("audit.risk.high-risk-area-locate", "high-risk-area", "audit.plan.project-scheme-build", "high-risk-area"),
    ("audit.risk.risk-advice-generate", "risk-advice-set", "audit.plan.project-scheme-build", "risk-advice-set"),
    # batch G: evidence & workpaper
    ("audit.field.workpaper-build", "workpaper-draft", "audit.evidence.workpaper-reconcile", "workpaper-draft"),
    ("audit.field.workpaper-build", "workpaper-draft", "audit.evidence.evidence-index-link", "workpaper-draft"),
    ("audit.field.workpaper-build", "workpaper-draft", "audit.evidence.workpaper-version-diff", "workpaper-draft"),
    ("audit.field.workpaper-build", "workpaper-draft", "audit.evidence.workpaper-template-update", "workpaper-draft"),
    ("audit.field.workpaper-build", "workpaper-draft", "audit.evidence.workpaper-review3", "workpaper-draft"),
    ("audit.field.evidence-photo", "photo-evidence", "audit.evidence.evidence-archive", "evidence-input"),
    ("audit.field.evidence-photo", "photo-evidence", "audit.evidence.evidence-index-link", "photo-evidence"),
    ("audit.evidence.evidence-index-link", "evidence-index", "audit.evidence.evidence-verify", "evidence-index"),
    ("audit.evidence.workpaper-reconcile", "reconcile-report", "audit.evidence.workpaper-review3", "reconcile-report"),
    ("audit.evidence.workpaper-review3", "review-status", "audit.evidence.e-signature", "review-status"),
    ("audit.evidence.e-signature", "signed-workpaper", "audit.evidence.workpaper-encrypt-store", "signed-workpaper"),
    # batch H: report stage (报告：框架→数据校验→三级审核→提炼/脱敏发布)
    ("audit.mandate.project-library", "project-snapshot", "audit.report.report-frame-build", "project-snapshot"),
    ("audit.finding.issue-final-review", "final-issue-set", "audit.report.advice-match", "final-issue-set"),
    ("audit.report.report-frame-build", "report-frame", "audit.report.report-data-check", "report-draft-input"),
    ("audit.report.report-data-check", "report-check-report", "audit.report.report-multi-review", "report-check-report"),
    ("audit.report.report-multi-review", "report-approved", "audit.report.result-distill", "report-approved"),
    ("audit.report.report-multi-review", "report-approved", "audit.report.notice-mask-publish", "report-approved"),
    # batch I: remedy stage (整改：派单→方案审核→进度→逾期预警→成效验证→销号→公示)
    ("audit.finding.issue-final-review", "final-issue-set", "audit.remedy.remedy-dispatch", "final-issue-set"),
    ("audit.remedy.remedy-dispatch", "remedy-task", "audit.remedy.remedy-plan-review", "remedy-task"),
    ("audit.remedy.remedy-plan-review", "remedy-plan-approved", "audit.remedy.remedy-progress-track", "remedy-plan-approved"),
    ("audit.remedy.remedy-progress-track", "remedy-progress", "audit.remedy.remedy-overdue-alert", "remedy-progress"),
    ("audit.evidence.evidence-index-link", "evidence-index", "audit.remedy.remedy-effect-verify", "evidence-index"),
    ("audit.remedy.remedy-effect-verify", "remedy-verdict", "audit.remedy.remedy-close", "remedy-verdict"),
    ("audit.remedy.remedy-close", "remedy-ledger", "audit.remedy.remedy-publish", "remedy-ledger"),
    # batch J: govern layer (治理：成效评估/案例库/趋势分析/规则迭代/方法沉淀/质量分)
    ("audit.remedy.remedy-close", "remedy-ledger", "audit.govern.effect-evaluate", "remedy-ledger"),
    ("audit.report.result-distill", "distilled-result", "audit.govern.case-library-update", "distilled-result"),
    ("audit.govern.issue-trend-analysis", "trend-report", "audit.govern.rule-iteration", "trend-report"),
]


def test_compose_verified_workflow_is_deterministic_and_compiles() -> None:
    verified = list(discover_plugins(lifecycle="verified"))
    first = compose_flow(goal="资金舞弊专项审计", select=verified, chain=MAIN_CHAIN, plan_key="plan-ai-composer-test")
    second = compose_flow(goal="资金舞弊专项审计", select=verified, chain=MAIN_CHAIN, plan_key="plan-ai-composer-test")
    assert first["nodes"] == second["nodes"]
    assert first["edges"] == second["edges"]
    assert first["seed_inputs"] == second["seed_inputs"]
    assert len(first["nodes"]) == 100
    # every explicit chain edge survived
    edge_keys = {(e["source_port"], e["target_port"]) for e in first["edges"]}
    for edge in MAIN_CHAIN:
        assert (edge[1], edge[3]) in edge_keys
    # field stage is part of the composed network
    node_ids = [n["node_instance_id"] for n in first["nodes"]]
    assert any(n.startswith("voucher-drilldown") for n in node_ids)
    assert any(n.startswith("workpaper-build") for n in node_ids)
    assert any(n.startswith("inventory-count") for n in node_ids)
    # mandate/plan start-of-chain is part of the composed network
    assert any(n.startswith("demand-collect") for n in node_ids)
    assert any(n.startswith("priority-rank") for n in node_ids)
    assert any(n.startswith("annual-plan-build") for n in node_ids)
    assert any(n.startswith("staff-schedule") for n in node_ids)
    assert any(n.startswith("program-template-match") for n in node_ids)
    # risk stage is part of the composed network
    assert any(n.startswith("macro-policy-risk-scan") for n in node_ids)
    assert any(n.startswith("risk-matrix-build") for n in node_ids)
    assert any(n.startswith("risk-level-assign") for n in node_ids)
    assert any(n.startswith("high-risk-area-locate") for n in node_ids)
    assert any(n.startswith("risk-advice-generate") for n in node_ids)
    # evidence stage is part of the composed network
    assert any(n.startswith("evidence-index-link") for n in node_ids)
    assert any(n.startswith("workpaper-reconcile") for n in node_ids)
    assert any(n.startswith("workpaper-review3") for n in node_ids)
    assert any(n.startswith("e-signature") for n in node_ids)
    assert any(n.startswith("workpaper-encrypt-store") for n in node_ids)
    # acyclic compile
    plan = compile_flow(first)
    assert len(plan.nodes) == 100
    assert len(plan.edges) == len(first["edges"])


def test_compose_and_compile_roundtrip() -> None:
    verified = list(discover_plugins(lifecycle="verified"))
    flow, plan = compose_and_compile(
        goal="资金舞弊专项审计", select=verified, chain=MAIN_CHAIN, plan_key="plan-ai-composer-rt",
    )
    assert flow["plan_key"] == "plan-ai-composer-rt"
    assert len(plan.edges) == len(flow["edges"])


def test_compose_rejects_unknown_plugin() -> None:
    try:
        compose_flow(goal="x", select=["audit.not-a-plugin"], plan_key="plan-ai-bad")
    except ComposeError as exc:
        assert "unknown plugin ids" in str(exc)
    else:
        raise AssertionError("expected ComposeError")


def test_compile_rejects_broken_contract_edge() -> None:
    def port(port_id: str, direction: str, schema_ref: str) -> dict:
        return {
            "port_id": port_id, "direction": direction, "schema_ref": schema_ref,
            "schema_version": "1.0.0", "schema_sha256": "a" * 64,
            "media_type": "application/json", "required": True,
            "cardinality": "one", "classification": "internal", "transport": "artifact_ref",
        }

    flow = {
        "plan_key": "plan-ai-broken",
        "nodes": [
            {
                "node_instance_id": "a-001", "plugin_id": "audit.foundation.nlp-process",
                "capability": "audit.foundation.nlp-process",
                "input_ports": [port("nlp-input", "input", "artifact-ref.schema.json")],
                "output_ports": [],
            },
            {
                "node_instance_id": "b-001", "plugin_id": "audit.foundation.rule-engine",
                "capability": "audit.foundation.rule-engine",
                "input_ports": [port("rule-input", "input", "artifact-ref.schema.json")],
                "output_ports": [],
            },
        ],
        "edges": [
            {
                "edge_id": "x1", "source_instance": "a-001", "source_port": "nlp-input",
                "target_instance": "b-001", "target_port": "rule-input",
            },
        ],
        "budget": {"max_chain_length": 4, "max_candidates": 100, "max_latency_ms": 1000},
        "seed_inputs": [],
    }
    # nlp-input is an input port used as an edge source: compile must fail
    try:
        compile_flow(flow)
    except CompileError:
        pass
    else:
        raise AssertionError("expected CompileError for broken edge")
