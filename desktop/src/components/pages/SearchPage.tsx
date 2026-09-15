import { CopyOutlined, SearchOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Divider, Empty, Input, Space, Tag, Typography, message } from "antd";
import { apiRequest, fmtInt, type PageProps } from "./common";

type SearchKind = "document" | "run" | "artifact" | "suggestion";

type SearchItem = {
  kind: string;
  item_id: string;
  title: string;
  subtitle: string | null;
  anchor: Record<string, unknown>;
  score: number;
  matched_by: string[];
};
type SearchResponse = {
  items: SearchItem[];
  degraded: { vector: boolean };
  trace_id: string;
};

const KIND_LABEL: Record<string, string> = {
  document: "文档",
  run: "运行",
  artifact: "产物",
  suggestion: "建议",
};
const KIND_COLOR: Record<string, string> = {
  document: "blue",
  run: "green",
  artifact: "orange",
  suggestion: "purple",
};
const KIND_OPTIONS: Array<{ value: SearchKind; label: string }> = [
  { value: "document", label: "文档" },
  { value: "run", label: "运行" },
  { value: "artifact", label: "产物" },
  { value: "suggestion", label: "建议" },
];
const HISTORY_KEY = "audit-search-history";
const GROUP_TOP_N = 5;

function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function saveHistory(history: string[]): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 10)));
  } catch {
    /* ignore */
  }
}

/**
 * 统一搜索：跨文档 / 运行 / 产物 / 建议四类只读聚合。
 * 向量不可用时显式黄色 Alert 标注降级，绝不静默。
 */
export default function SearchPage({ tenantId, onNotice, onNavigate }: PageProps) {
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [selectedKinds, setSelectedKinds] = useState<SearchKind[]>(["document", "run", "artifact", "suggestion"]);
  const [data, setData] = useState<SearchResponse>();
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const inputRef = useRef(null);

  useEffect(() => { setHistory(loadHistory()); }, []);

  const doSearch = useCallback(async (q: string, kinds: SearchKind[]) => {
    const trimmed = q.trim();
    if (!trimmed || !tenantId) return;
    setLoading(true);
    setSearched(true);
    setShowHistory(false);
    try {
      const payload = await apiRequest<SearchResponse>({
        path: "/api/v1/search",
        tenantId,
        query: { q: trimmed, kinds: kinds.join(","), limit: 20 },
      });
      setData(payload);
      setActiveQuery(trimmed);
      // 更新搜索历史
      setHistory((prev) => {
        const next = [trimmed, ...prev.filter((h) => h !== trimmed)].slice(0, 10);
        saveHistory(next);
        return next;
      });
    } catch (error) {
      message.error(error instanceof Error ? `搜索失败：${error.message}` : "搜索失败。");
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  const grouped = useMemo(() => {
    const map: Record<string, SearchItem[]> = {};
    for (const item of data?.items ?? []) {
      (map[item.kind] ??= []).push(item);
    }
    // 按固定顺序排列
    return ["document", "run", "artifact", "suggestion"]
      .filter((k) => map[k]?.length)
      .map((kind) => ({ kind, items: map[kind] }));
  }, [data]);

  const handleDrill = useCallback((item: SearchItem) => {
    switch (item.kind) {
      case "document":
        onNavigate?.("knowledge-search");
        break;
      case "run":
        onNavigate?.("runs");
        break;
      case "artifact":
        onNavigate?.("runs");
        onNotice?.(`产物 ${item.item_id.slice(0, 8)}（证据包）已定位到运行页。`);
        break;
      case "suggestion":
        onNavigate?.("suggestions");
        break;
      default:
        break;
    }
  }, [onNavigate, onNotice]);

  const copyAnchor = (item: SearchItem) => {
    const text = JSON.stringify(item.anchor);
    void navigator.clipboard?.writeText(text);
    message.success("锚点已复制");
  };

  return (
    <Card className="chart-card" title="统一搜索" extra={
      <Typography.Text type="secondary">跨文档 / 运行 / 产物 / 建议 · 只读</Typography.Text>
    }>
      <Space direction="vertical" style={{ width: "100%" }} size={12}>
        {/* 搜索框 + 类型筛选 */}
        <div>
          <Input.Search
            ref={inputRef}
            size="large"
            placeholder="输入关键词、运行 ID 或插件名…"
            enterButton={<><SearchOutlined /> 搜索</>}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onSearch={(value) => void doSearch(value, selectedKinds)}
            onFocus={() => setShowHistory(true)}
            onBlur={() => { setTimeout(() => setShowHistory(false), 150); }}
            loading={loading}
          />
          {showHistory && history.length > 0 && (
            <div style={{ marginTop: 4, padding: "4px 0", background: "rgba(255,255,255,0.06)", borderRadius: 4 }}>
              <Typography.Text type="secondary" style={{ padding: "0 8px" }}>搜索历史：</Typography.Text>
              <Space wrap size={[4, 4]} style={{ padding: "4px 8px" }}>
                {history.map((h) => (
                  <Tag key={h} style={{ cursor: "pointer" }} onClick={() => { setQuery(h); void doSearch(h, selectedKinds); }}>{h}</Tag>
                ))}
              </Space>
            </div>
          )}
        </div>

        <Checkbox.Group
          options={KIND_OPTIONS}
          value={selectedKinds}
          onChange={(values) => setSelectedKinds(values as SearchKind[])}
        />

        {/* 降级态显式标注 */}
        {data?.degraded.vector ? (
          <Alert type="warning" showIcon message="向量服务未启动，本次仅全文检索" banner />
        ) : null}

        {/* 结果区 */}
        {loading ? (
          <div className="empty">搜索中…</div>
        ) : !searched ? (
          <div className="empty">输入关键词开始搜索。可试 <code>plugin_failed</code>、<code>416b0efc</code>、<code>黔岭</code>。</div>
        ) : grouped.length === 0 ? (
          <>
            {data?.degraded.vector ? (
              <Alert type="warning" showIcon message="向量服务未启动，本次仅全文检索" banner style={{ marginBottom: 8 }} />
            ) : null}
            <Empty description={<>未命中。可试 <code>plugin_failed</code>、<code>416b0efc</code>、<code>黔岭</code>。</>} />
          </>
        ) : (
          grouped.map(({ kind, items }) => {
            const expanded = expandedGroups[kind] ?? false;
            const visible = expanded ? items : items.slice(0, GROUP_TOP_N);
            const hasMore = items.length > GROUP_TOP_N;
            return (
              <div key={kind}>
                <Divider orientation="left" style={{ margin: "8px 0" }}>
                  <Tag color={KIND_COLOR[kind]}>{KIND_LABEL[kind] ?? kind}</Tag>
                  <Typography.Text type="secondary">共 {fmtInt(items.length)} 条</Typography.Text>
                </Divider>
                {visible.map((item) => (
                  <div key={`${item.kind}-${item.item_id}`} style={{ padding: "6px 0", borderBottom: "1px solid rgba(255,255,255,0.06)", cursor: "pointer" }}
                    onClick={() => handleDrill(item)}>
                    <Space wrap align="start">
                      <Tag color={KIND_COLOR[item.kind]}>{KIND_LABEL[item.kind] ?? item.kind}</Tag>
                      <Typography.Text strong>{item.title}</Typography.Text>
                      {item.subtitle ? <Typography.Text type="secondary">{item.subtitle}</Typography.Text> : null}
                      <Typography.Text type="secondary">score={item.score.toFixed(3)}</Typography.Text>
                      {item.matched_by.map((m) => (
                        <Tag key={m} className="planned-tag" style={{ fontSize: 11 }}>{m}</Tag>
                      ))}
                      <Button size="small" type="text" icon={<CopyOutlined />} title="复制锚点"
                        onClick={(e) => { e.stopPropagation(); copyAnchor(item); }} />
                    </Space>
                  </div>
                ))}
                {hasMore && (
                  <Button type="link" size="small" style={{ paddingLeft: 0 }}
                    onClick={() => setExpandedGroups((s) => ({ ...s, [kind]: !expanded }))}>
                    {expanded ? "收起" : `查看全部 ${fmtInt(items.length)} 条`}
                  </Button>
                )}
              </div>
            );
          })
        )}
      </Space>
    </Card>
  );
}
