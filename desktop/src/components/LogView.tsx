import { Button, Checkbox, Empty, Input, Select, Space, Tag, Typography } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  LOG_LEVELS,
  LOG_SOURCES,
  formatLogTimestamp,
  logLevelLabel,
  logLevelTone,
  logSourceLabel,
  sanitizeLogMessage,
  type LogEntry,
  type LogLevel,
  type LogSource,
} from "../model/log";

const MAX_VISIBLE_LOGS = 2000;

const levelClass = (level: LogLevel): string => {
  if (level === "error") return "log-line log-line-error";
  if (level === "warning") return "log-line log-line-warning";
  return "log-line";
};

export default function LogView(): React.JSX.Element {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [source, setSource] = useState<LogSource | "all">("all");
  const [level, setLevel] = useState<LogLevel | "all">("all");
  const [keyword, setKeyword] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [connected, setConnected] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let unsubscribe: (() => void) | undefined;
    void window.auditControl.logList().then((history) => {
      if (cancelled) return;
      setEntries(history.slice(-MAX_VISIBLE_LOGS));
      setConnected(true);
    }).catch(() => { if (!cancelled) setConnected(false); });
    try {
      unsubscribe = window.auditControl.logOnEvent((entry) => {
        setEntries((previous) => {
          const next = [...previous, entry];
          return next.length > MAX_VISIBLE_LOGS ? next.slice(next.length - MAX_VISIBLE_LOGS) : next;
        });
      });
    } catch { /* 订阅失败时仅显示历史快照 */ }
    return () => { cancelled = true; unsubscribe?.(); };
  }, []);

  useEffect(() => {
    if (!autoScroll || !scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [entries, autoScroll]);

  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return entries.filter((entry) => {
      if (source !== "all" && entry.source !== source) return false;
      if (level !== "all" && entry.level !== level) return false;
      if (needle && !entry.message.toLowerCase().includes(needle) && !logSourceLabel(entry.source).includes(needle)) return false;
      return true;
    });
  }, [entries, source, level, keyword]);

  return (
    <div className="log-view">
      <div className="log-toolbar">
        <div className="log-title">
          <Typography.Title level={4} style={{ margin: 0 }}>统一日志</Typography.Title>
          <Typography.Text type="secondary">主进程 / 渲染器 / API / Worker 实时汇集 · {connected ? "已连接" : "未连接"}</Typography.Text>
        </div>
        <Space wrap>
          <Select
            aria-label="来源过滤"
            size="small"
            style={{ width: 120 }}
            value={source}
            onChange={(value) => setSource(value)}
            options={LOG_SOURCES}
          />
          <Select
            aria-label="级别过滤"
            size="small"
            style={{ width: 110 }}
            value={level}
            onChange={(value) => setLevel(value)}
            options={LOG_LEVELS}
          />
          <Input
            aria-label="搜索日志"
            size="small"
            allowClear
            placeholder="搜索关键词"
            style={{ width: 180 }}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <Checkbox checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)}>自动滚动</Checkbox>
          <Button
            size="small"
            onClick={() => { void window.auditControl.logClear().then(() => setEntries([])); }}
          >
            清屏
          </Button>
          <Typography.Text type="secondary">共 {filtered.length} 条</Typography.Text>
        </Space>
      </div>
      <div className="log-list" ref={scrollRef}>
        {filtered.length === 0 ? (
          <Empty description="暂无匹配日志" style={{ marginTop: 80 }} />
        ) : filtered.map((entry) => (
          <div key={entry.id} className={levelClass(entry.level)}>
            <span className="log-ts">{formatLogTimestamp(entry.ts)}</span>
            <Tag className={`log-source log-source-${entry.source}`}>{logSourceLabel(entry.source)}</Tag>
            <Tag className={`log-level log-level-${entry.level}`}>{logLevelLabel(entry.level)}</Tag>
            <span className="log-msg">{sanitizeLogMessage(entry.message)}</span>
          </div>
        ))}
      </div>
      <div className="log-statusbar">
        <Typography.Text type="secondary">统一日志写入 .data/unified-日期.log（按日归档），敏感键值已脱敏为 ***</Typography.Text>
      </div>
    </div>
  );
}
