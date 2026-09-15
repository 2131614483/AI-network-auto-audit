import { useEffect, useMemo, useRef, useState } from "react";
import type { CanvasEdge, CanvasNode, CanvasProjection } from "../model/runCanvas";
import { fileUriToPath } from "../model/runCanvas";
import type { DraftEdit } from "../model/canvasDraft";
import {
  buildFrame,
  buildTimeline,
  flowLayout,
  inputBindingEntries,
  isSeedBinding,
  runRank,
  type FlowTimeline,
  type LaidEdge,
  type LaidNode,
  type NodePhase,
} from "../model/flowPlayer";

type Props = {
  projection: CanvasProjection | null;
  busy: boolean;
  mode?: "run" | "draft";
  onDraftEdit?: (edit: DraftEdit) => void;
};

type ViewTransform = { scale: number; tx: number; ty: number };
type NodeDrag = { nodeInstanceId: string; startX: number; startY: number; originX: number; originY: number };

const STATUS_LABEL: Record<string, string> = {
  pending: "等待", running: "运行中", succeeded: "成功",
  failed: "失败", retry_wait: "退避", cancelled: "已取消",
};
const SPEEDS = [0.5, 1, 2, 4];

function realStatusColor(status: string): string {
  if (status === "succeeded") return "#52e08a";
  if (status === "failed") return "#ea6668";
  if (status === "running" || status === "pending" || status === "retry_wait") return "#faad14";
  return "#9eacea";
}

// Cubic-bezier point (matches the wire path) so the data packet rides the wire.
function cubic(p: number, from: { x: number; y: number }, to: { x: number; y: number }) {
  const dx = Math.max(40, (to.x - from.x) / 2);
  const p1 = { x: from.x + dx, y: from.y };
  const p2 = { x: to.x - dx, y: to.y };
  const u = 1 - p;
  return {
    x: u ** 3 * from.x + 3 * u ** 2 * p * p1.x + 3 * u * p ** 2 * p2.x + p ** 3 * to.x,
    y: u ** 3 * from.y + 3 * u ** 2 * p * p1.y + 3 * u * p ** 2 * p2.y + p ** 3 * to.y,
  };
}
function wirePath(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const dx = Math.max(40, (to.x - from.x) / 2);
  return `M ${from.x} ${from.y} C ${from.x + dx} ${from.y}, ${to.x - dx} ${to.y}, ${to.x} ${to.y}`;
}

export default function RunCanvas({ projection, busy, mode = "run", onDraftEdit }: Props): React.JSX.Element {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [transform, setTransform] = useState<ViewTransform>({ scale: 1, tx: 40, ty: 24 });
  const [selectedNode, setSelectedNode] = useState<CanvasNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<CanvasEdge | null>(null);
  const [maximized, setMaximized] = useState(false);
  const [draftPoints, setDraftPoints] = useState<Map<string, { x: number; y: number }>>(new Map());
  const dragRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const nodeDragRef = useRef<NodeDrag | null>(null);

  // -- flow player state -----------------------------------------------------
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [speedIdx, setSpeedIdx] = useState(1);
  const speedRef = useRef(SPEEDS[1]);
  speedRef.current = SPEEDS[speedIdx];
  const rafRef = useRef<number | null>(null);
  const lastRef = useRef<number>(0);

  const isDraft = mode === "draft";

  const rank = useMemo(
    () => (isDraft ? {} : runRank(projection?.nodes ?? [])),
    [isDraft, projection],
  );
  const timeline: FlowTimeline = useMemo(
    () => (projection ? buildTimeline(projection, rank) : {
      durationMs: 0, nodeOrder: [], nodes: {}, edges: [], incoming: {}, outgoing: {},
    }),
    [projection, rank],
  );
  const laid = useMemo(
    () => (projection ? flowLayout(projection, rank) : { width: 640, height: 360, nodes: {}, edges: [] }),
    [projection, rank],
  );
  const frame = useMemo(() => buildFrame(timeline, t), [timeline, t]);

  // Reset + auto-play once whenever a new draft/run is shown.
  const projectionKey = projection?.run_id ?? null;
  useEffect(() => {
    setT(0);
    setSelectedNode(null);
    setSelectedEdge(null);
    setDraftPoints(new Map());
    if (projection && (timeline.durationMs > 0)) setPlaying(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectionKey, isDraft]);

  useEffect(() => {
    if (!playing) return undefined;
    lastRef.current = performance.now();
    const tick = (now: number) => {
      const dt = now - lastRef.current;
      lastRef.current = now;
      setT((prev) => {
        const next = prev + dt * speedRef.current;
        if (next >= timeline.durationMs) {
          setPlaying(false);
          return timeline.durationMs;
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [playing, timeline.durationMs]);
  useEffect(() => () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); }, []);

  const nodeById = useMemo(() => {
    const map = new Map<string, CanvasNode>();
    for (const node of projection?.nodes ?? []) map.set(node.node_instance_id, node);
    return map;
  }, [projection]);
  const edgeByKey = useMemo(() => {
    const map = new Map<string, CanvasEdge>();
    for (const edge of projection?.edges ?? []) map.set(edge.key.join("|"), edge);
    return map;
  }, [projection]);

  const nodePoint = (ln: LaidNode) => draftPoints.get(ln.id) ?? { x: ln.x, y: ln.y };

  // -- pan / zoom ------------------------------------------------------------
  const handleWheel = (event: React.WheelEvent) => {
    if (!wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    setTransform((current) => {
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      const scale = Math.min(3, Math.max(0.2, current.scale * factor));
      const tx = px - ((px - current.tx) * scale) / current.scale;
      const ty = py - ((py - current.ty) * scale) / current.scale;
      return { scale, tx, ty };
    });
  };
  const startPan = (event: React.MouseEvent) => {
    if (isDraft && (event.target as Element).closest(".runcanvas-node")) return;
    dragRef.current = { x: event.clientX, y: event.clientY, tx: transform.tx, ty: transform.ty };
    event.preventDefault();
  };
  const movePan = (event: React.MouseEvent) => {
    if (!dragRef.current) return;
    setTransform((c) => ({ ...c, tx: dragRef.current!.tx + (event.clientX - dragRef.current!.x), ty: dragRef.current!.ty + (event.clientY - dragRef.current!.y) }));
  };
  const endPan = () => { dragRef.current = null; };

  const startNodeDrag = (event: React.PointerEvent, nodeId: string) => {
    if (!isDraft) return;
    const origin = nodePoint(laid.nodes[nodeId]);
    nodeDragRef.current = { nodeInstanceId: nodeId, startX: event.clientX, startY: event.clientY, originX: origin.x, originY: origin.y };
    (event.target as Element).setPointerCapture?.(event.pointerId);
    event.stopPropagation();
  };
  const moveNodeDrag = (event: React.PointerEvent) => {
    const drag = nodeDragRef.current;
    if (!drag) return;
    setDraftPoints((current) => {
      const next = new Map(current);
      next.set(drag.nodeInstanceId, {
        x: Math.round(drag.originX + (event.clientX - drag.startX) / transform.scale),
        y: Math.round(drag.originY + (event.clientY - drag.startY) / transform.scale),
      });
      return next;
    });
    event.stopPropagation();
  };
  const endNodeDrag = () => { nodeDragRef.current = null; };

  // -- player controls -------------------------------------------------------
  const stepTo = (dir: 1 | -1) => {
    setPlaying(false);
    setT((cur) => {
      const enters = timeline.nodeOrder.map((id) => timeline.nodes[id].enterAt);
      if (dir === 1) {
        const next = enters.find((e) => e > cur + 1);
        return next ?? timeline.durationMs;
      }
      const before = enters.filter((e) => e < cur - 1);
      return before.length ? before[before.length - 1] : 0;
    });
  };

  if (busy && !projection) return <div className="runcanvas-empty">正在加载画布定义…</div>;
  if (!projection) {
    return <div className="runcanvas-empty">{isDraft ? "尚无草稿；在右侧 AI 助手生成图谱流程并应用到画布。" : "请先在左侧运行历史中选择一个 run。"}</div>;
  }

  const phaseOf = (id: string): NodePhase => frame.nodePhase[id] ?? "idle";

  const nodeStroke = (id: string, status: string): string => {
    const phase = phaseOf(id);
    if (phase === "active") return "#ffd98a";
    if (phase === "idle") return "#33404f";
    return isDraft ? "#7f9cf5" : realStatusColor(status);
  };

  const edgeTone = (edge: LaidEdge): "idle" | "flow" | "live-ok" | "live-fail" => {
    if (frame.edgePacket[edge.key] !== undefined) return "flow";
    if (frame.edgeReached[edge.key]) {
      const target = nodeById.get(edge.target);
      return target?.status === "failed" ? "live-fail" : "live-ok";
    }
    return "idle";
  };

  return (
    <div className={`runcanvas${maximized ? " maximized" : ""}${isDraft ? " draft-mode" : ""}`}>
      <div className="runcanvas-toolbar">
        <span className="runcanvas-title">
          {isDraft
            ? `草稿 ${projection.plan_key ?? ""} · ${projection.nodes.length} 节点 · ${projection.edges.length} 边`
            : `run ${projection.run_id.slice(0, 8)} · ${projection.plan_key ?? "-"} · ${projection.status}`}
        </span>
        {isDraft ? <span className="runcanvas-mode-tag">plan_only · AI 正在组网</span> : null}
        <span className="flow-step">已激活 {frame.doneCount}/{frame.total}</span>
        <div className="flow-player" role="group" aria-label="数据流播放">
          <button type="button" title="回到起点" onClick={() => { setPlaying(false); setT(0); }}>⏮</button>
          <button type="button" title="上一步" onClick={() => stepTo(-1)}>◀</button>
          <button type="button" title={playing ? "暂停" : "播放数据流"} onClick={() => { if (t >= timeline.durationMs) setT(0); setPlaying((v) => !v); }}>
            {playing ? "⏸" : "▶"}
          </button>
          <button type="button" title="下一步" onClick={() => stepTo(1)}>▶|</button>
          {!isDraft ? <button type="button" className="flow-speed" onClick={() => setSpeedIdx((i) => (i + 1) % SPEEDS.length)}>{SPEEDS[speedIdx]}×</button>
            : <button type="button" className="flow-speed" onClick={() => { setT(0); setPlaying(true); }}>重播构建</button>}
          <input
            className="flow-scrub"
            type="range"
            min={0}
            max={timeline.durationMs}
            step={16}
            value={t}
            onChange={(e) => { setPlaying(false); setT(Number(e.target.value)); }}
            aria-label="数据流进度"
          />
        </div>
        <span className="runcanvas-zoom">缩放 {Math.round(transform.scale * 100)}%</span>
        <button type="button" onClick={() => setTransform({ scale: 1, tx: 40, ty: 24 })}>复位</button>
        <button type="button" onClick={() => setMaximized((v) => !v)}>{maximized ? "还原" : "最大化"}</button>
      </div>

      <div
        className="runcanvas-wrap"
        ref={wrapRef}
        onWheel={handleWheel}
        onMouseDown={startPan}
        onMouseMove={movePan}
        onMouseUp={endPan}
        onMouseLeave={endPan}
      >
        <svg width={laid.width} height={laid.height} style={{ transform: `translate(${transform.tx}px, ${transform.ty}px) scale(${transform.scale})`, transformOrigin: "0 0" }}>
          {/* wires */}
          {laid.edges.map((edge) => {
            const data = edgeByKey.get(edge.key);
            const phase = edgeTone(edge);
            if (phase === "idle") return null; // build animation: wire appears when data starts flowing
            const selected = selectedEdge?.key.join("|") === edge.key;
            const stroke = phase === "flow"
              ? "#ffd98a"
              : phase === "live-fail"
                ? "#ea6668"
                : isDraft ? "#7f9cf5" : "#52e08a";
            const packet = frame.edgePacket[edge.key];
            const dot = packet !== undefined ? cubic(packet, edge.from, edge.to) : null;
            return (
              <g key={edge.key} className={phase === "live-ok" ? "wire-live" : undefined}>
                <path d={wirePath(edge.from, edge.to)} fill="none" stroke={stroke}
                  strokeWidth={selected || phase === "flow" ? 2.4 : 1.6}
                  strokeDasharray={phase.startsWith("live") ? "7 5" : undefined}
                  className={phase.startsWith("live") ? "wire-dash" : undefined} />
                <path d={wirePath(edge.from, edge.to)} fill="none" stroke="transparent" strokeWidth={14}
                  style={{ cursor: "pointer" }}
                  onClick={(e) => { e.stopPropagation(); setSelectedEdge(data ?? null); setSelectedNode(null); }} />
                {dot ? <circle cx={dot.x} cy={dot.y} r={4.5} fill="#ffe3a8" stroke="#ffd98a" strokeWidth={1.4} className="flow-packet" /> : null}
                {phase.startsWith("live") ? (
                  <text x={(edge.from.x + edge.to.x) / 2} y={(edge.from.y + edge.to.y) / 2 - 6} textAnchor="middle" fontSize="9.5"
                    fill="#9fb4cc" style={{ paintOrder: "stroke", stroke: "#0b0e14", strokeWidth: 3, pointerEvents: "none" }}>
                    {edge.sourcePort} → {edge.targetPort}
                  </text>
                ) : null}
              </g>
            );
          })}

          {/* nodes */}
          {timeline.nodeOrder.map((id) => {
            const ln = laid.nodes[id];
            if (!ln) return null;
            const data = nodeById.get(id);
            const phase = phaseOf(id);
            const idle = phase === "idle"; // idle nodes stay as a dimmed skeleton so the full wiring is always visible
            const selected = selectedNode?.node_instance_id === id;
            const point = nodePoint(ln);
            const stroke = nodeStroke(id, data?.status ?? "pending");
            const outCount = Object.keys(data?.output_refs ?? {}).length;
            return (
              <g key={id} transform={`translate(${point.x}, ${point.y})`}
                className={`runcanvas-node node-${phase}${phase === "active" ? " node-pulse" : ""}`}
                opacity={idle ? 0.34 : 1}
                onClick={(e) => { e.stopPropagation(); setSelectedNode(data ?? null); setSelectedEdge(null); }}
                onPointerDown={(e) => startNodeDrag(e, id)}
                onPointerMove={moveNodeDrag}
                onPointerUp={endNodeDrag}
                onPointerCancel={endNodeDrag}>
                <rect width={ln.w} height={ln.h} rx={10}
                  fill={selected ? "#16202c" : "#0f1720"} stroke={idle ? "#232c39" : stroke} strokeWidth={selected ? 2.2 : 1.3} />
                <text x={12} y={20} fontSize={12.5} fontWeight={600} fill="#e5e7eb">{id}</text>
                <text x={12} y={36} fontSize="9.5" fill="#9ca3af">{(data?.capability ?? "-").slice(0, 34)}</text>
                {ln.inPorts.map((p) => (
                  <g key={`in-${p.port}`}>
                    <circle cx={6} cy={46 + p.row * 19 + 9.5} r={4} fill="#0b0e14" stroke="#6b7a90" strokeWidth={1.2} />
                    <text x={15} y={46 + p.row * 19 + 13} fontSize="9" fill={p.port === "数据入口" ? "#7fe0c0" : "#aab6c6"}>{p.port}</text>
                  </g>
                ))}
                {ln.outPorts.map((p) => (
                  <g key={`out-${p.port}`}>
                    <circle cx={ln.w - 6} cy={46 + p.row * 19 + 9.5} r={4} fill="#0b0e14" stroke="#6b7a90" strokeWidth={1.2} />
                    <text x={ln.w - 15} y={46 + p.row * 19 + 13} fontSize="9" fill={p.port === "最终输出" ? "#ffd98a" : "#aab6c6"} textAnchor="end">{p.port}</text>
                  </g>
                ))}
                <text x={12} y={ln.h - 6} fontSize="9" fill={idle ? "#5a6675" : phase === "active" ? "#ffd98a" : isDraft ? "#7f9cf5" : realStatusColor(data?.status ?? "pending")}>
                  {idle ? "待处理" : phase === "active" ? "● 处理中…" : isDraft ? "已放置" : `${STATUS_LABEL[data?.status ?? "pending"] ?? data?.status} · ${outCount} 产物`}
                </text>
                {data?.error_kind ? <text x={ln.w - 12} y={ln.h - 6} fontSize="9" textAnchor="end" fill="#ea6668">{data.error_kind.slice(0, 18)}</text> : null}
              </g>
            );
          })}
        </svg>
      </div>

      {(selectedNode || selectedEdge) ? (
        <div className="runcanvas-inspector">
          {selectedNode ? (
            <>
              <div className="runcanvas-inspector-title">节点 {selectedNode.node_instance_id}</div>
              <dl className="runcanvas-inspector-dl">
                <dt>capability</dt><dd>{selectedNode.capability}</dd>
                <dt>plugin</dt><dd>{selectedNode.plugin_id}</dd>
                {isDraft ? (
                  <>
                    <dt>mode</dt><dd>plan_only 草稿</dd>
                    <dt>操作</dt><dd>
                      <button type="button" className="runcanvas-action" onClick={() => { onDraftEdit?.({ kind: "remove_node", node_instance_id: selectedNode.node_instance_id }); setSelectedNode(null); }}>删除节点</button>
                    </dd>
                  </>
                ) : (
                  <>
                    <dt>status</dt><dd>{STATUS_LABEL[selectedNode.status] ?? selectedNode.status}</dd>
                    <dt>attempt</dt><dd>#{selectedNode.attempt_seq} ({selectedNode.attempt_id.slice(0, 8)})</dd>
                    {selectedNode.created_at ? <><dt>开始</dt><dd>{new Date(selectedNode.created_at).toLocaleTimeString("zh-CN")}</dd></> : null}
                    {selectedNode.finished_at ? <><dt>完成</dt><dd>{new Date(selectedNode.finished_at).toLocaleTimeString("zh-CN")}</dd></> : null}
                  </>
                )}
                {selectedNode.error_message ? <><dt>error</dt><dd>{selectedNode.error_message}</dd></> : null}
              </dl>

              {!isDraft ? (
                <>
                  <div className="runcanvas-inspector-title">数据从哪传入（{inputBindingEntries(selectedNode).length}）</div>
                  {inputBindingEntries(selectedNode).length === 0 ? <div className="runcanvas-muted">无上游：外部授权的数据入口</div> : null}
                  {inputBindingEntries(selectedNode).map((b, i) => (
                    <div key={`${b.port}-${i}`} className="runcanvas-binding">
                      <div className="runcanvas-binding-head">
                        <span className="runcanvas-porttag in">{b.port}</span>
                        {isSeedBinding(b) ? <span className="runcanvas-seed">外部注入</span> : <span className="runcanvas-src">← {b.source_instance}:{b.source_port}</span>}
                      </div>
                      {b.adapter ? <div className="runcanvas-muted">适配 {b.adapter}</div> : null}
                      {b.sha256 ? <div className="runcanvas-mono" title={b.uri ?? ""}>sha {b.sha256.slice(0, 20)}{b.uri ? ` · ${fileUriToPath(b.uri)}` : ""}</div> : null}
                    </div>
                  ))}

                  <div className="runcanvas-inspector-title">产出 / 中间结果（{Object.keys(selectedNode.output_refs).length}）</div>
                  {Object.entries(selectedNode.output_refs).length === 0 ? <div className="runcanvas-muted">尚未产出</div> : null}
                  {Object.entries(selectedNode.output_refs).map(([port, ref]) => (
                    <div key={port} className="runcanvas-artifact">
                      <div className="runcanvas-binding-head">
                        <span className="runcanvas-porttag out">{port}</span>
                        <span className="runcanvas-muted">{ref.size_bytes} B</span>
                      </div>
                      <div>sha {ref.sha256.slice(0, 16)}</div>
                      <div className="runcanvas-artifact-path" title={ref.uri}>{fileUriToPath(ref.uri)}</div>
                    </div>
                  ))}
                </>
              ) : null}
            </>
          ) : (
            <>
              <div className="runcanvas-inspector-title">数据连线（边）</div>
              <dl className="runcanvas-inspector-dl">
                <dt>从</dt><dd>{selectedEdge!.source_instance}:{selectedEdge!.source_port}</dd>
                <dt>到</dt><dd>{selectedEdge!.target_instance}:{selectedEdge!.target_port}</dd>
                <dt>sha256</dt><dd className="runcanvas-mono">{selectedEdge!.sha256?.slice(0, 24) ?? "-"}</dd>
                <dt>adapter</dt><dd>{selectedEdge!.adapter ?? "直连"}</dd>
              </dl>
              {isDraft ? (
                <button type="button" className="runcanvas-action" onClick={() => {
                  onDraftEdit?.({
                    kind: "remove_edge",
                    source_instance: selectedEdge!.source_instance,
                    source_port: selectedEdge!.source_port,
                    target_instance: selectedEdge!.target_instance,
                    target_port: selectedEdge!.target_port,
                  });
                  setSelectedEdge(null);
                }}>删除连线</button>
              ) : selectedEdge!.uri ? (
                <div className="runcanvas-artifact">
                  <div>线上承载的产物</div>
                  <div className="runcanvas-artifact-path" title={selectedEdge!.uri}>{fileUriToPath(selectedEdge!.uri)}</div>
                </div>
              ) : (
                <div className="runcanvas-muted">该边暂无落盘产物（端口直传 / 尚未运行）</div>
              )}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
