import { Alert, Button, Card, Descriptions, Input, Modal, Select, Space, Table, Tabs, Tag, Typography } from "antd";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import GraphExplorer, { type GraphVisualizationNode } from "../GraphExplorer";
import PluginFlowGraph from "../PluginFlowGraph";
import {
  anyRunRunning, createTopologyPlan, evidenceHashShort, evidenceMismatchLabel, evidenceScopeLabel,
  evidenceSourceTableLabel, evidenceVerifiedLabel, executionModeLabel, planningIntentCanGenerate,
  planningIntentEvidenceNote, planningIntentLengthHint, planningIntentMatchKindLabel, planningIntentMatchSourceLabel,
  planningIntentNodeTypeLabel, pluginTopologyCatalog, proposalActionLabel, proposalCanDecide, proposalCanRerun,
  proposalStatusLabel, rollbackActionLabel, runCanVerify, runStatusLabel, topologyAxes, topologyGraphForAxis,
  verificationStatusLabel,
  type ClusterAxis, type EvidenceAnchorEntry, type EvidenceExport, type EvidenceProof, type EvidenceScope,
  type EvidenceStatus, type PlanningIntent, type PlanningIntentListItem, type PlanningIntentMatchedNode,
  type RemediationProposal, type TopologyBlueprint, type TopologyPlan, type TopologyPlanNode,
} from "../../model/pluginTopology";
import { apiRequest, asRecord, type PageProps } from "./common";

type VerifiedPlugin = { plugin_id: string; plugin_version: string; capability: string; execution_mode: "isolated_subprocess"; catalog_registered: boolean; side_effects: "read_only" };
type Contribution = { plugin_id: string; plugin_version: string; navigation_count: number; view_count: number; action_count: number };
type OperationsSummary = { counts: Record<string, number>; recent_decisions: Array<{ capability: string; decision: string; risk_score: number; reason: string; trace_id: string; decided_at: string }> };
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

const topologyPlanColumns = [
  { title: "顺序", key: "order", width: 64, render: (_value: unknown, _row: TopologyPlanNode, index: number) => index + 1 },
  { title: "规划蓝图", dataIndex: "blueprintName", key: "blueprintName" },
  { title: "能力", dataIndex: "capability", key: "capability" },
  { title: "生命周期", dataIndex: "lifecycle", key: "lifecycle", render: (value: string) => <Tag className="planned-tag">{value}</Tag> },
  { title: "匹配说明", dataIndex: "reason", key: "reason" },
];

const releaseStatusTag = (status: string): ReactNode =>
  <Tag className={status === "published" ? "ready-tag" : status === "draft" ? "pending-tag" : "planned-tag"}>{status}</Tag>;

const topologyClusterColumns = [
  { title: "集群", dataIndex: "key", key: "key" },
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "调度轴", dataIndex: "axis", key: "axis" },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "active" ? "ready-tag" : "pending-tag"}>{value}</Tag> },
];

const topologyBlueprintColumns = [
  { title: "蓝图", dataIndex: "key", key: "key" },
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "类型", dataIndex: "blueprint_type", key: "blueprint_type" },
  { title: "能力", dataIndex: "capability", key: "capability", render: (value: string | null) => value ?? "—" },
  { title: "生命周期", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "released" ? "ready-tag" : "planned-tag"}>{value}</Tag> },
];

const topologyReleaseColumns = [
  { title: "发布", dataIndex: "release_key", key: "release_key" },
  { title: "版本", dataIndex: "version", key: "version" },
  { title: "状态", dataIndex: "status", key: "status", render: releaseStatusTag },
  { title: "目录校验和", dataIndex: "catalog_checksum", key: "catalog_checksum", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
];

const topologyPlanRowColumns = [
  { title: "计划", dataIndex: "plan_key", key: "plan_key" },
  { title: "任务", dataIndex: "mission_key", key: "mission_key" },
  { title: "模式", dataIndex: "mode", key: "mode", render: (value: string) => <Tag className="planned-tag">{value}</Tag> },
  { title: "规划器", dataIndex: "planner_version", key: "planner_version" },
  { title: "校验和", dataIndex: "checksum", key: "checksum", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const topologyBridgeColumns = [
  { title: "蓝图", dataIndex: "blueprint_key", key: "blueprint_key" },
  { title: "公共引用", dataIndex: "ref_kind", key: "ref_kind" },
  { title: "引用地址", dataIndex: "bridge_ref", key: "bridge_ref", ellipsis: true },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "active" ? "ready-tag" : "pending-tag"}>{value}</Tag> },
];

const topologyChainColumns = [
  { title: "调用链", dataIndex: "chain_key", key: "chain_key", ellipsis: true },
  { title: "来源计划", dataIndex: "plan_key", key: "plan_key", ellipsis: true },
  { title: "模式", dataIndex: "mode", key: "mode", render: (value: string) => <Tag className="planned-tag">{value}</Tag> },
  { title: "校验和", dataIndex: "checksum", key: "checksum", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const topologyIntentColumns = [
  { title: "槽位", dataIndex: "slot_key", key: "slot_key", ellipsis: true },
  { title: "能力", dataIndex: "capability", key: "capability", ellipsis: true },
  { title: "期望输入", dataIndex: "expected_inputs", key: "expected_inputs", ellipsis: true, render: (value: string[] | undefined) => value && value.length ? value.join("、") : "—" },
  { title: "期望输出", dataIndex: "expected_outputs", key: "expected_outputs", ellipsis: true, render: (value: string[] | undefined) => value && value.length ? value.join("、") : "—" },
  { title: "决策", dataIndex: "policy_decision", key: "policy_decision", render: (value: string) => <Tag className={value === "allowed" ? "ready-tag" : value === "denied" ? "risk-high" : "pending-tag"}>{value === "allowed" ? "允许" : value === "requires_approval" ? "需人工批准" : "拒绝"}</Tag> },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag className={value === "policy_allowed" ? "ready-tag" : value === "denied" ? "risk-high" : "pending-tag"}>{value === "policy_allowed" ? "策略已放行" : value === "approved_projection" ? "已批准投影" : value === "denied" ? "已拒绝" : "已物化"}</Tag> },
];

const topologyApprovalColumns = [
  { title: "槽位", dataIndex: "slot_key", key: "slot_key", ellipsis: true },
  { title: "决策", dataIndex: "decision", key: "decision", render: (value: string) => <Tag className={value === "approve" ? "ready-tag" : "risk-high"}>{value === "approve" ? "批准" : "驳回"}</Tag> },
  { title: "审批人", dataIndex: "approver", key: "approver", ellipsis: true },
  { title: "原因", dataIndex: "reason", key: "reason", ellipsis: true, render: (value: string) => value || "—" },
  { title: "时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const topologyExecutionColumns = [
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

const topologyRunColumns = [
  { title: "运行", dataIndex: "run_id", key: "run_id", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
  { title: "状态", dataIndex: "status", key: "status", width: 100, render: (value: string) => <Tag className={value === "success" ? "ready-tag" : value === "failed" ? "risk-high" : "pending-tag"}>{runStatusLabel(value)}</Tag> },
  { title: "节点", dataIndex: "node_total", key: "nodes", width: 128, render: (_value: number, row: TopologyRunRow) => row.status === "running" ? `${row.node_succeeded}/${row.node_total} 进行中` : `${row.node_succeeded} 成功 · ${row.node_failed} 失败` },
  { title: "原因", dataIndex: "reason", key: "reason", ellipsis: true, render: (value: string) => value || "—" },
  { title: "轮询版本", dataIndex: "planner_version", key: "planner_version", width: 92 },
  { title: "开始时间", dataIndex: "started_at", key: "started_at", width: 130, render: (value: string) => new Date(value).toLocaleString("zh-CN") },
  { title: "结束时间", dataIndex: "finished_at", key: "finished_at", width: 130, render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
];

const topologyRemediationColumns = [
  { title: "提案", dataIndex: "proposal_id", key: "proposal_id", ellipsis: true, render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
  { title: "状态", dataIndex: "status", key: "status", width: 96, render: (value: string) => <Tag className={value === "approved" ? "ready-tag" : value === "pending_approval" ? "pending-tag" : "risk-high"}>{proposalStatusLabel(value)}</Tag> },
  { title: "推荐动作", dataIndex: "action", key: "action", width: 128, render: (value: string) => proposalActionLabel(value) },
  { title: "受影响槽位", dataIndex: "affected_slots", key: "affected_slots", ellipsis: true, render: (value: string[]) => value.length ? value.join("、") : "—" },
  { title: "关联重跑", dataIndex: "remediation_run_id", key: "remediation_run_id", ellipsis: true, render: (value: string | null | undefined) => value ? <span title={value}>{value.slice(0, 16)}…</span> : <Typography.Text type="secondary">—</Typography.Text> },
  { title: "原因", dataIndex: "reason", key: "reason", ellipsis: true, render: (value: string) => value || "—" },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 130, render: (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
];

const topologyEvidenceColumns = [
  { title: "序号", dataIndex: "seq", key: "seq", width: 64 },
  { title: "来源表", dataIndex: "source_table", key: "source_table", ellipsis: true, render: (value: string) => <span title={value}>{evidenceSourceTableLabel(value)}</span> },
  { title: "来源主键", dataIndex: "source_pk", key: "source_pk", ellipsis: true, render: (value: string) => <span title={value}>{evidenceHashShort(value, 18)}</span> },
  { title: "行哈希", dataIndex: "row_hash", key: "row_hash", ellipsis: true, render: (value: string) => <span title={value}>{evidenceHashShort(value)}</span> },
  { title: "前驱哈希", dataIndex: "prev_hash", key: "prev_hash", ellipsis: true, render: (value: string) => <span title={value}>{evidenceHashShort(value)}</span> },
  { title: "批次", dataIndex: "anchor_scope", key: "anchor_scope", width: 72, render: (value: string) => <Tag className="planned-tag">{evidenceScopeLabel(value)}</Tag> },
  { title: "锚定时间", dataIndex: "created_at", key: "created_at", width: 132, render: (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
];

const topologyEvidenceScopes: EvidenceScope[] = ["full", "topology", "chain", "execution", "verification", "remediation"];

const topologyPlanningIntentNodeColumns = [
  { title: "图谱节点", dataIndex: "node_key", key: "node_key", ellipsis: true },
  { title: "空间", dataIndex: "space_key", key: "space_key", ellipsis: true },
  { title: "标签", dataIndex: "label", key: "label", ellipsis: true },
  { title: "类型", dataIndex: "node_type", key: "node_type", width: 76, render: (value: string) => <Tag className={value === "capability" ? "ready-tag" : "planned-tag"}>{planningIntentNodeTypeLabel(value)}</Tag> },
  { title: "分数", dataIndex: "score", key: "score", width: 72, render: (value: number) => <Typography.Text type="secondary">{value.toFixed(4)}</Typography.Text> },
  { title: "匹配方式", dataIndex: "match_kind", key: "match_kind", width: 104, render: (value: string, row: PlanningIntentMatchedNode) => <Tag className="planned-tag" title={row.source_node_key ? `来源节点：${row.source_node_key}` : undefined}>{planningIntentMatchKindLabel(value)}</Tag> },
  { title: "来源", dataIndex: "match_source", key: "match_source", width: 108, render: (value: string) => <Typography.Text type="secondary">{planningIntentMatchSourceLabel(value)}</Typography.Text> },
];

const topologyPlanningIntentColumns = [
  { title: "意图", dataIndex: "intent_text", key: "intent_text", ellipsis: true },
  { title: "能力需求", dataIndex: "capability_requirements", key: "capability_requirements", ellipsis: true, render: (value: string[]) => value.length ? value.join("、") : "—" },
  { title: "计划", dataIndex: "plan_key", key: "plan_key", ellipsis: true },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 132, render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const verifiedPluginColumns = [
  { title: "插件", dataIndex: "plugin_id", key: "plugin_id" },
  { title: "版本", dataIndex: "plugin_version", key: "plugin_version" },
  { title: "能力", dataIndex: "capability", key: "capability" },
  { title: "隔离方式", dataIndex: "execution_mode", key: "execution_mode", render: () => "独立本机子进程" },
  { title: "目录状态", dataIndex: "catalog_registered", key: "catalog_registered", render: (registered: boolean) => <Tag className={registered ? "ready-tag" : "pending-tag"}>{registered ? "已登记" : "未登记"}</Tag> },
  { title: "副作用", dataIndex: "side_effects", key: "side_effects", render: () => <Tag className="ready-tag">只读</Tag> },
];

const contributionColumns = [
  { title: "插件", dataIndex: "plugin_id", key: "plugin_id" },
  { title: "版本", dataIndex: "plugin_version", key: "plugin_version" },
  { title: "导航", dataIndex: "navigation_count", key: "navigation_count" },
  { title: "视图", dataIndex: "view_count", key: "view_count" },
  { title: "动作", dataIndex: "action_count", key: "action_count" },
];

/**
 * 插件工作台（原 App.tsx plugins 视图提级）。
 * 只搬运不重写：L0–L2 控制面图、规划蓝图、目录只读 API、执行/验证/修复账本全部保持原逻辑与端点。
 */
export default function PluginsPage({ tenantId, onNotice, tab }: PageProps) {
  const [modal, contextHolder] = Modal.useModal();
  const [busy, setBusy] = useState(false);
  const [verifiedPlugins, setVerifiedPlugins] = useState<VerifiedPlugin[]>([]);
  const [contributions, setContributions] = useState<Contribution[]>([]);
  const [operations, setOperations] = useState<OperationsSummary>({ counts: {}, recent_decisions: [] });
  const [topologyAxis, setTopologyAxis] = useState<ClusterAxis>("business_domain");
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
  const [topologyRunChain, setTopologyRunChain] = useState("");
  const [topologyRunReason, setTopologyRunReason] = useState("");
  const [topologyVerifyTarget, setTopologyVerifyTarget] = useState<TopologyRunRow | null>(null);
  const [topologyVerifyReference, setTopologyVerifyReference] = useState<string>();
  const [topologyVerifyReason, setTopologyVerifyReason] = useState("");
  const [topologyRemediations, setTopologyRemediations] = useState<TopologyRemediationRow[]>([]);
  const [topologyRemediateTarget, setTopologyRemediateTarget] = useState<TopologyRunVerificationRow | null>(null);
  const [topologyRemediateAction, setTopologyRemediateAction] = useState<RemediationProposal["action"]>("re-run-locked-release");
  const [topologyRemediationReason, setTopologyRemediationReason] = useState("");
  const [topologyRerunTarget, setTopologyRerunTarget] = useState<TopologyRemediationRow | null>(null);
  const [topologyRerunReason, setTopologyRerunReason] = useState("");
  const [topologyApprovalError, setTopologyApprovalError] = useState(false);
  const [topologyExecutionError, setTopologyExecutionError] = useState(false);
  const [topologyRunError, setTopologyRunError] = useState(false);
  const [topologyCatalogError, setTopologyCatalogError] = useState(false);
  const [topologyIntentError, setTopologyIntentError] = useState(false);
  const [topologyRemediationError, setTopologyRemediationError] = useState(false);
  const [topologyEvidenceStatus, setTopologyEvidenceStatus] = useState<EvidenceStatus>();
  const [topologyEvidenceAnchors, setTopologyEvidenceAnchors] = useState<TopologyEvidenceAnchorRow[]>([]);
  const [topologyEvidenceProof, setTopologyEvidenceProof] = useState<EvidenceProof>();
  const [topologyEvidenceExport, setTopologyEvidenceExport] = useState<EvidenceExport>();
  const [topologyEvidenceScope, setTopologyEvidenceScope] = useState<EvidenceScope>("full");
  const [topologyEvidenceError, setTopologyEvidenceError] = useState(false);
  const [topologyPlanningIntentText, setTopologyPlanningIntentText] = useState("");
  const [topologyPlanningIntents, setTopologyPlanningIntents] = useState<TopologyPlanningIntentListItemRow[]>([]);
  const [topologyPlanningIntentResult, setTopologyPlanningIntentResult] = useState<TopologyPlanningIntentRow | null>(null);
  const [topologyPlanningIntentError, setTopologyPlanningIntentError] = useState(false);
  const [topologyIntent, setTopologyIntent] = useState("将 PDF 加入知识库并进行本地提取");
  const [topologyPlan, setTopologyPlan] = useState<TopologyPlan>();

  const loadTopologyCatalog = useCallback(async (id: string): Promise<void> => {
    try {
      const [clustersResp, blueprintsResp, releasesResp, plansResp, bridgesResp, chainsResp] = await Promise.all([
        apiRequest({ path: "/api/v1/topology/clusters", tenantId: id }),
        apiRequest({ path: "/api/v1/topology/blueprints", tenantId: id }),
        apiRequest({ path: "/api/v1/topology/releases", tenantId: id }),
        apiRequest({ path: "/api/v1/topology/plans", tenantId: id }),
        apiRequest({ path: "/api/v1/topology/bridges", tenantId: id }),
        apiRequest({ path: "/api/v1/topology/chains", tenantId: id }),
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
            apiRequest({ path: `/api/v1/topology/chains/${chainKey}/intents`, tenantId: id }),
            apiRequest({ path: `/api/v1/topology/chains/${chainKey}/approvals`, tenantId: id }),
            apiRequest({ path: `/api/v1/topology/chains/${chainKey}/executions`, tenantId: id }),
            apiRequest({ path: `/api/v1/topology/chains/${chainKey}/runs`, tenantId: id }),
            apiRequest({ path: "/api/v1/topology/remediation", tenantId: id }),
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
        const planningResp = await apiRequest({ path: "/api/v1/topology/planning/intents", tenantId: id, query: { limit: 50 } });
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

  const loadTopologyEvidenceStatus = useCallback(async (id: string): Promise<void> => {
    try {
      const payload = asRecord(await apiRequest({ path: "/api/v1/topology/evidence/status", tenantId: id }));
      setTopologyEvidenceStatus(payload as EvidenceStatus);
      setTopologyEvidenceError(false);
    } catch (error) {
      setTopologyEvidenceError(true);
      console.error("Failed to load topology evidence status", error);
    }
  }, []);

  useEffect(() => {
    if (!tenantId) return;
    void (async () => {
      const [verifiedResp, contributionsResp, operationsResp] = await Promise.all([
        apiRequest({ path: "/api/v1/plugins/verified", tenantId }),
        apiRequest({ path: "/api/v1/ui/contributions", tenantId }),
        apiRequest({ path: "/api/v1/ui/operations", tenantId }),
      ]);
      setVerifiedPlugins((asRecord(verifiedResp).items as VerifiedPlugin[]) ?? []);
      setContributions((asRecord(contributionsResp).items as Contribution[]) ?? []);
      setOperations(operationsResp as OperationsSummary);
      await loadTopologyCatalog(tenantId);
      await loadTopologyEvidenceStatus(tenantId);
    })();
  }, [tenantId, loadTopologyCatalog, loadTopologyEvidenceStatus]);

  const decideTopologyIntent = async (chainKey: string, slotKey: string, decision: "approve" | "reject"): Promise<void> => {
    if (!tenantId) return;
    setBusy(true);
    try {
      await apiRequest({
        path: `/api/v1/topology/chains/${encodeURIComponent(chainKey)}/approvals`, method: "POST", tenantId,
        body: {
          chain_key: chainKey, slot_key: slotKey, decision,
          idempotency_key: crypto.randomUUID(),
          reason: decision === "approve" ? "桌面端人工批准（only projection，不执行）" : "桌面端人工驳回（冻结意图）",
        },
      });
      await loadTopologyCatalog(tenantId);
      onNotice?.(decision === "approve" ? "意图已批准为 approved_projection；未经整链模拟执行不会触达任何运行时。" : "意图已驳回为 denied；整链执行将 fail-closed 拒绝。");
    } catch (error) {
      onNotice?.(error instanceof Error ? `意图审批失败：${error.message}` : "意图审批失败。");
    } finally { setBusy(false); }
  };

  const submitTopologyPlanningIntent = (): void => {
    if (!tenantId) return;
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
          const result = await apiRequest({
            path: "/api/v1/topology/planning/intents", method: "POST", tenantId,
            body: {
              intent, reason,
              budget: { max_matches: 4, expand_hops: 1 },
              idempotency_key: crypto.randomUUID(),
            },
          });
          setTopologyPlanningIntentResult(result as TopologyPlanningIntentRow);
          setTopologyPlanningIntentError(false);
          await loadTopologyCatalog(tenantId);
          const planned = result as TopologyPlanningIntentRow;
          onNotice?.(planned.reused_plan || planned.idempotent
            ? `图谱规划完成（复用）：${planned.intent_text.slice(0, 24)}… → 计划 ${planned.plan_key}，匹配 ${planned.matched_nodes.length} 节点、需求 ${planned.capability_requirements.length} 项。`
            : `图谱规划完成（plan_only）：${planned.intent_text.slice(0, 24)}… → 计划 ${planned.plan_key}（${planned.node_count} 节点 · ${planned.edge_count} 边），请到「调用链/审批/执行运行」标签页继续后续审批与演练。`);
        } catch (error) {
          onNotice?.(error instanceof Error ? `图谱规划失败：${error.message}` : "图谱规划失败（无匹配/无链接蓝图时 fail-closed 拒绝，不落任何计划）。");
        } finally { setBusy(false); }
      },
    });
  };

  const anchorTopologyEvidence = async (): Promise<void> => {
    if (!tenantId) return;
    setBusy(true);
    try {
      const payload = asRecord(await apiRequest({
        path: "/api/v1/topology/evidence/anchors", method: "POST", tenantId,
        body: { scope: topologyEvidenceScope, idempotency_key: crypto.randomUUID() },
      }));
      const entries = (payload.entries as TopologyEvidenceAnchorRow[]) ?? [];
      setTopologyEvidenceAnchors(entries);
      onNotice?.(`证据链已锚定 ${entries.length} 条（${evidenceScopeLabel(topologyEvidenceScope)}）；纯数据库投影，零子进程，幂等键可重放。`);
    } catch (error) {
      onNotice?.(error instanceof Error ? `证据锚定失败：${error.message}` : "证据锚定失败。");
    } finally {
      setBusy(false);
      await loadTopologyEvidenceStatus(tenantId);
    }
  };

  const verifyTopologyEvidence = async (): Promise<void> => {
    if (!tenantId) return;
    setBusy(true);
    try {
      const payload = asRecord(await apiRequest({
        path: "/api/v1/topology/evidence/verify", tenantId, query: { scope: topologyEvidenceScope },
      }));
      setTopologyEvidenceProof(payload as EvidenceProof);
    } catch (error) {
      onNotice?.(error instanceof Error ? `证据链校验失败：${error.message}` : "证据链校验失败。");
    } finally { setBusy(false); }
  };

  const exportTopologyEvidence = async (): Promise<void> => {
    if (!tenantId) return;
    setBusy(true);
    try {
      const payload = asRecord(await apiRequest({
        path: "/api/v1/topology/evidence/export", tenantId, query: { scope: topologyEvidenceScope },
      }));
      setTopologyEvidenceExport(payload as EvidenceExport);
      onNotice?.("只读审计导出已在响应体中返回（整体 SHA256 + 证明摘要）；M9 不落盘任何文件。");
    } catch (error) {
      onNotice?.(error instanceof Error ? `审计导出失败：${error.message}` : "审计导出失败。");
    } finally { setBusy(false); }
  };

  const executeTopologyChain = (chainKey: string): void => {
    if (!tenantId) return;
    modal.confirm({
      title: "执行影子模拟？",
      content: "这会沿调用链拓扑序生成确定性影子输出 Ref 与 SHA256 写入执行账本；mode 恒为 simulated，不启动任何子进程、不触达外部系统。",
      okText: "模拟执行",
      cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await apiRequest({
            path: `/api/v1/topology/chains/${encodeURIComponent(chainKey)}/executions`, method: "POST", tenantId,
            body: { chain_key: chainKey, mode: "simulated", idempotency_key: crypto.randomUUID(), reason: "桌面端整链影子模拟执行" },
          });
          await loadTopologyCatalog(tenantId);
          onNotice?.("整链影子模拟执行已写入账本（simulated，仅状态投影）；没有调用任何插件。");
        } catch (error) {
          onNotice?.(error instanceof Error ? `模拟执行失败：${error.message}` : "模拟执行失败。");
        } finally { setBusy(false); }
      },
    });
  };

  const drillTopologyChain = (chainKey: string): void => {
    if (!tenantId) return;
    modal.confirm({
      title: "受限只读演练？",
      content: "这会启动已验证内置插件的只读隔离子进程，沿调用链拓扑序逐节点执行并写入 isolated 账本（插件版本/runtime 校验和/输入校验和/artifact Refs）。演练严格限定测试库；未经 execute.isolated 权限将 403 拒绝，失败节点即刻终止后续节点且绝不降级为 shadow。",
      okText: "开始演练",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        setBusy(true);
        try {
          await apiRequest({
            path: `/api/v1/topology/chains/${encodeURIComponent(chainKey)}/executions`, method: "POST", tenantId,
            body: { chain_key: chainKey, mode: "isolated", idempotency_key: crypto.randomUUID(), reason: "桌面端受限只读演练（经 Policy 网关与 ISO 门控）" },
          });
          await loadTopologyCatalog(tenantId);
          onNotice?.("受限只读演练已执行并在账本记录 isolated 行（插件只读、经 ISO 门控）；未获 execute.isolated 权限时返回 403 且零子进程。");
        } catch (error) {
          onNotice?.(error instanceof Error ? `受限演练失败：${error.message}` : "受限演练失败。");
        } finally { setBusy(false); }
      },
    });
  };

  const loadTopologyRunDetail = useCallback(async (runId: string): Promise<void> => {
    if (!tenantId || topologyRunDetail[runId]) return;
    try {
      const payload = asRecord(await apiRequest({ path: `/api/v1/topology/runs/${runId}`, tenantId }));
      setTopologyRunDetail((state) => ({ ...state, [runId]: (payload.entries as TopologyExecutionRow[]) ?? [] }));
    } catch (error) {
      onNotice?.(error instanceof Error ? `运行详情加载失败：${error.message}` : "运行详情加载失败。");
    }
  }, [tenantId, topologyRunDetail, onNotice]);

  const loadTopologyRunVerifications = useCallback(async (runId: string): Promise<void> => {
    if (!tenantId) return;
    try {
      const payload = asRecord(await apiRequest({ path: `/api/v1/topology/runs/${runId}/verifications`, tenantId }));
      setTopologyRunVerifications((state) => ({ ...state, [runId]: (payload.items as TopologyRunVerificationRow[]) ?? [] }));
    } catch (error) {
      onNotice?.(error instanceof Error ? `运行验证加载失败：${error.message}` : "运行验证加载失败。");
    }
  }, [tenantId, onNotice]);

  const startTopologyVerification = (run: TopologyRunRow): void => {
    setTopologyVerifyReason("");
    setTopologyVerifyReference(undefined);
    setTopologyVerifyTarget(run);
  };

  const confirmTopologyVerification = (): void => {
    const target = topologyVerifyTarget;
    if (!tenantId || !target || !topologyVerifyReason.trim()) return;
    modal.confirm({
      title: "确认发起运行验证？",
      content: `将对运行 ${target.run_id.slice(0, 16)}…（${runStatusLabel(target.status)}）做纯 DB 逐节点 output_checksum 核对（${target.node_total} 节点）。零子进程、仅限测试库；drift 时只生成只读回滚裁定投影，不执行任何回滚/修复。理由：${topologyVerifyReason.trim()}`,
      okText: "确认发起验证",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        setBusy(true);
        try {
          const payload = asRecord(await apiRequest({
            path: `/api/v1/topology/runs/${target.run_id}/verifications`, method: "POST", tenantId,
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
          onNotice?.(
            created.status === "verified"
              ? `运行验证完成：verified（${created.node_matched}/${created.node_total} 节点一致，无回滚裁定）。`
              : `运行验证完成：drifted（匹配 ${created.node_matched} · 偏差 ${created.node_mismatched} · 缺参照 ${created.node_ref_missing}）。仅生成只读回滚裁定，未执行任何修复。`,
          );
        } catch (error) {
          onNotice?.(error instanceof Error ? `发起运行验证失败：${error.message}` : "发起运行验证失败（run 非 success/参照冲突时 fail-closed 拒绝）。");
        } finally { setBusy(false); }
      },
    });
  };

  const startTopologyRun = (chainKey: string): void => {
    if (!tenantId) return;
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
          await apiRequest({
            path: `/api/v1/topology/chains/${encodeURIComponent(chainKey)}/runs`, method: "POST", tenantId,
            body: { chain_key: chainKey, mode: "isolated", idempotency_key: crypto.randomUUID(), reason },
          });
          setTopologyRunReason("");
          await loadTopologyCatalog(tenantId);
          onNotice?.("受限执行运行已发起并记录（独立 run_id + 状态机）；账本行按运行分组，可在「执行运行」tab 展开查看逐节点血缘。");
        } catch (error) {
          onNotice?.(error instanceof Error ? `发起运行失败：${error.message}` : "发起运行失败（未发布锁定/待决审批/运行中并发时 fail-closed 拒绝）。");
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
    if (!tenantId || !target || !topologyRemediationReason.trim()) return;
    setBusy(true);
    try {
      await apiRequest({
        path: `/api/v1/topology/verifications/${target.verification_id}/remediation`, method: "POST", tenantId,
        body: {
          action: topologyRemediateAction, reason: topologyRemediationReason.trim(),
          idempotency_key: crypto.randomUUID(),
        },
      });
      setTopologyRemediateTarget(null);
      setTopologyRemediationReason("");
      await loadTopologyCatalog(tenantId);
      onNotice?.("修复提案已打开（append-only 证据记录）；待人工批准/驳回/关闭，桌面端不提供任何自动修复执行。");
    } catch (error) {
      onNotice?.(error instanceof Error ? `发起修复提案失败：${error.message}` : "发起修复提案失败（仅 drifted 且带回滚裁定可提案，其余 fail-closed）。");
    } finally { setBusy(false); }
  };

  const decideTopologyRemediation = (proposal: TopologyRemediationRow, decision: "approve" | "reject" | "close"): void => {
    if (!tenantId) return;
    modal.confirm({
      title: decision === "approve" ? "批准修复提案？" : decision === "reject" ? "驳回修复提案？" : "关闭修复提案？",
      content: `该决策将追加写入不可变决策账本并派生终态（${proposalStatusLabel(decision === "approve" ? "approved" : decision === "reject" ? "rejected" : "closed")}）；决策不可逆，重复不同决策将 409 拒绝。${decision === "close" ? "仅 escalate-human 提案可关闭。" : ""}`,
      okText: "确认决策",
      cancelText: "取消",
      okButtonProps: { danger: decision !== "approve" },
      onOk: async () => {
        setBusy(true);
        try {
          await apiRequest({
            path: `/api/v1/topology/remediation/${proposal.proposal_id}/decisions`, method: "POST", tenantId,
            body: {
              decision, approver: "desktop-m8-reviewer",
              reason: decision === "approve" ? "桌面端批准（仅批准投影，重跑另行发起）" : decision === "reject" ? "桌面端驳回（升级人工/禁止执行）" : "桌面端关闭（escalate-human 结案）",
              idempotency_key: crypto.randomUUID(),
            },
          });
          await loadTopologyCatalog(tenantId);
          onNotice?.(decision === "approve" ? "提案已批准（approved 派生自决策账本）；re-run-locked-release 提案可另行发起受治理重跑。" : proposalStatusLabel(decision === "reject" ? "rejected" : "closed") + "；决策账本已追加，后续不可翻转。");
        } catch (error) {
          onNotice?.(error instanceof Error ? `决策失败：${error.message}` : "决策失败（提案已终态/close 非 escalate-human 时 409）。");
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
    if (!tenantId || !target || !topologyRerunReason.trim()) return;
    setBusy(true);
    try {
      await apiRequest({
        path: `/api/v1/topology/remediation/${target.proposal_id}/runs`, method: "POST", tenantId,
        body: { reason: topologyRerunReason.trim(), idempotency_key: crypto.randomUUID() },
      });
      setTopologyRerunTarget(null);
      setTopologyRerunReason("");
      await loadTopologyCatalog(tenantId);
      onNotice?.("受治理重跑已发起；提案→运行血缘已追加（remediation_run_links），账本不可变。");
    } catch (error) {
      onNotice?.(error instanceof Error ? `受治理重跑失败：${error.message}` : "受治理重跑失败（非 approved/re-run-locked-release 或并发运行中时 fail-closed）。");
    } finally { setBusy(false); }
  };

  const selectTopologyNode = useCallback((node: GraphVisualizationNode): void => { setSelectedTopologyNode(node); }, []);
  const generateTopologyPlan = (): void => {
    const plan = createTopologyPlan(topologyIntent);
    setTopologyPlan(plan);
    onNotice?.(plan.unresolvedCapabilities.length ? "规划目录存在能力缺口；没有选择不相关的蓝图。" : "不可执行候选计划已生成；未安装、启动或调用任何插件。");
  };

  const pluginTopologyGraph = useMemo(() => topologyGraphForAxis(topologyAxis), [topologyAxis]);
  const selectedTopologyBlueprint = useMemo<TopologyBlueprint | undefined>(() => {
    if (!selectedTopologyNode?.id.startsWith("blueprint:")) return undefined;
    return pluginTopologyCatalog.blueprints.find((blueprint) => blueprint.id === selectedTopologyNode.id.slice("blueprint:".length));
  }, [selectedTopologyNode]);

  const pluginRuntimeDecisions = operations.recent_decisions.filter((item) => item.capability === "knowledge.extract.document");
  const selectedTopologyCluster = selectedTopologyNode?.id.startsWith("cluster:")
    ? pluginTopologyCatalog.clusters.find((cluster) => cluster.id === selectedTopologyNode.id.slice("cluster:".length))
    : undefined;
  const selectedCapability = selectedTopologyNode?.id.startsWith("capability:")
    ? selectedTopologyNode.id.slice("capability:".length)
    : undefined;

  return <>
    {contextHolder}
    <section className="detail-head"><div><Typography.Title level={3}>插件拓扑工作台</Typography.Title><Typography.Text type="secondary">L0–L2 控制面图：按多轴集群查看规划蓝图与能力契约。</Typography.Text></div><Tag className="gateway-tag">规划，不执行</Tag></section>
    <section className="data-bar plugin-topology-metrics">
      <Card className="metric-card" size="small"><span>集群</span><strong>{pluginTopologyCatalog.clusters.length}</strong><small>四个独立调度轴</small></Card>
      <Card className="metric-card" size="small"><span>规划蓝图</span><strong>{pluginTopologyCatalog.blueprints.length}</strong><small>保持 plan_only</small></Card>
      <Card className="metric-card" size="small"><span>能力契约</span><strong>{pluginTopologyCatalog.blueprints.reduce((total, blueprint) => total + blueprint.capabilities.length, 0)}</strong><small>只描述输入输出</small></Card>
      <Card className="metric-card" size="small"><span>已验证运行时</span><strong>{verifiedPlugins.filter((plugin) => plugin.catalog_registered).length}</strong><small>仅本地只读</small></Card>
    </section>
    <Alert className="plugin-topology-alert" showIcon type="info" message="此处展示的是规划蓝图，不是已安装插件。生成结果始终为 plan_only，不能执行、安装、联网或申请权限。" />
    <Card className="chart-card" title="插件数据流图" extra={<Tag className="planned-tag">规划，不执行</Tag>}>
      <Typography.Paragraph>插件=节点，输入/输出契约=接口子节点；输出契约命中另一插件输入契约即形成数据流连线（绿色箭头）。灰色端口为未对接接口（外部来源 / 待下游）。</Typography.Paragraph>
      <PluginFlowGraph
        blueprints={pluginTopologyCatalog.blueprints}
        selectedId={selectedTopologyNode?.id.startsWith("blueprint:") ? selectedTopologyNode.id.slice("blueprint:".length) : undefined}
        onSelect={(blueprint) => setSelectedTopologyNode({ id: `blueprint:${blueprint.id}`, label: blueprint.name, node_type: "blueprint" })}
      />
    </Card>
    <Card className="chart-card plugin-runtime-card" title="已验证隔离运行时" extra={<Tag className="ready-tag">仅状态展示</Tag>}>
      <Typography.Paragraph>此列表来自控制平面已验证绑定与租户登记状态。桌面端不提供直接执行入口；真实调用只能由受控 API 经过策略网关后启动隔离进程。</Typography.Paragraph>
      <Table className="stock-table" size="small" rowKey={(row) => `${row.plugin_id}-${row.plugin_version}`} dataSource={verifiedPlugins} columns={verifiedPluginColumns} pagination={false} locale={{ emptyText: "暂无已验证运行时" }} />
      <Typography.Title level={5}>最近策略记录</Typography.Title>
      <Table className="stock-table" size="small" rowKey={(row) => `${row.trace_id}-${row.decided_at}`} dataSource={pluginRuntimeDecisions} pagination={{ pageSize: 5 }} columns={[{ title: "时间", dataIndex: "decided_at", key: "decided_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") }, { title: "结果", dataIndex: "decision", key: "decision", render: (value: string) => <Tag className={value === "ALLOW" ? "ready-tag" : "pending-tag"}>{value}</Tag> }, { title: "规则说明", dataIndex: "reason", key: "reason", ellipsis: true }, { title: "Trace", dataIndex: "trace_id", key: "trace_id", ellipsis: true }]} locale={{ emptyText: "暂无插件策略记录" }} />
    </Card>
    <div className="plugin-topology-grid">
      <Card className="chart-card graph-canvas-card" title="多轴集群图" extra={<Select aria-label="插件拓扑筛选轴" value={topologyAxis} onChange={(value: ClusterAxis) => { setTopologyAxis(value); setSelectedTopologyNode(undefined); }} options={topologyAxes} />}>
        <GraphExplorer data={pluginTopologyGraph} selectedNodeId={selectedTopologyNode?.id} onNodeClick={selectTopologyNode} />
      </Card>
      <Card className="chart-card graph-inspector" title="节点检查器">
        {selectedTopologyBlueprint ? <Descriptions size="small" column={1} bordered items={[
          { key: "name", label: "蓝图", children: selectedTopologyBlueprint.name },
          { key: "state", label: "状态", children: <Tag className="planned-tag">{selectedTopologyBlueprint.lifecycle} · {selectedTopologyBlueprint.maturity}</Tag> },
          { key: "ability", label: "能力", children: selectedTopologyBlueprint.capabilities.join("、") },
          { key: "io", label: "输入 / 输出", children: `${selectedTopologyBlueprint.inputContracts.join("、")} → ${selectedTopologyBlueprint.outputContracts.join("、")}` },
          { key: "risk", label: "未来风险", children: selectedTopologyBlueprint.riskClass },
          { key: "source", label: "来源", children: selectedTopologyBlueprint.sourceRef },
        ]} /> : selectedTopologyCluster ? <Descriptions size="small" column={1} bordered items={[
          { key: "cluster", label: "集群", children: selectedTopologyCluster.name },
          { key: "axis", label: "调度轴", children: topologyAxes.find((axis) => axis.value === selectedTopologyCluster.axis)?.label ?? selectedTopologyCluster.axis },
          { key: "description", label: "说明", children: selectedTopologyCluster.description },
        ]} /> : selectedCapability ? <Descriptions size="small" column={1} bordered items={[
          { key: "capability", label: "能力契约", children: selectedCapability },
          { key: "state", label: "状态", children: "仅作目录与候选计划描述，不触发调用。" },
        ]} /> : <div className="empty">点击集群或蓝图节点查看边界与来源；蓝图详情会展开能力契约。</div>}
      </Card>
    </div>
    <Card className="chart-card plugin-plan-card" title="不可执行计划" extra={<Tag className="planned-tag">plan_only</Tag>}>
      <Typography.Paragraph>根据意图从冻结目录选择候选链；无法匹配时会报告能力缺口，不会使用名称相近的替代项。</Typography.Paragraph>
      <Space.Compact className="plugin-plan-input"><Input aria-label="插件拓扑意图" value={topologyIntent} onChange={(event) => setTopologyIntent(event.target.value)} onPressEnter={generateTopologyPlan} placeholder="例如：将 PDF 加入知识库并进行本地提取" /><Button type="primary" onClick={generateTopologyPlan}>生成不可执行计划</Button></Space.Compact>
      {topologyPlan ? <div className="plugin-plan-result">
        <Alert showIcon type={topologyPlan.unresolvedCapabilities.length ? "warning" : "success"} message={topologyPlan.safetyNotice} description={`目录：${topologyPlan.catalogRelease.id} · ${topologyPlan.catalogRelease.version} · ${topologyPlan.catalogRelease.checksum.slice(0, 12)}…；候选 ${topologyPlan.budget.candidates} 个，链长 ${topologyPlan.budget.chainLength}。`} />
        {topologyPlan.unresolvedCapabilities.length ? <Alert className="plugin-gap-alert" showIcon type="warning" message={`能力缺口：${topologyPlan.unresolvedCapabilities.join("、")}`} description={topologyPlan.requiresApproval ? "涉及真实交易或未来高风险动作，必须保留人工审批；当前没有执行入口。" : "请补充经契约验证的规划蓝图，当前不会自动替代。"} /> : <Table className="stock-table" size="small" rowKey="key" dataSource={topologyPlan.nodes} columns={topologyPlanColumns} pagination={false} />}
      </div> : <div className="empty plugin-plan-empty">输入意图后生成候选链。此操作完全在桌面端目录中完成。</div>}
    </Card>
    <Card className="chart-card plugin-catalog-card" title="拓扑目录（真实只读 API）" extra={<Tag className="gateway-tag">规划，不执行</Tag>}>
      <Typography.Paragraph>以下数据来自控制平面拓扑只读 API，展示集群、蓝图、发布、计划、跨域桥接、调用链与调用意向的实时状态。调用链由 plan_only 计划确定性物化，绝不携执行能力。所有操作均受策略网关裁决。</Typography.Paragraph>
      <div className="plugin-catalog-banner">
        <Tag className={topologyCatalogError ? "pending-tag" : "ready-tag"}>{topologyCatalogError ? "加载失败" : "已连接"}</Tag>
        {topologyReleases.find((r) => r.status === "published") ? (
          <Tag className="ready-tag">已发布 · {topologyReleases.filter((r) => r.status === "published").length} 个版本</Tag>
        ) : null}
        <Tag className="planned-tag">plan_only</Tag>
        <Tag className="ready-tag">只读</Tag>
        <Tag className="gateway-tag">受限只读演练（经网关 · 逐运行账本）</Tag>
      </div>
      <Tabs size="small" type="card" defaultActiveKey={tab ?? "clusters"} items={[
        { key: "planning", label: `图谱规划 (${topologyPlanningIntents.length})`, children: <>
            <Alert className="plugin-catalog-banner-alert" showIcon type="info" message="把中文业务意图经确定性图谱语义索引（pg_trgm 打分 + 单跳有界扩展，零 LLM、零外网）解析为能力需求，复用既有规划器生成 plan_only 计划。只规划、不执行；匹配失败或缺少链接蓝图时 fail-closed 拒绝，不落任何计划。" />
            <Space.Compact className="plugin-plan-input" style={{ margin: "8px 0" }}>
              <Input aria-label="图谱规划意图" value={topologyPlanningIntentText} onChange={(event) => setTopologyPlanningIntentText(event.target.value)} onPressEnter={submitTopologyPlanningIntent} placeholder="例如：总账质量校验并出具量化研究结论" maxLength={512} />
              <Button type="primary" disabled={!tenantId || !planningIntentCanGenerate(topologyPlanningIntentText)} loading={busy} onClick={submitTopologyPlanningIntent}>生成不可执行计划</Button>
            </Space.Compact>
            <Typography.Text type="secondary">{planningIntentLengthHint(topologyPlanningIntentText)}</Typography.Text>
            {topologyPlanningIntentError ? <Alert className="plugin-gap-alert" showIcon type="warning" message="图谱规划历史加载失败" description="可能尚未发布 topology.intent.read 只读策略（fail-closed 403）；请先运行注册脚本发布 local-plugin-topology-read 扩展，或稍后重试。" /> : null}
            {topologyPlanningIntentResult ? <div className="plugin-plan-result">
              <Alert showIcon type="success" message={`${planningIntentEvidenceNote(topologyPlanningIntentResult)} · ${topologyPlanningIntentResult.capability_requirements.length} 项能力需求`} description={`意图：「${topologyPlanningIntentResult.intent_text}」→ 计划 ${topologyPlanningIntentResult.plan_key}（${topologyPlanningIntentResult.node_count} 节点 · ${topologyPlanningIntentResult.edge_count} 边）· 校验和 ${topologyPlanningIntentResult.plan_checksum.slice(0, 12)}…`} />
              <Typography.Title level={5}>匹配证据（图谱语义索引）</Typography.Title>
              <Table className="stock-table" size="small" rowKey="node_key" dataSource={topologyPlanningIntentResult.matched_nodes} columns={topologyPlanningIntentNodeColumns} pagination={false} locale={{ emptyText: "无匹配节点（fail-closed 未落计划）" }} />
              <Typography.Title level={5}>能力需求与后续引导</Typography.Title>
              <Descriptions size="small" column={1} bordered items={[
                { key: "requirements", label: "能力需求", children: topologyPlanningIntentResult.capability_requirements.join("、") },
                { key: "plan", label: "计划键", children: <Tag className="planned-tag">{topologyPlanningIntentResult.plan_key}</Tag> },
                { key: "next", label: "下一步", children: "请切换到「调用链 / 审批账本 / 执行运行」标签页：物化链 → 逐节点人工批准 → 受限只读演练（双策略门）→ M7 运行验证。" },
              ]} />
            </div> : null}
            <Typography.Title level={5}>意图历史（追加式证据）</Typography.Title>
            <Table className="stock-table" size="small" rowKey="intent_id" dataSource={topologyPlanningIntents} columns={topologyPlanningIntentColumns} pagination={{ pageSize: 8 }} locale={{ emptyText: "暂无图谱规划记录；未经 topology.intent.plan 权限时生成将被 403 拒绝" }} />
          </> },
        { key: "clusters", label: "集群", children: <Table className="stock-table" size="small" rowKey="key" dataSource={topologyClusters} columns={topologyClusterColumns} pagination={false} locale={{ emptyText: "暂无拓扑集群" }} /> },
        { key: "blueprints", label: "规划蓝图", children: <Table className="stock-table" size="small" rowKey="key" dataSource={topologyBlueprints} columns={topologyBlueprintColumns} pagination={false} locale={{ emptyText: "暂无规划蓝图" }} /> },
        { key: "releases", label: "发布", children: <Table className="stock-table" size="small" rowKey={(row) => `${row.release_key}-${row.version}`} dataSource={topologyReleases} columns={topologyReleaseColumns} pagination={false} locale={{ emptyText: "暂无目录发布" }} /> },
        { key: "plans", label: "计划", children: <Table className="stock-table" size="small" rowKey="plan_key" dataSource={topologyPlans} columns={topologyPlanRowColumns} pagination={false} locale={{ emptyText: "暂无路由计划" }} /> },
        { key: "bridges", label: "跨域桥接", children: <Table className="stock-table" size="small" rowKey={(row) => `${row.blueprint_key}-${row.ref_kind}`} dataSource={topologyBridges} columns={topologyBridgeColumns} pagination={false} locale={{ emptyText: "暂无跨域桥接" }} /> },
        { key: "chains", label: "调用链", children: <Table className="stock-table" size="small" rowKey="chain_key" dataSource={topologyChains} pagination={false} columns={[...topologyChainColumns, { title: "操作", key: "actions", width: 172, render: (_value, row: TopologyChainRow) => <Space size={4}><Button size="small" disabled={!tenantId} loading={busy} onClick={() => executeTopologyChain(row.chain_key)}>影子模拟</Button><Button size="small" danger disabled={!tenantId} loading={busy} onClick={() => drillTopologyChain(row.chain_key)}>受限演练</Button></Space> }]} locale={{ emptyText: "暂无调用链" }} /> },
        { key: "intents", label: "调用意向", children: topologyIntentError ? <Alert showIcon type="warning" message="调用意向加载失败" /> : <Table className="stock-table" size="small" rowKey={(row) => `${row.slot_key}-${row.role}`} dataSource={topologyIntents} pagination={false} columns={[...topologyIntentColumns, { title: "操作", key: "actions", width: 148, render: (_value, row: TopologyIntentRow) => row.policy_decision === "requires_approval" && row.status === "materialized" ? <Space size={4}><Button size="small" type="primary" disabled={!tenantId} loading={busy} onClick={() => void decideTopologyIntent(topologyChains[0].chain_key, row.slot_key, "approve")}>批准</Button><Button size="small" danger disabled={!tenantId} loading={busy} onClick={() => void decideTopologyIntent(topologyChains[0].chain_key, row.slot_key, "reject")}>驳回</Button></Space> : <Tag className={row.status === "approved_projection" ? "ready-tag" : "pending-tag"}>{row.status === "approved_projection" ? "已批准投影" : row.status === "denied" ? "已拒绝" : "无需审批"}</Tag> }]} locale={{ emptyText: "暂无调用意向" }} /> },
        { key: "approvals", label: "审批账本", children: topologyApprovalError ? <Alert showIcon type="warning" message="审批账本加载失败" /> : <Table className="stock-table" size="small" rowKey={(row) => `${row.slot_key}-${row.decision}-${row.created_at}`} dataSource={topologyApprovals} columns={topologyApprovalColumns} pagination={false} locale={{ emptyText: "暂无审批记录；权限不足时显示为失败" }} /> },
        { key: "executions", label: "执行账本", children: topologyExecutionError ? <Alert showIcon type="warning" message="执行账本加载失败" /> : <Table className="stock-table" size="small" rowKey="execution_id" dataSource={topologyExecutions} columns={topologyExecutionColumns} pagination={false} locale={{ emptyText: "暂无执行记录；simulated 为状态投影，isolated 为受限只读演练" }} /> },
        { key: "remediation", label: `修复提案 (${topologyRemediations.length})`, children: topologyRemediationError ? <Alert showIcon type="warning" message="修复提案加载失败" /> : <Table className="stock-table" size="small" rowKey="proposal_id" dataSource={topologyRemediations} columns={topologyRemediationColumns} pagination={false} locale={{ emptyText: "暂无修复提案；漂移验证展开后可发起追加型提案，终态由不可变决策账本派生" }} /> },
        { key: "runs", label: `执行运行 (${topologyRuns.length})`, children: topologyRunError ? <Alert showIcon type="warning" message="执行运行列表加载失败" /> : <>
            <Space wrap className="plugin-run-launcher" style={{ marginBottom: 8 }}>
              <Select aria-label="调用链" size="small" style={{ minWidth: 240 }} value={topologyRunChain || undefined} onChange={(value) => setTopologyRunChain(value)} options={topologyChains.map((chain) => ({ value: chain.chain_key, label: `${chain.chain_key.slice(0, 20)}…` }))} placeholder="选择调用链" />
              <Input size="small" style={{ width: 300 }} value={topologyRunReason} onChange={(event) => setTopologyRunReason(event.target.value)} placeholder="演练原因（必填；仅限测试库受限只读演练）" />
              <Button size="small" danger type="primary" disabled={!tenantId || !topologyRunReason.trim() || !topologyRunChain || anyRunRunning(topologyRuns)} loading={busy} onClick={() => startTopologyRun(topologyRunChain)}>发起受限演练运行</Button>
              {anyRunRunning(topologyRuns) ? <Tag className="pending-tag">有运行中(running)运行，禁止并发发起(409)</Tag> : null}
            </Space>
            <Table className="stock-table" size="small" rowKey="run_id" dataSource={topologyRuns} pagination={false}
              columns={[...topologyRunColumns,
                {
                  title: "验证状态", key: "verification_status", width: 112,
                  render: (_value: unknown, row: TopologyRunRow) => {
                    const list = topologyRunVerifications[row.run_id] ?? [];
                    const latest = list[0];
                    if (!runCanVerify(row)) return <Tag className="pending-tag">不可验证</Tag>;
                    if (!latest) return <Tag className="pending-tag">未验证</Tag>;
                    return <Tag className={latest.status === "verified" ? "ready-tag" : "risk-high"} title={latest.reason}>{verificationStatusLabel(latest.status)}</Tag>;
                  },
                },
                {
                  title: "操作", key: "verification_actions", width: 130,
                  render: (_value: unknown, row: TopologyRunRow) => (
                    <Button size="small" disabled={!tenantId || !runCanVerify(row) || busy} loading={busy} onClick={() => startTopologyVerification(row)}>发起运行验证</Button>
                  ),
                },
              ]}
              expandable={{
                expandedRowRender: (row) => <Space direction="vertical" style={{ width: "100%" }}>
                  <Table className="stock-table" size="small" rowKey="execution_id" dataSource={topologyRunDetail[row.run_id] ?? []} columns={topologyExecutionColumns} pagination={false} locale={{ emptyText: "展开后将加载该 run_id 的逐节点账本血缘" }} />
                  {(topologyRunVerifications[row.run_id] ?? []).length ? (topologyRunVerifications[row.run_id] ?? []).map((verification) => (
                    <Card key={verification.verification_id} size="small" className="verification-card" title={<Space size={4}><Tag className={verification.status === "verified" ? "ready-tag" : "risk-high"}>{verificationStatusLabel(verification.status)}</Tag><span title={verification.verification_id}>{verification.verification_id.slice(0, 16)}…</span></Space>} extra={verification.idempotent ? <Tag>幂等重放</Tag> : null}>
                      <Typography.Paragraph style={{ marginBottom: 8 }}>{verification.reason || "—"}</Typography.Paragraph>
                      <Space wrap style={{ marginBottom: 8 }}>
                        <Tag>节点 {verification.node_total}</Tag><Tag>匹配 {verification.node_matched}</Tag><Tag className={verification.node_mismatched ? "risk-high" : "ready-tag"}>偏差 {verification.node_mismatched}</Tag><Tag className={verification.node_ref_missing ? "pending-tag" : "ready-tag"}>缺参照 {verification.node_ref_missing}</Tag>
                        <Typography.Text type="secondary">计数守恒：{verification.node_matched} + {verification.node_mismatched} + {verification.node_ref_missing} = {verification.node_total}</Typography.Text>
                      </Space>
                      {verification.rollback_verdict ? <div className="rollback-verdict-card"><Typography.Text strong>只读回滚裁定：{rollbackActionLabel(verification.rollback_verdict.action)}</Typography.Text><div>受影响节点：{verification.rollback_verdict.affected_slots.join("、")}</div><Typography.Text type="secondary">基线参照 {verification.rollback_verdict.baseline.reference_run_id?.slice(0, 16) ?? "无"}… · 锁链 {verification.rollback_verdict.baseline.chain_checksum.slice(0, 12)}… · 规划器 {verification.rollback_verdict.baseline.planner_version}。此裁定仅为建议投影，桌面不提供任何回滚/修复执行入口。</Typography.Text></div> : <Tag className="ready-tag">无偏差，不含回滚裁定</Tag>}
                      {verification.status === "drifted" && verification.rollback_verdict ? <div className="remediation-card"><Space wrap><Tag className="gateway-tag">M8 修复提案</Tag><Button size="small" type="primary" danger disabled={!tenantId || busy} loading={busy} onClick={() => startTopologyRemediation(verification)}>发起修复提案</Button><Typography.Text type="secondary">仅 drifted 且带回滚裁定的验证可提案；提案追加型、终态由决策账本派生。</Typography.Text></Space>
                        {topologyRemediations.filter((proposal) => proposal.verification_id === verification.verification_id).map((proposal) => (
                          <div key={proposal.proposal_id} className="remediation-proposal-card">
                            <Space wrap style={{ marginBottom: 4 }}>
                              <Tag className={proposal.status === "approved" ? "ready-tag" : proposal.status === "pending_approval" ? "pending-tag" : "risk-high"}>{proposalStatusLabel(proposal.status)}</Tag>
                              <Tag>{proposalActionLabel(proposal.action)}</Tag>
                              <span title={proposal.proposal_id}>提案 {proposal.proposal_id.slice(0, 16)}…</span>
                              {proposal.idempotent ? <Tag>幂等重放</Tag> : null}
                            </Space>
                            <Typography.Paragraph style={{ marginBottom: 4 }} type="secondary">{proposal.reason || "—"}</Typography.Paragraph>
                            <Space wrap>
                              {proposalCanDecide(proposal) ? <>
                                <Button size="small" type="primary" disabled={!tenantId || busy} loading={busy} onClick={() => decideTopologyRemediation(proposal, "approve")}>批准</Button>
                                <Button size="small" danger disabled={!tenantId || busy} loading={busy} onClick={() => decideTopologyRemediation(proposal, "reject")}>驳回</Button>
                                {proposal.action === "escalate-human" ? <Button size="small" disabled={!tenantId || busy} loading={busy} onClick={() => decideTopologyRemediation(proposal, "close")}>关闭</Button> : null}
                              </> : proposalCanRerun(proposal) && !proposal.remediation_run_id ? <Button size="small" danger disabled={!tenantId || busy} loading={busy} onClick={() => startTopologyRerun(proposal)}>发起重跑</Button> : <Typography.Text type="secondary">{proposal.remediation_run_id ? `已关联重跑 ${proposal.remediation_run_id.slice(0, 16)}…` : "终态；决策账本不可逆"}</Typography.Text>}
                            </Space>
                          </div>
                        ))}
                      </div> : null}
                    </Card>
                  )) : <Typography.Text type="secondary">展开后将加载该 run_id 的验证记录（逐节点 checksum 对标与只读回滚裁定）。</Typography.Text>}
                </Space>,
                onExpand: (expanded, row) => {
                  if (expanded) {
                    void loadTopologyRunDetail(row.run_id);
                    void loadTopologyRunVerifications(row.run_id);
                  }
                },
              }}
              locale={{ emptyText: "暂无执行运行；发起后将以独立 run_id 展示状态机与逐节点血缘" }} />
          </> },
        { key: "evidence", label: `证据链 (${topologyEvidenceStatus?.total_anchors ?? 0})`, children: topologyEvidenceError ? <Alert showIcon type="warning" message="证据链状态加载失败；未经 topology.evidence.export 权限将 403（fail-closed）" /> : <>
            <Typography.Paragraph style={{ marginBottom: 8 }}>M9 把 M1–M8 证据账本按确定性序列化锚入租户级追加式 SHA256 前驱哈希链；锚定为纯数据库投影（零子进程），校验重算行哈希并回放前驱链以精确定位首个失配，导出仅以响应体返回审计元数据（整体 SHA256），不落盘任何文件。</Typography.Paragraph>
            {topologyEvidenceStatus ? <Descriptions size="small" bordered column={4} items={[
              { key: "chain", label: "链标识", children: <span title={topologyEvidenceStatus.chain_key}>{evidenceHashShort(topologyEvidenceStatus.chain_key, 36)}</span> },
              { key: "anchors", label: "锚点总数", children: topologyEvidenceStatus.total_anchors },
              { key: "tailSeq", label: "尾序号", children: topologyEvidenceStatus.tail_seq },
              { key: "tailHash", label: "尾哈希", children: <span title={topologyEvidenceStatus.tail_hash}>{evidenceHashShort(topologyEvidenceStatus.tail_hash)}</span> },
              { key: "lastAt", label: "最近锚定", children: topologyEvidenceStatus.last_anchored_at ? new Date(topologyEvidenceStatus.last_anchored_at).toLocaleString("zh-CN") : "—" },
            ]} /> : <Tag className="pending-tag">尚无锚点；点击“锚定”建立链条</Tag>}
            <Space wrap style={{ margin: "8px 0" }}>
              <Select aria-label="证据范围" size="small" style={{ width: 110 }} value={topologyEvidenceScope} onChange={(value: EvidenceScope) => setTopologyEvidenceScope(value)} options={topologyEvidenceScopes.map((scope) => ({ value: scope, label: evidenceScopeLabel(scope) }))} />
              <Button size="small" type="primary" disabled={!tenantId || busy} loading={busy} onClick={() => void anchorTopologyEvidence()}>锚定</Button>
              <Button size="small" disabled={!tenantId || busy} loading={busy} onClick={() => void verifyTopologyEvidence()}>校验整链</Button>
              <Button size="small" disabled={!tenantId || busy} loading={busy} onClick={() => void exportTopologyEvidence()}>只读导出</Button>
              <Typography.Text type="secondary">锚定需 topology.evidence.anchor；校验/导出需 verify/export（默认均 fail-closed）。</Typography.Text>
            </Space>
            {topologyEvidenceProof ? <Card size="small" className="verification-card" title={<Space size={4}><Tag className={topologyEvidenceProof.verified ? "ready-tag" : "risk-high"}>{evidenceVerifiedLabel(topologyEvidenceProof.verified)}</Tag><span title={topologyEvidenceProof.chain_key}>完整一致性证明</span></Space>} style={{ marginBottom: 8 }}>
              <Space wrap>
                <Tag>链长 {topologyEvidenceProof.total_anchors}</Tag>
                <Tag>尾哈希 {evidenceHashShort(topologyEvidenceProof.tail_hash)}</Tag>
                {topologyEvidenceProof.verified ? <Tag className="ready-tag">无失配</Tag> : <Tag className="risk-high" title={topologyEvidenceProof.first_mismatch ? `${topologyEvidenceProof.first_mismatch.source_table}#${topologyEvidenceProof.first_mismatch.source_pk}` : undefined}>首个失配：{evidenceMismatchLabel(topologyEvidenceProof.first_mismatch)}</Tag>}
              </Space>
              <Typography.Text type="secondary">校验时间：{new Date(topologyEvidenceProof.checked_at).toLocaleString("zh-CN")}</Typography.Text>
            </Card> : null}
            {topologyEvidenceExport ? <Card size="small" className="evidence-export-card" title={<Space size={4}><Tag className="ready-tag">只读导出</Tag><span title={topologyEvidenceExport.sha256}>整体 SHA256 {evidenceHashShort(topologyEvidenceExport.sha256)}</span></Space>} style={{ marginBottom: 8 }}>
              <Space wrap>
                <Tag>条目 {topologyEvidenceExport.entries.length}</Tag>
                <Tag>证明尾哈希 {evidenceHashShort(topologyEvidenceExport.proof_ref.tail_hash)}</Tag>
                <Tag>证明链长 {topologyEvidenceExport.proof_ref.total_anchors}</Tag>
                <Typography.Text type="secondary">导出时间：{new Date(topologyEvidenceExport.exported_at).toLocaleString("zh-CN")}；仅 API 响应体，不创建文件。</Typography.Text>
              </Space>
            </Card> : null}
            <Table className="stock-table" size="small" rowKey={(row) => `${row.seq}-${row.source_table}-${row.source_pk}`} dataSource={topologyEvidenceAnchors} columns={topologyEvidenceColumns} pagination={{ pageSize: 8 }} locale={{ emptyText: "尚未锚定证据；点击“锚定”将 M1–M8 账本行追加进哈希链" }} />
          </> },
      ]} />
    </Card>
    <Card className="chart-card plugin-contribution-card" title="已启用 GUI 声明式贡献" extra={<Tag>只读</Tag>}><Typography.Paragraph>已启用插件只能通过控制平面的模式验证声明界面贡献；它们不等同于上方的规划蓝图。</Typography.Paragraph><Table className="stock-table" size="small" rowKey={(row) => `${row.plugin_id}-${row.plugin_version}`} dataSource={contributions} columns={contributionColumns} pagination={false} locale={{ emptyText: "暂无已启用插件贡献" }} /></Card>

    <Modal
      title="发起修复提案"
      open={Boolean(topologyRemediateTarget)}
      okText="确认发起提案"
      okButtonProps={{ danger: true, disabled: busy || !topologyRemediationReason.trim() }}
      confirmLoading={busy}
      onOk={() => void confirmTopologyRemediation()}
      onCancel={() => setTopologyRemediateTarget(null)}
    >
      <Typography.Paragraph type="secondary">将为漂移验证 {topologyRemediateTarget?.verification_id.slice(0, 16)}… 打开追加型修复提案；提案行恒为 pending_approval，终态完全由不可变决策账本派生，未经 topology.chain.remediate 权限将 403 且零写入。</Typography.Paragraph>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Select aria-label="推荐动作" value={topologyRemediateAction} onChange={(value) => setTopologyRemediateAction(value)} options={["re-verify", "re-run-locked-release", "escalate-human"].map((action) => ({ value: action, label: proposalActionLabel(action) }))} placeholder="选择推荐动作" />
        <Input.TextArea rows={3} value={topologyRemediationReason} onChange={(event) => setTopologyRemediationReason(event.target.value)} placeholder="提案理由（必填）" />
      </Space>
    </Modal>
    <Modal
      title="发起受治理重跑"
      open={Boolean(topologyRerunTarget)}
      okText="确认重跑"
      okButtonProps={{ danger: true, disabled: busy || !topologyRerunReason.trim() }}
      confirmLoading={busy}
      onOk={() => void confirmTopologyRerun()}
      onCancel={() => setTopologyRerunTarget(null)}
    >
      <Typography.Paragraph type="secondary">将沿提案 {topologyRerunTarget?.proposal_id.slice(0, 16)}… 关联的调用链重用 M6 受限只读执行运行（独立 run_id），并在 remediation_run_links 追加提案→运行血缘。仅限已批准 + re-run-locked-release 提案；任何其他状态/动作 409 且零运行，未经 execute/execute.isolated/remediate 权限将 403。</Typography.Paragraph>
      <Input.TextArea rows={3} value={topologyRerunReason} onChange={(event) => setTopologyRerunReason(event.target.value)} placeholder="重跑原因（必填）" />
    </Modal>
  </>;
}
