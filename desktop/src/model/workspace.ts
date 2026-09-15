export type Connection = "connecting" | "online" | "error";

export type WorkspaceFacts = {
  apiVersion: string;
  shellVersion: string;
  slotCount: number;
  contributionCount: number;
  policyGated: boolean;
};

export const initialWorkspaceState = { connection: "connecting" as Connection };

export function controlPlaneState(available: boolean): { label: string; tone: "success" | "error" } {
  return available
    ? { label: "控制平面已连接", tone: "success" }
    : { label: "控制平面不可用", tone: "error" };
}

export function workspaceMetricRows(facts: WorkspaceFacts): Array<{ label: string; value: string; note: string }> {
  return [
    { label: "控制平面", value: facts.apiVersion, note: `Shell ${facts.shellVersion}` },
    { label: "安全 GUI 插槽", value: String(facts.slotCount), note: "声明式扩展点" },
    { label: "已启用插件贡献", value: String(facts.contributionCount), note: "受模式验证保护" },
    { label: "策略网关", value: facts.policyGated ? "已启用" : "不可用", note: "工具调用强制经过策略" },
  ];
}
