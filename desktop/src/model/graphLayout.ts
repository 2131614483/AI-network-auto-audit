/**
 * 图谱可视化的纯计算：层级推导、层级配色、稀疏图坐标。
 *
 * 抽成模块是为了能单测 —— 这几处原先都藏在组件里，出的又都是**静默**问题：
 * 配色退化成"所有类型同色"时画布照常渲染，只是图例变成了装饰；布局在节点很少时会
 * 挤成画布正中一小坨，页面看着像空的 —— 两者都不会报任何错。
 */

/** 节点少于此数时用显式坐标铺开；再多就交回 force 布局。 */
export const SPARSE_LAYOUT_MAX_NODES = 12;

/**
 * 层级色板：**一级 / 二级 / 三级 / 四级**，自上而下由暖到冷。
 *
 * 实测 `capability-l2` 的结构是 `capability_family (24) --contains--> capability (123)`，
 * 另有 80 条 `depends_on` 跨能力依赖。按层级上色比按 node_type 上色更贴问题：
 * 用户看图先想知道"哪几个是主干、哪些是展开出来的"，而不是内部类型名。
 */
export const TIER_PALETTE = ["#ffd98a", "#4a9eff", "#52e08a", "#c79bff"] as const;

/** 层级的中文名（0 起）。超出色板长度时按最后一档处理。 */
export function tierLabel(tier: number): string {
  return ["一级", "二级", "三级", "四级"][tier] ?? `第 ${tier + 1} 级`;
}

/**
 * 按 `contains` 层级给每个节点定"第几级"（根为 0）。
 *
 * 只有 `contains` 参与分层 —— 它就是层级关系（能力族 contains 能力）；跨层调用是
 * `depends_on`，属于依赖而非包含，参与分层会把网拍平。
 *
 * 环与自环都就地终止：这是个纯函数，不可以有超时或死循环的风险。同一节点有多个父时
 * 取输入顺序里第一个，保证结果确定。
 */
export function hierarchyTiers(
  nodeIds: readonly string[],
  edges: ReadonlyArray<{ source: string; target: string; relation: string }>,
): Map<string, number> {
  const parent = new Map<string, string>();
  for (const edge of edges) {
    if (edge.relation !== "contains" || edge.source === edge.target) continue;
    if (!parent.has(edge.target)) parent.set(edge.target, edge.source);
  }
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const resolve = (id: string): number => {
    const cached = depth.get(id);
    if (cached !== undefined) return cached;
    if (visiting.has(id)) return 0; // 环：就地终止，不递归下去
    visiting.add(id);
    const up = parent.get(id);
    const value = up === undefined ? 0 : resolve(up) + 1;
    visiting.delete(id);
    depth.set(id, value);
    return value;
  };
  for (const id of nodeIds) resolve(id);
  return depth;
}

/** 层级 → 颜色。超出色板长度时收敛到最后一档，避免出现"没有颜色"的节点。 */
export function tierColor(tier: number): string {
  return TIER_PALETTE[Math.min(tier, TIER_PALETTE.length - 1)];
}

/**
 * 稀疏图坐标：在画布上按椭圆铺开，起点取 0°（正右）。
 *
 * 为什么不用内置布局：`force` 在 1–2 个节点时会挤成画布正中一小坨；ECharts 的
 * `circular` 把 2 个节点放在圆的上下两极，在超宽画布（实测 1418×570）上会拉成一根
 * 几百像素的竖线。显式算坐标能同时利用宽高，且对 2 个节点给出自然的**左右**排布。
 *
 * 代价：窗口尺寸变化后坐标不重算（`chart.resize()` 只缩放画布），节点会偏离正中 ——
 * 可接受，且仍可拖拽与缩放。
 */
export function sparseLayoutPositions(
  count: number,
  width: number,
  height: number,
): Array<{ x: number; y: number }> {
  const centerX = width / 2;
  const centerY = height / 2;
  if (count <= 1) return [{ x: Math.round(centerX), y: Math.round(centerY) }];
  const radiusX = width * 0.3;
  const radiusY = height * 0.32;
  return Array.from({ length: count }, (_, index) => {
    const angle = (2 * Math.PI * index) / count;
    return {
      x: Math.round(centerX + radiusX * Math.cos(angle)),
      y: Math.round(centerY + radiusY * Math.sin(angle)),
    };
  });
}
