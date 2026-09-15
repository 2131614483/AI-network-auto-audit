export type ClusterAxis = "business_domain" | "capability_family" | "runtime_pool" | "governance_zone";

export type TopologyCluster = {
  id: string;
  name: string;
  axis: ClusterAxis;
  description: string;
};

export type BlueprintMembership = {
  clusterId: string;
  role: "primary" | "supporting" | "governance";
};

export type TopologyBlueprint = {
  id: string;
  name: string;
  summary: string;
  lifecycle: "planned";
  maturity: "idea" | "contracted" | "prototype" | "verified";
  memberships: BlueprintMembership[];
  capabilities: string[];
  inputContracts: string[];
  outputContracts: string[];
  riskClass: "read_only" | "low" | "medium" | "high" | "critical";
  sourceRef: string;
  tags: string[];
};

export type TopologyExecutionMode = "simulated" | "isolated";

export function executionModeLabel(mode: string): string {
  if (mode === "isolated") return "受限演练";
  if (mode === "simulated") return "影子模拟";
  return mode || "未知";
}

export function runStatusLabel(status: string): string {
  if (status === "running") return "运行中";
  if (status === "success") return "成功";
  if (status === "failed") return "失败";
  return status || "未知";
}

export function anyRunRunning(runs: ReadonlyArray<{ status: string }>): boolean {
  return runs.some((run) => run.status === "running");
}

export function verificationStatusLabel(status: string): string {
  if (status === "verified") return "已验证";
  if (status === "drifted") return "漂移";
  return status || "未验证";
}

export type VerifiableRun = { status: string };

export function runCanVerify(run: VerifiableRun): boolean {
  return run.status === "success";
}

export function rollbackActionLabel(action: string): string {
  if (action === "re-verify") return "重新核实（缺参照，需重跑一次对照组）";
  if (action === "re-run-locked-release") return "重跑锁定发布（校验和不一致）";
  if (action === "escalate-human") return "升级人工裁决（混合偏差）";
  return action || "未知建议";
}

export type RemediationProposal = {
  proposal_id: string;
  verification_id: string;
  run_id: string;
  chain_key: string;
  status: "pending_approval" | "approved" | "rejected" | "closed";
  action: "re-verify" | "re-run-locked-release" | "escalate-human";
  affected_slots: string[];
  baseline: { reference_run_id?: string | null; chain_checksum?: string; planner_version?: string };
  remediation_run_id?: string | null;
  reason: string;
  trace_id: string;
  created_at?: string | null;
  decision?: string | null;
  idempotent?: boolean;
};

export function proposalStatusLabel(status: string): string {
  if (status === "pending_approval") return "待审批";
  if (status === "approved") return "已批准";
  if (status === "rejected") return "已驳回";
  if (status === "closed") return "已关闭";
  return status || "未知";
}

export function proposalActionLabel(action: string): string {
  if (action === "re-verify") return "重新核实";
  if (action === "re-run-locked-release") return "重跑锁定发布";
  if (action === "escalate-human") return "升级人工裁决";
  return action || "未知动作";
}

export function proposalCanDecide(proposal: { status: string }): boolean {
  return proposal.status === "pending_approval";
}

export function proposalCanRerun(proposal: { status: string; action: string }): boolean {
  return proposal.status === "approved" && proposal.action === "re-run-locked-release";
}

export type EvidenceScope = "full" | "topology" | "chain" | "execution" | "verification" | "remediation";

export type EvidenceAnchorEntry = {
  anchor_id: string;
  chain_key: string;
  seq: number;
  source_table: string;
  source_pk: string;
  row_hash: string;
  prev_hash: string;
  group_id?: string;
  anchor_scope?: string;
  trace_id?: string;
  created_at?: string | null;
};

export type EvidenceProof = {
  chain_key: string;
  scope: string;
  total_anchors: number;
  tail_hash: string;
  verified: boolean;
  first_mismatch: { seq: number; source_table: string; source_pk: string } | null;
  checked_at: string;
};

export type EvidenceExport = {
  chain_key: string;
  scope: string;
  entries: Array<{ anchor_id: string; seq: number; source_table: string; source_pk: string; row_hash: string; created_at: string | null }>;
  sha256: string;
  proof_ref: { tail_hash: string; total_anchors: number };
  exported_at: string;
};

export type EvidenceStatus = {
  chain_key: string;
  total_anchors: number;
  tail_seq: number;
  tail_hash: string;
  last_anchored_at: string | null;
  checked_at: string;
};

export function evidenceScopeLabel(scope: string): string {
  if (scope === "full") return "全量";
  if (scope === "topology") return "目录";
  if (scope === "chain") return "调用链";
  if (scope === "execution") return "执行";
  if (scope === "verification") return "验证";
  if (scope === "remediation") return "修复";
  return scope || "未知";
}

export function evidenceSourceTableLabel(table: string): string {
  return (table || "").replace(/^topology\./, "") || table || "—";
}

export function evidenceVerifiedLabel(verified: boolean): string {
  return verified ? "完整一致" : "发现失配";
}

export function evidenceMismatchLabel(mismatch: { seq: number; source_table: string; source_pk: string } | null | undefined): string {
  if (!mismatch) return "无失配";
  const pk = mismatch.source_pk && mismatch.source_pk.length > 18 ? `${mismatch.source_pk.slice(0, 18)}…` : (mismatch.source_pk || "—");
  return `序号 ${mismatch.seq} · ${evidenceSourceTableLabel(mismatch.source_table)} · ${pk}`;
}

export function evidenceHashShort(hash: string | undefined | null, length = 12): string {
  if (!hash) return "—";
  return hash.length > length ? `${hash.slice(0, length)}…` : hash;
}

export type PlanningIntentMatchedNode = {
  node_key: string;
  space_key: string;
  label: string;
  node_type: "capability" | "domain";
  score: number;
  match_kind: "direct" | "alias" | "expanded";
  match_source: "trigram" | "bridge" | "depends_on";
  source_node_key?: string;
};

export type PlanningIntent = {
  intent_id: string;
  intent_text: string;
  matched_nodes: PlanningIntentMatchedNode[];
  capability_requirements: string[];
  plan_key: string;
  mode: "plan_only";
  plan_checksum: string;
  reused_plan: boolean;
  node_count: number;
  edge_count: number;
  created_at: string;
  trace_id: string;
  idempotent: boolean;
};

export type PlanningIntentListItem = {
  intent_id: string;
  intent_text: string;
  capability_requirements: string[];
  plan_key: string;
  trace_id: string;
  created_at: string;
};

export const PLANNING_INTENT_MIN = 1;
export const PLANNING_INTENT_MAX = 512;

export function planningIntentCanGenerate(intentText: string): boolean {
  const length = intentText.trim().length;
  return length >= PLANNING_INTENT_MIN && length <= PLANNING_INTENT_MAX;
}

export function planningIntentLengthHint(intentText: string): string {
  const length = intentText.length;
  if (length === 0) return `请输入意图（1–${PLANNING_INTENT_MAX} 字符）`;
  if (length > PLANNING_INTENT_MAX) return `意图超长 ${length}/${PLANNING_INTENT_MAX} 字符，请删减`;
  return `${length}/${PLANNING_INTENT_MAX} 字符`;
}

export function planningIntentMatchKindLabel(kind: string): string {
  if (kind === "direct") return "直接匹配";
  if (kind === "alias") return "别名匹配";
  if (kind === "expanded") return "图遍历扩展";
  return kind || "未知";
}

export function planningIntentMatchSourceLabel(source: string): string {
  if (source === "trigram") return "Trigram 打分";
  if (source === "bridge") return "域桥接扩展";
  if (source === "depends_on") return "依赖扩展";
  return source || "—";
}

export function planningIntentNodeTypeLabel(nodeType: string): string {
  if (nodeType === "capability") return "能力";
  if (nodeType === "domain") return "业务域";
  return nodeType || "—";
}

export function planningIntentEvidenceNote(intent: PlanningIntent): string {
  if (intent.idempotent) return "幂等重放（同幂等键返回既有证据行）";
  if (intent.reused_plan) return "计划已存在（确定性 plan_key 复用既有计划）";
  return "新生成 plan_only 计划并落库证据行";
}

export type TopologyCatalog = {
  release: { id: string; version: string; checksum: string };
  clusters: TopologyCluster[];
  blueprints: TopologyBlueprint[];
};

export type TopologyGraphNode = { id: string; label: string; node_type: "cluster" | "blueprint" | "capability" };
export type TopologyGraphEdge = { source: string; target: string; relation: string; weight: number };
export type TopologyGraph = { space_key: string; nodes: TopologyGraphNode[]; edges: TopologyGraphEdge[]; partial: boolean };

export type TopologyPlanNode = {
  key: string;
  blueprintId: string;
  blueprintName: string;
  capability: string;
  lifecycle: "planned";
  reason: string;
};

export type TopologyPlan = {
  planId: string;
  mode: "plan_only";
  plannerVersion: string;
  catalogRelease: TopologyCatalog["release"];
  candidateClusters: string[];
  nodes: TopologyPlanNode[];
  edges: Array<{ source: string; target: string; relation: "data_flow" | "depends_on" }>;
  budget: { candidates: number; chainLength: number; withinBudget: boolean };
  requiresApproval: boolean;
  unresolvedCapabilities: string[];
  safetyNotice: string;
};

const catalogRelease = {
  id: "plugin-topology-m0",
  version: "0.1.0-m0",
  checksum: "e4cf9ac3d8ddf4ca28988d59805c8ab01659949433d239a123ba694fd5827de6",
} as const;

const clusters: TopologyCluster[] = [
  { id: "domain-knowledge", name: "知识工程", axis: "business_domain", description: "文件理解、结构化和可追溯检索的规划域。" },
  { id: "domain-financial-audit", name: "财务审计", axis: "business_domain", description: "总账、证据、关联方和审计分析的规划域。" },
  { id: "domain-quant", name: "量化研究", axis: "business_domain", description: "仅模拟回测、数据质量和研究解释的规划域。" },
  { id: "domain-aiops", name: "智能运维", axis: "business_domain", description: "告警归因、变更提案和运行健康的规划域。" },
  { id: "family-llm-rag", name: "LLM / RAG", axis: "capability_family", description: "检索、切分和受控问答能力族。" },
  { id: "family-kg-gnn", name: "知识图谱 / GNN", axis: "capability_family", description: "关系发现、子图分析和图谱契约能力族。" },
  { id: "family-cv", name: "文档视觉", axis: "capability_family", description: "本地 PDF、图像与 OCR 接口能力族。" },
  { id: "family-streaming", name: "流式分析", axis: "capability_family", description: "事件、告警和连续审计分析能力族。" },
  { id: "family-thin", name: "轻量编排", axis: "capability_family", description: "计划校验、契约与策略模拟能力族。" },
  { id: "runtime-local-cpu", name: "本地 CPU", axis: "runtime_pool", description: "可在本机 CPU 环境验证的资源规划。" },
  { id: "runtime-local-gpu", name: "本地 GPU", axis: "runtime_pool", description: "需要本地加速资源但尚未部署的规划。" },
  { id: "runtime-long-running", name: "长运行任务", axis: "runtime_pool", description: "后台分析与连续处理的未来资源边界。" },
  { id: "zone-internal", name: "内部资料", axis: "governance_zone", description: "内部数据的规划边界。" },
  { id: "zone-audit-confidential", name: "审计保密", axis: "governance_zone", description: "审计证据和敏感关系分析的规划边界。" },
  { id: "zone-restricted", name: "受限数据", axis: "governance_zone", description: "需要额外数据分级控制的规划边界。" },
  { id: "zone-production-change", name: "生产变更", axis: "governance_zone", description: "运维变更在未来必须审批的规划边界。" },
];

const blueprints: TopologyBlueprint[] = [
  {
    id: "mineru-local-slot", name: "本地文档提取槽位", summary: "为 PDF、图片和扫描件预留的本地 MinerU/OCR 提取接口。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-knowledge", role: "primary" }, { clusterId: "family-cv", role: "supporting" }, { clusterId: "runtime-local-gpu", role: "supporting" }, { clusterId: "zone-internal", role: "governance" }],
    capabilities: ["knowledge.extract.document"], inputContracts: ["artifact-ref/pdf@1"], outputContracts: ["document-content@1"], riskClass: "read_only", sourceRef: "docs/plugin-topology-orchestration.md#2.4", tags: ["pdf", "图片", "MinerU", "OCR", "提取"],
  },
  {
    id: "knowledge-chunk-validation-slot", name: "知识切分与校验槽位", summary: "把已提取内容规范化、切分并保留来源定位。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-knowledge", role: "primary" }, { clusterId: "family-llm-rag", role: "supporting" }, { clusterId: "runtime-local-cpu", role: "supporting" }, { clusterId: "zone-internal", role: "governance" }],
    capabilities: ["knowledge.normalize.chunk"], inputContracts: ["document-content@1"], outputContracts: ["knowledge-chunks@1"], riskClass: "read_only", sourceRef: "docs/plugin-topology-orchestration.md#2.1", tags: ["知识库", "切分", "校验", "来源"],
  },
  {
    id: "knowledge-hybrid-retrieval-slot", name: "混合检索槽位", summary: "为关键词、向量和来源定位检索预留的只读能力接口。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-knowledge", role: "primary" }, { clusterId: "family-llm-rag", role: "supporting" }, { clusterId: "runtime-local-cpu", role: "supporting" }, { clusterId: "zone-internal", role: "governance" }],
    capabilities: ["knowledge.retrieve.hybrid"], inputContracts: ["knowledge-query@1"], outputContracts: ["knowledge-hits@1"], riskClass: "read_only", sourceRef: "docs/plugin-topology-orchestration.md#2.1", tags: ["检索", "向量", "全文", "知识库"],
  },
  {
    id: "related-party-graph-analysis-slot", name: "关联方图谱分析槽位", summary: "为关联方识别和受预算子图推理预留的图谱分析能力。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-financial-audit", role: "primary" }, { clusterId: "family-kg-gnn", role: "supporting" }, { clusterId: "runtime-local-gpu", role: "supporting" }, { clusterId: "zone-audit-confidential", role: "governance" }],
    capabilities: ["graph.related_party.discover"], inputContracts: ["released-graph-ref@1"], outputContracts: ["relationship-candidates@1"], riskClass: "medium", sourceRef: "D:/python_rejiance/gh_2131614483/Audit-Networking/modules/FA-10/README.md", tags: ["关联方", "图谱", "审计", "子图"],
  },
  {
    id: "audit-ledger-quality-slot", name: "总账质量分析槽位", summary: "为总账结构、完整性和异常候选校验预留的审计分析能力。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-financial-audit", role: "primary" }, { clusterId: "family-thin", role: "supporting" }, { clusterId: "runtime-local-cpu", role: "supporting" }, { clusterId: "zone-audit-confidential", role: "governance" }],
    capabilities: ["audit.ledger.validate"], inputContracts: ["ledger-artifact-ref@1"], outputContracts: ["audit-quality-candidates@1"], riskClass: "medium", sourceRef: "docs/reference-audit-networking-review.md#representative-modules", tags: ["总账", "审计", "质量", "异常"],
  },
  {
    id: "evidence-lineage-slot", name: "证据血缘槽位", summary: "为证据、主张、发现和来源链预留的只读追溯能力。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-financial-audit", role: "primary" }, { clusterId: "family-kg-gnn", role: "supporting" }, { clusterId: "runtime-local-cpu", role: "supporting" }, { clusterId: "zone-audit-confidential", role: "governance" }],
    capabilities: ["audit.evidence.lineage"], inputContracts: ["evidence-ref@1"], outputContracts: ["evidence-lineage@1"], riskClass: "read_only", sourceRef: "docs/plugin-topology-orchestration.md#2", tags: ["证据", "血缘", "审计", "追溯"],
  },
  {
    id: "quant-simulated-backtest-slot", name: "量化模拟回测槽位", summary: "为点时数据、策略哈希和非交易性研究回测预留的模拟能力。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-quant", role: "primary" }, { clusterId: "family-thin", role: "supporting" }, { clusterId: "runtime-local-cpu", role: "supporting" }, { clusterId: "zone-restricted", role: "governance" }],
    capabilities: ["quant.backtest.simulate"], inputContracts: ["market-snapshot-ref@1"], outputContracts: ["backtest-report@1"], riskClass: "medium", sourceRef: "docs/plugin-topology-orchestration.md#2.1", tags: ["量化", "回测", "模拟", "点时"],
  },
  {
    id: "aiops-alert-triage-slot", name: "告警归因槽位", summary: "为告警聚合、影响判断和人工复核提案预留的只读分析能力。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-aiops", role: "primary" }, { clusterId: "family-streaming", role: "supporting" }, { clusterId: "runtime-long-running", role: "supporting" }, { clusterId: "zone-production-change", role: "governance" }],
    capabilities: ["aiops.alert.triage"], inputContracts: ["alert-event@1"], outputContracts: ["incident-proposal@1"], riskClass: "high", sourceRef: "docs/reference-audit-networking-review.md#representative-modules", tags: ["AIOps", "告警", "归因", "运维"],
  },
  {
    id: "workflow-plan-validation-slot", name: "工作流计划校验槽位", summary: "校验候选链的契约、预算和无环性；只输出规划结果。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-knowledge", role: "supporting" }, { clusterId: "family-thin", role: "primary" }, { clusterId: "runtime-local-cpu", role: "supporting" }, { clusterId: "zone-internal", role: "governance" }],
    capabilities: ["workflow.plan.validate"], inputContracts: ["plugin-routing-plan@1"], outputContracts: ["plan-validation@1"], riskClass: "read_only", sourceRef: "docs/plugin-topology-orchestration.md#4", tags: ["工作流", "计划", "校验", "DAG"],
  },
  {
    id: "policy-route-simulation-slot", name: "路由策略模拟槽位", summary: "为未来计划的规则命中和审批提示预留的非执行模拟能力。", lifecycle: "planned", maturity: "contracted",
    memberships: [{ clusterId: "domain-aiops", role: "supporting" }, { clusterId: "family-thin", role: "primary" }, { clusterId: "runtime-local-cpu", role: "supporting" }, { clusterId: "zone-restricted", role: "governance" }],
    capabilities: ["policy.route.simulate"], inputContracts: ["plugin-routing-plan@1"], outputContracts: ["policy-simulation@1"], riskClass: "read_only", sourceRef: "docs/plugin-topology-orchestration.md#4", tags: ["策略", "审批", "模拟", "风控"],
  },
];

export const pluginTopologyCatalog: TopologyCatalog = { release: catalogRelease, clusters, blueprints };

export const topologyAxes: Array<{ value: ClusterAxis; label: string }> = [
  { value: "business_domain", label: "业务域" },
  { value: "capability_family", label: "能力族" },
  { value: "runtime_pool", label: "资源池" },
  { value: "governance_zone", label: "治理区" },
];

export function topologyGraphForAxis(axis: ClusterAxis): TopologyGraph {
  const axisClusters = clusters.filter((cluster) => cluster.axis === axis);
  const clusterIds = new Set(axisClusters.map((cluster) => cluster.id));
  const linkedBlueprints = blueprints.filter((blueprint) => blueprint.memberships.some((membership) => clusterIds.has(membership.clusterId)));
  const nodes: TopologyGraphNode[] = [
    ...axisClusters.map((cluster) => ({ id: `cluster:${cluster.id}`, label: cluster.name, node_type: "cluster" as const })),
    ...linkedBlueprints.map((blueprint) => ({ id: `blueprint:${blueprint.id}`, label: blueprint.name, node_type: "blueprint" as const })),
  ];
  const edges: TopologyGraphEdge[] = [
    ...linkedBlueprints.flatMap((blueprint) => blueprint.memberships
    .filter((membership) => clusterIds.has(membership.clusterId))
    .map((membership) => ({ source: `cluster:${membership.clusterId}`, target: `blueprint:${blueprint.id}`, relation: membership.role, weight: membership.role === "primary" ? 1 : 0.6 }))),
  ];
  return { space_key: `plugin-topology/${axis}`, nodes, edges, partial: false };
}

type IntentRecipe = { includes: string[]; blueprintIds: string[] };

const intentRecipes: IntentRecipe[] = [
  { includes: ["pdf"], blueprintIds: ["mineru-local-slot", "knowledge-chunk-validation-slot", "knowledge-hybrid-retrieval-slot"] },
  { includes: ["图片"], blueprintIds: ["mineru-local-slot", "knowledge-chunk-validation-slot"] },
  { includes: ["知识库"], blueprintIds: ["knowledge-chunk-validation-slot", "knowledge-hybrid-retrieval-slot"] },
  { includes: ["关联方"], blueprintIds: ["related-party-graph-analysis-slot", "evidence-lineage-slot"] },
  { includes: ["总账"], blueprintIds: ["audit-ledger-quality-slot", "evidence-lineage-slot"] },
  { includes: ["审计"], blueprintIds: ["audit-ledger-quality-slot", "evidence-lineage-slot"] },
  { includes: ["回测"], blueprintIds: ["quant-simulated-backtest-slot"] },
  { includes: ["量化"], blueprintIds: ["quant-simulated-backtest-slot"] },
  { includes: ["告警"], blueprintIds: ["aiops-alert-triage-slot", "policy-route-simulation-slot"] },
  { includes: ["aiops"], blueprintIds: ["aiops-alert-triage-slot", "policy-route-simulation-slot"] },
  { includes: ["策略"], blueprintIds: ["policy-route-simulation-slot", "workflow-plan-validation-slot"] },
];

function stablePlanSuffix(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) hash = Math.imul(hash ^ value.charCodeAt(index), 16777619);
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function blueprintById(id: string): TopologyBlueprint {
  const blueprint = blueprints.find((candidate) => candidate.id === id);
  if (!blueprint) throw new Error(`规划蓝图目录不完整：${id}`);
  return blueprint;
}

function chooseRecipe(normalizedIntent: string): IntentRecipe | undefined {
  return intentRecipes.find((recipe) => recipe.includes.some((word) => normalizedIntent.includes(word)));
}

export function createTopologyPlan(intent: string): TopologyPlan {
  const normalizedIntent = intent.trim().toLocaleLowerCase("zh-CN");
  const planId = `plan-${stablePlanSuffix(normalizedIntent || "empty")}`;
  const blockedLiveTrading = ["真实券商", "下单", "交易执行", "实盘", "live trading"].some((word) => normalizedIntent.includes(word.toLocaleLowerCase("zh-CN")));
  if (blockedLiveTrading) {
    return {
      planId, mode: "plan_only", plannerVersion: "desktop-m0.1", catalogRelease, candidateClusters: [], nodes: [], edges: [],
      budget: { candidates: 0, chainLength: 0, withinBudget: true }, requiresApproval: true, unresolvedCapabilities: ["trading.execute.live"],
      safetyNotice: "真实交易不属于规划目录；未选择替代蓝图，也没有生成执行请求。",
    };
  }
  const recipe = chooseRecipe(normalizedIntent);
  if (!recipe) {
    return {
      planId, mode: "plan_only", plannerVersion: "desktop-m0.1", catalogRelease, candidateClusters: [], nodes: [], edges: [],
      budget: { candidates: 0, chainLength: 0, withinBudget: true }, requiresApproval: false, unresolvedCapabilities: ["topology.intent.unresolved"],
      safetyNotice: "目录中没有与该意图契约匹配的规划蓝图；系统没有进行名称近似替代。",
    };
  }
  const selectedBlueprints = recipe.blueprintIds.map(blueprintById);
  const nodes = selectedBlueprints.map((blueprint, index) => ({
    key: `node-${index + 1}`, blueprintId: blueprint.id, blueprintName: blueprint.name, capability: blueprint.capabilities[0], lifecycle: blueprint.lifecycle,
    reason: `匹配规划标签：${blueprint.tags.slice(0, 3).join(" / ")}`,
  }));
  const candidateClusters = [...new Set(selectedBlueprints.flatMap((blueprint) => blueprint.memberships
    .filter((membership) => membership.role === "primary" || membership.role === "governance")
    .map((membership) => membership.clusterId)))];
  return {
    planId, mode: "plan_only", plannerVersion: "desktop-m0.1", catalogRelease, candidateClusters, nodes,
    edges: nodes.slice(1).map((node, index) => ({ source: nodes[index].key, target: node.key, relation: "data_flow" })),
    budget: { candidates: nodes.length, chainLength: nodes.length, withinBudget: nodes.length <= 8 },
    requiresApproval: selectedBlueprints.some((blueprint) => blueprint.riskClass === "high" || blueprint.riskClass === "critical"),
    unresolvedCapabilities: [], safetyNotice: "这是冻结目录上的候选计划，不会安装、启动、调用或授权任何真实插件。",
  };
}
