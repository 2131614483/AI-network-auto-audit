import { useCallback, useEffect, useState } from "react";
import { Button, Card, Space, Table, Tabs, Tag, Typography } from "antd";
import { apiRequest, fmtTime, type PageProps } from "./common";

type GraphMergeRecord = {
  id: string;
  target_node_key: string;
  status: string;
  reason: string | null;
  checksum: string;
  created_at: string;
  source_count: number;
  redirected_edges: number;
};
type GraphSplitRecord = {
  id: string;
  source_node_key: string;
  status: string;
  reason: string | null;
  checksum: string;
  created_at: string;
  part_count: number;
  redirected_edges: number;
};
type GraphConflict = {
  id: string;
  entity_key: string;
  status: string;
  severity: string;
  summary: string;
  item_count: number;
  created_at: string;
};

function statusTag(status: string) {
  const cls = status === "applied" ? "ready-tag" : status === "conflict" ? "pending-tag" : "risk-high";
  return <Tag className={cls}>{status}</Tag>;
}

/**
 * 合并/拆分/仲裁（原 App.tsx graph 视图合并/拆分账本提级）。
 * Tabs 切换：合并记录 / 拆分记录 / 冲突列表。只读治理清单，可回滚。
 */
export default function GraphMergePage({ tenantId }: PageProps) {
  const [merges, setMerges] = useState<GraphMergeRecord[]>([]);
  const [splits, setSplits] = useState<GraphSplitRecord[]>([]);
  const [conflicts, setConflicts] = useState<GraphConflict[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const [mergePayload, splitPayload, conflictPayload] = await Promise.all([
        apiRequest<{ items: GraphMergeRecord[] }>({
          path: "/api/v1/graph/merges", tenantId: id, query: { limit: 50 },
        }),
        apiRequest<{ items: GraphSplitRecord[] }>({
          path: "/api/v1/graph/splits", tenantId: id, query: { limit: 50 },
        }),
        apiRequest<{ items: GraphConflict[] }>({
          path: "/api/v1/graph/conflicts", tenantId: id, query: { limit: 30 },
        }),
      ]);
      setMerges(mergePayload.items ?? []);
      setSplits(splitPayload.items ?? []);
      setConflicts(conflictPayload.items ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  return (
    <Card
      className="chart-card"
      title="合并 / 拆分 / 仲裁"
      extra={
        <Space>
          <Tag className="gateway-tag">只读治理清单 · 可回滚</Tag>
          <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
        </Space>
      }
    >
      <Tabs
        size="small"
        items={[
          {
            key: "merges",
            label: `合并记录 (${merges.length})`,
            children: (
              <Table<GraphMergeRecord>
                className="stock-table" size="small" rowKey="id"
                dataSource={merges}
                pagination={{ pageSize: 10 }}
                columns={[
                  { title: "时间", dataIndex: "created_at", key: "created_at", width: 160, render: (v: string) => fmtTime(v) },
                  { title: "目标节点", dataIndex: "target_node_key", key: "target_node_key", ellipsis: true },
                  { title: "来源数", dataIndex: "source_count", key: "source_count", width: 64 },
                  { title: "重指边", dataIndex: "redirected_edges", key: "redirected_edges", width: 64 },
                  { title: "状态", dataIndex: "status", key: "status", width: 84, render: statusTag },
                  {
                    title: "原因", dataIndex: "reason", key: "reason",
                    render: (v: string | null) => v ?? "—", ellipsis: true,
                  },
                ]}
                locale={{ emptyText: "无合并记录。合并仅重指受限空间内的边并把源节点软删进回收站，历史证据不硬删除。" }}
              />
            ),
          },
          {
            key: "splits",
            label: `拆分记录 (${splits.length})`,
            children: (
              <Table<GraphSplitRecord>
                className="stock-table" size="small" rowKey="id"
                dataSource={splits}
                pagination={{ pageSize: 10 }}
                columns={[
                  { title: "时间", dataIndex: "created_at", key: "created_at", width: 160, render: (v: string) => fmtTime(v) },
                  { title: "源节点", dataIndex: "source_node_key", key: "source_node_key", ellipsis: true },
                  { title: "子节点数", dataIndex: "part_count", key: "part_count", width: 72 },
                  { title: "重指边", dataIndex: "redirected_edges", key: "redirected_edges", width: 64 },
                  { title: "状态", dataIndex: "status", key: "status", width: 84, render: statusTag },
                  {
                    title: "原因", dataIndex: "reason", key: "reason",
                    render: (v: string | null) => v ?? "—", ellipsis: true,
                  },
                ]}
                locale={{ emptyText: "无拆分记录。拆分只把声明关系类型的边重指到新子节点，源节点保持存活。" }}
              />
            ),
          },
          {
            key: "conflicts",
            label: `冲突列表 (${conflicts.length})`,
            children: (
              <Table<GraphConflict>
                className="stock-table" size="small" rowKey="id"
                dataSource={conflicts}
                pagination={{ pageSize: 10 }}
                columns={[
                  {
                    title: "严重度", dataIndex: "severity", key: "severity", width: 74,
                    render: (v: string) => (
                      <Tag className={v === "high" ? "risk-high" : "pending-tag"}>{v}</Tag>
                    ),
                  },
                  { title: "冲突摘要", dataIndex: "summary", key: "summary", ellipsis: true },
                  { title: "来源", dataIndex: "item_count", key: "item_count", width: 54 },
                  {
                    title: "时间", dataIndex: "created_at", key: "created_at", width: 160,
                    render: (v: string) => fmtTime(v),
                  },
                ]}
                locale={{ emptyText: "无合并/拆分/冲突记录。" }}
              />
            ),
          },
        ]}
      />
    </Card>
  );
}
