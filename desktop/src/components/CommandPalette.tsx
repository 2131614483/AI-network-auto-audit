import {
  ApartmentOutlined,
  AuditOutlined,
  BookOutlined,
  ClockCircleOutlined,
  CloseOutlined,
  ClusterOutlined,
  DashboardOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { Input, Modal, Tag } from "antd";
import type { InputRef } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest } from "./pages/common";

/**
 * 全局 Ctrl+K 命令面板（模态浮层，不是导航页）。
 * - 命令模式：输入为空或以 `>` 开头 → 按导航分组列出全部可跳转页面，关键词过滤。
 * - 搜索模式：输入非空且不以 `>` 开头 → 防抖调用 GET /api/v1/search，跨类 Top 8。
 * - 空输入落地：最近搜索历史（localStorage）+ 6 个常用页面。
 * 仅依赖 antd 5 + React，不引入新依赖。
 */

/** App.tsx 从 navItems 拍平后注入：页 key → 中文标签 + 所属导航组。 */
export interface NavPage {
  key: string;
  label: string;
  group: string;
  groupKey: string;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  /** 跨页深链跳转（App 顶层 navigateTo，同时写 URL ?view=&tab=）。 */
  onNavigate: (view: string, tab?: string) => void;
  /** App 顶层 navItems 拍平得到的全部可跳转页面。 */
  pages: NavPage[];
  /** 当前租户（只读搜索经策略网关；主进程已注入 Trace）。 */
  tenantId?: string;
}

type SearchKind = "document" | "run" | "artifact" | "suggestion";

interface SearchItem {
  kind: SearchKind;
  item_id: string;
  title: string;
  subtitle?: string | null;
  anchor?: unknown;
  score?: number;
  matched_by?: string[];
}

interface SearchResponse {
  items: SearchItem[];
  degraded?: { vector?: boolean };
  trace_id?: string;
}

/** 跨类搜索结果下钻目标页（与设计文档 §14.1 一致）。 */
const KIND_VIEW: Record<SearchKind, string> = {
  document: "knowledge-search",
  run: "runs",
  artifact: "runs",
  suggestion: "suggestions",
};

const KIND_META: Record<SearchKind, { label: string; color: string }> = {
  document: { label: "文档", color: "blue" },
  run: { label: "运行", color: "green" },
  artifact: { label: "产物", color: "orange" },
  suggestion: { label: "建议", color: "purple" },
};

/** 命令模式下复用的导航组图标（与 App.tsx navItems 组图标一致）。 */
const GROUP_ICONS: Record<string, React.ReactNode> = {
  "group-overview": <DashboardOutlined />,
  "group-run": <PlayCircleOutlined />,
  "group-knowledge": <BookOutlined />,
  "group-graph": <ApartmentOutlined />,
  "group-plugins": <ClusterOutlined />,
  "group-business": <AuditOutlined />,
  "group-governance": <SafetyCertificateOutlined />,
  "group-system": <SettingOutlined />,
};

/** 命令模式空输入时展示的高频页面（固定 6 个）。 */
const COMMON_KEYS = ["hub", "runs", "diagnose", "knowledge", "plugins", "evolution"];

const HISTORY_KEY = "audit-command-history";
const HISTORY_LIMIT = 5;

type Entry =
  | { id: string; type: "history"; text: string }
  | { id: string; type: "page"; page: NavPage }
  | { id: string; type: "result"; item: SearchItem };

function loadHistory(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.slice(0, HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
}

function persistHistory(list: string[]): void {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_LIMIT)));
}

export default function CommandPalette({ open, onClose, onNavigate, pages, tenantId }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [history, setHistory] = useState<string[]>([]);
  const [searchResults, setSearchResults] = useState<SearchItem[]>([]);
  const [degradedVector, setDegradedVector] = useState(false);
  const [searching, setSearching] = useState(false);

  const inputRef = useRef<InputRef>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const reqSeq = useRef(0);

  const isCommand = query.startsWith(">");
  const trimmed = query.trim();
  const isSearchingMode = !isCommand && trimmed.length > 0;
  // 命令模式支持 "> <页面> <tab>"：首词过滤页面，其余词作为待传 tab。
  const commandRest = isCommand ? query.slice(1).trim() : "";
  const commandTokens = commandRest.split(/\s+/).filter(Boolean);
  const commandQuery = commandTokens[0] ?? "";
  const commandTab = commandTokens.length > 1 ? commandTokens.slice(1).join(" ") : undefined;

  // 拉取跨类搜索（带请求序号，丢弃过期响应）。
  const runSearch = useCallback(
    (text: string) => {
      const q = text.trim();
      if (!q) return;
      const seq = ++reqSeq.current;
      setSearching(true);
      void apiRequest<SearchResponse>({
        path: "/api/v1/search",
        tenantId,
        query: { q, limit: 8 },
      })
        .then((resp) => {
          if (seq !== reqSeq.current) return;
          setSearchResults(resp.items ?? []);
          setDegradedVector(resp.degraded?.vector === true);
          setSearching(false);
        })
        .catch(() => {
          if (seq !== reqSeq.current) return;
          setSearchResults([]);
          setDegradedVector(false);
          setSearching(false);
        });
    },
    [tenantId],
  );

  // 唤起时：重置输入、加载历史、聚焦并全选；卸载时无需额外清理。
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
    setHistory(loadHistory());
    setSearchResults([]);
    setDegradedVector(false);
    setSearching(false);
    const timer = window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 60);
    return () => window.clearTimeout(timer);
  }, [open]);

  // 搜索模式 300ms 防抖。
  useEffect(() => {
    if (isCommand || !trimmed) {
      setSearching(false);
      return;
    }
    const timer = window.setTimeout(() => runSearch(trimmed), 300);
    return () => window.clearTimeout(timer);
  }, [query, isCommand, trimmed, runSearch]);

  // 拍平成单一可选项列表，键盘 ↑↓ 仅在这一维移动。
  const entries = useMemo<Entry[]>(() => {
    if (isCommand) {
      const q = commandQuery.toLowerCase();
      return pages
        .filter((p) => !q || p.label.toLowerCase().includes(q) || p.key.toLowerCase().includes(q))
        .map((p) => ({ id: `page:${p.key}`, type: "page" as const, page: p }));
    }
    if (trimmed) {
      return searchResults.map((item, i) => ({
        id: `result:${item.kind}:${item.item_id}:${i}`,
        type: "result" as const,
        item,
      }));
    }
    // 空输入落地：最近搜索 + 常用页面
    const hist: Entry[] = history.map((text) => ({ id: `hist:${text}`, type: "history" as const, text }));
    const common: Entry[] = COMMON_KEYS.map((k) => pages.find((p) => p.key === k))
      .filter((p): p is NavPage => Boolean(p))
      .map((p) => ({ id: `page:${p.key}`, type: "page" as const, page: p }));
    return [...hist, ...common];
  }, [isCommand, trimmed, commandQuery, pages, searchResults, history]);

  // 输入变化时高亮回到首项。
  useEffect(() => {
    setActiveIndex(0);
  }, [query, searchResults, history]);

  // 高亮项滚动到可视区。
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-entry-index="${activeIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  const pushHistory = useCallback((text: string) => {
    const t = text.trim();
    if (!t) return;
    setHistory((prev) => {
      const next = [t, ...prev.filter((h) => h !== t)].slice(0, HISTORY_LIMIT);
      persistHistory(next);
      return next;
    });
  }, []);

  const removeHistory = useCallback((text: string) => {
    setHistory((prev) => {
      const next = prev.filter((h) => h !== text);
      persistHistory(next);
      return next;
    });
  }, []);

  const selectEntry = useCallback(
    (entry: Entry) => {
      if (entry.type === "history") {
        setQuery(entry.text);
        runSearch(entry.text);
        return;
      }
      if (entry.type === "page") {
        onNavigate(entry.page.key, commandTab);
        onClose();
        return;
      }
      onNavigate(KIND_VIEW[entry.item.kind] ?? "runs");
      pushHistory(trimmed);
      onClose();
    },
    [onNavigate, onClose, pushHistory, runSearch, trimmed, commandTab],
  );

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, entries.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const entry = entries[activeIndex];
      if (entry) selectEntry(entry);
    }
  };

  const rowBase: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "8px 12px",
    cursor: "pointer",
    borderRadius: 6,
  };

  const renderEntry = (entry: Entry, index: number) => {
    const active = index === activeIndex;
    const style: React.CSSProperties = {
      ...rowBase,
      background: active ? "rgba(47,129,247,0.22)" : "transparent",
    };
    if (entry.type === "history") {
      return (
        <div
          key={entry.id}
          data-entry-index={index}
          style={style}
          onMouseEnter={() => setActiveIndex(index)}
          onClick={() => selectEntry(entry)}
        >
          <ClockCircleOutlined style={{ opacity: 0.55 }} />
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{entry.text}</span>
          <CloseOutlined
            style={{ opacity: 0.5 }}
            title="删除该历史"
            onClick={(e) => {
              e.stopPropagation();
              removeHistory(entry.text);
            }}
          />
        </div>
      );
    }
    if (entry.type === "page") {
      const p = entry.page;
      return (
        <div
          key={entry.id}
          data-entry-index={index}
          style={style}
          onMouseEnter={() => setActiveIndex(index)}
          onClick={() => selectEntry(entry)}
        >
          <span style={{ opacity: 0.7 }}>{GROUP_ICONS[p.groupKey] ?? <DashboardOutlined />}</span>
          <span style={{ flex: 1 }}>{p.label}</span>
          <span style={{ fontSize: 12, opacity: 0.45 }}>{p.group}</span>
        </div>
      );
    }
    const meta = KIND_META[entry.item.kind] ?? { label: entry.item.kind, color: "default" };
    return (
      <div
        key={entry.id}
        data-entry-index={index}
        style={style}
        onMouseEnter={() => setActiveIndex(index)}
        onClick={() => selectEntry(entry)}
      >
        <Tag color={meta.color} style={{ marginInlineEnd: 0, minWidth: 44, textAlign: "center" }}>
          {meta.label}
        </Tag>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{entry.item.title}</div>
          {entry.item.subtitle ? (
            <div style={{ fontSize: 12, opacity: 0.45, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {entry.item.subtitle}
            </div>
          ) : null}
        </div>
        {typeof entry.item.score === "number" ? (
          <span style={{ fontSize: 12, opacity: 0.45 }}>{entry.item.score.toFixed(2)}</span>
        ) : null}
      </div>
    );
  };

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={560}
      closable={false}
      footer={null}
      style={{ top: "15vh" }}
      styles={{ body: { padding: 0 } }}
      centered={false}
    >
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "10px 12px 8px" }}>
          <Input
            ref={inputRef}
            variant="borderless"
            size="large"
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKeyDown}
            prefix={<SearchOutlined style={{ opacity: 0.5 }} />}
            placeholder="搜索页面、运行、文档… 或按 > 输入命令"
            style={{ fontSize: 16 }}
          />
        </div>

        {isSearchingMode && degradedVector ? (
          <div style={{ padding: "0 12px 6px", fontSize: 12, opacity: 0.6, color: "#faad14" }}>
            仅全文检索（向量未启动）
          </div>
        ) : null}

        <div ref={listRef} style={{ maxHeight: 400, overflowY: "auto", padding: "0 6px 6px" }}>
          {entries.length === 0 && !searching ? (
            <div style={{ padding: "18px 12px", opacity: 0.55 }}>未命中，试试其他关键词</div>
          ) : searching && entries.length === 0 ? (
            <div style={{ padding: "18px 12px", opacity: 0.55 }}>搜索中…</div>
          ) : (
            entries.map((entry, index) => renderEntry(entry, index))
          )}
        </div>

        <div
          style={{
            borderTop: "1px solid rgba(255,255,255,0.08)",
            padding: "6px 12px",
            fontSize: 12,
            opacity: 0.5,
          }}
        >
          ↑↓ 导航 · Enter 选择 · Esc 关闭
        </div>
      </div>
    </Modal>
  );
}
