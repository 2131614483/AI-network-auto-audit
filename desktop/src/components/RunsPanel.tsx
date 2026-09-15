import { useEffect, useState } from "react";
import type { RunSummary, RunsFeed } from "../model/runCanvas";

type Props = {
  feed: RunsFeed | null;
  busy: boolean;
  offline: boolean;
  selectedRunId: string | null;
  tenantId?: string;
  onSelect: (runId: string) => void;
  onRefresh: () => void;
  onCreateDraft: () => void;
};

function timeLabel(createdAt: string | null): string {
  if (!createdAt) return "-";
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN", { hour12: false });
}

export default function RunsPanel({ feed, busy, offline, selectedRunId, tenantId, onSelect, onRefresh, onCreateDraft }: Props): React.JSX.Element {
  const [auto, setAuto] = useState(true);
  useEffect(() => {
    // Load immediately on mount (or when the refresh callback changes, e.g.
    // after the tenant resolves), not after the first 15s tick.
    onRefresh();
    if (!auto) return;
    const timer = window.setInterval(onRefresh, 15_000);
    return () => window.clearInterval(timer);
  }, [auto, onRefresh]);

  const items = feed?.items ?? [];
  const running = items.filter((item) => item.status === "running").length;
  const succeeded = items.filter((item) => item.status === "succeeded").length;
  const failed = items.filter((item) => item.status === "failed").length;

  return (
    <div className="runspanel">
      <div className="runspanel-head">
        <span>运行历史 · 队列</span>
        <label className="runspanel-auto">
          <input type="checkbox" checked={auto} onChange={(event) => setAuto(event.target.checked)} /> 每 15s 订阅
        </label>
      </div>
      <div className="runspanel-stats">
        <span className="runspanel-stat">{items.length} 个 run</span>
        <span className="runspanel-stat runspanel-stat-warn">{running} 运行中</span>
        <span className="runspanel-stat runspanel-stat-ok">{succeeded} 成功</span>
        <span className="runspanel-stat runspanel-stat-bad">{failed} 失败</span>
        {offline ? <span className="runspanel-offline">断线，等待补拉…</span> : null}
      </div>
      <div className="runspanel-list">
        {items.length === 0 && !busy ? (
          <div className="runspanel-empty">
            <div>暂无 run。可先生成 AI 组网草稿，草稿不会执行。</div>
            <button type="button" className="runspanel-create" onClick={onCreateDraft}>新建 AI 组网</button>
          </div>
        ) : null}
        {items.map((item) => (
          <button
            type="button"
            key={item.run_id}
            className={`runspanel-item${selectedRunId === item.run_id ? " selected" : ""}`}
            onClick={() => onSelect(item.run_id)}
          >
            <div className="runspanel-item-row">
              <span className={`runspanel-dot runspanel-dot-${item.status}`} />
              <span className="runspanel-item-id">{item.run_id.slice(0, 8)}</span>
              <span className="runspanel-item-status">{item.status}</span>
            </div>
            <div className="runspanel-item-sub">{item.plan_key ?? "-"}</div>
            <div className="runspanel-item-sub">
              attempts {item.attempt_succeeded}/{item.attempt_total} · {timeLabel(item.created_at)}
            </div>
          </button>
        ))}
      </div>
      <div className="runspanel-foot">
        <button type="button" className="runspanel-create compact" onClick={onCreateDraft}>+ 新建 AI 组网</button>
        <span>租户：{tenantId?.slice(0, 8) ?? "-"}</span>
      </div>
    </div>
  );
}
