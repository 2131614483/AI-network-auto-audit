import {
  ApartmentOutlined,
  AuditOutlined,
  BorderOutlined,
  BookOutlined,
  BulbOutlined,
  CloseOutlined,
  ClusterOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  FullscreenExitOutlined,
  FundProjectionScreenOutlined,
  MinusOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { Alert, App as AntApp, Badge, Button, Card, ConfigProvider, Descriptions, Form, Input, Layout, Menu, Modal, Progress, Result, Select, Space, Spin, Table, Tabs, Tag, Typography, theme } from "antd";
import type { MenuProps, TableColumnsType } from "antd";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import GraphExplorer, { graphNodeTypeLabel, type GraphVisualization, type GraphVisualizationNode } from "./components/GraphExplorer";
import CommandPalette, { type NavPage } from "./components/CommandPalette";
import ComingSoonPage from "./components/pages/ComingSoonPage";
import AIChatPanel from "./components/AIChatPanel";
import AISettings from "./components/AISettings";
import LogView from "./components/LogView";
import PageErrorBoundary from "./components/PageErrorBoundary";
import PluginFlowGraph from "./components/PluginFlowGraph";
import ShowcaseFrame from "./components/ShowcaseFrame";
import RunCanvas from "./components/RunCanvas";
import RunsPanel from "./components/RunsPanel";
import { pageRegistry, type PageProps } from "./components/pages";
import type { ChatDraft } from "./model/canvasChat";
import { applyDraftEdit, draftToProjection, type DraftEdit } from "./model/canvasDraft";
import { sourcePairsFor } from "./model/canvasStarter";
import type { CanvasProjection, RunsFeed } from "./model/runCanvas";
import { anyRunRunning, createTopologyPlan, evidenceHashShort, evidenceMismatchLabel, evidenceScopeLabel, evidenceSourceTableLabel, evidenceVerifiedLabel, executionModeLabel, planningIntentCanGenerate, planningIntentEvidenceNote, planningIntentLengthHint, planningIntentMatchKindLabel, planningIntentMatchSourceLabel, planningIntentNodeTypeLabel, pluginTopologyCatalog, proposalActionLabel, proposalCanDecide, proposalCanRerun, proposalStatusLabel, rollbackActionLabel, runCanVerify, runStatusLabel, topologyAxes, topologyGraphForAxis, verificationStatusLabel, type ClusterAxis, type EvidenceAnchorEntry, type EvidenceExport, type EvidenceProof, type EvidenceScope, type EvidenceStatus, type PlanningIntent, type PlanningIntentListItem, type PlanningIntentMatchedNode, type RemediationProposal, type TopologyBlueprint, type TopologyPlan, type TopologyPlanNode } from "./model/pluginTopology";
import { controlPlaneState, type Connection, type WorkspaceFacts, workspaceMetricRows } from "./model/workspace";

type ViewKey = string;
type Bootstrap = { api_version: string; shell_version: string; slots: string[]; features: { policy_gated_actions: boolean } };
type Contribution = { plugin_id: string; plugin_version: string; navigation_count: number; view_count: number; action_count: number };
type Approval = { capability: string; risk_class: string; status: string; created_at: string; id?: string; tool_call_id?: string; argument_hash?: string; reason?: string | null; expires_at?: string | null; decided_at?: string | null; authorization_lease_id?: string | null; trace_id?: string };
type Tenant = { tenant_id: string; tenant_slug: string };
type KnowledgeStats = { documents: number; chunks: number; embedding_chunks: number; embedding_model: string | null; embedding_coverage: number; embedding_models: Record<string, number>; waiting_extractor: number; failed_files: number; vector_ready: boolean };
type KnowledgeDocument = { id: string; title: string; source_uri: string; status: string; version: number; chunks: number; embedding_chunks: number; embedding_status: string; updated_at: string };
type KnowledgeBatch = { id: string; source_uri: string; status: string; scanned: number; accepted: number; skipped: number; failed: number; deferred: number; created_at: string };
type KnowledgeHit = { chunk_id: string; title: string; source_uri: string; ordinal: number; content: string; score: number; retrieval_mode: string; matched_by: string[] };
type KnowledgeSelection = { selectionId: string; sourceKind: "files" | "folder"; files: Array<{ relativePath: string; sizeBytes: number }> };
type KnowledgeRecycleItem = { id: string; entity_id: string; title: string | null; source_uri: string | null; prior_status: string; reason: string; deleted_at: string; restored_at: string | null };
type KnowledgeAdapter = { key: string; label: string; supported_suffixes: string[]; status: string; execution_mode: string; next_step: string };
type KnowledgePendingFile = { id: string; relative_path: string; source_uri: string; mime_type: string; size_bytes: number; status: "waiting_extractor" | "queued" | "processing" | "failed"; error_detail?: string | null; created_at: string | null };
type OperationsSummary = { counts: Record<string, number>; recent_decisions: Array<{ capability: string; decision: string; risk_score: number; reason: string; trace_id: string; decided_at: string }> };
type OperationsAgentRun = { id: string; role_key: string; model_key: string; status: string; token_input: number; token_output: number; cost: number; started_at: string | null; finished_at: string | null; error_detail: string | null };
type OperationsTaskRun = { id: string; node_key: string | null; capability: string; status: string; attempt: number; max_attempts: number; started_at: string | null; finished_at: string | null; error_detail: string | null; trace_id: string | null; agents: OperationsAgentRun[] };
type OperationsWorkflowRun = { id: string; workflow_key: string; workflow_version: string; status: string; started_at: string | null; finished_at: string | null; trace_id: string | null; tasks: OperationsTaskRun[] };
type OperationsDetailMission = { id: string; title: string; domain: string; autonomy_mode: string; status: string; created_at: string; workflows: OperationsWorkflowRun[] };
type GraphSpace = { key: string; level: string; name: string; node_count: number; edge_count: number };
type GraphPayload = GraphVisualization & { spaces: GraphSpace[] };

/**
 * 图空间下拉项：带规模，并**按内容多少排序**。
 *
 * 本库有 200+ 个图空间，其中绝大多数是 1 个节点的空壳（check-* / e2e-* 这类测试残留）。
 * 原先按接口返回顺序排，用户第一眼看到的是空壳列表，默认还落在一个 2 节点的演示空间上，
 * 画布自然几乎空白 —— 观感就是"这页坏了"。带上规模 + 有内容优先，用户才知道该点哪个。
 */
function graphSpaceOptions(spaces: GraphSpace[]): Array<{ value: string; label: string }> {
  return [...spaces]
    .sort((left, right) =>
      (right.node_count + right.edge_count) - (left.node_count + left.edge_count)
      || left.name.localeCompare(right.name, "zh-CN"))
    .map((space) => ({
      value: space.key,
      label: `${space.name} · ${space.level} · ${space.node_count} 节点 / ${space.edge_count} 边`,
    }));
}

/**
 * 图空间目录行：**有内容的排前面**。
 *
 * 225 个空间里只有 5 个带桥接（capability-l2 124 / audit-l3 108 / aiops-l3 7 /
 * quant-l3 5 / knowledge-l3 4），其余是 L1 空壳（check-* / e2e-* 这类测试残留）。
 * 原顺序按 level+name 排、每页 6 条 —— 真正有内容的要翻三十多页才看得到，
 * 第一眼看见的是一屏 0，观感就是"这页没数据"。
 */
/**
 * 关系类型的中文名。
 *
 * 图上直接显示 `contains` / `depends_on` 对读图的人没有帮助 —— 界面文案说人话，
 * 原始类型名放在 title 里备查即可。
 */
const GRAPH_RELATION_LABELS: Record<string, string> = {
  contains: "包含",
  depends_on: "依赖",
  related_to: "相关",
  links: "链接",
  supported_by: "支撑",
};

function graphRelationLabel(relation: string): string {
  return GRAPH_RELATION_LABELS[relation] ?? relation;
}

function graphGovernanceRows(spaces: GraphGovernanceSpace[]): GraphGovernanceSpace[] {
  return [...spaces].sort((left, right) =>
    (right.active_bridge_count + right.revision_count) - (left.active_bridge_count + left.revision_count)
    || left.level.localeCompare(right.level)
    || left.name.localeCompare(right.name, "zh-CN"));
}
type GraphGovernanceSpace = { key: string; level: string; name: string; graph_role: string; cluster_key: string; active_bridge_count: number; revision_count: number };
type GraphGovernance = { spaces: GraphGovernanceSpace[]; active_bridge_count: number; open_conflict_count: number };
type GraphRoute = { nodes: string[]; edges: Array<{ source: string; target: string; relation: string; weight: number; kind: "internal" | "bridge"; source_space_key: string; target_space_key: string }>; visited_space_keys: string[]; partial: boolean; truncation_reasons: string[]; budget: Record<string, unknown> };
type GraphRevision = { id: string; row_version: number; event_type: string; snapshot: { label?: string; node_type?: string }; created_at: string };
type GraphConflict = { id: string; entity_key: string; status: string; severity: string; summary: string; item_count: number; created_at: string };
type GraphProposalNode = { node_key: string; node_type: string; label: string; space_key: string; operation: string };
type GraphProposal = { id: string; title: string; status: string; risk_class: string; graph_space_key: string | null; candidate_count: number; nodes: GraphProposalNode[]; submitted_at: string | null; approved_at: string | null; applied_at: string | null };
type GraphMergeRecord = { id: string; target_node_key: string; status: string; reason: string | null; checksum: string; created_at: string; source_count: number; redirected_edges: number };
type GraphSplitRecord = { id: string; source_node_key: string; status: string; reason: string | null; checksum: string; created_at: string; part_count: number; redirected_edges: number };
type AuditEngagement = { id: string; name: string; status: string; created_at: string; evidence_count: number; open_anomaly_count: number; finding_count: number };
type AuditEvidence = { id: string; evidence_type: string; artifact_id: string | null; metadata: Record<string, unknown> };
type AuditAnomaly = { id: string; source_ref: string; rule_key: string; score: number; status: string };
type AuditClaim = { claim_id: string; reviewer_label: string };
type AuditFinding = { id: string; title: string; severity: string; status: string; claims: AuditClaim[] };
type AuditLineage = { engagement_id: string; engagement: { id: string; name: string; status: string; created_at: string }; evidence: AuditEvidence[]; anomalies: AuditAnomaly[]; findings: AuditFinding[] };
type QuantBacktest = { id: string; strategy_key: string; status: string; dataset_key: string | null; created_at: string; freshness_status: string; total_return: number; volatility: number; max_drawdown: number; observations: number };
type QuantLineage = { backtest_id: string; backtest: { id: string; strategy_key: string; status: string; parameters: { data_snapshot_sha256?: string; code_sha256?: string; point_in_time_gate?: string } | Record<string, unknown>; metrics: { total_return?: number; volatility?: number; max_drawdown?: number; simulated_only?: boolean } | Record<string, unknown>; created_at: string }; dataset: { id: string; key: string; as_of: string | null; freshness_status: string; metadata: Record<string, unknown> } | null };
type AIOpsIncident = { id: string; title: string; status: string; created_at: string; alert_count: number; proposal_count: number; change_request_count: number; execution_count: number; simulated_only: true };
type AIOpsAlert = { id: string; source: string; fingerprint: string; severity: string; occurred_at: string };
type AIOpsProposal = { id: string; playbook_key: string; risk_class: string; status: string; created_at: string };
type AIOpsChangeRequest = { id: string; proposal_id: string; command_hash: string; status: string; approved_at: string | null; expires_at: string | null; created_at: string };
type AIOpsExecution = { id: string; proposal_id: string; change_request_id: string | null; command_hash: string | null; mode: string; status: string; simulated: boolean; started_at: string | null; finished_at: string | null };
type AIOpsVerification = { id: string; execution_id: string; outcome: "healthy" | "rollback"; reviewer_label: string; trace_id: string; created_at: string };
type AIOpsLineage = { kind: "aiops_governance_chain"; incident_id: string; simulated_only: true; incident: { id: string; title: string; status: string; created_at: string }; alerts: AIOpsAlert[]; proposals: AIOpsProposal[]; change_requests: AIOpsChangeRequest[]; executions: AIOpsExecution[]; verifications: AIOpsVerification[] };
type VerifiedPlugin = { plugin_id: string; plugin_version: string; capability: string; execution_mode: "isolated_subprocess"; catalog_registered: boolean; side_effects: "read_only" };
type TopologyClusterRow = { key: string; name: string; axis: string; status: string };
type TopologyBlueprintRow = { key: string; name: string; blueprint_type: string; capability: string | null; status: string };
type TopologyReleaseRow = { release_key: string; version: string; status: string; catalog_checksum: string };
type TopologyPlanRow = { plan_key: string; mission_key: string; mode: string; checksum: string; planner_version: string; created_at: string };
type TopologyBridgeRow = { blueprint_key: string; ref_kind: string; bridge_ref: string; status: string };
type TopologyChainRow = { chain_key: string; plan_key: string; mode: string; checksum: string; planner_version: string; created_at: string };
type TopologyIntentRow = { slot_key: string; role: string; capability: string; expected_inputs?: string[]; expected_outputs?: string[]; policy_decision: string; status: string; approval_ref: string | null };
type TopologyApprovalRow = { slot_key: string; decision: string; approver: string; reason: string; created_at: string };
type TopologyExecutionRow = {
  execution_id: string; chain_key: string; slot_key: string; ordinal: number; mode: string;
  input_refs?: string[]; output_refs?: string[]; output_contract: string; output_checksum: string;
  status: string; policy_ref: string; started_at: string;
  plugin_id?: string; plugin_version?: string; runtime_code_sha256?: string; input_sha256?: string;
  output_artifact_refs?: Array<{ uri: string; sha256: string; media_type: string }>;
};
type TopologyRunRow = {
  run_id: string; chain_key: string; mode: string; status: string;
  node_total: number; node_succeeded: number; node_failed: number;
  reason: string; trace_id: string; started_at: string; finished_at: string | null;
  chain_checksum: string; planner_version: string;
};
type TopologyRunVerificationRow = {
  verification_id: string; run_id: string; chain_key: string; reference_run_id: string | null;
  status: string; node_total: number; node_matched: number; node_mismatched: number; node_ref_missing: number;
  reason: string; rollback_verdict: {
    action: string; affected_slots: string[]; baseline: { reference_run_id: string | null; chain_checksum: string; planner_version: string };
  } | null; trace_id: string; created_at: string; idempotent?: boolean;
};
type TopologyRemediationRow = RemediationProposal;
type TopologyEvidenceAnchorRow = EvidenceAnchorEntry;
type TopologyEvidenceProofRow = EvidenceProof;
type TopologyPlanningIntentRow = PlanningIntent;
type TopologyPlanningIntentListItemRow = PlanningIntentListItem;

const urlParams = new URLSearchParams(window.location.search);
const requestedView = urlParams.get("view");
const requestedTab = urlParams.get("tab");
// 旧版深链兼容：overview→hub、log→logs。
const LEGACY_VIEW_MAP: Record<string, string> = { overview: "hub", log: "logs" };
const initialView: ViewKey = LEGACY_VIEW_MAP[requestedView ?? ""] ?? requestedView ?? "hub";

/** 信息架构 v2：8 组 SubMenu，37 页全量注册；未实现项走 ComingSoonPage，不禁用菜单。 */
const navItems: MenuProps["items"] = [
  { key: "group-overview", icon: <DashboardOutlined />, label: "总览", children: [
    { key: "hub", label: "信息中枢" },
    { key: "search", label: "统一搜索" },
  ] },
  { key: "group-run", icon: <PlayCircleOutlined />, label: "运行", children: [
    { key: "operations", label: "任务编排" },
    { key: "runcanvas", label: "Run 画布" },
    { key: "runs", label: "运行与产物库" },
    { key: "diagnose", label: "运行诊断" },
    { key: "schedules", label: "调度与租约" },
  ] },
  { key: "group-knowledge", icon: <BookOutlined />, label: "知识与文档", children: [
    { key: "knowledge", label: "文档工作台" },
    { key: "knowledge-search", label: "语料检索" },
    { key: "knowledge-pending", label: "解析队列" },
    { key: "knowledge-recycle", label: "回收站" },
    { key: "library", label: "文档资产库" },
    { key: "rules", label: "规则与模板" },
  ] },
  { key: "group-graph", icon: <ApartmentOutlined />, label: "图谱", children: [
    { key: "graph", label: "图谱总览" },
    { key: "graph-spaces", label: "图空间与桥" },
    { key: "graph-extract", label: "抽取提案与审批" },
    { key: "graph-merge", label: "合并/拆分/仲裁" },
    { key: "lineage", label: "版本与衍生谱系" },
  ] },
  { key: "group-plugins", icon: <ClusterOutlined />, label: "插件与组网", children: [
    { key: "plugins", label: "插件工作台" },
    { key: "plugin-catalog", label: "插件清单与契约" },
    { key: "plugin-lifecycle", label: "生命周期与版本" },
    { key: "connectivity", label: "连通性与供需闭合" },
    { key: "planning", label: "组网规划与意图" },
  ] },
  { key: "group-business", icon: <AuditOutlined />, label: "业务与案例", children: [
    { key: "audit", label: "审计工作台" },
    { key: "quant", label: "量化工作台" },
    { key: "aiops", label: "AIOps 工作台" },
    { key: "cases", label: "案例库" },
  ] },
  { key: "group-governance", icon: <SafetyCertificateOutlined />, label: "经验与治理", children: [
    { key: "experience", label: "经验观测与统计" },
    { key: "suggestions", label: "建议与决策" },
    { key: "evolution", label: "进化闭环" },
    { key: "publications", label: "知识发布" },
    { key: "approvals", label: "审批中心" },
    { key: "policy", label: "策略与风险" },
    { key: "evidence", label: "证据链与复验" },
  ] },
  { key: "group-showcase", icon: <FundProjectionScreenOutlined />, label: "可视化与演示", children: [
    { key: "showcase-nebula", label: "知识星云（实时）" },
    { key: "showcase-brain", label: "插件大脑 · 组网树" },
    { key: "showcase-flow", label: "Run 画布 · 数据流" },
    { key: "showcase-flow-audit", label: "画布审计（真实复算）" },
    { key: "showcase-plugin-flow", label: "插件数据流全景" },
  ] },
  { key: "group-system", icon: <SettingOutlined />, label: "系统", children: [
    { key: "aisettings", label: "AI 设置" },
    { key: "logs", label: "统一日志" },
    { key: "health", label: "健康与自检" },
  ] },
];

/** 命令面板用：把 navItems 拍平成 页 key → 中文标签 + 所属组 的只读索引（不改导航结构，单一数据源）。 */
type NavItemGroup = { key: string; label: string; children?: Array<{ key: string; label: string }> };
const navPageIndex: NavPage[] = (navItems as unknown as NavItemGroup[]).flatMap((group) =>
  (group.children ?? []).map((child) => ({
    key: child.key,
    label: String(child.label),
    group: String(group.label),
    groupKey: group.key,
  })),
);

/**
 * 内嵌可视化视图：view key → 打包进应用的**相对**资源路径。
 *
 * 这些页面原先是独立的 Electron 窗口，现在以 iframe 承载在主控台内（单窗口）。
 * 路径一律以 `./` 开头：相对路径在开发（`ELECTRON_RENDERER_URL`）与生产
 * （`file://.../renderer/index.html`）下都能解析，也天然排除了远程地址。
 *
 * `showcase-nebula` 与其余四项的区别：它经 postMessage 桥借用父窗口的
 * `auditControl` 拉真实数据（`/api/v1/topology/plugin-nebula`），其余的是零后端
 * 演示（合成数据）。
 */
const SHOWCASE_VIEWS: Record<string, { src: string; title: string; hint: string }> = {
  "showcase-nebula": {
    src: "./knowledge-nebula.html",
    title: "知识星云 · 审计插件动态知识图谱",
    hint: "实时数据 · 经 auditControl 拉取",
  },
  "showcase-brain": {
    src: "./showcase/audit-brain.html",
    title: "审计插件大脑 · 树状组网与数据接口流",
    hint: "零后端演示",
  },
  "showcase-flow": {
    src: "./showcase/flow-canvas-demo.html",
    title: "Run 画布 · 数据流播放演示",
    hint: "合成数据 · 零后端",
  },
  "showcase-flow-audit": {
    src: "./showcase/flow-canvas-audit/flow-canvas-audit.html",
    title: "画布审计 · AI 组网复算流水线",
    hint: "真实数据逐行确定性重算（UCI audit_risk 776×27）",
  },
  "showcase-plugin-flow": {
    src: "./showcase/plugin-flow-showcase.html",
    title: "插件数据流全景",
    hint: "零后端演示",
  },
};

const approvalColumns: TableColumnsType<Approval> = [
  { title: "能力", dataIndex: "capability", key: "capability" },
  { title: "风险", dataIndex: "risk_class", key: "risk_class", render: (value: string) => <Tag className={`risk-tag risk-${value}`}>{value}</Tag> },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={`approval-tag approval-${value}`}>{value}</Tag> },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const runStatusTag = (value: string): ReactNode =>
  <Tag className={value === "completed" || value === "succeeded" || value === "ready" ? "ready-tag" : value === "failed" ? "pending-tag" : "gateway-tag"}>{value}</Tag>;

const contributionColumns: TableColumnsType<Contribution> = [
  { title: "插件", dataIndex: "plugin_id", key: "plugin_id" },
  { title: "版本", dataIndex: "plugin_version", key: "plugin_version" },
  { title: "导航", dataIndex: "navigation_count", key: "navigation_count" },
  { title: "视图", dataIndex: "view_count", key: "view_count" },
  { title: "动作", dataIndex: "action_count", key: "action_count" },
];

const topologyPlanColumns: TableColumnsType<TopologyPlanNode> = [
  { title: "顺序", key: "order", width: 64, render: (_value, _row, index) => index + 1 },
  { title: "规划蓝图", dataIndex: "blueprintName", key: "blueprintName" },
  { title: "能力", dataIndex: "capability", key: "capability" },
  { title: "生命周期", dataIndex: "lifecycle", key: "lifecycle", render: (value: string) => <Tag className="planned-tag">{value}</Tag> },
  { title: "匹配说明", dataIndex: "reason", key: "reason" },
];

const releaseStatusTag = (status: string): ReactNode =>
  <Tag className={status === "published" ? "ready-tag" : status === "draft" ? "pending-tag" : "planned-tag"}>{status}</Tag>;

const topologyClusterColumns: TableColumnsType<TopologyClusterRow> = [
  { title: "集群", dataIndex: "key", key: "key" },
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "调度轴", dataIndex: "axis", key: "axis" },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "active" ? "ready-tag" : "pending-tag"}>{value}</Tag> },
];

const topologyBlueprintColumns: TableColumnsType<TopologyBlueprintRow> = [
  { title: "蓝图", dataIndex: "key", key: "key" },
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "类型", dataIndex: "blueprint_type", key: "blueprint_type" },
  { title: "能力", dataIndex: "capability", key: "capability", render: (value: string | null) => value ?? "—" },
  { title: "生命周期", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "released" ? "ready-tag" : "planned-tag"}>{value}</Tag> },
];

const topologyReleaseColumns: TableColumnsType<TopologyReleaseRow> = [
  { title: "发布", dataIndex: "release_key", key: "release_key" },
  { title: "版本", dataIndex: "version", key: "version" },
  { title: "状态", dataIndex: "status", key: "status", render: releaseStatusTag },
  { title: "目录校验和", dataIndex: "catalog_checksum", key: "catalog_checksum", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
];

const topologyPlanRowColumns: TableColumnsType<TopologyPlanRow> = [
  { title: "计划", dataIndex: "plan_key", key: "plan_key" },
  { title: "任务", dataIndex: "mission_key", key: "mission_key" },
  { title: "模式", dataIndex: "mode", key: "mode", render: (value: string) => <Tag className="planned-tag">{value}</Tag> },
  { title: "规划器", dataIndex: "planner_version", key: "planner_version" },
  { title: "校验和", dataIndex: "checksum", key: "checksum", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const topologyBridgeColumns: TableColumnsType<TopologyBridgeRow> = [
  { title: "蓝图", dataIndex: "blueprint_key", key: "blueprint_key" },
  { title: "公共引用", dataIndex: "ref_kind", key: "ref_kind" },
  { title: "引用地址", dataIndex: "bridge_ref", key: "bridge_ref", ellipsis: true },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "active" ? "ready-tag" : "pending-tag"}>{value}</Tag> },
];

const topologyChainColumns: TableColumnsType<TopologyChainRow> = [
  { title: "调用链", dataIndex: "chain_key", key: "chain_key", ellipsis: true },
  { title: "来源计划", dataIndex: "plan_key", key: "plan_key", ellipsis: true },
  { title: "模式", dataIndex: "mode", key: "mode", render: (value: string) => <Tag className="planned-tag">{value}</Tag> },
  { title: "校验和", dataIndex: "checksum", key: "checksum", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const topologyIntentColumns: TableColumnsType<TopologyIntentRow> = [
  { title: "槽位", dataIndex: "slot_key", key: "slot_key", ellipsis: true },
  { title: "能力", dataIndex: "capability", key: "capability", ellipsis: true },
  { title: "期望输入", dataIndex: "expected_inputs", key: "expected_inputs", ellipsis: true, render: (value: string[] | undefined) => value && value.length ? value.join("、") : "—" },
  { title: "期望输出", dataIndex: "expected_outputs", key: "expected_outputs", ellipsis: true, render: (value: string[] | undefined) => value && value.length ? value.join("、") : "—" },
  { title: "决策", dataIndex: "policy_decision", key: "policy_decision", render: (value: string) => <Tag className={value === "allowed" ? "ready-tag" : value === "denied" ? "risk-high" : "pending-tag"}>{value === "allowed" ? "允许" : value === "requires_approval" ? "需人工批准" : "拒绝"}</Tag> },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "policy_allowed" ? "ready-tag" : value === "denied" ? "risk-high" : "pending-tag"}>{value === "policy_allowed" ? "策略已放行" : value === "approved_projection" ? "已批准投影" : value === "denied" ? "已拒绝" : "已物化"}</Tag> },
];

const topologyApprovalColumns: TableColumnsType<TopologyApprovalRow> = [
  { title: "槽位", dataIndex: "slot_key", key: "slot_key", ellipsis: true },
  { title: "决策", dataIndex: "decision", key: "decision", render: (value: string) => <Tag className={value === "approve" ? "ready-tag" : "risk-high"}>{value === "approve" ? "批准" : "驳回"}</Tag> },
  { title: "审批人", dataIndex: "approver", key: "approver", ellipsis: true },
  { title: "原因", dataIndex: "reason", key: "reason", ellipsis: true, render: (value: string) => value || "—" },
  { title: "时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const topologyExecutionColumns: TableColumnsType<TopologyExecutionRow> = [
  { title: "槽位", dataIndex: "slot_key", key: "slot_key", ellipsis: true },
  { title: "序号", dataIndex: "ordinal", key: "ordinal", width: 64 },
  { title: "模式", dataIndex: "mode", key: "mode", width: 96, render: (value: string) => value === "isolated" ? <Tag className="gateway-tag">{executionModeLabel(value)}</Tag> : <Tag className="planned-tag">{executionModeLabel(value)}</Tag> },
  { title: "插件", key: "plugin", width: 150, render: (_value: unknown, row: TopologyExecutionRow) => row.plugin_id ? <span title={row.plugin_id}>{row.plugin_id}<small style={{ color: "rgba(255,255,255,0.45)" }}>@{row.plugin_version}</small></span> : "—" },
  { title: "输入校验和", dataIndex: "input_sha256", key: "input_sha256", width: 110, render: (value: string | undefined) => value && value.length === 64 ? <span title={value}>{value.slice(0, 10)}…</span> : "—" },
  { title: "输出引用", dataIndex: "output_refs", key: "output_refs", ellipsis: true, render: (value: string[] | undefined) => value && value.length ? value.join("、") : "—" },
  { title: "输出校验和", dataIndex: "output_checksum", key: "output_checksum", ellipsis: true, render: (value: string) => <span title={value}>{value ? `${value.slice(0, 16)}…` : "—"}</span> },
  { title: "Artifacts", key: "artifacts", width: 90, render: (_value: unknown, row: TopologyExecutionRow) => row.output_artifact_refs?.length ? <span title={`${row.output_artifact_refs.length} 个只读产物`}>{row.output_artifact_refs.length} 个</span> : "—" },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "succeeded" ? "ready-tag" : value === "failed" ? "risk-high" : "pending-tag"}>{value === "succeeded" ? "成功" : value === "failed" ? "失败" : "挂起"}</Tag> },
  { title: "开始时间", dataIndex: "started_at", key: "started_at", width: 130, render: (value: string) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
];

const topologyRunColumns: TableColumnsType<TopologyRunRow> = [
  { title: "运行", dataIndex: "run_id", key: "run_id", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
  { title: "状态", dataIndex: "status", key: "status", width: 100, render: (value: string) => <Tag className={value === "success" ? "ready-tag" : value === "failed" ? "risk-high" : "pending-tag"}>{runStatusLabel(value)}</Tag> },
  { title: "节点", dataIndex: "node_total", key: "nodes", width: 128, render: (_value: number, row: TopologyRunRow) => row.status === "running" ? `${row.node_succeeded}/${row.node_total} 进行中` : `${row.node_succeeded} 成功 · ${row.node_failed} 失败` },
  { title: "原因", dataIndex: "reason", key: "reason", ellipsis: true, render: (value: string) => value || "—" },
  { title: "轮询版本", dataIndex: "planner_version", key: "planner_version", width: 92 },
  { title: "开始时间", dataIndex: "started_at", key: "started_at", width: 130, render: (value: string) => new Date(value).toLocaleString("zh-CN") },
  { title: "结束时间", dataIndex: "finished_at", key: "finished_at", width: 130, render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
];

const topologyRemediationColumns: TableColumnsType<TopologyRemediationRow> = [
  { title: "提案", dataIndex: "proposal_id", key: "proposal_id", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
  { title: "状态", dataIndex: "status", key: "status", width: 96, render: (value: string) => <Tag className={value === "approved" ? "ready-tag" : value === "pending_approval" ? "pending-tag" : "risk-high"}>{proposalStatusLabel(value)}</Tag> },
  { title: "推荐动作", dataIndex: "action", key: "action", width: 128, render: (value: string) => proposalActionLabel(value) },
  { title: "受影响槽位", dataIndex: "affected_slots", key: "affected_slots", ellipsis: true, render: (value: string[]) => value.length ? value.join("、") : "—" },
  { title: "关联重跑", dataIndex: "remediation_run_id", key: "remediation_run_id", ellipsis: true, render: (value: string | null | undefined) => value ? <span title={value}>{value.slice(0, 16)}…</span> : <Typography.Text type="secondary">—</Typography.Text> },
  { title: "原因", dataIndex: "reason", key: "reason", ellipsis: true, render: (value: string) => value || "—" },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 130, render: (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
];

const topologyEvidenceColumns: TableColumnsType<TopologyEvidenceAnchorRow> = [
  { title: "序号", dataIndex: "seq", key: "seq", width: 64 },
  { title: "来源表", dataIndex: "source_table", key: "source_table", ellipsis: true, render: (value: string) => <span title={value}>{evidenceSourceTableLabel(value)}</span> },
  { title: "来源主键", dataIndex: "source_pk", key: "source_pk", ellipsis: true, render: (value: string) => <span title={value}>{evidenceHashShort(value, 18)}</span> },
  { title: "行哈希", dataIndex: "row_hash", key: "row_hash", ellipsis: true, render: (value: string) => <span title={value}>{evidenceHashShort(value)}</span> },
  { title: "前驱哈希", dataIndex: "prev_hash", key: "prev_hash", ellipsis: true, render: (value: string) => <span title={value}>{evidenceHashShort(value)}</span> },
  { title: "批次", dataIndex: "anchor_scope", key: "anchor_scope", width: 72, render: (value: string) => <Tag className="planned-tag">{evidenceScopeLabel(value)}</Tag> },
  { title: "锚定时间", dataIndex: "created_at", key: "created_at", width: 132, render: (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
];

const topologyEvidenceScopes: EvidenceScope[] = ["full", "topology", "chain", "execution", "verification", "remediation"];

const topologyPlanningIntentNodeColumns: TableColumnsType<PlanningIntentMatchedNode> = [
  { title: "图谱节点", dataIndex: "node_key", key: "node_key", ellipsis: true },
  { title: "空间", dataIndex: "space_key", key: "space_key", ellipsis: true },
  { title: "标签", dataIndex: "label", key: "label", ellipsis: true },
  { title: "类型", dataIndex: "node_type", key: "node_type", width: 76, render: (value: string) => <Tag className={value === "capability" ? "ready-tag" : "planned-tag"}>{planningIntentNodeTypeLabel(value)}</Tag> },
  { title: "分数", dataIndex: "score", key: "score", width: 72, render: (value: number) => <Typography.Text type="secondary">{value.toFixed(4)}</Typography.Text> },
  { title: "匹配方式", dataIndex: "match_kind", key: "match_kind", width: 104, render: (value: string, row: PlanningIntentMatchedNode) => <Tag className="planned-tag" title={row.source_node_key ? `来源节点：${row.source_node_key}` : undefined}>{planningIntentMatchKindLabel(value)}</Tag> },
  { title: "来源", dataIndex: "match_source", key: "match_source", width: 108, render: (value: string) => <Typography.Text type="secondary">{planningIntentMatchSourceLabel(value)}</Typography.Text> },
];

const topologyPlanningIntentColumns: TableColumnsType<TopologyPlanningIntentListItemRow> = [
  { title: "意图", dataIndex: "intent_text", key: "intent_text", ellipsis: true },
  { title: "能力需求", dataIndex: "capability_requirements", key: "capability_requirements", ellipsis: true, render: (value: string[]) => value.length ? value.join("、") : "—" },
  { title: "计划", dataIndex: "plan_key", key: "plan_key", ellipsis: true },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 132, render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const verifiedPluginColumns: TableColumnsType<VerifiedPlugin> = [
  { title: "插件", dataIndex: "plugin_id", key: "plugin_id" },
  { title: "版本", dataIndex: "plugin_version", key: "plugin_version" },
  { title: "能力", dataIndex: "capability", key: "capability" },
  { title: "隔离方式", dataIndex: "execution_mode", key: "execution_mode", render: () => "独立本机子进程" },
  { title: "目录状态", dataIndex: "catalog_registered", key: "catalog_registered", render: (registered: boolean) => <Tag className={registered ? "ready-tag" : "pending-tag"}>{registered ? "已登记" : "未登记"}</Tag> },
  { title: "副作用", dataIndex: "side_effects", key: "side_effects", render: () => <Tag className="ready-tag">只读</Tag> },
];

function asRecord(value: unknown): Record<string, unknown> { return value as Record<string, unknown>; }

export default function App() {
  const [view, setView] = useState<ViewKey>(initialView);
  // 全局 Ctrl/Cmd+K 命令面板开关。
  const [commandOpen, setCommandOpen] = useState(false);
  // Ctrl+K（Win/Linux）/ Cmd+K（macOS）唤起或关闭命令面板；卸载时移除监听。
  useEffect(() => {
    const onGlobalKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onGlobalKey);
    return () => window.removeEventListener("keydown", onGlobalKey);
  }, []);
  // 侧栏分组折叠状态持久化（信息架构 v2）。
  const [navOpenKeys, setNavOpenKeys] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("audit-nav-open-keys") ?? "[]");
      return Array.isArray(saved) && saved.length ? saved as string[] : ["group-overview", "group-run", "group-plugins"];
    } catch { return ["group-overview", "group-run", "group-plugins"]; }
  });
  const [tabParam, setTabParam] = useState<string | undefined>(requestedTab ?? undefined);
  // 跨页深链：更新 view 与 URL 的 ?view=&tab=。
  const navigateTo = useCallback((nextView: string, tab?: string) => {
    setView(nextView);
    setTabParam(tab);
    const params = new URLSearchParams(window.location.search);
    params.set("view", nextView);
    if (tab) params.set("tab", tab); else params.delete("tab");
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  }, []);
  const onNavClick = useCallback((key: string) => navigateTo(key), [navigateTo]);
  const onNavOpenChange = useCallback((keys: string[]) => {
    setNavOpenKeys(keys);
    localStorage.setItem("audit-nav-open-keys", JSON.stringify(keys));
  }, []);
  const [tenantSlug, setTenantSlug] = useState("local-dev");
  const [tenant, setTenant] = useState<Tenant>();
  const [bootstrap, setBootstrap] = useState<Bootstrap>();
  const [contributions, setContributions] = useState<Contribution[]>([]);
  const [verifiedPlugins, setVerifiedPlugins] = useState<VerifiedPlugin[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [connection, setConnection] = useState<Connection>("connecting");
  // 主进程的截图工具（AUDIT_NETWORK_CAPTURE_*）靠轮询这个 DOM 标志等待
  // 「连接就绪」再拍照，固定延时经常截到「正在建立安全工作区」加载占位。
  useEffect(() => {
    document.documentElement.dataset.connection = connection;
  }, [connection]);
  const [notice, setNotice] = useState("正在读取本机控制平面…");
  const [busy, setBusy] = useState(false);
  const [policyResult, setPolicyResult] = useState<unknown>();
  const [selectedKnowledge, setSelectedKnowledge] = useState<KnowledgeSelection>();
  const [importResult, setImportResult] = useState<unknown>();
  const [knowledgeStats, setKnowledgeStats] = useState<KnowledgeStats>();
  const [knowledgeDocuments, setKnowledgeDocuments] = useState<KnowledgeDocument[]>([]);
  const [knowledgeBatches, setKnowledgeBatches] = useState<KnowledgeBatch[]>([]);
  const [knowledgeRecycleBin, setKnowledgeRecycleBin] = useState<KnowledgeRecycleItem[]>([]);
  const [knowledgeAdapters, setKnowledgeAdapters] = useState<KnowledgeAdapter[]>([]);
  const [knowledgePending, setKnowledgePending] = useState<KnowledgePendingFile[]>([]);
  const [knowledgeHits, setKnowledgeHits] = useState<KnowledgeHit[]>([]);
  const [searchMode, setSearchMode] = useState("hybrid");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchDegraded, setSearchDegraded] = useState(false);
  const [operations, setOperations] = useState<OperationsSummary>({ counts: {}, recent_decisions: [] });
  const [operationsDetail, setOperationsDetail] = useState<OperationsDetailMission[]>([]);
  const [graphVisualization, setGraphVisualization] = useState<GraphPayload>({ space_key: null, spaces: [], nodes: [], edges: [], partial: false });
  const [graphSpaceKey, setGraphSpaceKey] = useState<string>();
  const [selectedGraphNode, setSelectedGraphNode] = useState<GraphVisualizationNode>();
  const [graphGovernance, setGraphGovernance] = useState<GraphGovernance>({ spaces: [], active_bridge_count: 0, open_conflict_count: 0 });
  const [graphRoute, setGraphRoute] = useState<GraphRoute>();
  const [graphRevisions, setGraphRevisions] = useState<GraphRevision[]>([]);
  const [graphConflicts, setGraphConflicts] = useState<GraphConflict[]>([]);
  const [graphProposals, setGraphProposals] = useState<GraphProposal[]>([]);
  const [graphMerges, setGraphMerges] = useState<GraphMergeRecord[]>([]);
  const [graphSplits, setGraphSplits] = useState<GraphSplitRecord[]>([]);
  const [auditEngagements, setAuditEngagements] = useState<AuditEngagement[]>([]);
  const [auditSelectedEngagement, setAuditSelectedEngagement] = useState<string>();
  const [auditLineage, setAuditLineage] = useState<AuditLineage>();
  const [auditReviewerLabel, setAuditReviewerLabel] = useState("desktop-reviewer");
  const [quantBacktests, setQuantBacktests] = useState<QuantBacktest[]>([]);
  const [quantSelectedBacktest, setQuantSelectedBacktest] = useState<string>();
  const [quantLineage, setQuantLineage] = useState<QuantLineage>();
  const [aiopsIncidents, setAIOpsIncidents] = useState<AIOpsIncident[]>([]);
  const [aiopsSelectedIncident, setAIOpsSelectedIncident] = useState<string>();
  const [aiopsLineage, setAIOpsLineage] = useState<AIOpsLineage>();
  const [aiopsReviewerLabel, setAIOpsReviewerLabel] = useState("desktop-aiops-reviewer");
  const [topologyAxis, setTopologyAxis] = useState<ClusterAxis>("business_domain");
  const [runFeed, setRunFeed] = useState<RunsFeed | null>(null);
  const [runFeedOffline, setRunFeedOffline] = useState(false);
  const [runCanvas, setRunCanvas] = useState<CanvasProjection | null>(null);
  const [runCanvasBusy, setRunCanvasBusy] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runContext, setRunContext] = useState<string | null>(null);
  const [chatDraft, setChatDraft] = useState<ChatDraft | null>(null);
  const [planningNewCanvas, setPlanningNewCanvas] = useState(false);
  const [planningSourceKeys, setPlanningSourceKeys] = useState<string[]>(["ledger-a:ledger"]);
  const [topologyIntent, setTopologyIntent] = useState("将 PDF 加入知识库并进行本地提取");
  const [topologyPlan, setTopologyPlan] = useState<TopologyPlan>();
  const [selectedTopologyNode, setSelectedTopologyNode] = useState<GraphVisualizationNode>();
  const [topologyClusters, setTopologyClusters] = useState<TopologyClusterRow[]>([]);
  const [topologyBlueprints, setTopologyBlueprints] = useState<TopologyBlueprintRow[]>([]);
  const [topologyReleases, setTopologyReleases] = useState<TopologyReleaseRow[]>([]);
  const [topologyPlans, setTopologyPlans] = useState<TopologyPlanRow[]>([]);
  const [topologyBridges, setTopologyBridges] = useState<TopologyBridgeRow[]>([]);
  const [topologyChains, setTopologyChains] = useState<TopologyChainRow[]>([]);
  const [topologyIntents, setTopologyIntents] = useState<TopologyIntentRow[]>([]);
  const [topologyApprovals, setTopologyApprovals] = useState<TopologyApprovalRow[]>([]);
  const [topologyExecutions, setTopologyExecutions] = useState<TopologyExecutionRow[]>([]);
  const [topologyRuns, setTopologyRuns] = useState<TopologyRunRow[]>([]);
  const [topologyRunDetail, setTopologyRunDetail] = useState<Record<string, TopologyExecutionRow[]>>({});
  const [topologyRunVerifications, setTopologyRunVerifications] = useState<Record<string, TopologyRunVerificationRow[]>>({});
  const [topologyRunLoading, setTopologyRunLoading] = useState(false);
  const [topologyRunReason, setTopologyRunReason] = useState("");
  const [topologyRunChain, setTopologyRunChain] = useState("");
  const [topologyVerifyTarget, setTopologyVerifyTarget] = useState<TopologyRunRow | null>(null);
  const [topologyVerifyReference, setTopologyVerifyReference] = useState<string>();
  const [topologyVerifyReason, setTopologyVerifyReason] = useState("");
  const [topologyRemediations, setTopologyRemediations] = useState<TopologyRemediationRow[]>([]);
  const [topologyRemediationError, setTopologyRemediationError] = useState(false);
  const [topologyRemediateTarget, setTopologyRemediateTarget] = useState<TopologyRunVerificationRow | null>(null);
  const [topologyRemediateAction, setTopologyRemediateAction] = useState<RemediationProposal["action"]>("re-run-locked-release");
  const [topologyRemediationReason, setTopologyRemediationReason] = useState("");
  const [topologyRerunReason, setTopologyRerunReason] = useState("");
  const [topologyRerunTarget, setTopologyRerunTarget] = useState<TopologyRemediationRow | null>(null);
  const [topologyApprovalError, setTopologyApprovalError] = useState(false);
  const [topologyExecutionError, setTopologyExecutionError] = useState(false);
  const [topologyRunError, setTopologyRunError] = useState(false);
  const [topologyCatalogError, setTopologyCatalogError] = useState(false);
  const [topologyIntentError, setTopologyIntentError] = useState(false);
  const [topologyEvidenceStatus, setTopologyEvidenceStatus] = useState<EvidenceStatus>();
  const [topologyEvidenceAnchors, setTopologyEvidenceAnchors] = useState<TopologyEvidenceAnchorRow[]>([]);
  const [topologyEvidenceProof, setTopologyEvidenceProof] = useState<TopologyEvidenceProofRow>();
  const [topologyEvidenceExport, setTopologyEvidenceExport] = useState<EvidenceExport>();
  const [topologyEvidenceScope, setTopologyEvidenceScope] = useState<EvidenceScope>("full");
  const [topologyEvidenceError, setTopologyEvidenceError] = useState(false);
  const [topologyPlanningIntentText, setTopologyPlanningIntentText] = useState("");
  const [topologyPlanningIntents, setTopologyPlanningIntents] = useState<TopologyPlanningIntentListItemRow[]>([]);
  const [topologyPlanningIntentResult, setTopologyPlanningIntentResult] = useState<TopologyPlanningIntentRow | null>(null);
  const [topologyPlanningIntentError, setTopologyPlanningIntentError] = useState(false);
  const [windowMaximized, setWindowMaximized] = useState(false);
  const [zoomPercent, setZoomPercent] = useState(100);
  const [modal, contextHolder] = Modal.useModal();

  useEffect(() => {
    // 启动时同步主进程已保存/已应用的缩放因子（无桥接时视为浏览器直开，不启用）。
    if (window.auditControl?.zoomControl) {
      void window.auditControl.zoomControl("get").then((factor) => {
        if (Number.isFinite(factor)) setZoomPercent(Math.round(factor * 100));
      }).catch(() => { /* 主进程尚未就绪时忽略，工具栏按钮可再次校准。 */ });
    }
    // 同步窗口最大化状态：主进程默认最大化启动，标题栏按钮需显示“还原”。
    if (window.auditControl?.windowControl) {
      void window.auditControl.windowControl("query").then((state) => {
        setWindowMaximized(Boolean(state?.isMaximized));
      }).catch(() => { /* 查询失败时保持默认态。 */ });
    }
  }, []);

  const loadKnowledge = useCallback(async (tenantId: string): Promise<void> => {
    const [statsPayload, documentsPayload, batchesPayload, adaptersPayload] = await Promise.all([
      window.auditControl.request({ path: "/api/v1/knowledge/stats", tenantId }),
      window.auditControl.request({ path: "/api/v1/knowledge/documents", tenantId }),
      window.auditControl.request({ path: "/api/v1/knowledge/batches", tenantId }),
      window.auditControl.request({ path: "/api/v1/knowledge/adapters", tenantId }),
    ]);
    setKnowledgeStats(statsPayload as KnowledgeStats);
    setKnowledgeDocuments((asRecord(documentsPayload).items as KnowledgeDocument[]) ?? []);
    setKnowledgeBatches((asRecord(batchesPayload).items as KnowledgeBatch[]) ?? []);
    setKnowledgeAdapters((asRecord(adaptersPayload).items as KnowledgeAdapter[]) ?? []);
  }, []);

  const loadTopologyCatalog = useCallback(async (tenantId: string): Promise<void> => {
    try {
      const [clustersResp, blueprintsResp, releasesResp, plansResp, bridgesResp, chainsResp] = await Promise.all([
        window.auditControl.request({ path: "/api/v1/topology/clusters", tenantId }),
        window.auditControl.request({ path: "/api/v1/topology/blueprints", tenantId }),
        window.auditControl.request({ path: "/api/v1/topology/releases", tenantId }),
        window.auditControl.request({ path: "/api/v1/topology/plans", tenantId }),
        window.auditControl.request({ path: "/api/v1/topology/bridges", tenantId }),
        window.auditControl.request({ path: "/api/v1/topology/chains", tenantId }),
      ]);
      setTopologyClusters((asRecord(clustersResp).items as TopologyClusterRow[]) ?? []);
      setTopologyBlueprints((asRecord(blueprintsResp).items as TopologyBlueprintRow[]) ?? []);
      setTopologyReleases((asRecord(releasesResp).items as TopologyReleaseRow[]) ?? []);
      setTopologyPlans((asRecord(plansResp).items as TopologyPlanRow[]) ?? []);
      setTopologyBridges((asRecord(bridgesResp).items as TopologyBridgeRow[]) ?? []);
      const chains = (asRecord(chainsResp).items as TopologyChainRow[]) ?? [];
      setTopologyChains(chains);
      setTopologyRunChain((current) => current || (chains[0]?.chain_key ?? ""));
      setTopologyCatalogError(false);
      if (chains.length) {
        try {
          const chainKey = encodeURIComponent(chains[0].chain_key);
          const [intentsResp, approvalsResp, executionsResp, runsResp, remediationsResp] = await Promise.all([
            window.auditControl.request({ path: `/api/v1/topology/chains/${chainKey}/intents`, tenantId }),
            window.auditControl.request({ path: `/api/v1/topology/chains/${chainKey}/approvals`, tenantId }),
            window.auditControl.request({ path: `/api/v1/topology/chains/${chainKey}/executions`, tenantId }),
            window.auditControl.request({ path: `/api/v1/topology/chains/${chainKey}/runs`, tenantId }),
            window.auditControl.request({ path: "/api/v1/topology/remediation", tenantId }),
          ]);
          setTopologyIntents((asRecord(intentsResp).items as TopologyIntentRow[]) ?? []);
          setTopologyIntentError(false);
          setTopologyApprovals((asRecord(approvalsResp).items as TopologyApprovalRow[]) ?? []);
          setTopologyApprovalError(false);
          setTopologyExecutions((asRecord(executionsResp).items as TopologyExecutionRow[]) ?? []);
          setTopologyExecutionError(false);
          setTopologyRuns((asRecord(runsResp).items as TopologyRunRow[]) ?? []);
          setTopologyRunError(false);
          setTopologyRemediations((asRecord(remediationsResp).items as TopologyRemediationRow[]) ?? []);
          setTopologyRemediationError(false);
        } catch (error) {
          setTopologyIntents([]);
          setTopologyIntentError(true);
          setTopologyApprovals([]);
          setTopologyApprovalError(true);
          setTopologyExecutions([]);
          setTopologyExecutionError(true);
          setTopologyRuns([]);
          setTopologyRunError(true);
          setTopologyRemediations([]);
          setTopologyRemediationError(true);
          console.error("Failed to load topology chain detail", error);
        }
      } else {
        setTopologyIntents([]);
        setTopologyIntentError(false);
        setTopologyApprovals([]);
        setTopologyApprovalError(false);
        setTopologyExecutions([]);
        setTopologyExecutionError(false);
        setTopologyRuns([]);
        setTopologyRunError(false);
        setTopologyRemediations([]);
        setTopologyRemediationError(false);
      }
      try {
        const planningResp = await window.auditControl.request({ path: "/api/v1/topology/planning/intents", tenantId, query: { limit: 50 } });
        setTopologyPlanningIntents((asRecord(planningResp).items as TopologyPlanningIntentListItemRow[]) ?? []);
        setTopologyPlanningIntentError(false);
      } catch (error) {
        setTopologyPlanningIntents([]);
        setTopologyPlanningIntentError(true);
        console.error("Failed to load planning intents", error);
      }
    } catch (error) {
      setTopologyCatalogError(true);
      console.error("Failed to load topology catalog", error);
    }
  }, []);

  const decideTopologyIntent = useCallback(async (chainKey: string, slotKey: string, decision: "approve" | "reject"): Promise<void> => {
    if (!tenant) return;
    setBusy(true);
    try {
      await window.auditControl.request({
        path: `/api/v1/topology/chains/${encodeURIComponent(chainKey)}/approvals`, method: "POST", tenantId: tenant.tenant_id,
        body: {
          chain_key: chainKey, slot_key: slotKey, decision,
          idempotency_key: crypto.randomUUID(),
          reason: decision === "approve" ? "桌面端人工批准（only projection，不执行）" : "桌面端人工驳回（冻结意图）",
        },
      });
      await loadTopologyCatalog(tenant.tenant_id);
      setNotice(decision === "approve" ? "意图已批准为 approved_projection；未经整链模拟执行不会触达任何运行时。" : "意图已驳回为 denied；整链执行将 fail-closed 拒绝。");
    } catch (error) {
      setNotice(error instanceof Error ? `意图审批失败：${error.message}` : "意图审批失败。");
    } finally { setBusy(false); }
  }, [tenant, loadTopologyCatalog]);

  const submitTopologyPlanningIntent = (): void => {
    if (!tenant) return;
    const intent = topologyPlanningIntentText.trim();
    if (!planningIntentCanGenerate(intent)) return;
    const reason = `桌面端图谱规划：${intent.slice(0, 40)}`;
    modal.confirm({
      title: "按图谱匹配生成 plan_only 计划？",
      content: `将把意图「${intent.slice(0, 60)}${intent.length > 60 ? "…" : ""}」送入图谱语义索引（pg_trgm 确定性打分 + 单跳有界扩展，零 LLM、零外网），解析为能力需求后复用既有规划器生成 plan_only 计划并落库追加式证据行。此操作只规划、绝不执行、不触达任何运行时；未经 topology.intent.plan 权限将 403 且零写入。`,
      okText: "确认生成计划",
      cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          const result = await window.auditControl.request({
            path: "/api/v1/topology/planning/intents", method: "POST", tenantId: tenant.tenant_id,
            body: {
              intent, reason,
              budget: { max_matches: 4, expand_hops: 1 },
              idempotency_key: crypto.randomUUID(),
            },
          });
          setTopologyPlanningIntentResult(result as TopologyPlanningIntentRow);
          setTopologyPlanningIntentError(false);
          await loadTopologyCatalog(tenant.tenant_id);
          const planned = result as TopologyPlanningIntentRow;
          setNotice(planned.reused_plan || planned.idempotent
            ? `图谱规划完成（复用）：${planned.intent_text.slice(0, 24)}… → 计划 ${planned.plan_key}，匹配 ${planned.matched_nodes.length} 节点、需求 ${planned.capability_requirements.length} 项。`
            : `图谱规划完成（plan_only）：${planned.intent_text.slice(0, 24)}… → 计划 ${planned.plan_key}（${planned.node_count} 节点 · ${planned.edge_count} 边），请到「调用链/审批/执行运行」标签页继续后续审批与演练。`);
        } catch (error) {
          setNotice(error instanceof Error ? `图谱规划失败：${error.message}` : "图谱规划失败（无匹配/无链接蓝图时 fail-closed 拒绝，不落任何计划）。");
        } finally { setBusy(false); }
      },
    });
  };

  const loadTopologyEvidenceStatus = useCallback(async (tenantId: string): Promise<void> => {
    try {
      const payload = asRecord(await window.auditControl.request({ path: "/api/v1/topology/evidence/status", tenantId }));
      setTopologyEvidenceStatus(payload as EvidenceStatus);
      setTopologyEvidenceError(false);
    } catch (error) {
      setTopologyEvidenceError(true);
      console.error("Failed to load topology evidence status", error);
    }
  }, []);

  const anchorTopologyEvidence = async (): Promise<void> => {
    if (!tenant) return;
    setBusy(true);
    try {
      const payload = asRecord(await window.auditControl.request({
        path: "/api/v1/topology/evidence/anchors", method: "POST", tenantId: tenant.tenant_id,
        body: { scope: topologyEvidenceScope, idempotency_key: crypto.randomUUID() },
      }));
      const entries = (payload.entries as TopologyEvidenceAnchorRow[]) ?? [];
      setTopologyEvidenceAnchors(entries);
      setNotice(`证据链已锚定 ${entries.length} 条（${evidenceScopeLabel(topologyEvidenceScope)}）；纯数据库投影，零子进程，幂等键可重放。`);
    } catch (error) {
      setNotice(error instanceof Error ? `证据锚定失败：${error.message}` : "证据锚定失败。");
    } finally {
      setBusy(false);
      await loadTopologyEvidenceStatus(tenant.tenant_id);
    }
  };

  const verifyTopologyEvidence = async (): Promise<void> => {
    if (!tenant) return;
    setBusy(true);
    try {
      const payload = asRecord(await window.auditControl.request({
        path: "/api/v1/topology/evidence/verify", tenantId: tenant.tenant_id, query: { scope: topologyEvidenceScope },
      }));
      setTopologyEvidenceProof(payload as EvidenceProof);
    } catch (error) {
      setNotice(error instanceof Error ? `证据链校验失败：${error.message}` : "证据链校验失败。");
    } finally { setBusy(false); }
  };

  const exportTopologyEvidence = async (): Promise<void> => {
    if (!tenant) return;
    setBusy(true);
    try {
      const payload = asRecord(await window.auditControl.request({
        path: "/api/v1/topology/evidence/export", tenantId: tenant.tenant_id, query: { scope: topologyEvidenceScope },
      }));
      setTopologyEvidenceExport(payload as EvidenceExport);
      setNotice("只读审计导出已在响应体中返回（整体 SHA256 + 证明摘要）；M9 不落盘任何文件。");
    } catch (error) {
      setNotice(error instanceof Error ? `审计导出失败：${error.message}` : "审计导出失败。");
    } finally { setBusy(false); }
  };

  const executeTopologyChain = (chainKey: string): void => {
    if (!tenant) return;
    modal.confirm({
      title: "执行影子模拟？",
      content: "这会沿调用链拓扑序生成确定性影子输出 Ref 与 SHA256 写入执行账本；mode 恒为 simulated，不启动任何子进程、不触达外部系统。",
      okText: "模拟执行",
      cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await window.auditControl.request({
            path: `/api/v1/topology/chains/${encodeURIComponent(chainKey)}/executions`, method: "POST", tenantId: tenant.tenant_id,
            body: { chain_key: chainKey, mode: "simulated", idempotency_key: crypto.randomUUID(), reason: "桌面端整链影子模拟执行" },
          });
          await loadTopologyCatalog(tenant.tenant_id);
          setNotice("整链影子模拟执行已写入账本（simulated，仅状态投影）；没有调用任何插件。");
        } catch (error) {
          setNotice(error instanceof Error ? `模拟执行失败：${error.message}` : "模拟执行失败。");
        } finally { setBusy(false); }
      },
    });
  };

  const drillTopologyChain = (chainKey: string): void => {
    if (!tenant) return;
    modal.confirm({
      title: "受限只读演练？",
      content: "这会启动已验证内置插件的只读隔离子进程，沿调用链拓扑序逐节点执行并写入 isolated 账本（插件版本/runtime 校验和/输入校验和/artifact Refs）。演练严格限定测试库；未经 execute.isolated 权限将被 403 拒绝，失败节点即刻终止后续节点且绝不降级为 shadow。",
      okText: "开始演练",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        setBusy(true);
        try {
          await window.auditControl.request({
            path: `/api/v1/topology/chains/${encodeURIComponent(chainKey)}/executions`, method: "POST", tenantId: tenant.tenant_id,
            body: { chain_key: chainKey, mode: "isolated", idempotency_key: crypto.randomUUID(), reason: "桌面端受限只读演练（经 Policy 网关与 ISO 门控）" },
          });
          await loadTopologyCatalog(tenant.tenant_id);
          setNotice("受限只读演练已执行并在账本记录 isolated 行（插件只读、经 ISO 门控）；未获 execute.isolated 权限时返回 403 且零子进程。");
        } catch (error) {
          setNotice(error instanceof Error ? `受限演练失败：${error.message}` : "受限演练失败。");
        } finally { setBusy(false); }
      },
    });
  };

  const loadTopologyRunDetail = useCallback(async (runId: string): Promise<void> => {
    if (!tenant || topologyRunDetail[runId]) return;
    setTopologyRunLoading(true);
    try {
      const payload = asRecord(await window.auditControl.request({
        path: `/api/v1/topology/runs/${runId}`, tenantId: tenant.tenant_id,
      }));
      setTopologyRunDetail((state) => ({ ...state, [runId]: (payload.entries as TopologyExecutionRow[]) ?? [] }));
    } catch (error) {
      setNotice(error instanceof Error ? `运行详情加载失败：${error.message}` : "运行详情加载失败。");
    } finally { setTopologyRunLoading(false); }
  }, [tenant, topologyRunDetail]);

  const loadTopologyRunVerifications = useCallback(async (runId: string): Promise<void> => {
    if (!tenant) return;
    try {
      const payload = asRecord(await window.auditControl.request({
        path: `/api/v1/topology/runs/${runId}/verifications`, tenantId: tenant.tenant_id,
      }));
      setTopologyRunVerifications((state) => ({ ...state, [runId]: (payload.items as TopologyRunVerificationRow[]) ?? [] }));
    } catch (error) {
      setNotice(error instanceof Error ? `运行验证加载失败：${error.message}` : "运行验证加载失败。");
    }
  }, [tenant]);

  const startTopologyVerification = (run: TopologyRunRow): void => {
    setTopologyVerifyReason("");
    setTopologyVerifyReference(undefined);
    setTopologyVerifyTarget(run);
  };

  const confirmTopologyVerification = (): void => {
    const target = topologyVerifyTarget;
    if (!tenant || !target || !topologyVerifyReason.trim()) return;
    modal.confirm({
      title: "确认发起运行验证？",
      content: `将对运行 ${target.run_id.slice(0, 16)}…（${runStatusLabel(target.status)}）做纯 DB 逐节点 output_checksum 核对（${target.node_total} 节点）。零子进程、仅限测试库；drift 时只生成只读回滚裁定投影，不执行任何回滚/修复。理由：${topologyVerifyReason.trim()}`,
      okText: "确认发起验证",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        setBusy(true);
        try {
          const payload = asRecord(await window.auditControl.request({
            path: `/api/v1/topology/runs/${target.run_id}/verifications`, method: "POST", tenantId: tenant.tenant_id,
            body: {
              reference_run_id: topologyVerifyReference || null,
              reason: topologyVerifyReason.trim(),
              idempotency_key: crypto.randomUUID(),
            },
          }));
          const created = (payload as unknown as TopologyRunVerificationRow);
          setTopologyRunVerifications((state) => ({
            ...state,
            [target.run_id]: [created, ...(state[target.run_id] ?? [])],
          }));
          setTopologyVerifyTarget(null);
          setTopologyVerifyReason("");
          setTopologyVerifyReference(undefined);
          setNotice(
            created.status === "verified"
              ? `运行验证完成：verified（${created.node_matched}/${created.node_total} 节点一致，无回滚裁定）。`
              : `运行验证完成：drifted（匹配 ${created.node_matched} · 偏差 ${created.node_mismatched} · 缺参照 ${created.node_ref_missing}）。仅生成只读回滚裁定，未执行任何修复。`,
          );
        } catch (error) {
          setNotice(error instanceof Error ? `发起运行验证失败：${error.message}` : "发起运行验证失败（run 非 success/参照冲突时 fail-closed 拒绝）。");
        } finally { setBusy(false); }
      },
    });
  };

  const startTopologyRun = (chainKey: string): void => {
    if (!tenant) return;
    const reason = topologyRunReason.trim();
    modal.confirm({
      title: "发起受限执行运行？",
      content: `将对调用链 ${chainKey.slice(0, 16)}… 发起一次独立 run_id 的受限只读隔离执行运行（状态机 running → success/failed，账本行按 run_id 分组）。仅限已验证内置插件与测试库演练；未经 execute.isolated 权限将 403 且零子进程；同链有运行中(running)运行时将 409 拒绝。原因：${reason || "未填写"}`,
      okText: "确认发起运行",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        setBusy(true);
        try {
          await window.auditControl.request({
            path: `/api/v1/topology/chains/${encodeURIComponent(chainKey)}/runs`, method: "POST", tenantId: tenant.tenant_id,
            body: { chain_key: chainKey, mode: "isolated", idempotency_key: crypto.randomUUID(), reason },
          });
          setTopologyRunReason("");
          await loadTopologyCatalog(tenant.tenant_id);
          setNotice("受限执行运行已发起并记录（独立 run_id + 状态机）；账本行按运行分组，可在「执行运行」tab 展开查看逐节点血缘。");
        } catch (error) {
          setNotice(error instanceof Error ? `发起运行失败：${error.message}` : "发起运行失败（未发布锁定/待决审批/运行中并发时 fail-closed 拒绝）。");
        } finally { setBusy(false); }
      },
    });
  };

  const startTopologyRemediation = (verification: TopologyRunVerificationRow): void => {
    setTopologyRemediationReason("");
    setTopologyRemediateAction(verification.rollback_verdict?.action === "escalate-human" ? "escalate-human" : "re-run-locked-release");
    setTopologyRemediateTarget(verification);
  };

  const confirmTopologyRemediation = async (): Promise<void> => {
    const target = topologyRemediateTarget;
    if (!tenant || !target || !topologyRemediationReason.trim()) return;
    setBusy(true);
    try {
      await window.auditControl.request({
        path: `/api/v1/topology/verifications/${target.verification_id}/remediation`, method: "POST", tenantId: tenant.tenant_id,
        body: {
          action: topologyRemediateAction, reason: topologyRemediationReason.trim(),
          idempotency_key: crypto.randomUUID(),
        },
      });
      setTopologyRemediateTarget(null);
      setTopologyRemediationReason("");
      await loadTopologyCatalog(tenant.tenant_id);
      setNotice("修复提案已打开（append-only 证据记录）；待人工批准/驳回/关闭，桌面端不提供任何自动修复执行。");
    } catch (error) {
      setNotice(error instanceof Error ? `发起修复提案失败：${error.message}` : "发起修复提案失败（仅 drifted 且带回滚裁定可提案，其余 fail-closed）。");
    } finally { setBusy(false); }
  };

  const decideTopologyRemediation = (proposal: TopologyRemediationRow, decision: "approve" | "reject" | "close"): void => {
    if (!tenant) return;
    modal.confirm({
      title: decision === "approve" ? "批准修复提案？" : decision === "reject" ? "驳回修复提案？" : "关闭修复提案？",
      content: `该决策将追加写入不可变决策账本并派生终态（${proposalStatusLabel(decision === "approve" ? "approved" : decision === "reject" ? "rejected" : "closed")}）；决策不可逆，重复不同决策将 409 拒绝。${decision === "close" ? "仅 escalate-human 提案可关闭。" : ""}`,
      okText: "确认决策",
      cancelText: "取消",
      okButtonProps: { danger: decision !== "approve" },
      onOk: async () => {
        setBusy(true);
        try {
          await window.auditControl.request({
            path: `/api/v1/topology/remediation/${proposal.proposal_id}/decisions`, method: "POST", tenantId: tenant.tenant_id,
            body: {
              decision, approver: "desktop-m8-reviewer",
              reason: decision === "approve" ? "桌面端批准（仅批准投影，重跑另行发起）" : decision === "reject" ? "桌面端驳回（升级人工/禁止执行）" : "桌面端关闭（escalate-human 结案）",
              idempotency_key: crypto.randomUUID(),
            },
          });
          await loadTopologyCatalog(tenant.tenant_id);
          setNotice(decision === "approve" ? "提案已批准（approved 派生自决策账本）；re-run-locked-release 提案可另行发起受治理重跑。" : proposalStatusLabel(decision === "reject" ? "rejected" : "closed") + "；决策账本已追加，后续不可翻转。");
        } catch (error) {
          setNotice(error instanceof Error ? `决策失败：${error.message}` : "决策失败（提案已终态/close 非 escalate-human 时 409）。");
        } finally { setBusy(false); }
      },
    });
  };

  const startTopologyRerun = (proposal: TopologyRemediationRow): void => {
    setTopologyRerunReason("");
    setTopologyRerunTarget(proposal);
  };

  const confirmTopologyRerun = async (): Promise<void> => {
    const target = topologyRerunTarget;
    if (!tenant || !target || !topologyRerunReason.trim()) return;
    setBusy(true);
    try {
      await window.auditControl.request({
        path: `/api/v1/topology/remediation/${target.proposal_id}/runs`, method: "POST", tenantId: tenant.tenant_id,
        body: { reason: topologyRerunReason.trim(), idempotency_key: crypto.randomUUID() },
      });
      setTopologyRerunTarget(null);
      setTopologyRerunReason("");
      await loadTopologyCatalog(tenant.tenant_id);
      setNotice("受治理重跑已发起；提案→运行血缘已追加（remediation_run_links），账本不可变。");
    } catch (error) {
      setNotice(error instanceof Error ? `受治理重跑失败：${error.message}` : "受治理重跑失败（非 approved/re-run-locked-release 或并发运行中时 fail-closed）。");
    } finally { setBusy(false); }
  };

  const loadKnowledgeRecycleBin = useCallback(async (tenantId: string): Promise<void> => {
    const payload = asRecord(await window.auditControl.request({ path: "/api/v1/knowledge/recycle-bin", tenantId }));
    setKnowledgeRecycleBin((payload.items as KnowledgeRecycleItem[]) ?? []);
  }, []);

  const loadPendingRichMedia = useCallback(async (tenantId: string): Promise<void> => {
    const payload = asRecord(await window.auditControl.request({ path: "/api/v1/knowledge/rich-media/pending", tenantId }));
    setKnowledgePending((payload.items as KnowledgePendingFile[]) ?? []);
  }, []);

  const loadGraph = useCallback(async (tenantId: string, spaceKey?: string): Promise<void> => {
    const payload = await window.auditControl.request({
      path: "/api/v1/graph/visualization",
      tenantId,
      query: { space_key: spaceKey, max_nodes: 180, max_edges: 360 },
    }) as GraphPayload;
    setGraphVisualization(payload);
    setGraphSpaceKey(payload.space_key ?? undefined);
    setSelectedGraphNode(undefined);
    setGraphRoute(undefined);
    setGraphRevisions([]);
  }, []);

  const loadGraphGovernance = useCallback(async (tenantId: string, spaceKey?: string): Promise<void> => {
    const space = spaceKey ?? "audit-l1";
    const [governancePayload, conflictPayload, proposalPayload, mergePayload, splitPayload] = await Promise.all([
      window.auditControl.request({ path: "/api/v1/graph/governance", tenantId }),
      window.auditControl.request({ path: "/api/v1/graph/conflicts", tenantId, query: { limit: 30 } }),
      window.auditControl.request({ path: "/api/v1/graph/extractions/proposals", tenantId, query: { limit: 50 } }),
      window.auditControl.request({ path: "/api/v1/graph/merges", tenantId, query: { space_key: space, limit: 50 } }),
      window.auditControl.request({ path: "/api/v1/graph/splits", tenantId, query: { space_key: space, limit: 50 } }),
    ]);
    setGraphGovernance(governancePayload as GraphGovernance);
    setGraphConflicts((asRecord(conflictPayload).items as GraphConflict[]) ?? []);
    setGraphProposals((asRecord(proposalPayload).proposals as GraphProposal[]) ?? []);
    setGraphMerges((asRecord(mergePayload).items as GraphMergeRecord[]) ?? []);
    setGraphSplits((asRecord(splitPayload).items as GraphSplitRecord[]) ?? []);
  }, []);

  const loadAuditEngagements = useCallback(async (tenantId: string): Promise<void> => {
    const payload = asRecord(await window.auditControl.request({ path: "/api/v1/audit/engagements", tenantId, query: { limit: 50 } }));
    const items = (payload.items as AuditEngagement[]) ?? [];
    setAuditEngagements(items);
    if (items.length && !auditSelectedEngagement) setAuditSelectedEngagement(items[0].id);
  }, [auditSelectedEngagement]);

  const loadAuditLineage = useCallback(async (tenantId: string, engagementId?: string): Promise<void> => {
    if (!engagementId) { setAuditLineage(undefined); return; }
    const payload = await window.auditControl.request({ path: `/api/v1/audit/engagements/${engagementId}/lineage`, tenantId });
    setAuditLineage(payload as AuditLineage);
  }, []);

  const confirmAuditFinding = useCallback(async (candidateId: string, reviewerLabel: string): Promise<void> => {
    if (!tenant || !auditSelectedEngagement) return;
    setBusy(true);
    try {
      await window.auditControl.request({
        path: "/api/v1/audit/findings/confirm", method: "POST", tenantId: tenant.tenant_id,
        body: { candidate_id: candidateId, reviewer_label: reviewerLabel },
      });
      await Promise.all([loadAuditEngagements(tenant.tenant_id), loadAuditLineage(tenant.tenant_id, auditSelectedEngagement)]);
      setNotice("异常候选已确认为可报告发现（带证据来源），不会自动生成其它发现。");
    } catch (error) {
      setNotice(error instanceof Error ? `发现确认失败：${error.message}` : "发现确认失败。");
    } finally { setBusy(false); }
  }, [tenant, auditSelectedEngagement, loadAuditEngagements, loadAuditLineage]);

  const loadQuantBacktests = useCallback(async (tenantId: string): Promise<void> => {
    const payload = asRecord(await window.auditControl.request({ path: "/api/v1/quant/backtests", tenantId, query: { limit: 50 } }));
    const items = (payload.items as QuantBacktest[]) ?? [];
    setQuantBacktests(items);
    if (items.length && !quantSelectedBacktest) setQuantSelectedBacktest(items[0].id);
  }, [quantSelectedBacktest]);

  const loadQuantLineage = useCallback(async (tenantId: string, backtestId?: string): Promise<void> => {
    if (!backtestId) { setQuantLineage(undefined); return; }
    const payload = await window.auditControl.request({ path: `/api/v1/quant/backtests/${backtestId}/lineage`, tenantId });
    setQuantLineage(payload as QuantLineage);
  }, []);

  const loadAIOpsIncidents = useCallback(async (tenantId: string): Promise<void> => {
    const payload = asRecord(await window.auditControl.request({ path: "/api/v1/aiops/incidents", tenantId, query: { limit: 50 } }));
    const items = (payload.items as AIOpsIncident[]) ?? [];
    setAIOpsIncidents(items);
    if (items.length && !aiopsSelectedIncident) setAIOpsSelectedIncident(items[0].id);
  }, [aiopsSelectedIncident]);

  const loadAIOpsLineage = useCallback(async (tenantId: string, incidentId?: string): Promise<void> => {
    if (!incidentId) { setAIOpsLineage(undefined); return; }
    const payload = await window.auditControl.request({ path: `/api/v1/aiops/incidents/${incidentId}/lineage`, tenantId });
    setAIOpsLineage(payload as AIOpsLineage);
  }, []);

  const reviewAIOpsCanary = (execution: AIOpsExecution, outcome: "healthy" | "rollback"): void => {
    if (!tenant || !aiopsSelectedIncident) return;
    const isRollback = outcome === "rollback";
    modal.confirm({
      title: isRollback ? "记录模拟回滚？" : "确认模拟 Canary 健康？",
      content: isRollback
        ? "这会为当前本地模拟记录写入一次不可变的回滚核验，并打开该提案的本地熔断状态；不会对任何外部系统执行回滚。"
        : "这会为当前本地模拟记录写入一次不可变的健康核验；不会连接基础设施或执行任何 Playbook。",
      okText: isRollback ? "记录模拟回滚" : "核验通过",
      okButtonProps: isRollback ? { danger: true } : undefined,
      cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await window.auditControl.request({
            path: `/api/v1/aiops/executions/${execution.id}/verify`, method: "POST", tenantId: tenant.tenant_id,
            body: { outcome, reviewer_label: aiopsReviewerLabel.trim() },
          });
          await Promise.all([
            loadAIOpsIncidents(tenant.tenant_id),
            loadAIOpsLineage(tenant.tenant_id, aiopsSelectedIncident),
          ]);
          setNotice(isRollback ? "已记录本地模拟回滚并打开提案熔断状态；没有调用外部回滚。" : "已记录本地模拟 Canary 健康核验；没有执行外部动作。");
        } catch (error) {
          setNotice(error instanceof Error ? `Canary 核验失败：${error.message}` : "Canary 核验失败。");
        } finally { setBusy(false); }
      },
    });
  };

  const generateExtractionProposal = useCallback(async (): Promise<void> => {
    if (!tenant) return;
    setBusy(true);
    try {
      const spaceKey = graphSpaceKey ?? "audit-l1";
      await window.auditControl.request({
        path: "/api/v1/graph/extractions/propose", method: "POST", tenantId: tenant.tenant_id,
        body: { space_key: spaceKey, limit_documents: 10 },
      });
      await loadGraphGovernance(tenant.tenant_id, graphSpaceKey);
      setNotice(`已把最近文档的候选节点/边装入影子 ChangeSet（${spaceKey}），未经放行不会写入活图。`);
    } catch (error) {
      setNotice(error instanceof Error ? `提案生成失败：${error.message}` : "提案生成失败。");
    } finally { setBusy(false); }
  }, [tenant, graphSpaceKey, loadGraphGovernance]);

  const decideExtractionProposal = useCallback(async (proposalId: string, decision: "approve" | "reject"): Promise<void> => {
    if (!tenant) return;
    setBusy(true);
    try {
      await window.auditControl.request({
        path: `/api/v1/graph/extractions/proposals/${proposalId}/${decision}`, method: "POST", tenantId: tenant.tenant_id,
      });
      await Promise.all([loadGraphGovernance(tenant.tenant_id, graphSpaceKey), loadGraph(tenant.tenant_id, graphSpaceKey)]);
      setNotice(decision === "approve" ? "提案已放行，节点已发布到目标图空间并留痕。" : "提案已驳回，未写入活图。");
    } catch (error) {
      setNotice(error instanceof Error ? `决策失败：${error.message}` : "决策失败。");
    } finally { setBusy(false); }
  }, [tenant, graphSpaceKey, loadGraph, loadGraphGovernance]);

  const loadWorkspace = useCallback(async () => {
    setBusy(true); setConnection("connecting"); setNotice("正在加载租户上下文与 GUI 契约…");
    try {
      const tenantPayload = asRecord(await window.auditControl.request({ path: "/api/v1/ui/tenant-context", tenantSlug }));
      const resolvedTenant = { tenant_id: String(tenantPayload.tenant_id), tenant_slug: String(tenantPayload.tenant_slug) };
      const [bootstrapPayload, contributionPayload, verifiedPluginPayload, approvalPayload, operationsPayload, operationsDetailPayload] = await Promise.all([
        window.auditControl.request({ path: "/api/v1/ui/bootstrap", tenantId: resolvedTenant.tenant_id }),
        window.auditControl.request({ path: "/api/v1/ui/contributions", tenantId: resolvedTenant.tenant_id }),
        window.auditControl.request({ path: "/api/v1/plugins/verified", tenantId: resolvedTenant.tenant_id }),
        window.auditControl.request({ path: "/api/v1/approvals", tenantId: resolvedTenant.tenant_id }),
        window.auditControl.request({ path: "/api/v1/ui/operations", tenantId: resolvedTenant.tenant_id }),
        window.auditControl.request({ path: "/api/v1/ui/operations/detail", tenantId: resolvedTenant.tenant_id }),
      ]);
      setTenant(resolvedTenant);
      setBootstrap(bootstrapPayload as Bootstrap);
      setContributions((asRecord(contributionPayload).items as Contribution[]) ?? []);
      setVerifiedPlugins((asRecord(verifiedPluginPayload).items as VerifiedPlugin[]) ?? []);
      setApprovals((asRecord(approvalPayload).items as Approval[]) ?? []);
      setOperations(operationsPayload as OperationsSummary);
      setOperationsDetail((asRecord(operationsDetailPayload).missions as OperationsDetailMission[]) ?? []);
      setConnection("online"); setNotice("工作区已加载。写入与工具调用仍由策略网关裁决。");
      try { await loadKnowledge(resolvedTenant.tenant_id); }
      catch (error) { setNotice(error instanceof Error ? `工作区已连接；知识视图加载失败：${error.message}` : "知识视图加载失败。"); }
      try { await loadGraph(resolvedTenant.tenant_id); }
      catch (error) { setNotice(error instanceof Error ? `工作区已连接；图谱视图加载失败：${error.message}` : "图谱视图加载失败。"); }
      try { await loadGraphGovernance(resolvedTenant.tenant_id, graphSpaceKey); }
      catch (error) { setNotice(error instanceof Error ? `工作区已连接；图谱治理加载失败：${error.message}` : "图谱治理加载失败。"); }
      try { await loadAuditEngagements(resolvedTenant.tenant_id); }
      catch (error) { setNotice(error instanceof Error ? `工作区已连接；审计证据链加载失败：${error.message}` : "审计证据链加载失败。"); }
      try { await loadQuantBacktests(resolvedTenant.tenant_id); }
      catch (error) { setNotice(error instanceof Error ? `工作区已连接；量化证据链加载失败：${error.message}` : "量化证据链加载失败。"); }
      try { await loadAIOpsIncidents(resolvedTenant.tenant_id); }
      catch (error) { setNotice(error instanceof Error ? `工作区已连接；AIOps 治理加载失败：${error.message}` : "AIOps 治理加载失败。"); }
      try { await loadTopologyCatalog(resolvedTenant.tenant_id); }
      catch (error) { setNotice(error instanceof Error ? `工作区已连接；拓扑目录加载失败：${error.message}` : "拓扑目录加载失败。"); }
    } catch (error) {
      setConnection("error"); setNotice(error instanceof Error ? error.message : "控制平面连接失败。");
    } finally { setBusy(false); }
  }, [loadGraph, loadGraphGovernance, loadKnowledge, tenantSlug, loadAuditEngagements, loadQuantBacktests, loadAIOpsIncidents, loadTopologyCatalog]);

  useEffect(() => { void loadWorkspace(); }, [loadWorkspace]);

  useEffect(() => {
    if (tenant) void loadTopologyEvidenceStatus(tenant.tenant_id);
  }, [tenant, loadTopologyEvidenceStatus]);

  useEffect(() => {
    if (tenant && auditSelectedEngagement) void loadAuditLineage(tenant.tenant_id, auditSelectedEngagement);
  }, [tenant, auditSelectedEngagement, loadAuditLineage]);

  useEffect(() => {
    if (tenant && quantSelectedBacktest) void loadQuantLineage(tenant.tenant_id, quantSelectedBacktest);
  }, [tenant, quantSelectedBacktest, loadQuantLineage]);

  useEffect(() => {
    if (tenant && aiopsSelectedIncident) void loadAIOpsLineage(tenant.tenant_id, aiopsSelectedIncident);
  }, [tenant, aiopsSelectedIncident, loadAIOpsLineage]);

  const metrics = useMemo(() => {
    if (!bootstrap) return [];
    const facts: WorkspaceFacts = {
      apiVersion: bootstrap.api_version, shellVersion: bootstrap.shell_version,
      slotCount: bootstrap.slots.length, contributionCount: contributions.length,
      policyGated: bootstrap.features.policy_gated_actions,
    };
    return workspaceMetricRows(facts);
  }, [bootstrap, contributions]);
  const pluginTopologyGraph = useMemo(() => topologyGraphForAxis(topologyAxis), [topologyAxis]);
  const selectedTopologyBlueprint = useMemo<TopologyBlueprint | undefined>(() => {
    if (!selectedTopologyNode?.id.startsWith("blueprint:")) return undefined;
    return pluginTopologyCatalog.blueprints.find((blueprint) => blueprint.id === selectedTopologyNode.id.slice("blueprint:".length));
  }, [selectedTopologyNode]);
  const connectionBadge = connection === "online" ? controlPlaneState(true) : connection === "error" ? controlPlaneState(false) : { label: "正在连接控制平面", tone: "processing" as const };

  const chooseKnowledgeFiles = async (): Promise<void> => {
    const selection = await window.auditControl.selectKnowledgeFiles();
    setSelectedKnowledge(selection ?? undefined); setImportResult(undefined);
  };
  const chooseKnowledgeFolder = async (): Promise<void> => {
    const selection = await window.auditControl.selectKnowledgeFolder();
    setSelectedKnowledge(selection ?? undefined); setImportResult(undefined);
  };
  const importKnowledge = async (): Promise<void> => {
    if (!tenant || !selectedKnowledge) return;
    setBusy(true);
    try {
      const result = await window.auditControl.uploadKnowledge(tenant.tenant_id, selectedKnowledge.selectionId);
      setImportResult(result); await loadKnowledge(tenant.tenant_id); setNotice("知识任务已提交至策略网关与本地处理队列。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "知识导入失败。"); }
    finally { setBusy(false); }
  };
  const retireKnowledgeDocument = (document: KnowledgeDocument): void => {
    if (!tenant) return;
    modal.confirm({
      title: "移入知识回收站？",
      content: `“${document.title}”会从知识库和检索结果中隐藏，原始记录与版本会保留，可随时恢复。`,
      okText: "移入回收站",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await window.auditControl.request({
            path: `/api/v1/knowledge/documents/${document.id}/retire`, method: "POST", tenantId: tenant.tenant_id,
            body: { reason: "桌面端用户移入知识回收站" },
          });
          await Promise.all([loadKnowledge(tenant.tenant_id), loadKnowledgeRecycleBin(tenant.tenant_id)]);
          setNotice("文档已移入回收站；历史内容没有被硬删除。");
        } catch (error) { setNotice(error instanceof Error ? error.message : "移入知识回收站失败。"); }
        finally { setBusy(false); }
      },
    });
  };
  const restoreKnowledgeDocument = (item: KnowledgeRecycleItem): void => {
    if (!tenant) return;
    modal.confirm({
      title: "恢复知识文档？",
      content: `“${item.title ?? "未命名文档"}”将恢复到知识库和检索范围，保留原来的版本与来源。`,
      okText: "恢复文档",
      cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await window.auditControl.request({
            path: `/api/v1/knowledge/recycle-bin/${item.id}/restore`, method: "POST", tenantId: tenant.tenant_id,
            body: { reason: "桌面端用户恢复知识文档" },
          });
          await Promise.all([loadKnowledge(tenant.tenant_id), loadKnowledgeRecycleBin(tenant.tenant_id)]);
          setNotice("文档已恢复到知识库和检索范围。");
        } catch (error) { setNotice(error instanceof Error ? error.message : "恢复知识文档失败。"); }
        finally { setBusy(false); }
      },
    });
  };
  const extractRichMedia = (file: KnowledgePendingFile, retry = false): void => {
    if (!tenant) return;
    modal.confirm({
      title: retry ? "重新提交本地解析？" : "提交本地解析？",
      content: retry
        ? `“${file.relative_path}”上次解析失败。确认后会创建一次新的、经过策略网关审核的本地解析任务。`
        : `“${file.relative_path}”将进入本地 MinerU 队列，解析为带页码引用的文本块。Worker 会一次只处理一个文件，避免争用本机算力。`,
      okText: retry ? "重新提交" : "提交队列",
      cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await window.auditControl.request({
            path: retry ? "/api/v1/knowledge/rich-media/retry" : "/api/v1/knowledge/rich-media/extract", method: "POST", tenantId: tenant.tenant_id,
            body: { ingest_file_id: file.id, method: "auto" },
          });
          await Promise.all([loadKnowledge(tenant.tenant_id), loadPendingRichMedia(tenant.tenant_id)]);
          setNotice(retry ? "失败文件已重新进入本地解析队列。" : "文件已进入本地 MinerU 队列，完成后会自动写入页码引用。");
        } catch (error) { setNotice(error instanceof Error ? error.message : "本地解析任务提交失败。"); }
        finally { setBusy(false); }
      },
    });
  };
  const searchKnowledge = async (): Promise<void> => {
    if (!tenant || !searchQuery.trim()) return;
    setBusy(true);
    try {
      const payload = asRecord(await window.auditControl.request({
        path: "/api/v1/knowledge/search", method: "POST", tenantId: tenant.tenant_id,
        body: { query: searchQuery.trim(), mode: searchMode, limit: 20 },
      }));
      setKnowledgeHits((payload.items as KnowledgeHit[]) ?? []);
      setSearchDegraded(Boolean(payload.degraded));
      setNotice(Boolean(payload.degraded) ? "向量服务不可用，本次已降级为全文检索。" : "知识检索完成，结果保留来源与分块定位。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "知识检索失败。"); }
    finally { setBusy(false); }
  };
  const embedNextKnowledgeBatch = async (): Promise<void> => {
    if (!tenant) return;
    setBusy(true);
    try {
      const payload = asRecord(await window.auditControl.request({ path: "/api/v1/knowledge/embed", method: "POST", tenantId: tenant.tenant_id, body: { limit: 16 } }));
      await loadKnowledge(tenant.tenant_id);
      setNotice(`本地向量化完成：${String(payload.embedded)} 个知识块，模型 ${String(payload.model)}。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "本地向量化失败。"); }
    finally { setBusy(false); }
  };
  const selectGraphNode = useCallback((node: GraphVisualizationNode): void => {
    setSelectedGraphNode(node);
    setGraphRoute(undefined);
    setGraphRevisions([]);
    if (!tenant || !graphSpaceKey) return;
    void Promise.all([
      window.auditControl.request({
        path: "/api/v1/graph/routes", tenantId: tenant.tenant_id,
        query: { space_key: graphSpaceKey, start_node_id: node.id, max_graphs: 2, max_hops: 2, max_frontier: 40, max_nodes: 80, max_edges: 120, max_bridge_hops: 1, max_latency_ms: 3000, min_confidence: 0 },
      }),
      window.auditControl.request({ path: `/api/v1/graph/nodes/${node.id}/revisions`, tenantId: tenant.tenant_id, query: { limit: 12 } }),
    ]).then(([routePayload, revisionsPayload]) => {
      setGraphRoute(routePayload as GraphRoute);
      setGraphRevisions((asRecord(revisionsPayload).items as GraphRevision[]) ?? []);
    }).catch((error: unknown) => {
      setNotice(error instanceof Error ? `节点治理信息加载失败：${error.message}` : "节点治理信息加载失败。");
    });
  }, [graphSpaceKey, tenant]);
  const selectTopologyNode = useCallback((node: GraphVisualizationNode): void => { setSelectedTopologyNode(node); }, []);
  const generateTopologyPlan = (): void => {
    const plan = createTopologyPlan(topologyIntent);
    setTopologyPlan(plan);
    setNotice(plan.unresolvedCapabilities.length ? "规划目录存在能力缺口；没有选择不相关的蓝图。" : "不可执行候选计划已生成；未安装、启动或调用任何插件。");
  };
  const chooseGraphSpace = async (spaceKey: string): Promise<void> => {
    if (!tenant) return;
    setBusy(true);
    try {
      await Promise.all([loadGraph(tenant.tenant_id, spaceKey), loadGraphGovernance(tenant.tenant_id, spaceKey)]);
      setNotice("图谱局部视图已刷新；缩放与拖拽仅影响本机画布。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "图谱视图加载失败。"); }
    finally { setBusy(false); }
  };
  const simulatePolicy = async (values: { capability: string; risk_class: string; side_effects: string; arguments: string }): Promise<void> => {
    if (!tenant) { setNotice("请先连接一个租户工作区。"); return; }
    try {
      const result = await window.auditControl.request({ path: "/api/v1/policy/simulate", method: "POST", tenantId: tenant.tenant_id, body: { ...values, arguments: JSON.parse(values.arguments) } });
      setPolicyResult(result); void modal.success({ title: "策略模拟完成", content: "本次操作未创建工具调用、审批或授权租约。" });
    } catch (error) { void modal.error({ title: "策略模拟失败", content: error instanceof Error ? error.message : "请检查输入。" }); }
  };
  const controlWindow = async (action: "minimize" | "toggle-maximize" | "close" | "query"): Promise<void> => {
    if (action === "query") return;
    try {
      const result = await window.auditControl.windowControl(action);
      setWindowMaximized(result.isMaximized);
    } catch (error) { setNotice(error instanceof Error ? error.message : "窗口操作失败。"); }
  };
  const controlZoom = async (action: "in" | "out" | "reset"): Promise<void> => {
    if (!window.auditControl?.zoomControl) return;
    try {
      const factor = await window.auditControl.zoomControl(action);
      setZoomPercent(Math.round(factor * 100));
    } catch (error) { setNotice(error instanceof Error ? error.message : "缩放操作失败。"); }
  };
  const loadRunFeed = useCallback(async (): Promise<void> => {
    if (!tenant?.tenant_id || !window.auditControl?.runsSnapshot) return;
    try {
      const snapshot = await window.auditControl.runsSnapshot(tenant.tenant_id);
      setRunFeed({ items: (snapshot.items ?? []) as RunsFeed["items"], trace_id: snapshot.traceId });
      setRunFeedOffline(snapshot.offline);
    } catch (error) {
      setRunFeedOffline(true);
      setNotice(error instanceof Error ? error.message : "运行历史订阅失败。");
    }
  }, [tenant?.tenant_id, setNotice]);
  const selectRun = useCallback(async (runId: string): Promise<void> => {
    if (!tenant?.tenant_id) return;
    setSelectedRunId(runId);
    setPlanningNewCanvas(false);
    setChatDraft(null);
    setRunCanvasBusy(true);
    setRunCanvas(null);
    try {
      const projection = await window.auditControl.request({ path: `/api/v1/topology/canvas/${runId}`, tenantId: tenant.tenant_id });
      setRunCanvas(projection as CanvasProjection);
      setRunContext(`run ${runId.slice(0, 8)} · ${(projection as CanvasProjection).plan_key ?? "-"} · ${(projection as CanvasProjection).status}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "画布定义加载失败。");
    } finally {
      setRunCanvasBusy(false);
    }
  }, [tenant?.tenant_id, setNotice]);

  const applyChatDraft = useCallback((draft: ChatDraft): void => {
    setChatDraft(draft);
    setSelectedRunId(null);
    setPlanningNewCanvas(true);
    setRunCanvas(draftToProjection(draft));
    setRunCanvasBusy(false);
  }, []);

  const startAiCanvas = useCallback((): void => {
    setSelectedRunId(null);
    setChatDraft(null);
    setRunCanvas(null);
    setRunCanvasBusy(false);
    setPlanningNewCanvas(true);
    const sources = sourcePairsFor(planningSourceKeys);
    setRunContext(`新建 AI 组网草稿 · 已授权 ${sources.length} 个规划数据入口`);
  }, [planningSourceKeys]);

  const handleDraftEdit = useCallback((edit: DraftEdit): void => {
    setChatDraft((current) => {
      if (!current) return current;
      const next = applyDraftEdit(current, edit);
      setRunCanvas(draftToProjection(next));
      return next;
    });
  }, []);

  // 内嵌视图的 auditControl 桥：iframe 里没有 preload，所以需要后端数据的视图
  // （目前只有知识星云）经 postMessage 把请求转到这里，由父窗口代调。
  // 只接受同源 frame 的请求 —— 也正因为要显式写这个桥，才不必打开
  // nodeIntegrationInSubFrames（那会把 preload 注入每一个子框架）。
  useEffect(() => {
    const onEmbedRequest = (event: MessageEvent) => {
      const data = event.data as { source?: string; id?: string; request?: unknown } | null;
      if (!data || data.source !== "audit-network-embed" || !data.id) return;
      if (event.origin !== window.location.origin) return;
      const reply = (payload: Record<string, unknown>) => {
        // file:// 下 origin 是字符串 "null"，不能当 targetOrigin 用。
        const target = event.origin === "null" || !event.origin ? "*" : event.origin;
        (event.source as WindowProxy | null)?.postMessage(
          { source: "audit-network-embed-response", id: data.id, ...payload },
          target,
        );
      };
      const api = window.auditControl;
      if (!api) {
        reply({ ok: false, error: "auditControl unavailable" });
        return;
      }
      void api
        .request(data.request as Parameters<typeof api.request>[0])
        .then((result) => reply({ ok: true, data: result }))
        .catch((error: unknown) => reply({ ok: false, error: String(error) }));
    };
    window.addEventListener("message", onEmbedRequest);
    return () => window.removeEventListener("message", onEmbedRequest);
  }, []);

  const renderView = () => {
    const showcase = SHOWCASE_VIEWS[view];
    if (showcase) {
      return <ShowcaseFrame src={showcase.src} title={showcase.title} hint={showcase.hint} />;
    }
    // 页面注册表：本批 9 页 + ComingSoon 全部从 components/pages/ 注入；
    // 8 个不动页面（runcanvas/graph/audit/quant/aiops/approvals/aisettings/logs）与
    // 既有 operations/policy 视图继续走下方内联分支，保持原样。
    const RegisteredPage = pageRegistry[view];
    if (RegisteredPage) {
      const commonProps: PageProps = {
        tenantId: tenant?.tenant_id,
        tenantSlug: tenant?.tenant_slug,
        onNotice: setNotice,
        onNavigate: navigateTo,
        tab: tabParam,
      };
      return <RegisteredPage {...commonProps} />;
    }
    if (view === "logs" || view === "log") return <LogView />;
    if (view === "runcanvas") {
      return <section className="detail-head runcanvas-view">
        <div className="runcanvas-split">
          <RunsPanel
            feed={runFeed}
            busy={runCanvasBusy}
            offline={runFeedOffline}
            selectedRunId={selectedRunId}
            tenantId={tenant?.tenant_id}
            onSelect={(runId) => void selectRun(runId)}
            onRefresh={() => void loadRunFeed()}
            onCreateDraft={startAiCanvas}
          />
          <div className="canvas-mode-col">
            <div className="canvas-mode-bar">
              <span className="canvas-mode-title">Run 画布</span>
              <button
                className="canvas-mode-btn"
                onClick={() => navigateTo("showcase-brain")}
                title="在主控台内打开审计插件大脑（树状组网 + 判断分支 + 数据接口流）"
              >
                审计大脑
              </button>
              <button
                className="canvas-mode-btn canvas-mode-btn-nebula"
                onClick={() => navigateTo("showcase-nebula")}
                title="在主控台内打开动态知识星云（电子云轨道 + 星座网络 + 插件实时流转）"
              >
                知识星云
              </button>
              <span className="canvas-mode-hint">运行历史投影：节点 = 真实执行 attempt，边 = 数据流</span>
            </div>
            <RunCanvas
              projection={runCanvas}
              busy={runCanvasBusy}
              mode={chatDraft || planningNewCanvas ? "draft" : "run"}
              onDraftEdit={chatDraft ? handleDraftEdit : undefined}
            />
          </div>
          <AIChatPanel
            tenantId={tenant?.tenant_id}
            runContext={runContext}
            baseDraft={chatDraft}
            planningSourceKeys={planningSourceKeys}
            onPlanningSourceKeysChange={setPlanningSourceKeys}
            onApplyDraft={applyChatDraft}
            onNotice={setNotice}
          />
        </div>
      </section>;
    }
    if (connection === "error") return <Result status="warning" title="本机控制平面未连接" subTitle={notice} extra={<Button type="primary" onClick={() => void loadWorkspace()}>重新连接</Button>} />;
    if (!bootstrap) return <div className="loading"><Spin /> 正在建立安全工作区…</div>;
    // AI settings sits behind the same shell guards as every other view, so a
    // not-yet-connected control plane reports "未连接" instead of a confusing
    // "缺少租户上下文" from inside the page.
    if (view === "aisettings") return <AISettings tenantId={tenant?.tenant_id} onNotice={setNotice} />;
    if (view === "graph") {
      const selectedEdges = selectedGraphNode ? graphVisualization.edges.filter((edge) => edge.source === selectedGraphNode.id || edge.target === selectedGraphNode.id) : [];
      const connectedNodes = selectedEdges.map((edge) => graphVisualization.nodes.find((node) => node.id === (edge.source === selectedGraphNode?.id ? edge.target : edge.source))).filter((node): node is GraphVisualizationNode => Boolean(node));
      const selectedSpace = graphVisualization.spaces.find((space) => space.key === graphSpaceKey);
      const selectedGovernanceSpace = graphGovernance.spaces.find((space) => space.key === graphSpaceKey);
      return <>
        <section className="detail-head"><div><Typography.Title level={3}>多级图谱治理</Typography.Title><Typography.Text type="secondary">L0–L4 分层、已登记有限桥接和预算路由；拖拽、缩放或点击节点查看受限路径。</Typography.Text></div><Space><Select aria-label="图空间" value={graphSpaceKey} loading={busy} onChange={(value) => void chooseGraphSpace(value)} options={graphSpaceOptions(graphVisualization.spaces)} placeholder="暂无图空间" /><Button size="small" onClick={() => { if (tenant) void Promise.all([loadGraph(tenant.tenant_id, graphSpaceKey), loadGraphGovernance(tenant.tenant_id, graphSpaceKey)]); }}>刷新图谱</Button></Space></section>
        <section className="data-bar"><Card className="metric-card" size="small"><span>当前图空间</span><strong>{selectedSpace?.level ?? "—"}</strong><small>{selectedSpace?.name ?? "暂无数据"}</small></Card><Card className="metric-card" size="small"><span>分层角色</span><strong>{selectedGovernanceSpace?.graph_role ?? "—"}</strong><small>{selectedGovernanceSpace?.cluster_key ?? "未归类"}</small></Card><Card className="metric-card" size="small"><span>已登记桥接</span><strong>{graphGovernance.active_bridge_count}</strong><small>全空间合计 · 仅四类有限关系</small></Card><Card className="metric-card" size="small"><span>待处理冲突</span><strong>{graphGovernance.open_conflict_count}</strong><small>来源主张保留，不自动删除</small></Card></section>
        {graphVisualization.partial ? <Alert className="search-alert" type="info" showIcon message="当前仅绘制受预算的局部关系网络。请通过图空间分层或节点检索继续收敛范围。" /> : null}
        <section className="graph-workbench-grid"><Card className="chart-card graph-canvas-card" title="图空间关系网络" extra={<Tag className="gateway-tag">策略网关读取</Tag>}><GraphExplorer data={graphVisualization} selectedNodeId={selectedGraphNode?.id} onNodeClick={selectGraphNode} /></Card><Card className="chart-card graph-inspector" title="节点与路由检查器" extra={selectedGraphNode ? <Tag>{graphNodeTypeLabel(selectedGraphNode.node_type)}</Tag> : null}>{selectedGraphNode ? <><Typography.Title level={5}>{selectedGraphNode.label}</Typography.Title><Typography.Text type="secondary">节点 ID：{selectedGraphNode.id}{selectedGraphNode.family ? ` · 族 ${selectedGraphNode.family}` : ""}{selectedGraphNode.lifecycle ? ` · ${selectedGraphNode.lifecycle}` : ""}</Typography.Text>{selectedGraphNode.description ? <Typography.Paragraph className="graph-node-description" title="取自插件目录的登记描述">{selectedGraphNode.description}</Typography.Paragraph> : null}<Space wrap size={4} style={{ marginBottom: 8 }}>{(selectedGraphNode.outputs ?? []).map((port) => <Tag key={`out-${port}`} className="ready-tag" title="产出端口：本节点把该契约交给下游">产出 {port}</Tag>)}{(selectedGraphNode.inputs ?? []).map((port) => <Tag key={`in-${port}`} className="pending-tag" title="输入端口：本节点需要上游提供该契约">输入 {port}</Tag>)}</Space><div className="graph-relation-list">{selectedEdges.length ? selectedEdges.map((edge, index) => <div className="graph-relation" key={`${edge.relation}-${index}`}><Tag>{graphRelationLabel(edge.relation)}</Tag><span>{connectedNodes[index]?.label ?? "关联节点"}</span><small title={edge.basis ?? ""}>权重 {edge.weight.toFixed(2)} · {edge.basis ?? "未标注依据"}</small></div>) : <div className="empty">该节点暂无可见同图关系。</div>}</div><div className="graph-route-summary"><Typography.Text strong>受预算路由</Typography.Text>{graphRoute ? <><Typography.Paragraph>{graphRoute.visited_space_keys.join(" → ")}</Typography.Paragraph><Space wrap><Tag>{graphRoute.nodes.length} 节点</Tag><Tag>{graphRoute.edges.filter((edge) => edge.kind === "bridge").length} 条桥接</Tag><Tag className={graphRoute.partial ? "pending-tag" : "ready-tag"}>{graphRoute.partial ? `已截断：${graphRoute.truncation_reasons.join("、")}` : "预算内完成"}</Tag></Space></> : <Typography.Paragraph type="secondary">正在按图数、跳数、前沿、节点、边和时延预算计算。</Typography.Paragraph>}</div><Typography.Title level={5}>版本历史</Typography.Title><Table className="stock-table graph-revision-table" size="small" rowKey="id" dataSource={graphRevisions} pagination={false} columns={[{ title: "版本", dataIndex: "row_version", key: "row_version", width: 56 }, { title: "事件", dataIndex: "event_type", key: "event_type", render: (value: string) => <Tag>{value}</Tag> }, { title: "快照标签", key: "label", render: (_value, row: GraphRevision) => row.snapshot.label ?? "—", ellipsis: true }, { title: "时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN"), width: 124 }]} locale={{ emptyText: "暂无历史版本；节点首次更新、回收、恢复或回滚后会保留快照。" }} /></> : <div className="empty">点击图中的节点，查看同图关系、跨图预算路径与可回滚的历史版本。</div>}</Card></section>
        <section className="graph-governance-grid"><Card className="chart-card" title="图空间目录" extra={<Tag>有限桥接</Tag>}><Table className="stock-table" size="small" rowKey="key" dataSource={graphGovernanceRows(graphGovernance.spaces)} pagination={{ pageSize: 6 }} columns={[{ title: "层级", dataIndex: "level", key: "level", width: 56 }, { title: "图空间", dataIndex: "name", key: "name" }, { title: "角色 / 集群", key: "role", render: (_value, row: GraphGovernanceSpace) => `${row.graph_role} · ${row.cluster_key}`, ellipsis: true }, { title: "本空间桥接", dataIndex: "active_bridge_count", key: "active_bridge_count", width: 92 }, { title: "版本", dataIndex: "revision_count", key: "revision_count", width: 60 }]} locale={{ emptyText: "暂无已登记图空间" }} /></Card><Card className="chart-card" title="冲突收件箱" extra={<Tag className={graphConflicts.length ? "pending-tag" : "ready-tag"}>{graphConflicts.length ? "需人工处理" : "当前为空"}</Tag>}><Table className="stock-table" size="small" rowKey="id" dataSource={graphConflicts} pagination={{ pageSize: 5 }} columns={[{ title: "严重度", dataIndex: "severity", key: "severity", render: (value: string) => <Tag className={value === "high" ? "risk-high" : "pending-tag"}>{value}</Tag>, width: 74 }, { title: "冲突摘要", dataIndex: "summary", key: "summary", ellipsis: true }, { title: "来源", dataIndex: "item_count", key: "item_count", width: 54 }]} locale={{ emptyText: "暂无冲突；系统不会因路由而删除不同来源的主张。" }} /></Card></section>
        <section className="graph-merge-panel"><Card className="chart-card" title="合并/拆分账本" extra={<Tag className="gateway-tag">只读治理清单 · 可回滚</Tag>}><Tabs size="small" items={[
          { key: "merges", label: `合并记录 (${graphMerges.length})`, children: <Table className="stock-table" size="small" rowKey="id" dataSource={graphMerges} pagination={{ pageSize: 5 }} columns={[{ title: "时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN"), width: 124 }, { title: "目标节点", dataIndex: "target_node_key", key: "target_node_key", ellipsis: true }, { title: "来源数", dataIndex: "source_count", key: "source_count", width: 64 }, { title: "重指边", dataIndex: "redirected_edges", key: "redirected_edges", width: 64 }, { title: "状态", dataIndex: "status", key: "status", width: 84, render: (value: string) => <Tag className={value === "applied" ? "ready-tag" : value === "conflict" ? "pending-tag" : "risk-high"}>{value}</Tag> }, { title: "原因", dataIndex: "reason", key: "reason", render: (value: string | null) => value ?? "—", ellipsis: true }]} locale={{ emptyText: "当前图空间暂无合并记录。合并仅重指受限空间内的边并把源节点软删进回收站，历史证据不硬删除。" }} /> },
          { key: "splits", label: `拆分记录 (${graphSplits.length})`, children: <Table className="stock-table" size="small" rowKey="id" dataSource={graphSplits} pagination={{ pageSize: 5 }} columns={[{ title: "时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN"), width: 124 }, { title: "源节点", dataIndex: "source_node_key", key: "source_node_key", ellipsis: true }, { title: "子节点数", dataIndex: "part_count", key: "part_count", width: 72 }, { title: "重指边", dataIndex: "redirected_edges", key: "redirected_edges", width: 64 }, { title: "状态", dataIndex: "status", key: "status", width: 84, render: (value: string) => <Tag className={value === "applied" ? "ready-tag" : value === "conflict" ? "pending-tag" : "risk-high"}>{value}</Tag> }, { title: "原因", dataIndex: "reason", key: "reason", render: (value: string | null) => value ?? "—", ellipsis: true }]} locale={{ emptyText: "当前图空间暂无拆分记录。拆分只把声明关系类型的边重指到新子节点，源节点保持存活。" }} /> },
        ]} /></Card></section>
      </>;
    }
    if (view === "audit") {
      const totalFindings = auditEngagements.reduce((sum, engagement) => sum + engagement.finding_count, 0);
      const totalOpenAnomalies = auditEngagements.reduce((sum, engagement) => sum + engagement.open_anomaly_count, 0);
      return <>
        <section className="detail-head"><div><Typography.Title level={3}>审计工作台</Typography.Title><Typography.Text type="secondary">审计项目、证据链血缘与异常候选确认；读经策略网关，确认写操作幂等且带租户/Trace。</Typography.Text></div><Button size="small" loading={busy} onClick={() => { if (tenant) void Promise.all([loadAuditEngagements(tenant.tenant_id), auditSelectedEngagement ? loadAuditLineage(tenant.tenant_id, auditSelectedEngagement) : Promise.resolve()]); }}>刷新证据链</Button></section>
        <section className="data-bar">
          <Card className="metric-card" size="small"><span>审计项目</span><strong>{auditEngagements.length}</strong><small>当前租户</small></Card>
          <Card className="metric-card" size="small"><span>已确认发现</span><strong>{totalFindings}</strong><small>带证据来源</small></Card>
          <Card className="metric-card" size="small"><span>待确认异常</span><strong>{totalOpenAnomalies}</strong><small>只读候选 · 人工复评</small></Card>
          <Card className="metric-card" size="small"><span>策略裁决</span><strong>{operations.recent_decisions.length}</strong><small>读 / 确认均经网关</small></Card>
        </section>
        <Card className="chart-card" title="审计项目" extra={<Tag className={auditSelectedEngagement ? "ready-tag" : "pending-tag"}>{auditSelectedEngagement ? "已选择项目" : "请选择项目查看血缘"}</Tag>}>
          <Typography.Paragraph>点击一行以查看该项目从审计证据、总账工件、异常候选到已确认发现的完整血缘。异常候选确认是幂等写操作，不会自动生成其它发现。</Typography.Paragraph>
          <Table className="stock-table" size="small" rowKey="id" dataSource={auditEngagements} pagination={false}
            rowClassName={(row) => (row.id === auditSelectedEngagement ? "selected-row" : "")}
            onRow={(row) => ({ onClick: () => setAuditSelectedEngagement(row.id), style: { cursor: "pointer" } })}
            columns={[
              { title: "项目", dataIndex: "name", key: "name", ellipsis: true },
              { title: "状态", dataIndex: "status", key: "status", width: 110, render: (value: string) => <Tag className={value === "completed" ? "ready-tag" : "pending-tag"}>{value}</Tag> },
              { title: "证据", dataIndex: "evidence_count", key: "evidence_count", width: 72 },
              { title: "打开异常", dataIndex: "open_anomaly_count", key: "open_anomaly_count", width: 90, render: (value: number) => <Tag className={value ? "pending-tag" : "ready-tag"}>{value}</Tag> },
              { title: "发现", dataIndex: "finding_count", key: "finding_count", width: 72 },
              { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 150, render: (value: string) => new Date(value).toLocaleString("zh-CN") },
            ]}
            locale={{ emptyText: "暂无审计项目；可通过审计流水线新建总账项目并生成异常候选。" }} />
        </Card>
        <Card className="chart-card" title="证据链血缘" extra={<Space><Tag className="gateway-tag">confirm 幂等</Tag><span className="empty">评审人：</span><Input size="small" value={auditReviewerLabel} aria-label="评审人标签" onChange={(event) => setAuditReviewerLabel(event.target.value)} style={{ width: 180 }} /></Space>}>
          {auditLineage ? <>
            <Descriptions className="lineage-desc" size="small" column={4} bordered items={[
              { key: "name", label: "项目", children: auditLineage.engagement.name },
              { key: "status", label: "状态", children: <Tag className={auditLineage.engagement.status === "completed" ? "ready-tag" : "pending-tag"}>{auditLineage.engagement.status}</Tag> },
              { key: "evidence", label: "证据", children: auditLineage.evidence.length },
              { key: "anomalies", label: "异常候选", children: auditLineage.anomalies.length },
            ]} />
            <Tabs size="small" items={[
              { key: "evidence", label: `证据 (${auditLineage.evidence.length})`, children: <Table className="stock-table" size="small" rowKey="id" pagination={false} dataSource={auditLineage.evidence} columns={[{ title: "证据类型", dataIndex: "evidence_type", key: "evidence_type", width: 200 }, { title: "工件", dataIndex: "artifact_id", key: "artifact_id", render: (value: string | null) => value ?? "—", ellipsis: true }, { title: "元数据", dataIndex: "metadata", key: "metadata", render: (value: Record<string, unknown>) => (value && Object.keys(value).length) ? JSON.stringify(value).slice(0, 160) : "—", ellipsis: true }]} locale={{ emptyText: "暂无证据" }} /> },
              { key: "anomalies", label: `异常候选 (${auditLineage.anomalies.length})`, children: (() => { const openCount = auditLineage.anomalies.filter((anomaly) => anomaly.status !== "confirmed").length; return <><Typography.Paragraph type="secondary" className="extraction-note">{openCount ? `有 ${openCount} 条异常待复评确认；每条只能确认出单个发现，重复点击会返回同一发现而不会重复生成。` : "当前项目中的所有异常候选均已确认。"}</Typography.Paragraph><Table className="stock-table" size="small" rowKey="id" pagination={false} dataSource={auditLineage.anomalies} columns={[{ title: "来源引用", dataIndex: "source_ref", key: "source_ref", ellipsis: true }, { title: "规则", dataIndex: "rule_key", key: "rule_key" }, { title: "风险分", dataIndex: "score", key: "score", width: 80 }, { title: "状态", dataIndex: "status", key: "status", width: 100, render: (value: string) => <Tag className={value === "confirmed" ? "ready-tag" : "pending-tag"}>{value === "confirmed" ? "已确认" : "待确认"}</Tag> }, { title: "操作", key: "actions", width: 120, render: (_value, row: AuditAnomaly) => row.status === "confirmed" ? <Tag className="ready-tag">已生成发现</Tag> : <Button size="small" type="primary" danger loading={busy} disabled={!tenant || !auditReviewerLabel.trim()} onClick={() => void confirmAuditFinding(row.id, auditReviewerLabel.trim())}>确认发现</Button> }]} locale={{ emptyText: "暂无异常候选" }} /></>; })() },
              { key: "findings", label: `已确认发现 (${auditLineage.findings.length})`, children: <Table className="stock-table" size="small" rowKey="id" pagination={false} dataSource={auditLineage.findings} columns={[{ title: "发现", dataIndex: "title", key: "title", ellipsis: true }, { title: "严重度", dataIndex: "severity", key: "severity", width: 100, render: (value: string) => <Tag className={value === "high" ? "risk-high" : value === "medium" ? "pending-tag" : "ready-tag"}>{value}</Tag> }, { title: "状态", dataIndex: "status", key: "status", width: 110 }, { title: "评审人主张", dataIndex: "claims", key: "claims", render: (value: AuditClaim[]) => (value ?? []).map((claim) => `${claim.reviewer_label} · ${claim.claim_id.slice(0, 8)}`).join("、") || "—" }]} locale={{ emptyText: "暂无已确认发现；需先在异常候选页复评确认。" }} /> },
            ]} />
          </> : <div className="empty">请先在上方选择审计项目以查看其证据链血缘。血缘范围仅限当前项目，读取经策略网关裁决。</div>}
        </Card>
      </>;
    }
    if (view === "quant") {
      return <>
        <section className="detail-head"><div><Typography.Title level={3}>量化工作台</Typography.Title><Typography.Text type="secondary">仅展示模拟回测与可追溯证据链（数据快照 / 代码哈希 / 点时门 / 结果指标），不含真实下单；读取经策略网关。</Typography.Text></div><Button size="small" loading={busy} onClick={() => { if (tenant) void Promise.all([loadQuantBacktests(tenant.tenant_id), quantSelectedBacktest ? loadQuantLineage(tenant.tenant_id, quantSelectedBacktest) : Promise.resolve()]); }}>刷新证据链</Button></section>
        <section className="data-bar">
          <Card className="metric-card" size="small"><span>模拟回测</span><strong>{quantBacktests.length}</strong><small>当前租户</small></Card>
          <Card className="metric-card" size="small"><span>仅模拟标记</span><strong>{quantBacktests.filter((backtest) => backtest.status === "completed").length}</strong><small>completed 均 simulated_only</small></Card>
          <Card className="metric-card" size="small"><span>数据新鲜</span><strong>{quantBacktests.filter((backtest) => backtest.freshness_status === "fresh").length}</strong><small>数据集 freshness</small></Card>
          <Card className="metric-card" size="small"><span>策略裁决</span><strong>{operations.recent_decisions.length}</strong><small>读取均经网关</small></Card>
        </section>
        <Card className="chart-card" title="模拟回测" extra={<Tag className={quantSelectedBacktest ? "ready-tag" : "pending-tag"}>{quantSelectedBacktest ? "已选择回测" : "请选择回测查看血缘"}</Tag>}>
          <Typography.Paragraph>点击一行以查看该回测从数据快照、数据集到策略/代码哈希与结果指标的完整血缘。本阶段为只读证据链，不会发起新回测。</Typography.Paragraph>
          <Table className="stock-table" size="small" rowKey="id" dataSource={quantBacktests} pagination={false}
            rowClassName={(row) => (row.id === quantSelectedBacktest ? "selected-row" : "")}
            onRow={(row) => ({ onClick: () => setQuantSelectedBacktest(row.id), style: { cursor: "pointer" } })}
            columns={[
              { title: "策略", dataIndex: "strategy_key", key: "strategy_key", width: 160 },
              { title: "状态", dataIndex: "status", key: "status", width: 110, render: (value: string) => <Tag className={value === "completed" ? "ready-tag" : "pending-tag"}>{value}</Tag> },
              { title: "数据集", dataIndex: "dataset_key", key: "dataset_key", ellipsis: true, render: (value: string | null) => value ?? "—" },
              { title: "新鲜度", dataIndex: "freshness_status", key: "freshness_status", width: 90, render: (value: string) => <Tag className={value === "fresh" ? "ready-tag" : "pending-tag"}>{value}</Tag> },
              { title: "收益", dataIndex: "total_return", key: "total_return", width: 90, render: (value: number) => `${(value * 100).toFixed(1)}%` },
              { title: "波动", dataIndex: "volatility", key: "volatility", width: 90, render: (value: number) => `${(value * 100).toFixed(1)}%` },
              { title: "回撤", dataIndex: "max_drawdown", key: "max_drawdown", width: 90, render: (value: number) => `${(value * 100).toFixed(1)}%` },
              { title: "观测", dataIndex: "observations", key: "observations", width: 72 },
              { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 150, render: (value: string) => new Date(value).toLocaleString("zh-CN") },
            ]}
            locale={{ emptyText: "暂无模拟回测；可通过 CSV 回测管线生成数据快照与回测记录。" }} />
        </Card>
        <Card className="chart-card" title="回测证据链血缘" extra={<Tag className="gateway-tag">只读 · 点时门留痕</Tag>}>
          {quantLineage ? <>
            <Descriptions className="lineage-desc" size="small" column={4} bordered items={[
              { key: "strategy", label: "策略", children: quantLineage.backtest.strategy_key },
              { key: "status", label: "状态", children: <Tag className={quantLineage.backtest.status === "completed" ? "ready-tag" : "pending-tag"}>{quantLineage.backtest.status}</Tag> },
              { key: "dataset", label: "数据集", children: quantLineage.dataset?.key ?? "—" },
              { key: "freshness", label: "新鲜度", children: quantLineage.dataset ? <Tag className={quantLineage.dataset.freshness_status === "fresh" ? "ready-tag" : "pending-tag"}>{quantLineage.dataset.freshness_status}</Tag> : "—" },
              { key: "data_sha", label: "数据快照 SHA", children: <Typography.Text code copyable>{String(quantLineage.backtest.parameters.data_snapshot_sha256 ?? quantLineage.dataset?.metadata.source_sha256 ?? "—").slice(0, 64)}</Typography.Text> },
              { key: "code_sha", label: "代码 SHA", children: <Typography.Text code copyable>{String(quantLineage.backtest.parameters.code_sha256 ?? "—").slice(0, 64)}</Typography.Text> },
              { key: "pit_gate", label: "点时门", children: <Tag className={String(quantLineage.backtest.parameters.point_in_time_gate) === "passed" ? "ready-tag" : "pending-tag"}>{String(quantLineage.backtest.parameters.point_in_time_gate ?? "—")}</Tag> },
              { key: "simulated", label: "仅模拟", children: <Tag className="gateway-tag">{String(quantLineage.backtest.metrics.simulated_only ?? true)}</Tag> },
              { key: "total_return", label: "总收益", children: `${(Number(quantLineage.backtest.metrics.total_return ?? 0) * 100).toFixed(1)}%` },
              { key: "volatility", label: "年化波动", children: `${(Number(quantLineage.backtest.metrics.volatility ?? 0) * 100).toFixed(1)}%` },
              { key: "max_drawdown", label: "最大回撤", children: `${(Number(quantLineage.backtest.metrics.max_drawdown ?? 0) * 100).toFixed(1)}%` },
              { key: "created_at", label: "创建时间", children: new Date(quantLineage.backtest.created_at).toLocaleString("zh-CN") },
            ]} />
            {quantLineage.dataset ? <Typography.Paragraph type="secondary" className="extraction-note">数据集 <Typography.Text code>{quantLineage.dataset.key}</Typography.Text> 于 {quantLineage.dataset.as_of ? new Date(quantLineage.dataset.as_of).toLocaleString("zh-CN") : "未知时间"} 定格为 {String(quantLineage.dataset.metadata.classification ?? "—")} 分类，源 SHA 为 {String(quantLineage.dataset.metadata.source_sha256 ?? "—").slice(0, 64)}，共 {String(quantLineage.dataset.metadata.row_count ?? "—")} 行。回测生成的仅信号不使用未来数据（点时门 passed）。</Typography.Paragraph> : null}
          </> : <div className="empty">请先在上方选择回测以查看其证据链血缘。血缘范围仅限当前回测，读取经策略网关裁决。</div>}
        </Card>
      </>;
    }
    if (view === "aiops") {
      const totalProposals = aiopsIncidents.reduce((sum, incident) => sum + incident.proposal_count, 0);
      const totalCanaries = aiopsIncidents.reduce((sum, incident) => sum + incident.execution_count, 0);
      const completedCanaries = aiopsLineage?.executions.filter((execution) => execution.mode === "canary" && execution.status === "completed") ?? [];
      return <>
        <section className="detail-head"><div><Typography.Title level={3}>AIOps 工作台</Typography.Title><Typography.Text type="secondary">只读查看本地模拟链路与人工 Canary 核验；不连接基础设施、不运行 Playbook、不提供 live 模式。</Typography.Text></div><Space><Input size="small" value={aiopsReviewerLabel} onChange={(event) => setAIOpsReviewerLabel(event.target.value)} aria-label="AIOps 核验人标签" placeholder="核验人标签" /><Button size="small" loading={busy} onClick={() => { if (tenant) void Promise.all([loadAIOpsIncidents(tenant.tenant_id), aiopsSelectedIncident ? loadAIOpsLineage(tenant.tenant_id, aiopsSelectedIncident) : Promise.resolve()]); }}>刷新治理链</Button></Space></section>
        <section className="data-bar">
          <Card className="metric-card" size="small"><span>事故</span><strong>{aiopsIncidents.length}</strong><small>当前租户 · 本地记录</small></Card>
          <Card className="metric-card" size="small"><span>修复提案</span><strong>{totalProposals}</strong><small>不提供执行入口</small></Card>
          <Card className="metric-card" size="small"><span>模拟执行</span><strong>{totalCanaries}</strong><small>无外部执行器</small></Card>
          <Card className="metric-card" size="small"><span>待核验 Canary</span><strong>{completedCanaries.length}</strong><small>仅人工确认</small></Card>
        </section>
        <Card className="chart-card" title="AIOps 事故" extra={<Tag className="gateway-tag">只读 · 租户隔离</Tag>}>
          <Table className="stock-table" size="small" rowKey="id" dataSource={aiopsIncidents} pagination={{ pageSize: 10 }} rowClassName={(row) => row.id === aiopsSelectedIncident ? "selected-row" : ""} onRow={(row) => ({ onClick: () => setAIOpsSelectedIncident(row.id) })} columns={[
            { title: "事故", dataIndex: "title", key: "title", ellipsis: true },
            { title: "状态", dataIndex: "status", key: "status", width: 110, render: (value: string) => <Tag className={value === "open" ? "pending-tag" : "ready-tag"}>{value}</Tag> },
            { title: "告警", dataIndex: "alert_count", key: "alert_count", width: 64 },
            { title: "提案", dataIndex: "proposal_count", key: "proposal_count", width: 64 },
            { title: "变更", dataIndex: "change_request_count", key: "change_request_count", width: 64 },
            { title: "模拟", dataIndex: "execution_count", key: "execution_count", width: 64 },
            { title: "时间", dataIndex: "created_at", key: "created_at", width: 148, render: (value: string) => new Date(value).toLocaleString("zh-CN") },
          ]} locale={{ emptyText: "暂无 AIOps 事故；本阶段不会自动创建、执行或修复任何外部系统。" }} />
        </Card>
        <Card className="chart-card" title="事故模拟证据链" extra={<Tag className="gateway-tag">模拟限定 · 人工核验</Tag>}>
          {aiopsLineage ? <>
            <Descriptions className="lineage-desc" size="small" column={4} bordered items={[
              { key: "title", label: "事故", children: aiopsLineage.incident.title },
              { key: "status", label: "状态", children: <Tag className={aiopsLineage.incident.status === "open" ? "pending-tag" : "ready-tag"}>{aiopsLineage.incident.status}</Tag> },
              { key: "simulated", label: "执行边界", children: <Tag className="gateway-tag">仅本地模拟</Tag> },
              { key: "canaries", label: "待核验", children: completedCanaries.length },
            ]} />
            <Tabs size="small" items={[
              { key: "alerts", label: `告警 (${aiopsLineage.alerts.length})`, children: <Table className="stock-table" size="small" rowKey="id" pagination={false} dataSource={aiopsLineage.alerts} columns={[{ title: "来源", dataIndex: "source", key: "source" }, { title: "指纹", dataIndex: "fingerprint", key: "fingerprint", ellipsis: true }, { title: "严重度", dataIndex: "severity", key: "severity", width: 100 }, { title: "时间", dataIndex: "occurred_at", key: "occurred_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") }]} locale={{ emptyText: "暂无关联告警" }} /> },
              { key: "proposals", label: `提案 (${aiopsLineage.proposals.length})`, children: <Table className="stock-table" size="small" rowKey="id" pagination={false} dataSource={aiopsLineage.proposals} columns={[{ title: "Playbook 标识", dataIndex: "playbook_key", key: "playbook_key" }, { title: "风险", dataIndex: "risk_class", key: "risk_class", width: 100, render: (value: string) => <Tag className={`risk-${value}`}>{value}</Tag> }, { title: "状态", dataIndex: "status", key: "status", width: 120 }, { title: "时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") }]} locale={{ emptyText: "暂无修复提案" }} /> },
              { key: "changes", label: `变更授权 (${aiopsLineage.change_requests.length})`, children: <Table className="stock-table" size="small" rowKey="id" pagination={false} dataSource={aiopsLineage.change_requests} columns={[{ title: "状态", dataIndex: "status", key: "status", width: 100, render: (value: string) => <Tag className={value === "approved" ? "ready-tag" : "pending-tag"}>{value}</Tag> }, { title: "参数哈希", dataIndex: "command_hash", key: "command_hash", render: (value: string) => <Typography.Text code copyable>{value.slice(0, 64)}</Typography.Text> }, { title: "批准时间", dataIndex: "approved_at", key: "approved_at", render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" }, { title: "过期时间", dataIndex: "expires_at", key: "expires_at", render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" }]} locale={{ emptyText: "暂无变更授权" }} /> },
              { key: "executions", label: `模拟执行 (${aiopsLineage.executions.length})`, children: <Table className="stock-table" size="small" rowKey="id" pagination={false} dataSource={aiopsLineage.executions} columns={[{ title: "模式", dataIndex: "mode", key: "mode", width: 100, render: (value: string) => <Tag className="gateway-tag">{value}</Tag> }, { title: "状态", dataIndex: "status", key: "status", width: 120, render: (value: string) => <Tag className={value === "verified" ? "ready-tag" : value === "rolled_back" ? "risk-high" : "pending-tag"}>{value}</Tag> }, { title: "模拟限定", dataIndex: "simulated", key: "simulated", width: 100, render: () => <Tag className="gateway-tag">仅模拟</Tag> }, { title: "参数哈希", dataIndex: "command_hash", key: "command_hash", render: (value: string | null) => value ? <Typography.Text code copyable>{value.slice(0, 16)}</Typography.Text> : "—" }, { title: "操作", key: "actions", width: 210, render: (_value, row: AIOpsExecution) => row.mode === "canary" && row.status === "completed" ? <Space><Button size="small" type="primary" disabled={!tenant || busy || !aiopsReviewerLabel.trim()} onClick={() => reviewAIOpsCanary(row, "healthy")}>核验通过</Button><Button size="small" danger disabled={!tenant || busy || !aiopsReviewerLabel.trim()} onClick={() => reviewAIOpsCanary(row, "rollback")}>记录模拟回滚</Button></Space> : <Tag className="pending-tag">不可再核验</Tag> }]} locale={{ emptyText: "暂无模拟执行" }} /> },
              { key: "verifications", label: `核验账本 (${aiopsLineage.verifications.length})`, children: <Table className="stock-table" size="small" rowKey="id" pagination={false} dataSource={aiopsLineage.verifications} columns={[{ title: "结果", dataIndex: "outcome", key: "outcome", width: 110, render: (value: AIOpsVerification["outcome"]) => <Tag className={value === "healthy" ? "ready-tag" : "risk-high"}>{value === "healthy" ? "健康" : "模拟回滚"}</Tag> }, { title: "核验人", dataIndex: "reviewer_label", key: "reviewer_label" }, { title: "Trace", dataIndex: "trace_id", key: "trace_id", ellipsis: true }, { title: "时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") }]} locale={{ emptyText: "暂无人工核验记录" }} /> },
            ]} />
          </> : <div className="empty">请先在上方选择事故以查看其告警、提案、变更授权、模拟执行和核验账本。读取仅限当前租户并经策略网关裁决。</div>}
        </Card>
      </>;
    }
    if (view === "operations") {
      const workbenches = {
        operations: { title: "任务编排", note: "Mission → Workflow → Task → Agent 的持久运行投影", metrics: [["Mission", "missions"], ["工作流运行", "workflow_runs"], ["任务运行", "task_runs"], ["Agent 运行", "agent_runs"]] },
        aiops: { title: "AIOps 工作台", note: "事故、受控执行与策略审计；生产 Playbook 尚需独立验收", metrics: [["事故", "aiops_incidents"], ["受控执行", "aiops_executions"], ["任务运行", "task_runs"], ["策略裁决", "decisions"]] },
      } as const;
      const current = workbenches[view as keyof typeof workbenches];
      const decisionCount = operations.recent_decisions.length;
      return <><section className="detail-head"><div><Typography.Title level={3}>{current.title}</Typography.Title><Typography.Text type="secondary">{current.note}</Typography.Text></div><Button size="small" onClick={() => void loadWorkspace()}>刷新运行投影</Button></section><section className="data-bar">{current.metrics.map(([label, key]) => <Card className="metric-card" size="small" key={key}><span>{label}</span><strong>{key === "decisions" ? decisionCount : operations.counts[key] ?? 0}</strong><small>当前租户 · 实时数据库</small></Card>)}</section><Card className="chart-card" title="最近策略裁决" extra={<Tag className="gateway-tag">来源 / 规则 / 结果</Tag>}><Table className="stock-table" size="small" rowKey={(row) => `${row.trace_id}-${row.capability}`} dataSource={operations.recent_decisions} pagination={{ pageSize: 12 }} columns={[{ title: "时间", dataIndex: "decided_at", key: "decided_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },{ title: "能力", dataIndex: "capability", key: "capability" },{ title: "结果", dataIndex: "decision", key: "decision", render: (value: string) => <Tag className={value === "ALLOW" ? "ready-tag" : "pending-tag"}>{value}</Tag> },{ title: "风险分", dataIndex: "risk_score", key: "risk_score" },{ title: "规则说明", dataIndex: "reason", key: "reason", ellipsis: true },{ title: "Trace", dataIndex: "trace_id", key: "trace_id", ellipsis: true }]} locale={{ emptyText: "暂无策略裁决记录" }} /></Card><Card className="chart-card" title="运行投影明细" extra={<Tag className="gateway-tag">Mission → Workflow → Task → Agent</Tag>}><Table<OperationsDetailMission> className="stock-table" size="small" rowKey="id" dataSource={operationsDetail} pagination={{ pageSize: 10 }} columns={[{ title: "Mission", dataIndex: "title", key: "title" },{ title: "领域", dataIndex: "domain", key: "domain" },{ title: "自治模式", dataIndex: "autonomy_mode", key: "autonomy_mode", render: (value: string) => <Tag className="gateway-tag">{value}</Tag> },{ title: "状态", dataIndex: "status", key: "status", render: runStatusTag },{ title: "创建时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") }]} expandable={{ expandedRowRender: (mission) => <Table<OperationsWorkflowRun> className="nested-table" size="small" rowKey="id" dataSource={mission.workflows} pagination={false} columns={[{ title: "工作流", dataIndex: "workflow_key", key: "workflow_key" },{ title: "版本", dataIndex: "workflow_version", key: "workflow_version" },{ title: "状态", dataIndex: "status", key: "status", render: runStatusTag },{ title: "开始", dataIndex: "started_at", key: "started_at", render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },{ title: "结束", dataIndex: "finished_at", key: "finished_at", render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },{ title: "Trace", dataIndex: "trace_id", key: "trace_id", ellipsis: true, render: (value: string | null) => value ?? "—" }]} expandable={{ expandedRowRender: (wf) => <Table<OperationsTaskRun> className="nested-table" size="small" rowKey="id" dataSource={wf.tasks} pagination={false} columns={[{ title: "节点", dataIndex: "node_key", key: "node_key", render: (value: string | null) => value ?? "—" },{ title: "能力", dataIndex: "capability", key: "capability" },{ title: "状态", dataIndex: "status", key: "status", render: runStatusTag },{ title: "尝试", key: "attempt", render: (_value, row) => `${row.attempt}/${row.max_attempts}` },{ title: "开始", dataIndex: "started_at", key: "started_at", render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },{ title: "结束", dataIndex: "finished_at", key: "finished_at", render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },{ title: "错误", dataIndex: "error_detail", key: "error_detail", ellipsis: true, render: (value: string | null) => value ?? "—" }]} expandable={{ expandedRowRender: (task) => <Table<OperationsAgentRun> className="nested-table" size="small" rowKey="id" dataSource={task.agents} pagination={false} columns={[{ title: "角色", dataIndex: "role_key", key: "role_key" },{ title: "模型", dataIndex: "model_key", key: "model_key" },{ title: "状态", dataIndex: "status", key: "status", render: runStatusTag },{ title: "Token 入/出", key: "tokens", render: (_value, row) => `${row.token_input} / ${row.token_output}` },{ title: "成本", dataIndex: "cost", key: "cost", render: (value: number) => value.toFixed(4) },{ title: "开始", dataIndex: "started_at", key: "started_at", render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },{ title: "结束", dataIndex: "finished_at", key: "finished_at", render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" }]} locale={{ emptyText: "暂无 Agent 运行记录" }} /> }} locale={{ emptyText: "暂无任务运行记录" }} /> }} locale={{ emptyText: "暂无工作流运行记录" }} /> }} locale={{ emptyText: "暂无 Mission 运行投影；点击刷新从控制平面拉取。" }} /></Card></>;
    }
    if (view === "approvals") return <Card className="chart-card" title="审批中心" extra={<Button size="small" onClick={() => void loadWorkspace()}>刷新</Button>}><Table className="stock-table" size="small" rowKey={(row) => row.id ?? `${row.capability}-${row.trace_id ?? row.created_at}`} dataSource={approvals} columns={approvalColumns} pagination={false} expandable={{ expandedRowRender: (row) => <Descriptions className="nested-descriptions" size="small" column={2} bordered items={[
      { key: "id", label: "审批 ID", children: row.id ?? "—" },
      { key: "tool_call", label: "工具调用 ID", children: row.tool_call_id ?? "—" },
      { key: "argument_hash", label: "参数哈希", children: row.argument_hash ?? "—" },
      { key: "lease", label: "授权租约", children: row.authorization_lease_id ?? "—" },
      { key: "reason", label: "理由", children: row.reason ?? "—" },
      { key: "trace", label: "Trace ID", children: row.trace_id ?? "—" },
      { key: "expires", label: "过期时间", children: row.expires_at ? new Date(row.expires_at).toLocaleString("zh-CN") : "—" },
      { key: "decided", label: "裁决时间", children: row.decided_at ? new Date(row.decided_at).toLocaleString("zh-CN") : "—" },
    ]} /> }} locale={{ emptyText: "当前没有审批记录" }} /></Card>;
    // policy 页已迁至 pages/PolicyPage.tsx（注册表接管）
    // 注册表未覆盖、又非既有内联视图的 key（如 health）统一落到 ComingSoon 占位，不允许静默回退到 policy 页。
    return <ComingSoonPage tenantId={tenant?.tenant_id} tenantSlug={tenant?.tenant_slug} onNotice={setNotice} onNavigate={navigateTo} tab={tabParam} />;
  };

  if (view === "log") {
    return <ConfigProvider theme={{ algorithm: theme.darkAlgorithm, token: { colorPrimary: "#2f81f7", borderRadius: 4, fontFamily: "Microsoft YaHei, Inter, system-ui, sans-serif" } }}><AntApp><LogView /></AntApp></ConfigProvider>;
  }

  return <ConfigProvider theme={{ algorithm: theme.darkAlgorithm, token: { colorPrimary: "#2f81f7", borderRadius: 4, fontFamily: "Microsoft YaHei, Inter, system-ui, sans-serif" } }}><AntApp>
    <Layout className="app-shell"><Layout.Header className="toolbar"><div className="brand"><ExperimentOutlined /><span>审计智能中枢</span><small>DESKTOP CONTROL PLANE</small></div><div className="index-bar"><span className="index-item"><small>当前租户</small><strong>{tenant?.tenant_slug ?? "未连接"}</strong></span><span className="index-item"><small>策略模式</small><strong>{bootstrap?.features.policy_gated_actions ? "网关托管" : "读取中"}</strong></span><span className="index-item"><small>连接状态</small><Badge status={connectionBadge.tone} text={connectionBadge.label} /></span></div><div className="tenant-switch"><Input aria-label="租户标识" value={tenantSlug} onChange={(event) => setTenantSlug(event.target.value)} /><Button type="primary" loading={busy} onClick={() => void loadWorkspace()}>切换工作区</Button><Button size="small" icon={<FileTextOutlined />} title="打开统一日志（独立窗口）" aria-label="打开统一日志" onClick={() => void window.auditControl?.logOpenWindow?.()}>日志</Button></div><div className="window-controls" aria-label="窗口控制"><button className="window-control" type="button" aria-label="最小化窗口" title="最小化窗口" onClick={() => void controlWindow("minimize")}><MinusOutlined /></button><button className="window-control" type="button" aria-label={windowMaximized ? "还原窗口" : "最大化窗口"} title={windowMaximized ? "还原窗口" : "最大化窗口"} onClick={() => void controlWindow("toggle-maximize")}>{windowMaximized ? <FullscreenExitOutlined /> : <BorderOutlined />}</button><button className="window-control window-control-close" type="button" aria-label="关闭窗口" title="关闭窗口" onClick={() => void controlWindow("close")}><CloseOutlined /></button></div><div className="zoom-controls" role="group" aria-label="界面缩放"><button className="zoom-step" type="button" aria-label="缩小界面" title="缩小界面 (Ctrl+-)" onClick={() => void controlZoom("out")}><MinusOutlined /></button><button className="zoom-pct" type="button" aria-label="重置界面缩放" title="点击还原到 100% (Ctrl+0)" onClick={() => void controlZoom("reset")}>{zoomPercent}%</button><button className="zoom-step" type="button" aria-label="放大界面" title="放大界面 (Ctrl+=)" onClick={() => void controlZoom("in")}><PlusOutlined /></button></div></Layout.Header>
    <Layout><Layout.Sider className="sidebar" width={224}><div className="sidebar-title">中枢导航</div><Menu mode="inline" theme="dark" selectedKeys={[view]} defaultOpenKeys={navOpenKeys} openKeys={navOpenKeys} onOpenChange={onNavOpenChange} items={navItems} onClick={({ key }) => onNavClick(key)} /></Layout.Sider><Layout.Content className="content"><div className="notice-row"><Typography.Text type={connection === "error" ? "danger" : "secondary"}>{notice}</Typography.Text></div><PageErrorBoundary key={view}>{renderView()}</PageErrorBoundary></Layout.Content></Layout>
    </Layout>
    {contextHolder}
    <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} onNavigate={navigateTo} pages={navPageIndex} tenantId={tenant?.tenant_id} />
  </AntApp></ConfigProvider>;
}
