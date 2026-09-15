import * as echarts from "echarts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type GraphVisualizationNode = {
  id: string;
  label: string;
  node_type: string;
};

export type GraphVisualizationEdge = {
  source: string;
  target: string;
  relation: string;
  weight: number;
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
  plugin: "插件",
  risk: "风险",
};

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 3.0;
const WHEEL_ZOOM_STEP = 0.12;
const BUTTON_ZOOM_STEP = 0.25;

function zoomClamp(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

function colorToken(nodeType: string): string {
  const typeToken: Record<string, string> = {
    control: "--graph-control",
    cluster: "--graph-cluster",
    blueprint: "--graph-blueprint",
    capability: "--graph-capability",
    document: "--graph-document",
    evidence: "--graph-evidence",
    finding: "--graph-finding",
    risk: "--graph-risk",
  };
  return getComputedStyle(document.documentElement).getPropertyValue(typeToken[nodeType] ?? "--graph-default").trim();
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

  // 所有缩放只经 applyZoomTo（按钮/滚轮）发生：roam 为 move 时 ECharts 自身不产生缩放，
  // 因此直接用本地 ref 记账即可，无需读取 Model 私有结构。
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
    zoomRef.current = targetZoom;
    setZoomPercent(Math.round(targetZoom * 100));
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
    const categories = Array.from(new Set(data.nodes.map((node) => node.node_type))).sort();
    const categoryIndex = new Map(categories.map((category, index) => [category, index]));
    const denseTopology = data.nodes.length > 10;
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
        data: categories.map((category) => graphNodeTypeLabel(category)),
      },
      series: [{
        type: "graph",
        layout: "force",
        // roam 只保留拖动平移；缩放统一走下方 wheel/按钮（graphRoam action），避免双击向。
        roam: "move",
        draggable: true,
        focusNodeAdjacency: true,
        scaleLimit: { min: MIN_ZOOM, max: MAX_ZOOM },
        data: data.nodes.map((node) => ({
          ...node,
          name: node.label,
          category: categoryIndex.get(node.node_type) ?? 0,
          symbolSize: Math.min(48, 22 + (degreeByNode.get(node.id) ?? 0) * 4),
          itemStyle: {
            color: colorToken(node.node_type),
            borderColor: node.id === selectedNodeId
              ? getComputedStyle(document.documentElement).getPropertyValue("--text").trim()
              : getComputedStyle(document.documentElement).getPropertyValue("--bg-panel").trim(),
            borderWidth: node.id === selectedNodeId ? 2 : 1,
          },
        })),
        links: data.edges.map((edge) => ({
          ...edge,
          source: edge.source,
          target: edge.target,
          lineStyle: { width: Math.max(1, edge.weight * 2), opacity: 0.72, curveness: 0.12 },
        })),
        categories: categories.map((category) => ({ name: graphNodeTypeLabel(category), itemStyle: { color: colorToken(category) } })),
        label: { show: true, color: getComputedStyle(document.documentElement).getPropertyValue("--text").trim(), fontSize: 11, position: "right", formatter: (params: { data?: GraphVisualizationNode }) => compactNodeLabel(params.data?.label ?? "") },
        labelLayout: { hideOverlap: true },
        lineStyle: { color: getComputedStyle(document.documentElement).getPropertyValue("--border-light").trim() },
        emphasis: { focus: "adjacency", lineStyle: { width: 3, opacity: 1 }, label: { show: true } },
        force: denseTopology
          ? { repulsion: 165, edgeLength: [60, 115], gravity: 0.11 }
          : { repulsion: 240, edgeLength: [75, 155], gravity: 0.06 },
      }],
    }, { notMerge: true });
    chart.on("click", (params) => {
      const node = params.data as GraphVisualizationNode | undefined;
      if (params.dataType === "node" && node) onNodeClick(node);
    });
    // 滚轮直接缩放（无需按住 Ctrl）；capture 阶段先于 ECharts 内部 wheel 处理。
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const rect = canvas.getBoundingClientRect();
      const zoom = currentZoom();
      const factor = event.deltaY < 0 ? 1 + WHEEL_ZOOM_STEP : 1 - WHEEL_ZOOM_STEP;
      applyZoomTo(zoom * factor, event.clientX - rect.left, event.clientY - rect.top);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("wheel", onWheel);
      chart.dispose();
      chartRef.current = null;
    };
  }, [data, degreeByNode, onNodeClick, selectedNodeId, applyZoomTo, currentZoom]);

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
