import * as echarts from "echarts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  SPARSE_LAYOUT_MAX_NODES,
  hierarchyTiers,
  sparseLayoutPositions,
  tierColor,
  tierLabel,
} from "../model/graphLayout";

export type GraphVisualizationNode = {
  id: string;
  label: string;
  node_type: string;
  /** 能力做什么（取自节点 properties）。没有就不显示，不编造。 */
  description?: string;
  inputs?: string[];
  outputs?: string[];
  family?: string;
  stage?: string;
  lifecycle?: string;
};

export type GraphVisualizationEdge = {
  source: string;
  target: string;
  relation: string;
  weight: number;
  /** 权重的依据（经验实测 / 契约衔接 / 声明层级）。由后端给出，界面照实显示。 */
  basis?: string;
};

export type GraphVisualization = {
  space_key: string | null;
  nodes: GraphVisualizationNode[];
  edges: GraphVisualizationEdge[];
  partial: boolean;
};

type Props = {
  data: GraphVisualization;
  selectedNodeId?: string;
  onNodeClick: (node: GraphVisualizationNode) => void;
};

const nodeTypeLabels: Record<string, string> = {
  claim: "主张",
  control: "控制",
  document: "文档",
  entity: "实体",
  evidence: "证据",
  finding: "发现",
  cluster: "集群",
  blueprint: "规划蓝图",
  capability: "能力契约",
  // `capability_family` / `domain` 是能力网络里的上级层（实测 capability-l2 的结构是
  // 族 contains 能力），漏了中文名图例就会直接显示原始类型名。
  capability_family: "能力族",
  domain: "业务域",
  plugin: "插件",
  risk: "风险",
};

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 3.0;
const BUTTON_ZOOM_STEP = 0.25;

function zoomClamp(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

function compactNodeLabel(label: string): string {
  return label.length > 20 ? `${label.slice(0, 19)}…` : label;
}

export function graphNodeTypeLabel(nodeType: string): string {
  return nodeTypeLabels[nodeType] ?? nodeType;
}

export default function GraphExplorer({ data, selectedNodeId, onNodeClick }: Props) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [zoomPercent, setZoomPercent] = useState(100);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const zoomRef = useRef(1);
  const degreeByNode = useMemo(() => {
    const degree = new Map<string, number>();
    data.edges.forEach((edge) => {
      degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
    });
    return degree;
  }, [data.edges]);

  const tiers = useMemo(() => hierarchyTiers(data.nodes.map((node) => node.id), data.edges), [data.nodes, data.edges]);

  // 图例按**层级**分组：每层显示"几级 · 该层的主要节点类型"，用户一眼能看出主干与展开。
  const tiersPresent = useMemo(() => {
    const typesByTier = new Map<number, Set<string>>();
    for (const node of data.nodes) {
      const tier = tiers.get(node.id) ?? 0;
      if (!typesByTier.has(tier)) typesByTier.set(tier, new Set());
      typesByTier.get(tier)!.add(node.node_type);
    }
    return [...typesByTier.entries()]
      .sort((left, right) => left[0] - right[0])
      .map(([tier, types]) => {
        const names = [...types].sort().map(graphNodeTypeLabel);
        // 该层只有一种类型时把类型名带上，多于一种就只显示层级（避免图例说谎）。
        return { tier, label: names.length === 1 ? `${tierLabel(tier)} · ${names[0]}` : tierLabel(tier) };
      });
  }, [data.nodes, tiers]);
  const tierIndex = useMemo(
    () => new Map(tiersPresent.map((entry, index) => [entry.tier, index])),
    [tiersPresent],
  );

  // 缩放只由 ECharts 的 roam 控制器执行（滚轮/按钮都汇到它），本组件的 ref 只做**镜像**：
  // 数值一律从图里读回来，不再自己记账。原先用本地 ref 记账 + 手动 dispatch `graphRoam`
  // action 的写法是错的 —— 该 action 的 `update: 'none'`，只改坐标系、不触发节点/连线
  // 重算（ECharts 内部在 action 之外还调了 _updateNodeAndLinkScale / adjustEdge /
  // updateLabelLayout），于是标签在变、画面纹丝不动。
  const currentZoom = useCallback((): number => zoomRef.current, []);

  const applyZoomTo = useCallback((target: number, originX?: number, originY?: number) => {
    const chart = chartRef.current;
    const canvas = canvasRef.current;
    if (!chart || !canvas) return;
    const targetZoom = zoomClamp(target);
    const previous = currentZoom();
    if (Math.abs(targetZoom - previous) < 0.001) return;
    if (originX === undefined || originY === undefined) {
      const rect = canvas.getBoundingClientRect();
      originX = rect.width / 2;
      originY = rect.height / 2;
    }
    chart.dispatchAction({
      type: "graphRoam",
      seriesIndex: 0,
      zoom: targetZoom / previous,
      originX,
      originY,
    });
  }, [currentZoom]);

  const zoomIn = useCallback(() => applyZoomTo(currentZoom() * (1 + BUTTON_ZOOM_STEP)), [applyZoomTo, currentZoom]);
  const zoomOut = useCallback(() => applyZoomTo(currentZoom() * (1 - BUTTON_ZOOM_STEP)), [applyZoomTo, currentZoom]);
  const zoomReset = useCallback(() => applyZoomTo(1), [applyZoomTo]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const existing = echarts.getInstanceByDom(canvas);
    if (existing) existing.dispose();
    const chart = echarts.init(canvas, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const categoryIndex = new Map(tiersPresent.map((entry, index) => [entry.tier, index]));
    const denseTopology = data.nodes.length > 10;
    // 上百个节点时用默认的斥力/边长会把网络撑出画布（底排标签被裁），
    // 收紧一档让整张网落在画布内；仍可拖拽与缩放细看。
    const veryDenseTopology = data.nodes.length > 60;
    const sparseTopology = data.nodes.length <= SPARSE_LAYOUT_MAX_NODES;
    const sparsePositions = sparseTopology
      ? sparseLayoutPositions(data.nodes.length, canvasRef.current?.clientWidth ?? 800, canvasRef.current?.clientHeight ?? 520)
      : [];
    chart.setOption({
      animationDurationUpdate: 280,
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        renderMode: "richText",
        formatter: (params: { dataType?: string; data?: GraphVisualizationNode & GraphVisualizationEdge }) => {
          if (params.dataType === "edge" && params.data) return `关系：${params.data.relation}\n权重：${params.data.weight.toFixed(2)}`;
          if (params.data) return `${params.data.label}\n类型：${graphNodeTypeLabel(params.data.node_type)}`;
          return "";
        },
      },
      legend: {
        bottom: 4,
        type: "scroll",
        textStyle: { color: getComputedStyle(document.documentElement).getPropertyValue("--text-dim").trim() },
        data: tiersPresent.map((entry) => entry.label),
      },
      series: [{
        type: "graph",
        // 稀疏图用显式坐标铺开（见 sparseLayoutPositions 的取舍说明），
        // 节点一多再交给 force：那时它才真的在帮忙（自动把稠密子图分开）。
        layout: sparseTopology ? "none" : "force",
        // roam 只保留拖动平移；缩放统一走下方 wheel/按钮（graphRoam action），避免双击向。
        roam: true,
        // 与 scaleLimit 保持一致（越界由 ECharts 的 View 与本地镜像各夹一次）。
        zoom: zoomRef.current,
        draggable: true,
        focusNodeAdjacency: true,
        scaleLimit: { min: MIN_ZOOM, max: MAX_ZOOM },
        data: data.nodes.map((node, index) => {
          const tier = tiers.get(node.id) ?? 0;
          const degree = degreeByNode.get(node.id) ?? 0;
          return {
            ...node,
            ...(sparseTopology ? sparsePositions[index] : {}),
            name: node.label,
            category: categoryIndex.get(tier) ?? 0,
            // 圆圈整体收小一档：上百个节点铺满画布时，原尺寸（基础 22、封顶 48）
            // 会让相邻节点糊在一起。上层节点（度数高）略大，保持主干可辨。
            symbolSize: Math.min(26, 9 + degree * 2.2),
            itemStyle: {
              color: tierColor(tier),
              borderColor: node.id === selectedNodeId
                ? getComputedStyle(document.documentElement).getPropertyValue("--text").trim()
                : getComputedStyle(document.documentElement).getPropertyValue("--bg-panel").trim(),
              borderWidth: node.id === selectedNodeId ? 2 : 1,
            },
          };
        }),
        links: data.edges.map((edge) => ({
          ...edge,
          source: edge.source,
          target: edge.target,
          lineStyle: { width: Math.max(1, edge.weight * 2), opacity: 0.72, curveness: 0.12 },
        })),
        categories: tiersPresent.map((entry) => ({ name: entry.label, itemStyle: { color: tierColor(entry.tier) } })),
        label: { show: true, color: getComputedStyle(document.documentElement).getPropertyValue("--text").trim(), fontSize: 11, position: "right", formatter: (params: { data?: GraphVisualizationNode }) => compactNodeLabel(params.data?.label ?? "") },
        labelLayout: { hideOverlap: true },
        lineStyle: { color: getComputedStyle(document.documentElement).getPropertyValue("--border-light").trim() },
        emphasis: { focus: "adjacency", lineStyle: { width: 3, opacity: 1 }, label: { show: true } },
        force: veryDenseTopology
          ? { repulsion: 110, edgeLength: [38, 80], gravity: 0.22 }
          : denseTopology
            ? { repulsion: 165, edgeLength: [60, 115], gravity: 0.11 }
            : { repulsion: 240, edgeLength: [75, 155], gravity: 0.06 },
      }],
    }, { notMerge: true });
    chart.on("click", (params) => {
      const node = params.data as GraphVisualizationNode | undefined;
      if (params.dataType === "node" && node) onNodeClick(node);
    });
    // 缩放与平移统一交给 ECharts 的 roam 控制器：滚轮、拖拽、按钮 dispatch 都会汇到它，
    // 本组件只把结果**回读**进标签。原先自己在 wheel 上记账 + 手动 dispatch `graphRoam`
    // action，正是"标签在变、画面纹丝不动"的来源（该 action `update: 'none'`，只改坐标系，
    // 不触发节点/连线重算）。平移事件也走 graphRoam，但 payload 里没有 zoom，这里只处理带 zoom 的。
    const onRoam = (...args: unknown[]) => {
      const payload = args[0] as { zoom?: number } | undefined;
      if (typeof payload?.zoom === "number" && payload.zoom > 0) {
        zoomRef.current = zoomClamp(zoomRef.current * payload.zoom);
      }
      setZoomPercent(Math.round(zoomRef.current * 100));
    };
    chart.on("graphRoam", onRoam);
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.off("graphRoam", onRoam);
      chart.dispose();
      chartRef.current = null;
    };
  }, [data, degreeByNode, tiers, tiersPresent, onNodeClick, selectedNodeId, applyZoomTo]);

  if (!data.nodes.length) return <div className="graph-explorer-empty">这个图空间暂无可展示的有效节点。</div>;
  return (
    <div className="graph-explorer-wrap">
      <div className="graph-explorer-magnify" role="group" aria-label="图谱缩放控制">
        <button type="button" className="graph-magnify-btn" title="放大 (滚轮向上)" aria-label="放大" onClick={zoomIn}>＋</button>
        <button type="button" className="graph-magnify-btn" title="缩小 (滚轮向下)" aria-label="缩小" onClick={zoomOut}>－</button>
        <button type="button" className="graph-magnify-btn" title="还原到 100%" aria-label="还原缩放" onClick={zoomReset}>⟲</button>
        <span className="graph-magnify-pct">{zoomPercent}%</span>
      </div>
      <div ref={canvasRef} className="graph-explorer-canvas" aria-label="知识图谱可视化画布" />
    </div>
  );
}
