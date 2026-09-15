"""CW7 demo v5: the AI composes a 62-plugin workflow (mandate + risk +
plan + finding + field stages), compiles it and executes it against the
production Runs history.

Goal: 资金舞弊专项审计 — every verified network plugin is selected, the
main chain is pinned by explicit hints (chain), schema fallback fills
support edges, cycle rollback keeps the DAG acyclic, seeds are injected and
the whole flow runs through isolated children + policy gate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from packages.ai_planner.composer import compose_and_compile
from packages.plugin_runtime.runner import ArtifactInput
from packages.plugin_topology.service import TopologyService
from packages.policy.engine import PolicyEngine

DB = "postgresql://audit_app:admin@localhost:5432/audit_network"
ROOT = Path(r"D:\pythonpro\audit_network")
SIM = ROOT / ".data" / "audit-sim"
STAGING = ROOT / ".data" / "isolated-ai-composed-100"
GOAL = ("资金舞弊专项审计：需求征集、战略对齐、立项申报、立项评分、项目库、优先级排序、"
        "审计通知书、资料报送与预检、项目组组建、年度计划、项目方案、程序模板、抽样、样本量、"
        "排班、工时预算、资源冲突、计划版本；宏观政策风险、行业风险对标、内控流程风险、流程断点、"
        "舞弊特征、风险矩阵、风险等级、高风险领域、风险热图、风险建议；清洗凭证、扫描异常、"
        "现场签到与取证、凭证穿透、监盘盘点、访谈纪要、进度填报、证据校验、疑点合并、定性、"
        "违规条款、责任认定、问题分级、意见反馈、定性复核、报告框架、建议匹配、数据校验、"
        "三级审核、成果提炼、脱敏发布；整改派单、方案审核、进度跟踪、逾期预警、成效验证、"
        "整改销号、结果公示；项目质量评分、审计成效评估、案例库更新、问题趋势分析、"
        "规则库迭代、审计方法沉淀；多源采集、业务标准化、主数据映射、元数据管理、数据质量、"
        "全文检索、OCR、NLP、规则引擎、可视化、标签、权限、脱敏、加密、血缘追踪、工作流引擎")

_ALLOW = [
    "topology.chain.execute", "topology.chain.execute.isolated",
    "audit.foundation.finance-clean", "audit.risk.finance-anomaly-alert",
    "audit.risk.risk-matrix-build", "audit.finding.issue-type-judge",
    "audit.finding.issue-amount-compute", "audit.report.issue-desc-write",
    "audit.foundation.quality-check", "audit.foundation.tag-manage",
    "audit.foundation.metric-compute",
    "audit.foundation.nlp-process", "audit.foundation.rule-engine",
    "audit.foundation.ocr-extract",
    "audit.evidence.evidence-verify", "audit.finding.suspicion-merge",
    "audit.remedy.remedy-progress-track",
    "audit.finding.violation-clause-match", "audit.finding.responsible-party-find",
    "audit.finding.issue-grade", "audit.finding.auditee-feedback",
    "audit.finding.issue-final-review", "audit.field.suspicion-flag",
    # batch D: field stage
    "audit.field.asset-check", "audit.field.audit-log", "audit.field.confirm-letter",
    "audit.field.cross-dept-inquiry", "audit.field.evidence-photo",
    "audit.field.extension-approve", "audit.field.interview-record",
    "audit.field.inventory-count", "audit.field.meeting-minutes",
    "audit.field.progress-report", "audit.field.site-checkin-track",
    "audit.field.voucher-drilldown", "audit.field.workpaper-build",
    # batch E: mandate (立项) + plan (计划)
    "audit.mandate.demand-collect", "audit.mandate.strategy-align",
    "audit.mandate.annual-propose", "audit.mandate.proposal-score",
    "audit.mandate.project-library", "audit.mandate.priority-rank",
    "audit.mandate.notice-generate", "audit.mandate.material-submit",
    "audit.mandate.material-precheck", "audit.mandate.team-forming",
    "audit.plan.annual-plan-build", "audit.plan.project-scheme-build",
    "audit.plan.program-template-match", "audit.plan.sampling-select",
    "audit.plan.sample-size-compute", "audit.plan.staff-schedule",
    "audit.plan.effort-budget", "audit.plan.resource-conflict-detect",
    "audit.plan.plan-version-control",
    # batch F: risk (风险识别与评估)
    "audit.risk.macro-policy-risk-scan", "audit.risk.industry-risk-benchmark",
    "audit.risk.internal-control-risk-map", "audit.risk.process-gap-detect",
    "audit.risk.fraud-risk-match", "audit.risk.risk-level-assign",
    "audit.risk.high-risk-area-locate", "audit.risk.risk-heatmap-draw",
    "audit.risk.risk-advice-generate",
    # batch G: evidence & workpaper
    "audit.evidence.evidence-archive", "audit.evidence.evidence-index-link",
    "audit.evidence.workpaper-reconcile", "audit.evidence.workpaper-review3",
    "audit.evidence.e-signature", "audit.evidence.workpaper-encrypt-store",
    "audit.evidence.workpaper-version-diff", "audit.evidence.workpaper-template-update",
    "audit.evidence.workpaper-borrow-approve",
    # batch H: report stage
    "audit.report.report-frame-build", "audit.report.advice-match",
    "audit.report.report-data-check", "audit.report.report-multi-review",
    "audit.report.result-distill", "audit.report.notice-mask-publish",
    # batch I: remedy stage
    "audit.remedy.remedy-dispatch", "audit.remedy.remedy-plan-review",
    "audit.remedy.remedy-overdue-alert", "audit.remedy.remedy-effect-verify",
    "audit.remedy.remedy-close", "audit.remedy.remedy-publish",
    # batch J: govern layer
    "audit.govern.project-quality-score", "audit.govern.effect-evaluate",
    "audit.govern.case-library-update", "audit.govern.issue-trend-analysis",
    "audit.govern.rule-iteration", "audit.govern.method-distill",
    # batch K: foundation/support layer (补缺 11 个)
    "audit.foundation.biz-standardize", "audit.foundation.data-encrypt",
    "audit.foundation.data-mask", "audit.foundation.fulltext-search",
    "audit.foundation.lineage-track", "audit.foundation.master-mapping",
    "audit.foundation.metadata-manage", "audit.foundation.multi-source-collect",
    "audit.foundation.permission-control", "audit.foundation.viz-analysis",
    "audit.foundation.workflow-engine",
]

CHAIN = [
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
    # batch F: risk stage
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
    # batch H: report stage
    ("audit.mandate.project-library", "project-snapshot", "audit.report.report-frame-build", "project-snapshot"),
    ("audit.finding.issue-final-review", "final-issue-set", "audit.report.advice-match", "final-issue-set"),
    ("audit.report.report-frame-build", "report-frame", "audit.report.report-data-check", "report-draft-input"),
    ("audit.report.report-data-check", "report-check-report", "audit.report.report-multi-review", "report-check-report"),
    ("audit.report.report-multi-review", "report-approved", "audit.report.result-distill", "report-approved"),
    ("audit.report.report-multi-review", "report-approved", "audit.report.notice-mask-publish", "report-approved"),
    # batch I: remedy stage
    ("audit.finding.issue-final-review", "final-issue-set", "audit.remedy.remedy-dispatch", "final-issue-set"),
    ("audit.remedy.remedy-dispatch", "remedy-task", "audit.remedy.remedy-plan-review", "remedy-task"),
    ("audit.remedy.remedy-plan-review", "remedy-plan-approved", "audit.remedy.remedy-progress-track", "remedy-plan-approved"),
    ("audit.remedy.remedy-progress-track", "remedy-progress", "audit.remedy.remedy-overdue-alert", "remedy-progress"),
    ("audit.evidence.evidence-index-link", "evidence-index", "audit.remedy.remedy-effect-verify", "evidence-index"),
    ("audit.remedy.remedy-effect-verify", "remedy-verdict", "audit.remedy.remedy-close", "remedy-verdict"),
    ("audit.remedy.remedy-close", "remedy-ledger", "audit.remedy.remedy-publish", "remedy-ledger"),
    # batch J: govern layer
    ("audit.remedy.remedy-close", "remedy-ledger", "audit.govern.effect-evaluate", "remedy-ledger"),
    ("audit.report.result-distill", "distilled-result", "audit.govern.case-library-update", "distilled-result"),
    ("audit.govern.issue-trend-analysis", "trend-report", "audit.govern.rule-iteration", "trend-report"),
]

VERIFIED = [
    "audit.foundation.finance-clean", "audit.risk.finance-anomaly-alert",
    "audit.risk.risk-matrix-build", "audit.finding.issue-type-judge",
    "audit.finding.issue-amount-compute", "audit.report.issue-desc-write",
    "audit.foundation.quality-check", "audit.foundation.tag-manage",
    "audit.foundation.metric-compute",
    "audit.foundation.nlp-process", "audit.foundation.rule-engine",
    "audit.foundation.ocr-extract",
    "audit.evidence.evidence-verify", "audit.finding.suspicion-merge",
    "audit.remedy.remedy-progress-track",
    "audit.finding.violation-clause-match", "audit.finding.responsible-party-find",
    "audit.finding.issue-grade", "audit.finding.auditee-feedback",
    "audit.finding.issue-final-review", "audit.field.suspicion-flag",
    # batch D: field stage
    "audit.field.asset-check", "audit.field.audit-log", "audit.field.confirm-letter",
    "audit.field.cross-dept-inquiry", "audit.field.evidence-photo",
    "audit.field.extension-approve", "audit.field.interview-record",
    "audit.field.inventory-count", "audit.field.meeting-minutes",
    "audit.field.progress-report", "audit.field.site-checkin-track",
    "audit.field.voucher-drilldown", "audit.field.workpaper-build",
    # batch E: mandate (立项) + plan (计划)
    "audit.mandate.demand-collect", "audit.mandate.strategy-align",
    "audit.mandate.annual-propose", "audit.mandate.proposal-score",
    "audit.mandate.project-library", "audit.mandate.priority-rank",
    "audit.mandate.notice-generate", "audit.mandate.material-submit",
    "audit.mandate.material-precheck", "audit.mandate.team-forming",
    "audit.plan.annual-plan-build", "audit.plan.project-scheme-build",
    "audit.plan.program-template-match", "audit.plan.sampling-select",
    "audit.plan.sample-size-compute", "audit.plan.staff-schedule",
    "audit.plan.effort-budget", "audit.plan.resource-conflict-detect",
    "audit.plan.plan-version-control",
    # batch F: risk (风险识别与评估)
    "audit.risk.macro-policy-risk-scan", "audit.risk.industry-risk-benchmark",
    "audit.risk.internal-control-risk-map", "audit.risk.process-gap-detect",
    "audit.risk.fraud-risk-match", "audit.risk.risk-level-assign",
    "audit.risk.high-risk-area-locate", "audit.risk.risk-heatmap-draw",
    "audit.risk.risk-advice-generate",
    # batch G: evidence & workpaper
    "audit.evidence.evidence-archive", "audit.evidence.evidence-index-link",
    "audit.evidence.workpaper-reconcile", "audit.evidence.workpaper-review3",
    "audit.evidence.e-signature", "audit.evidence.workpaper-encrypt-store",
    "audit.evidence.workpaper-version-diff", "audit.evidence.workpaper-template-update",
    "audit.evidence.workpaper-borrow-approve",
    # batch H: report stage
    "audit.report.report-frame-build", "audit.report.advice-match",
    "audit.report.report-data-check", "audit.report.report-multi-review",
    "audit.report.result-distill", "audit.report.notice-mask-publish",
    # batch I: remedy stage
    "audit.remedy.remedy-dispatch", "audit.remedy.remedy-plan-review",
    "audit.remedy.remedy-overdue-alert", "audit.remedy.remedy-effect-verify",
    "audit.remedy.remedy-close", "audit.remedy.remedy-publish",
    # batch J: govern layer
    "audit.govern.project-quality-score", "audit.govern.effect-evaluate",
    "audit.govern.case-library-update", "audit.govern.issue-trend-analysis",
    "audit.govern.rule-iteration", "audit.govern.method-distill",
    # batch K: foundation/support layer (补缺 11 个)
    "audit.foundation.biz-standardize", "audit.foundation.data-encrypt",
    "audit.foundation.data-mask", "audit.foundation.fulltext-search",
    "audit.foundation.lineage-track", "audit.foundation.master-mapping",
    "audit.foundation.metadata-manage", "audit.foundation.multi-source-collect",
    "audit.foundation.permission-control", "audit.foundation.viz-analysis",
    "audit.foundation.workflow-engine",
]


def _input(staging: Path, name: str, raw: bytes, media_type: str) -> ArtifactInput:
    path = staging / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return ArtifactInput(
        artifact_id=uuid4(), tenant_id=uuid4(), uri=path.resolve().as_uri(),
        media_type=media_type, sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw), classification="audit_confidential",
    )


def _json(data) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def main() -> None:
    staging = STAGING
    flow, plan = compose_and_compile(
        goal=GOAL, select=VERIFIED, chain=CHAIN, plan_key="plan-ai-fund-fraud-100",
    )
    print("AI composed workflow:")
    print(f"  nodes={len(flow['nodes'])}  edges={len(flow['edges'])}  seeds={len(flow['seed_inputs'])}  chain={len(flow['chain'])}")
    print("  edges:")
    for edge in flow["edges"]:
        print(f"    {edge['source_instance']}.{edge['source_port']} -> {edge['target_instance']}.{edge['target_port']}")

    ledger_raw = (SIM / "ledger.csv").read_bytes()
    master_rows = [
        {"dept_code": "FIN", "dept_name": "财务部", "owner": "张会计", "role": "制单", "entry_id": "E9301"},
        {"dept_code": "PUR", "dept_name": "采购部", "owner": "李采购", "role": "经办", "entry_id": "E9201"},
        {"dept_code": "SAL", "dept_name": "销售部", "owner": "王销售", "role": "经办", "entry_id": "E9101"},
        {"dept_code": "OPS", "dept_name": "运营部", "owner": "赵运营", "role": "经办", "entry_id": "E9001"},
    ]
    seeds: dict[tuple[str, str], ArtifactInput] = {
        ("finance-clean-001", "raw-finance-set"): _input(staging, "ledger.csv", ledger_raw, "text/csv"),
        ("quality-check-001", "quality-input"): _input(staging, "quality-ledger.csv", ledger_raw, "text/csv"),
        ("metric-compute-001", "metric-input"): _input(staging, "metric-ledger.csv", ledger_raw, "text/csv"),
        ("nlp-process-001", "nlp-input"): _input(
            staging, "nlp.json",
            _json({"mode": "summary", "text": "审计发现异常。供应商重复入账三次。涉及金额重大。建议整改。",
                   "keywords": ["重复", "重大"]}), "application/json",
        ),
        ("ocr-extract-001", "ocr-image"): _input(
            staging, "invoice-scan.txt", "发票号码 INV-2026-001\n金额 12,000.00".encode("utf-8"), "text/plain",
        ),
        ("rule-engine-001", "rule-input"): _input(
            staging, "rules.json",
            _json({"rules": [{"rule_id": "R1", "rule_key": "outlier_amount", "operator": "gte",
                              "field": "debit_amount", "value": 1000000}],
                   "rows": [{"entry_id": r["entry_id"], "debit_amount": r["debit_amount"]}
                            for r in (dict(zip(("entry_id", "date", "account_code", "description",
                                                "debit_amount", "credit_amount"), line.split(",")))
                                      for line in ledger_raw.decode("utf-8-sig").strip().splitlines()[1:])]}),
            "application/json",
        ),
        ("tag-manage-001", "tag-query"): _input(
            staging, "tag-query.json",
            _json({"object_type": "ledger", "dimensions": ["amount-band", "entry-status", "risk-level"],
                   "rows": [{"entry_id": "T1", "amount": "1200000", "status": "outlier", "severity": "高"},
                            {"entry_id": "T2", "amount": "50000", "status": "ok", "severity": "中"}]}),
            "application/json",
        ),
        ("evidence-verify-001", "evidence-index"): _input(
            staging, "lineage.json",
            _json({"contract_id": "evidence-lineage", "contract_version": "1.0.0",
                   "lineage_id": "L1", "release_sha256": "b" * 64, "truncated": False,
                   "nodes": [{"node_id": "e1", "kind": "photo", "sha256": "c" * 64},
                             {"node_id": "e2", "kind": "invoice", "sha256": "d" * 64}],
                   "edges": [{"edge_id": "x1", "source": "e1", "target": "e2"}]}),
            "application/json",
        ),
        ("suspicion-merge-001", "photo-evidence"): _input(
            staging, "photos.json", _json({"photos": [{"photo_id": "P1", "row_ref": "E9301"}]}),
            "application/json",
        ),
        ("suspicion-flag-001", "field-finding-input"): _input(
            staging, "field.json",
            _json({"period": "2026-01", "ledger_sha256": "e" * 64, "rule_pack_sha256": "f" * 64,
                   "field_findings": [
                       {"rule_id": "FIELD-1", "rule_key": "voucher_gap", "severity": "high", "row_ref": "E9301",
                        "source_ref": "field", "score": 0.9, "note": "现场发现凭证缺失"},
                       {"rule_id": "FIELD-2", "rule_key": "sign_mismatch", "severity": "medium", "row_ref": "E9201",
                        "source_ref": "field", "score": 0.7, "note": "签字不一致"},
                   ]}),
            "application/json",
        ),
        ("responsible-party-find-001", "master-map"): _input(
            staging, "master.json",
            _json({"contract_id": "dataset-validation", "contract_version": "1.0.0",
                   "checks": [], "violations": [], "summary": {}, "rows": master_rows}),
            "application/json",
        ),
        ("remedy-progress-track-001", "remedy-plan-approved"): _input(
            staging, "remedy.json",
            _json({"workflow_id": "W1", "tenant_id": "t1", "key": "remedy-2026-01", "version": "1",
                   "input_schema": {}, "output_schema": {}, "as_of": "2026-06-30", "edges": [],
                   "nodes": [
                       {"id": "t1", "name": "补录凭证", "props": {"owner": "张三", "due_date": "2026-06-01", "status": "done"}},
                       {"id": "t2", "name": "调整分录", "props": {"owner": "李四", "due_date": "2026-06-10", "status": "done"}},
                       {"id": "t3", "name": "回收资金", "props": {"owner": "王五", "due_date": "2026-07-20", "status": "in_progress"}},
                       {"id": "t4", "name": "追责处理", "props": {"owner": "赵六", "due_date": "2026-05-01", "status": "not_started"}},
                   ]}),
            "application/json",
        ),
        # ---- batch D: field-stage seeds ----
        ("site-checkin-track-001", "checkin-input"): _input(
            staging, "checkins.json",
            _json({"period": "2026-01", "checkins": [
                {"staff_id": "S1", "name": "张审计", "ts": "2026-01-15T09:00:00+08:00", "location": "被审单位A"},
                {"staff_id": "S2", "name": "李审计", "ts": "2026-01-15T09:05:00+08:00", "location": "被审单位A"},
                {"staff_id": "S1", "name": "张审计", "ts": "2026-01-16T09:00:00+08:00", "location": "被审单位A"},
            ]}),
            "application/json",
        ),
        ("audit-log-001", "log-event"): _input(
            staging, "log-events.json",
            _json({"log_id": "L-2026-01", "tenant_id": "local-dev", "events": [
                {"op": "read", "node": "ledger", "ts": "2026-01-15T10:00:00Z", "actor": "s1"},
                {"op": "export", "node": "workpaper", "ts": "2026-01-15T11:00:00Z", "actor": "s2"},
                {"op": "approve", "node": "review", "ts": "2026-01-15T09:30:00Z", "actor": "m1"},
            ]}),
            "application/json",
        ),
        ("confirm-letter-001", "confirm-input"): _input(
            staging, "confirmations.json",
            _json({"period": "2026-01", "confirmations": [
                {"counterparty": "供应商A", "amount": 120000, "reply_status": "replied", "replied_at": "2026-02-01"},
                {"counterparty": "供应商B", "amount": 80000, "reply_status": "pending"},
            ]}),
            "application/json",
        ),
        ("cross-dept-inquiry-001", "inquiry-request"): _input(
            staging, "inquiries.json",
            _json({"period": "2026-01", "requests": [
                {"dept": "采购部", "data_type": "合同台账", "purpose": "核验供应商", "status": "delivered"},
                {"dept": "销售部", "data_type": "订单明细", "purpose": "抽样核对"},
            ]}),
            "application/json",
        ),
        ("evidence-photo-001", "photo-input"): _input(
            staging, "photos.json",
            _json({"period": "2026-01", "evidence_bundle_id": "ev-2026-01", "photos": [
                {"photo_id": "P1", "row_ref": "E9301", "ts": "2026-01-15T10:00:00Z", "sha256": "c" * 64,
                 "note": "现场拍摄凭证缺失证据"},
                {"photo_id": "P2", "row_ref": "E9201", "ts": "2026-01-15T11:00:00Z", "sha256": "d" * 64,
                 "note": "仓库盘点照片"},
            ]}),
            "application/json",
        ),
        ("extension-approve-001", "extension-request"): _input(
            staging, "extension.json",
            _json({"request_id": "R1", "reason": "函证回函周期较长", "days": 7,
                   "due_date": "2026-02-06", "applicant": "张审计", "tenant_id": "local-dev"}),
            "application/json",
        ),
        ("interview-record-001", "interview-input"): _input(
            staging, "interviews.json",
            _json({"period": "2026-01", "interviews": [
                {"person": "王采购", "role": "经办", "date": "2026-01-14", "qa": [
                    {"q": "供应商审批流程？", "a": "需要两级审批"},
                    {"q": "供应商选择方式？", "a": ""},
                ]},
                {"person": "刘会计", "role": "制单", "date": "2026-01-14", "qa": [
                    {"q": "对账周期？", "a": "月度"},
                ]},
            ]}),
            "application/json",
        ),
        ("inventory-count-001", "inventory-input"): _input(
            staging, "inventory.json",
            _json({"period": "2026-01", "items": [
                {"sku": "S001", "name": "原料A", "book_qty": 10, "count_qty": 10},
                {"sku": "S002", "name": "原料B", "book_qty": 10, "count_qty": 5},
            ]}),
            "application/json",
        ),
        ("meeting-minutes-001", "meeting-audio"): _input(
            staging, "meeting.json",
            _json({"meeting_id": "M1", "segments": [
                {"speaker": "组长", "text": "今天主要讨论资金专项问题。", "ts": "2026-01-15T14:00:00Z"},
                {"speaker": "组长", "text": "决议：成立整改小组，一周内完成。", "ts": "2026-01-15T14:05:00Z"},
            ]}),
            "application/json",
        ),
        ("progress-report-001", "daily-progress"): _input(
            staging, "budget.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"period": "2026-01", "date": "2026-01-15T00:00:00+08:00",
                                "budget_hours": 10, "completed_hours": 7}}),
            "application/json",
        ),
        ("asset-check-001", "asset-input"): _input(
            staging, "assets.json",
            _json({"assets": [
                {"asset_code": "AST-001", "name": "服务器", "book_loc": "机房A", "found_loc": "机房A"},
                {"asset_code": "AST-002", "name": "笔记本", "book_loc": "机房B", "found_loc": "仓库C"},
            ]}),
            "application/json",
        ),
        ("workpaper-build-001", "program-template"): _input(
            staging, "program-template.json",
            _json({"engagement_id": "ENG-2026-001", "engagement_name": "资金专项审计",
                   "template_id": "TPL-1", "procedures": [
                       {"name": "凭证抽查", "detail": "对全部大额凭证逐笔核查"},
                       {"name": "函证核对", "detail": "向供应商发函核对余额"},
                   ]}),
            "application/json",
        ),
        ("workpaper-build-001", "sample-size"): _input(
            staging, "sample-size.json",
            _json({"contract_id": "metric-series", "contract_version": "1.0.0",
                   "series_id": "sample-1", "metric": "sample_size", "unit": "count",
                   "window_minutes": 1440,
                   "points": [{"at": "2026-01-15T00:00:00+08:00", "value": 5}]}),
            "application/json",
        ),
        # ---- batch E: mandate + plan seeds ----
        ("demand-collect-001", "demand-input"): _input(
            staging, "demands.json",
            _json({"period": "2026", "demands": [
                {"demand_id": "DM-0001", "dept": "财务部", "title": "资金流向核查",
                 "priority": "high", "detail": "核查大额资金流向与异常支付"},
                {"demand_id": "DM-0002", "dept": "采购部", "title": "采购合规检查",
                 "priority": "medium", "detail": "抽查采购合同与比价记录"},
                {"demand_id": "DM-0003", "dept": "运营部", "title": "日常费用报销",
                 "priority": "low", "detail": "一般性复核"},
            ]}),
            "application/json",
        ),
        ("project-scheme-build-001", "high-risk-area"): _input(
            staging, "high-risk.json",
            _json({"contract_id": "metric-series", "contract_version": "1.0.0",
                   "series_id": "high-risk-2026", "metric": "risk_score", "unit": "score",
                   "window_minutes": 1440,
                   "points": [{"at": "2026-01-01T00:00:00+08:00", "value": 0.9, "label": "资金收付"},
                              {"at": "2026-01-01T00:00:00+08:00", "value": 0.8, "label": "采购合同"}]}),
            "application/json",
        ),
        ("project-scheme-build-001", "risk-advice-set"): _input(
            staging, "risk-advice.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"title": "风险应对建议", "advices": [
                       {"advice": "重点核查大额资金流向与异常支付"},
                       {"advice": "对采购合同执行全量比价核验"},
                   ]}}),
            "application/json",
        ),
        ("sampling-select-001", "sampling-input"): _input(
            staging, "population.json",
            _json({"method": "materiality", "population": [
                {"item_id": "I1", "amount": 2000000},
                {"item_id": "I2", "amount": 50000},
                {"item_id": "I3", "amount": 30000},
                {"item_id": "I4", "amount": 10000},
            ]}),
            "application/json",
        ),
        ("plan-version-control-001", "plan-change"): _input(
            staging, "plan-changes.json",
            _json({"plan_key": "annual-2026", "tenant_id": "local-dev", "changes": [
                {"description": "调整抽样比例", "author": "张审计"},
                {"description": "新增资金领域程序", "author": "李审计"},
            ]}),
            "application/json",
        ),
        ("material-submit-001", "notice-accept"): _input(
            staging, "submitted-units.json",
            _json({"project": "PR-0002", "tenant_id": "local-dev", "units": [
                {"unit_name": "财务部", "materials": ["通知书回执", "营业执照", "财务报表", "资金流水", "内控文档"],
                 "submitted_at": "2026-01-10"},
                {"unit_name": "采购部", "materials": ["通知书回执", "营业执照"], "submitted_at": "2026-01-11"},
            ]}),
            "application/json",
        ),
        # ---- batch F: risk-scan seeds ----
        ("macro-policy-risk-scan-001", "policy-input"): _input(
            staging, "policy-input.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"period": "2026", "policies": [
                       {"policy_id": "P1", "title": "大额资金支付监管办法", "content": "对大额资金流向实施穿透核查"},
                       {"policy_id": "P2", "title": "员工考勤制度", "content": "规范考勤管理"},
                   ]}}),
            "application/json",
        ),
        ("industry-risk-benchmark-001", "industry-benchmark"): _input(
            staging, "industry-benchmark.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"period": "2026", "industry_risks": [
                       {"risk_id": "I1", "name": "供应商舞弊高发", "severity": "high", "score": 0.8},
                       {"risk_id": "I2", "name": "应收账期恶化", "severity": "medium", "score": 0.5},
                   ]}}),
            "application/json",
        ),
        ("internal-control-risk-map-001", "ic-flow"): _input(
            staging, "ic-flow.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"period": "2026", "control_steps": [
                       {"step_id": "S1", "name": "付款申请", "control": True, "approval": True},
                       {"step_id": "S2", "name": "资金支付", "control": False, "approval": True},
                       {"step_id": "S3", "name": "对账复核", "control": True, "approval": False},
                   ]}}),
            "application/json",
        ),
        ("process-gap-detect-001", "process-input"): _input(
            staging, "process-input.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"period": "2026", "process_steps": [
                       {"step_id": "P1", "name": "合同签订", "owner": "采购部", "approval": True},
                       {"step_id": "P2", "name": "收货入库", "owner": "", "approval": True},
                   ]}}),
            "application/json",
        ),
        ("workpaper-borrow-approve-001", "borrow-request"): _input(
            staging, "borrow-request.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"period": "2026", "requests": [
                       {"request_id": "B1", "workpaper_id": "WP-0001", "purpose": "复核底稿"}, {"request_id": "B2", "workpaper_id": "WP-0002", "purpose": ""},
                   ]}}),
            "application/json",
        ),
        ("fraud-risk-match-001", "fraud-input"): _input(
            staging, "fraud-input.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"period": "2026", "fraud_scenarios": [
                       {"scenario_id": "F1", "name": "虚构供应商", "pressure": True, "opportunity": True, "rationalization": True},
                       {"scenario_id": "F2", "name": "审批流于形式", "opportunity": True, "rationalization": False, "pressure": False},
                   ]}}),
            "application/json",
        ),
        ("remedy-effect-verify-001", "remedy-evidence"): _input(
            staging, "remedy-evidence.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"period": "2026", "evidence": [
                       {"evidence_id": "EV-1", "valid": True, "source": "整改证据-补录凭证"},
                       {"evidence_id": "EV-2", "valid": True, "source": "整改证据-调整分录"},
                       {"evidence_id": "EV-3", "valid": True, "source": "整改证据-回收资金"},
                   ]}}),
            "application/json",
        ),
        ("project-quality-score-001", "project-flow-input"): _input(
            staging, "project-flow.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"checks": [
                       {"check_id": "columns", "status": "pass", "message": "立项完整"},
                       {"check_id": "freshness", "status": "pass", "message": "现场及时"},
                       {"check_id": "missing_rate", "status": "pass", "message": "底稿完整"},
                       {"check_id": "point_in_time", "status": "warn", "message": "个别样本偏差"},
                   ]}}),
            "application/json",
        ),
        ("issue-trend-analysis-001", "history-issue-set"): _input(
            staging, "history-issues.json",
            _json({"contract_id": "finding-draft", "contract_version": "1.0.0",
                   "findings": [
                       {"issue_id": "H-1", "category": "资金管理", "severity": "high"},
                       {"issue_id": "H-2", "category": "资金管理", "severity": "medium"},
                       {"issue_id": "H-3", "category": "采购管理", "severity": "low"},
                       {"issue_id": "H-4", "category": "内控缺陷", "severity": "high"},
                   ]}),
            "application/json",
        ),
        ("method-distill-001", "project-review-input"): _input(
            staging, "review-notes.json",
            _json({"contract_id": "document-content", "contract_version": "1.0.0",
                   "artifact": {"review_notes": ["凭证穿透核查法", "供应商函证全量法", "内控穿行测试法"]}}),
            "application/json",
        ),
        # batch K: foundation/support seeds (11 independent support capabilities)
        ("biz-standardize-001", "raw-biz-set"): _input(
            staging, "raw-biz.json",
            _json({"contract_id": "dataset", "rows": [{"name": " 甲公司 ", "amt": 100}, {"name": "乙公司", "amt": 200}]}),
            "application/json",
        ),
        ("data-encrypt-001", "encrypt-request"): _input(
            staging, "encrypt-req.json", _json({"sensitive": True, "rows": [{"id": 1}]}), "application/json",
        ),
        ("data-mask-001", "mask-request"): _input(
            staging, "mask-req.json",
            _json({"rows": [{"user_name": "张三", "phone": "13800000000", "amt": 100}]}), "application/json",
        ),
        ("fulltext-search-001", "search-query"): _input(
            staging, "search-q.json",
            _json({"query": "资金", "documents": [{"doc_id": "D1", "title": "资金支付双人复核"}, {"doc_id": "D2", "title": "采购验收"}]}),
            "application/json",
        ),
        ("lineage-track-001", "lineage-query"): _input(
            staging, "lineage-q.json",
            _json({"nodes": [{"node_id": "e1"}], "edges": [{"edge_id": "x1", "source": "e1", "target": "e1"}]}),
            "application/json",
        ),
        ("master-mapping-001", "master-source"): _input(
            staging, "master-src.json",
            _json({"mappings": [{"source_code": "S01", "target_code": "T01"}]}), "application/json",
        ),
        ("metadata-manage-001", "metadata-query"): _input(
            staging, "meta-q.json",
            _json({"dictionary": [{"column": "amount", "type": "decimal"}]}), "application/json",
        ),
        ("multi-source-collect-001", "audit-source-request"): _input(
            staging, "collect-req.json",
            _json({"sources": ["finance", "oa", "crm"]}), "application/json",
        ),
        ("permission-control-001", "access-request"): _input(
            staging, "perm-req.json", _json({"role": "auditor", "action": "read"}), "application/json",
        ),
        ("viz-analysis-001", "viz-input"): _input(
            staging, "viz-in.json",
            _json({"series_id": "s1", "points": [{"at": "2026", "value": 1.0}]}), "application/json",
        ),
        ("workflow-engine-001", "workflow-request"): _input(
            staging, "wf-req.json",
            _json({"nodes": [{"node_id": "n1", "state": "approved"}, {"node_id": "n2", "state": "pending"}]}),
            "application/json",
        ),
    }
    for port_id, rule_key, row_ref in (
        ("industry-risk-set", "market_shift", "seed-industry"),
        ("ic-risk-set", "control_gap", "seed-ic"),
        ("process-gap-set", "process_gap", "seed-process"),
        ("fraud-risk-set", "fraud_triangle", "seed-fraud"),
        ("policy-risk-set", "policy_breach", "seed-policy"),
    ):
        channel = {
            "contract_id": "anomaly-candidates", "contract_version": "1.0.0",
            "ledger_sha256": "c" * 64, "schema_mapping_version": "1.0.0",
            "period": "2026-01", "rule_pack_sha256": "d" * 64, "summary": {},
            "candidates": [{"rule_id": f"{port_id}:seed", "rule_key": rule_key,
                            "severity": "medium", "row_ref": row_ref, "source_ref": port_id, "score": 0.6}],
        }
        seeds[("risk-matrix-build-001", port_id)] = _input(
            staging, f"{port_id}.json", _json(channel), "application/json",
        )

    missing = [s for s in flow["seed_inputs"] if s not in seeds]
    if missing:
        print("MISSING SEEDS:", missing)
        raise SystemExit(1)

    service = TopologyService(DB, policy=PolicyEngine(allow=_ALLOW))
    result = service.start_plan_run(
        plan, seed_inputs=seeds, worker_id="ai-composer-100", staging_root=staging,
    )
    print("run_id:", result.get("run_id"))
    print("status:", result.get("status"))
    attempts = result.get("attempts") or []
    print("attempt_count:", len(attempts))
    print("succeeded:", sum(1 for a in attempts if a.get("status") == "succeeded"))
    print("failed:", sum(1 for a in attempts if a.get("status") != "succeeded"))
    for attempt in attempts:
        print("  attempt:", attempt.get("node_instance_id"), attempt.get("capability"), attempt.get("status"), str(attempt.get("error") or "")[:160])
    print("OK" if result.get("status") == "succeeded" else "FAILED")


if __name__ == "__main__":
    main()
