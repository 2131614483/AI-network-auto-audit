import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Line2 } from "three/examples/jsm/lines/Line2.js";
import { LineGeometry } from "three/examples/jsm/lines/LineGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import type { GraphVisualization, GraphVisualizationNode } from "./GraphExplorer";

type Props = {
  graph: GraphVisualization;
  selectedNodeId?: string | null;
  onNodeClick?: (node: GraphVisualizationNode) => void;
};

// ===== EVE 星图视觉令牌 =====
const SAFETY_COLORS: Record<string, string> = {
  control: "#22c55e", // 安全 → 绿
  risk: "#ff3b4e", // 风险 → 红
  cluster: "#7fd4ff",
  blueprint: "#4f8fff",
  capability: "#6f9bff",
  document: "#c68bff",
  entity: "#5eead4",
  evidence: "#9be8ff",
  finding: "#ffab4f",
};
const TYPE_LAYER: Record<string, number> = {
  control: 0,
  cluster: 0,
  blueprint: 1,
  capability: 1,
  document: 1,
  entity: 2,
  evidence: 2,
  risk: 2,
  finding: 2,
};
const LAYER_RADIUS = [2.3, 4.7, 7.4];
const DEFAULT_COLOR = "#a8c4ff";
const CAMERA_HOME_DISTANCE = 21.5; // 初始相机距离，用于缩放百分比换算

function layerOf(nodeType: string): number {
  return TYPE_LAYER[nodeType] ?? 2;
}

function safetyColorOf(nodeType: string): string {
  return SAFETY_COLORS[nodeType] ?? DEFAULT_COLOR;
}

// ===== canvas 纹理工具（EVE 星图质感） =====
function glowTexture(color: string): THREE.Texture {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext("2d")!;
  const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, "#ffffff");
  gradient.addColorStop(0.1, "#ffffff");
  gradient.addColorStop(0.24, color);
  gradient.addColorStop(0.52, color + "bb");
  gradient.addColorStop(0.8, color + "3d");
  gradient.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(canvas);
}

function labelTexture(text: string, sub: string, color: string): THREE.Texture {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  const font = "600 14px 'Microsoft YaHei', 'PingFang SC', sans-serif";
  ctx.font = font;
  const textWidth = ctx.measureText(text).width;
  const subWidth = ctx.measureText(sub).width;
  const width = Math.ceil(Math.max(textWidth, subWidth) + 34);
  const height = 46;
  canvas.width = Math.ceil(width);
  canvas.height = height;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.beginPath();
  ctx.roundRect(0.5, 0.5, width - 1, height - 1, 7);
  ctx.fillStyle = "rgba(8,14,28,0.9)";
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = color + "99";
  ctx.stroke();
  ctx.font = font;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = color;
  ctx.fillText(text, width / 2, 16);
  ctx.font = "500 10.5px 'Microsoft YaHei', sans-serif";
  ctx.fillStyle = "rgba(240,246,255,0.9)";
  ctx.fillText(sub, width / 2, 35);
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

function starfieldTexture(): THREE.Texture {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#02040a";
  ctx.fillRect(0, 0, 512, 512);
  const nebula = [
    "rgba(74,120,235,0.032)",
    "rgba(150,90,230,0.028)",
    "rgba(48,170,205,0.032)",
    "rgba(200,70,150,0.024)",
  ];
  for (let i = 0; i < 7; i++) {
    const x = Math.random() * 512;
    const y = Math.random() * 512;
    const radius = 110 + Math.random() * 170;
    const g = ctx.createRadialGradient(x, y, 0, x, y, radius);
    g.addColorStop(0, nebula[i % nebula.length]);
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 512, 512);
  }
  for (let i = 0; i < 760; i++) {
    const x = Math.random() * 512;
    const y = Math.random() * 512;
    const r = Math.random() * 0.8 + 0.2;
    const alpha = Math.random() * 0.5 + 0.18;
    ctx.fillStyle = `rgba(255,255,255,${alpha})`;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }
  for (let i = 0; i < 52; i++) {
    const x = Math.random() * 512;
    const y = Math.random() * 512;
    const r = Math.random() * 1.8 + 0.9;
    const glow = ctx.createRadialGradient(x, y, 0, x, y, r * 4.5);
    glow.addColorStop(0, "rgba(255,255,255,0.9)");
    glow.addColorStop(0.4, "rgba(215,228,255,0.32)");
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(x, y, r * 4.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }
  return new THREE.CanvasTexture(canvas);
}

// ===== 3D 球壳布局：Fibonacci 球面均匀分布 + 同层连接聚簇 =====
const layoutCache = new Map<string, Map<string, { x: number; y: number; z: number }>>();

function sphereShellLayout(graph: GraphVisualization): Map<string, { x: number; y: number; z: number }> {
  const fp = `${graph.nodes.map((n) => n.id).join(",")}|${graph.edges.map((e) => `${e.source}>${e.target}`).join(",")}`;
  const hit = layoutCache.get(fp);
  if (hit) return hit;

  const buckets: Record<number, GraphVisualizationNode[]> = { 0: [], 1: [], 2: [] };
  for (const n of graph.nodes) buckets[layerOf(n.node_type)].push(n);

  const adjacency = new Map<string, Set<string>>();
  for (const e of graph.edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
    if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
    adjacency.get(e.source)!.add(e.target);
    adjacency.get(e.target)!.add(e.source);
  }

  const pos = new Map<string, { x: number; y: number; z: number }>();
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  for (const layer of [0, 1, 2] as const) {
    const arr = buckets[layer];
    if (arr.length === 0) continue;
    const targetR = LAYER_RADIUS[layer] ?? 5;
    const rMin = targetR * 0.82;
    const rMax = targetR * 1.18;
    const assigned = new Set<string>();
    const clusters: GraphVisualizationNode[][] = [];
    for (const seed of arr) {
      if (assigned.has(seed.id)) continue;
      const cluster: GraphVisualizationNode[] = [seed];
      assigned.add(seed.id);
      const queue = [seed];
      while (queue.length > 0 && cluster.length < 6) {
        const cur = queue.shift()!;
        for (const nbId of adjacency.get(cur.id) ?? []) {
          if (cluster.length >= 6) break;
          const nb = arr.find((x) => x.id === nbId);
          if (nb && !assigned.has(nb.id)) {
            assigned.add(nb.id);
            cluster.push(nb);
            queue.push(nb);
          }
        }
      }
      clusters.push(cluster);
    }
    for (const n of arr) if (!assigned.has(n.id)) clusters.push([n]);
    clusters.forEach((cluster, ci) => {
      const y01 = 1 - (2 * (ci + 0.5)) / clusters.length;
      const phiC = Math.acos(Math.min(1, Math.max(-1, y01)));
      const thetaC = GOLDEN * ci;
      const rC = rMin + Math.random() * (rMax - rMin);
      cluster.forEach((n) => {
        const spread = cluster.length > 1 ? 0.16 : 0.1;
        const phi = phiC + (Math.random() - 0.5) * spread * 2;
        const theta = thetaC + (Math.random() - 0.5) * spread * 2;
        const r = rC * (0.92 + Math.random() * 0.16);
        pos.set(n.id, {
          x: r * Math.sin(phi) * Math.cos(theta),
          y: r * Math.cos(phi),
          z: r * Math.sin(phi) * Math.sin(theta),
        });
      });
    });
  }
  layoutCache.set(fp, pos);
  if (layoutCache.size > 8) {
    const oldest = layoutCache.keys().next().value;
    if (oldest !== undefined) layoutCache.delete(oldest);
  }
  return pos;
}

// ===== 组件：EVE 星图风格 3D 知识图谱（分层级展示，点击逐级展开星链） =====
export default function StarMap3D({ graph, selectedNodeId, onNodeClick }: Props): React.JSX.Element {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneHandleRef = useRef<{ zoomBy: (factor: number) => void; resetView: () => void; applyVisibility: (visible: Set<string>, expanded: Set<string>) => void } | null>(null);
  const selectedNodeIdRef = useRef<string | null>(null);
  const [zoomPercent, setZoomPercent] = useState(100);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [visibleIds, setVisibleIds] = useState<Set<string>>(() => new Set());

  const graphNodeById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const graphNeighbors = useMemo(() => {
    const adj = new Map<string, Set<string>>();
    for (const e of graph.edges) {
      if (!adj.has(e.source)) adj.set(e.source, new Set());
      if (!adj.has(e.target)) adj.set(e.target, new Set());
      adj.get(e.source)!.add(e.target);
      adj.get(e.target)!.add(e.source);
    }
    return adj;
  }, [graph.edges]);

  // 初始层级：全部节点作为小光点（LOD：概览是点），仅内核（层0）带标签；点击逐级展开
  const initialExpanded = useMemo(
    () => new Set(graph.nodes.filter((n) => layerOf(n.node_type) === 0).map((n) => n.id)),
    [graph.nodes],
  );
  const initialVisible = useMemo(
    () => new Set(graph.nodes.map((n) => n.id)),
    [graph.nodes],
  );

  // 图谱结构指纹：内容真正变化时才重置展开状态
  const graphFp = useMemo(
    () => `${graph.nodes.map((n) => n.id).join(",")}|${graph.edges.map((e) => `${e.source}>${e.target}`).join(",")}`,
    [graph],
  );
  const prevFp = useRef("");
  useEffect(() => {
    if (graphFp === prevFp.current) return;
    prevFp.current = graphFp;
    setExpandedIds(initialExpanded);
    setVisibleIds(initialVisible);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphFp]);

  useEffect(() => {
    selectedNodeIdRef.current = selectedNodeId ?? null;
    // 选中节点常显标签（即使未展开）
    sceneHandleRef.current?.applyVisibility(visibleIds, new Set(expandedIds).add(selectedNodeId ?? ""));
  }, [selectedNodeId, visibleIds, expandedIds]);

  // 点击节点：展开其直接邻居（星链逐级展开）并回调选中
  const handleNodePick = (nodeId: string): void => {
    const node = graphNodeById.get(nodeId);
    if (!node) return;
    setExpandedIds((prev) => new Set(prev).add(nodeId));
    setVisibleIds((prev) => {
      const next = new Set(prev);
      for (const nb of graphNeighbors.get(nodeId) ?? []) next.add(nb);
      return next;
    });
    onNodeClick?.(node);
  };

  // ===== 场景构建 =====
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const disposables: { dispose: () => void }[] = [];
    const textureCache = new Map<string, THREE.Texture>();
    const glowFor = (color: string): THREE.Texture => {
      if (!textureCache.has(`glow:${color}`)) textureCache.set(`glow:${color}`, glowTexture(color));
      return textureCache.get(`glow:${color}`)!;
    };
    const labelFor = (text: string, sub: string, color: string): THREE.Texture => {
      const key = `label:${text}|${sub}|${color}`;
      if (!textureCache.has(key)) textureCache.set(key, labelTexture(text, sub, color));
      return textureCache.get(key)!;
    };

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    mount.appendChild(renderer.domElement);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.style.display = "block";
    renderer.domElement.style.borderRadius = "6px";
    disposables.push({ dispose: () => renderer.dispose() });

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#02040a");
    scene.fog = new THREE.FogExp2("#02040a", 0.005);
    const camera = new THREE.PerspectiveCamera(48, mount.clientWidth / mount.clientHeight, 0.1, 200);
    camera.position.set(0, 13, 17);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0, 0);
    controls.minDistance = 3.5;
    controls.maxDistance = 55;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    disposables.push({ dispose: () => controls.dispose() });

    const bgTexture = starfieldTexture();
    const bgMaterial = new THREE.MeshBasicMaterial({ map: bgTexture });
    const bg = new THREE.Mesh(new THREE.PlaneGeometry(120, 120), bgMaterial);
    bg.position.z = -60;
    scene.add(bg);
    disposables.push(bgMaterial, bgTexture);

    const graphGroup = new THREE.Group();
    const edgeGroup = new THREE.Group();
    const fxGroup = new THREE.Group();
    scene.add(graphGroup, edgeGroup, fxGroup);

    const spriteByKind = new Map<string, { sprite: THREE.Sprite; label: THREE.Sprite; color: string; size: number; nodeType: string }>();
    const makeSprite = (x: number, z: number, y: number, size: number, color: string, texture: THREE.Texture): THREE.Sprite => {
      const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false, opacity: 0.95 });
      const sprite = new THREE.Sprite(material);
      sprite.position.set(x, y, z);
      sprite.scale.set(size, size, 1);
      disposables.push(material);
      return sprite;
    };

    const graphLayout = sphereShellLayout(graph);
    for (const node of graph.nodes) {
      const layer = layerOf(node.node_type);
      const p = graphLayout.get(node.id) ?? { x: (Math.random() - 0.5) * 5, y: (Math.random() - 0.5) * 5, z: (Math.random() - 0.5) * 5 };
      const color = safetyColorOf(node.node_type);
      const size = layer === 0 ? 1.5 : layer === 1 ? 1.18 : 0.92;
      const sprite = makeSprite(p.x, p.z, p.y, size, color, glowFor(color));
      const degree = graphNeighbors.get(node.id)?.size ?? 0;
      const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: labelFor(node.label, `度 ${degree}`, color), transparent: true, depthWrite: false }));
      label.position.set(p.x, p.y - 0.9, p.z);
      label.scale.set(3.6, 1.1, 1);
      spriteByKind.set(node.id, { sprite, label, color, size, nodeType: node.node_type });
      graphGroup.add(sprite, label);
    }

    // 星路连线：Line2 渐变（起点语义色 → 终点语义色），可见性随两端节点
    const graphLineMaterials: LineMaterial[] = [];
    const graphEdgeInfos: { line: Line2; source: string; target: string }[] = [];
    for (const edge of graph.edges) {
      const a = graphNodeById.get(edge.source);
      const b = graphNodeById.get(edge.target);
      if (!a || !b) continue;
      const pa = graphLayout.get(edge.source);
      const pb = graphLayout.get(edge.target);
      if (!pa || !pb) continue;
      const cA = new THREE.Color(safetyColorOf(a.node_type));
      const cB = new THREE.Color(safetyColorOf(b.node_type));
      const geometry = new LineGeometry();
      geometry.setPositions([pa.x, pa.y, pa.z, pb.x, pb.y, pb.z]);
      geometry.setColors([cA.r, cA.g, cA.b, cB.r, cB.g, cB.b]);
      const weight = edge.weight ?? 0.5;
      const material = new LineMaterial({
        vertexColors: true,
        linewidth: Math.min(4.2, 2.2 + weight * 3),
        transparent: true,
        opacity: 0.7 + Math.min(0.22, weight / 10),
        depthWrite: false,
      });
      material.resolution.set(mount.clientWidth, mount.clientHeight);
      const line = new Line2(geometry, material);
      graphEdgeInfos.push({ line, source: edge.source, target: edge.target });
      graphLineMaterials.push(material);
      edgeGroup.add(line);
      disposables.push(geometry, material);
    }

    // 可见性：sprite 随可见集；label 随展开集（光点无标签，展开/选中才显名）
    const applyVisibility = (visible: Set<string>, expanded: Set<string>): void => {
      for (const node of graph.nodes) {
        const entry = spriteByKind.get(node.id);
        if (!entry) continue;
        const vis = visible.has(node.id);
        entry.sprite.visible = vis;
        entry.label.visible = vis && (expanded.has(node.id) || node.id === selectedNodeIdRef.current);
      }
      for (const e of graphEdgeInfos) e.line.visible = visible.has(e.source) && visible.has(e.target);
    };

    // 射线拾取
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const clickable: THREE.Sprite[] = [...spriteByKind.values()].map((e) => e.sprite);
    const spriteKeyByObj = new Map<THREE.Sprite, string>();
    for (const [key, entry] of spriteByKind) spriteKeyByObj.set(entry.sprite, key);
    const onPointerDown = (event: MouseEvent): void => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(clickable, false);
      if (hits.length === 0) return;
      const key = spriteKeyByObj.get(hits[0].object as THREE.Sprite) ?? "";
      if (key) handleNodePick(key);
    };
    renderer.domElement.addEventListener("pointerdown", onPointerDown);

    // 动画：图谱呼吸 / 风险节点红色闪光 + 扩散警示环
    const clock = new THREE.Clock();
    let disposed = false;
    const fxRings = new Map<string, THREE.Mesh>();
    const createFxRing = (id: string): THREE.Mesh => {
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.2, 0.42, 40),
        new THREE.MeshBasicMaterial({ color: "#ff3b4e", transparent: true, side: THREE.DoubleSide, depthWrite: false }),
      );
      ring.rotation.x = -Math.PI / 2;
      fxGroup.add(ring);
      fxRings.set(id, ring);
      return ring;
    };
    const animate = (): void => {
      if (disposed) return;
      const elapsed = clock.getElapsedTime();
      controls.update();
      bg.material.map!.offset.x = elapsed * 0.004;
      bg.material.map!.offset.y = elapsed * 0.002;
      for (const [id, entry] of spriteByKind) {
        if (!entry.sprite.visible) continue;
        const isRisk = entry.nodeType === "risk" || entry.nodeType === "finding";
        const selected = id === selectedNodeIdRef.current;
        const base = selected ? 1.28 : 1;
        if (isRisk) {
          const flash = 0.78 + 0.22 * Math.sin(elapsed * 4.6 + id.length);
          entry.sprite.scale.set(entry.size * base * (1 + 0.1 * flash), entry.size * base * (1 + 0.1 * flash), 1);
          (entry.sprite.material as THREE.SpriteMaterial).opacity = flash;
          const phase = (elapsed + id.length * 0.41) % 2.6;
          const ring = fxRings.get(id) ?? createFxRing(id);
          const progress = phase / 2.6;
          ring.scale.setScalar(1 + progress * 3);
          (ring.material as THREE.MeshBasicMaterial).opacity = 0.8 * (1 - progress);
          ring.position.set(entry.sprite.position.x, entry.sprite.position.y - 0.02, entry.sprite.position.z);
        } else {
          const pulse = 0.9 + 0.1 * Math.sin(elapsed * 0.8 + id.length);
          entry.sprite.scale.set(entry.size * base * pulse, entry.size * base * pulse, 1);
          (entry.sprite.material as THREE.SpriteMaterial).opacity = 0.95;
        }
      }
      const dist = camera.position.length();
      const labelScale = dist * 0.052;
      for (const entry of spriteByKind.values()) {
        if (!entry.label.visible) continue;
        entry.label.scale.set(3.6 * labelScale, 3.6 * 0.3 * labelScale, 1);
      }
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    };
    animate();

    const handleResize = (): void => {
      const width = mount.clientWidth;
      const height = mount.clientHeight;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
      for (const material of graphLineMaterials) material.resolution.set(width, height);
    };
    window.addEventListener("resize", handleResize);

    const zoomBy = (factor: number): void => {
      const distance = camera.position.distanceTo(controls.target);
      const next = Math.max(3.5, Math.min(55, distance * factor));
      if (Math.abs(next - distance) < 0.001) return;
      camera.position.lerp(controls.target, 1 - next / distance);
      setZoomPercent(Math.round((CAMERA_HOME_DISTANCE / next) * 100));
    };
    const resetView = (): void => {
      camera.position.set(0, 13, 17);
      controls.target.set(0, 0, 0);
      setZoomPercent(100);
    };

    sceneHandleRef.current = { zoomBy, resetView, applyVisibility };
    applyVisibility(visibleIds, expandedIds);

    return () => {
      disposed = true;
      window.removeEventListener("resize", handleResize);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      controls.dispose();
      for (const item of disposables) item.dispose();
      for (const texture of textureCache.values()) texture.dispose();
      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        if (mesh.material) {
          const material = mesh.material as THREE.Material;
          if (Array.isArray(material)) material.forEach((m) => m.dispose());
          else material.dispose();
        }
      });
      renderer.dispose();
      if (renderer.domElement.parentElement === mount) mount.removeChild(renderer.domElement);
      sceneHandleRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  // 展开/可见/选中同步到场景（场景重建后也会经此应用最新状态）
  useEffect(() => {
    sceneHandleRef.current?.applyVisibility(visibleIds, expandedIds);
  }, [visibleIds, expandedIds, graph]);

  const riskCount = graph.nodes.filter((n) => n.node_type === "risk" || n.node_type === "finding").length;

  return (
    <div className="starmap-wrap" style={{ height: "100%", minHeight: 420, border: "none", borderRadius: 6 }}>
      <div className="starmap-canvas" ref={mountRef} style={{ borderRadius: 6, overflow: "hidden" }} />
      <div className="starmap-hud starmap-topbar">
        <span className="starmap-breadcrumb">星图〈 知识图谱</span>
        <span className="starmap-breadcrumb-sub">点击光点逐级展开星链 · 滚轮缩放 · 拖拽旋转</span>
      </div>
      <div className="starmap-hud starmap-summary">
        <span>星点 {visibleIds.size}</span>
        <span className="starmap-failed">风险 {riskCount}</span>
      </div>
      <div className="starmap-hud starmap-legend">
        <span><i style={{ color: "#22c55e" }} />安全 · 内核</span>
        <span><i style={{ color: "#ff3b4e" }} />风险 · 红色闪光</span>
        <span><i style={{ color: "#7fd4ff" }} />类型分色</span>
      </div>
      <div className="starmap-hud starmap-zoom" role="group" aria-label="星图缩放控制">
        <button type="button" title="放大" aria-label="放大" onClick={() => sceneHandleRef.current?.zoomBy(1.25)}>＋</button>
        <button type="button" title="缩小" aria-label="缩小" onClick={() => sceneHandleRef.current?.zoomBy(0.8)}>－</button>
        <button type="button" title="还原视角" aria-label="还原视角" onClick={() => sceneHandleRef.current?.resetView()}>⟲</button>
        <span>{zoomPercent}%</span>
      </div>
    </div>
  );
}
