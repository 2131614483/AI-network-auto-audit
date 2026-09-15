export interface ContributionSummary {
  plugin_id: string;
  plugin_version: string;
  navigation_count: number;
  view_count: number;
  action_count: number;
}

export function dashboardMetrics(
  slots: readonly string[],
  contributions: readonly ContributionSummary[],
  policyGated: boolean,
): Array<{ label: string; value: string }> {
  return [
    { label: "安全 GUI 插槽", value: String(slots.length) },
    { label: "已启用插件贡献", value: String(contributions.length) },
    { label: "策略网关", value: policyGated ? "已启用" : "不可用" },
  ];
}

export function approvalStatusClass(status: string): string {
  const supported = new Set(["pending", "approved", "rejected", "expired"]);
  return supported.has(status) ? status : "unknown";
}
