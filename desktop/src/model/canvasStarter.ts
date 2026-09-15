// First-run canvas planning inputs.  These are logical, policy-authorized
// entry ports for the planner, not file paths or execution-time artifacts.

export type CanvasStarterSource = {
  key: string;
  nodeInstanceId: string;
  portId: string;
  label: string;
  detail: string;
};

export const CANVAS_STARTER_SOURCES: readonly CanvasStarterSource[] = [
  {
    key: "ledger-a:ledger",
    nodeInstanceId: "ledger-a",
    portId: "ledger",
    label: "日记账数据源 A",
    detail: "审计规划入口；执行前仍需绑定已登记的账簿工件。",
  },
  {
    key: "ledger-b:ledger",
    nodeInstanceId: "ledger-b",
    portId: "ledger",
    label: "日记账数据源 B",
    detail: "用于双账簿对比或并行审计；执行前仍需绑定真实工件。",
  },
];

export const CANVAS_TASK_EXAMPLES = [
  {
    key: "audit-screening",
    label: "审计异常初筛",
    message: "请使用我选择的日记账数据完成审计异常初筛：校验日期、金额、重复记录和借贷平衡；识别异常分录并按风险排序；为高风险异常生成调查计划和待补证据清单；生成发现草稿，但不要确认 Finding、不要导出报告、不要修改原始数据。请展示数据流、插件输入输出和人工确认节点。",
  },
  {
    key: "dual-ledger",
    label: "双账簿比对",
    message: "请分别校验我选择的两份日记账数据，比较异常候选的规则、金额区间和重复记录；为每个数据源生成独立调查计划，并在画布中保留两条可追溯分支。只生成 plan_only 草稿，不执行、不确认发现。",
  },
] as const;

export function sourcePairsFor(keys: readonly string[]): Array<[string, string]> {
  const selected = new Set(keys);
  return CANVAS_STARTER_SOURCES
    .filter((source) => selected.has(source.key))
    .map((source) => [source.nodeInstanceId, source.portId]);
}

export function selectedSourcesFor(keys: readonly string[]): CanvasStarterSource[] {
  const selected = new Set(keys);
  return CANVAS_STARTER_SOURCES.filter((source) => selected.has(source.key));
}
