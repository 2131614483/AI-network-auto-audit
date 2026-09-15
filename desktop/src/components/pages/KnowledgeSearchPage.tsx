import { useCallback, useState } from "react";
import { Alert, Button, Card, Input, Select, Space, Tag, Typography } from "antd";
import { apiRequest, asRecord, fmtTime, type PageProps } from "./common";

type KnowledgeHit = {
  chunk_id: string;
  title: string;
  source_uri: string;
  ordinal: number;
  content: string;
  score: number;
  retrieval_mode: string;
  matched_by: string[];
};

/**
 * 语料检索（原 KnowledgePage 混合检索标签提级）。
 * 搜索框 + 模式切换（keyword/vector/hybrid）+ 结果列表。
 * 向量降级时显式黄色 Alert，绝不静默。
 */
export default function KnowledgeSearchPage({ tenantId, onNotice }: PageProps) {
  const [searchMode, setSearchMode] = useState("hybrid");
  const [searchQuery, setSearchQuery] = useState("");
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);

  const searchKnowledge = useCallback(async () => {
    if (!tenantId || !searchQuery.trim()) return;
    setBusy(true);
    setSearched(true);
    try {
      const payload = asRecord(await apiRequest({
        path: "/api/v1/knowledge/search",
        method: "POST",
        tenantId,
        body: { query: searchQuery.trim(), mode: searchMode, limit: 20 },
      }));
      setHits((payload.items as KnowledgeHit[]) ?? []);
      setDegraded(Boolean(payload.degraded));
      onNotice?.(
        Boolean(payload.degraded)
          ? "向量服务不可用，本次已降级为全文检索。"
          : "知识检索完成，结果保留来源与分块定位。",
      );
    } catch (error) {
      onNotice?.(error instanceof Error ? error.message : "知识检索失败。");
    } finally {
      setBusy(false);
    }
  }, [tenantId, searchQuery, searchMode, onNotice]);

  return (
    <Card
      className="chart-card"
      title="语料检索"
      extra={<Typography.Text type="secondary">keyword / vector / hybrid · 只读</Typography.Text>}
    >
      <Space direction="vertical" style={{ width: "100%" }} size={12}>
        <Space.Compact className="knowledge-search">
          <Select
            value={searchMode}
            onChange={setSearchMode}
            options={[
              { value: "hybrid", label: "混合检索" },
              { value: "keyword", label: "全文检索" },
              { value: "vector", label: "向量检索" },
            ]}
          />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onPressEnter={() => void searchKnowledge()}
            placeholder="输入审计问题、制度或实体"
          />
          <Button type="primary" loading={busy} onClick={() => void searchKnowledge()}>检索</Button>
        </Space.Compact>

        {degraded ? (
          <Alert
            className="search-alert"
            type="warning"
            showIcon
            message="向量未启动，本次仅全文检索"
            banner
          />
        ) : null}

        {busy ? (
          <div className="empty">检索中…</div>
        ) : !searched ? (
          <div className="empty">输入关键词开始检索。</div>
        ) : hits.length === 0 ? (
          <div className="empty">未命中结果。</div>
        ) : (
          <div className="search-results">
            {hits.map((hit) => (
              <Card
                size="small"
                className="search-hit"
                key={hit.chunk_id}
                title={hit.title}
                extra={
                  <Space>
                    <Tag>{hit.retrieval_mode}</Tag>
                    <Tag>{hit.score.toFixed(3)}</Tag>
                  </Space>
                }
              >
                <Typography.Paragraph ellipsis={{ rows: 4, expandable: true }}>
                  {hit.content}
                </Typography.Paragraph>
                <Typography.Text type="secondary">
                  来源：{hit.source_uri} · 分块 {hit.ordinal}
                </Typography.Text>
              </Card>
            ))}
          </div>
        )}
      </Space>
    </Card>
  );
}
