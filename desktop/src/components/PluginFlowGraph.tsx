import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { TopologyBlueprint } from "../model/pluginTopology";

type Props = {
  blueprints: TopologyBlueprint[];
  selectedId?: string;
  onSelect?: (blueprint: TopologyBlueprint) => void;
};

const GROUP_LABELS: Record<string, string> = {
  "domain-knowledge": "知识工程",
  "domain-financial-audit": "财务审计",
  "domain-quant": "量化研究",
  "domain-aiops": "智能运维",
  "family-thin": "轻量编排",
};

function groupOf(blueprint: TopologyBlueprint): string {
  const primary = blueprint.memberships.find((membership) => membership.role === "primary");
  return primary?.clusterId ?? "family-thin";
}

type FlowLine = {
  path: string;
  label: string;
  sourceId: string;
  targetId: string;
  sx: number;
  sy: number;
  ex: number;
  ey: number;
};

// 最朴素实现：插件=卡片节点，输入/输出契约=左右端口，输出契约命中某插件输入契约即生成连线。
export default function PluginFlowGraph({ blueprints, selectedId, onSelect }: Props): React.JSX.Element {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<FlowLine[]>([]);

  const groups = useMemo(() => {
    const order = ["domain-knowledge", "domain-financial-audit", "domain-quant", "domain-aiops", "family-thin"];
    const buckets = new Map<string, TopologyBlueprint[]>();
    for (const blueprint of blueprints) {
      const key = groupOf(blueprint);
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key)!.push(blueprint);
    }
    return order.filter((key) => buckets.has(key)).map((key) => ({ key, label: GROUP_LABELS[key] ?? key, items: buckets.get(key)! }));
  }, [blueprints]);

  // 端口测量 + 数据流连线（输出契约 == 输入契约）
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const outPoints = new Map<string, { x: number; y: number }>();
    const inPoints = new Map<string, { x: number; y: number }>();
    wrap.querySelectorAll<HTMLElement>("[data-port]").forEach((el) => {
      const key = el.dataset.port;
      if (!key) return;
      const r = el.getBoundingClientRect();
      const point = { x: r.left - rect.left + r.width / 2, y: r.top - rect.top + r.height / 2 };
      if (el.dataset.kind === "out") outPoints.set(key, point);
      else inPoints.set(key, point);
    });
    const matches: Array<{ source: string; target: string; contract: string }> = [];
    for (const blueprint of blueprints) {
      for (const contract of blueprint.outputContracts) {
        const consumer = blueprints.find((candidate) => candidate.id !== blueprint.id && candidate.inputContracts.includes(contract));
        if (consumer) matches.push({ source: blueprint.id, target: consumer.id, contract });
      }
    }
    setLines(matches.map((match) => {
      const from = outPoints.get(`out:${match.source}:${match.contract}`);
      const to = inPoints.get(`in:${match.target}:${match.contract}`);
      if (!from || !to) return null;
      const dx = Math.max(46, (to.x - from.x) / 2);
      return {
        path: `M ${from.x} ${from.y} C ${from.x + dx} ${from.y}, ${to.x - dx} ${to.y}, ${to.x} ${to.y}`,
        label: match.contract,
        sourceId: match.source,
        targetId: match.target,
        sx: from.x,
        sy: from.y,
        ex: to.x,
        ey: to.y,
      };
    }).filter((line): line is FlowLine => line !== null));
  }, [blueprints]);

  if (!blueprints.length) return <div className="graph-explorer-empty">暂无规划蓝图，无法绘制数据流。</div>;
  return (
    <div className="plugin-flow-wrap" ref={wrapRef}>
      <svg className="plugin-flow-lines" width="100%" height="100%" aria-hidden="true">
        {lines.map((line) => {
          const active = line.sourceId === selectedId || line.targetId === selectedId;
          return <g key={`${line.sourceId}-${line.label}-${line.targetId}`} opacity={selectedId && !active ? 0.22 : 0.9}>
            <path d={line.path} fill="none" stroke="#52e08a" strokeWidth={1.6} markerEnd="url(#flowArrow)" />
            <text x={(line.sx + line.ex) / 2} y={(line.sy + line.ey) / 2 - 4} textAnchor="middle" fontSize="10" fill="#9fe8bd" style={{ paintOrder: "stroke", stroke: "#02040a", strokeWidth: 3 }}>{line.label}</text>
          </g>;
        })}
        <defs>
          <marker id="flowArrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#52e08a" />
          </marker>
        </defs>
      </svg>
      <div className="plugin-flow-cols">
        {groups.map((group) => (
          <div className="plugin-flow-col" key={group.key}>
            <div className="plugin-flow-colhead">{group.label}</div>
            {group.items.map((blueprint) => {
              const selected = blueprint.id === selectedId;
              return (
                <button
                  type="button"
                  key={blueprint.id}
                  className={`plugin-flow-card${selected ? " selected" : ""}`}
                  onClick={() => onSelect?.(blueprint)}
                >
                  <div className="plugin-flow-name">{blueprint.name}</div>
                  <div className="plugin-flow-cap">{blueprint.capabilities.join(" · ")}</div>
                  <div className="plugin-flow-portrow">
                    <span className="plugin-flow-portgroup">
                      {blueprint.inputContracts.map((contract) => {
                        const matched = blueprints.some((candidate) => candidate.id !== blueprint.id && candidate.outputContracts.includes(contract));
                        return <span key={contract} className="plugin-flow-port" data-port={`in:${blueprint.id}:${contract}`} data-kind="in" title={`输入接口：${contract}${matched ? "（已对接）" : "（外部来源）"}`}>
                          <i className={matched ? "matched" : "open"} />{contract}
                          {!matched ? <em className="plugin-flow-open">外部</em> : null}
                        </span>;
                      })}
                    </span>
                    <span className="plugin-flow-portgroup">
                      {blueprint.outputContracts.map((contract) => {
                        const matched = blueprints.some((candidate) => candidate.id !== blueprint.id && candidate.inputContracts.includes(contract));
                        return <span key={contract} className="plugin-flow-port" data-port={`out:${blueprint.id}:${contract}`} data-kind="out" title={`输出接口：${contract}${matched ? "（已对接）" : "（待下游消费）"}`}>
                          <i className={matched ? "matched" : "open"} />{contract}
                          {!matched ? <em className="plugin-flow-open">待下游</em> : null}
                        </span>;
                      })}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
