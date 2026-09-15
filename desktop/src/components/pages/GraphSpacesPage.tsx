import { useCallback, useEffect, useState } from "react";
import { Button, Card, Space, Table, Tag, Typography } from "antd";
import { apiRequest, fmtInt, fmtTime, type PageProps } from "./common";

type GraphGovernanceSpace = {
  key: string;
  level: string;
  name: string;
  graph_role: string;
  cluster_key: string;
  active_bridge_count: number;
  revision_count: number;
};
type GraphGovernance = {
  spaces: GraphGovernanceSpace[];
  active_bridge_count: number;
  open_conflict_count: number;
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
type GraphSpaceViz = {
  key: string;
  level: string;
  name: string;
  node_count: number;
  edge_count: number;
};
type GraphVisualizationResponse = {
  spaces: GraphSpaceViz[];
  partial: boolean;
};

/**
 * 图空间与桥（原 App.tsx graph 视图图空间目录提级）。
 * 图空间列表（层级/角色/节点边数/桥接数）+ 冲突收件箱摘要。
 */
export default function GraphSpacesPage({ tenantId }: PageProps) {
  const [governance, setGovernance] = useState<GraphGovernance>({
    spaces: [], active_bridge_count: 0, open_conflict_count: 0,
  });
  const [conflicts, setConflicts] = useState<GraphConflict[]>([]);
  const [vizSpaces, setVizSpaces] = useState<GraphSpaceViz[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const [govPayload, conflictPayload, vizPayload] = await Promise.all([
        apiRequest<GraphGovernance>({ path: "/api/v1/graph/governance", tenantId: id }),
        apiRequest<{ items: GraphConflict[] }>({ path: "/api/v1/graph/conflicts", tenantId: id, query: { limit: 30 } }),
        apiRequest<GraphVisualizationResponse>({ path: "/api/v1/graph/visualization", tenantId: id }),
      ]);
      setGovernance(govPayload);
      setConflicts(conflictPayload.items ?? []);
      setVizSpaces(vizPayload.spaces ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  // Merge governance spaces with visualization node/edge counts by key
  const mergedSpaces = governance.spaces.map((space) => {
    const viz = vizSpaces.find((v) => v.key === space.key);
    return {
      ...space,
      node_count: viz?.node_count ?? null,
      edge_count: viz?.edge_count ?? null,
    };
  });

  return (
    <>
      <section className="detail-head">
        <div>
          <Typography.Title level={3}>图空间与桥</Typography.Title>
          <Typography.Text type="secondary">L0–L4 分层、已登记有限桥接；只读治理目录</Typography.Text>
        </div>
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
      </section>

      <section className="data-bar">
        <Card className="metric-card" size="small">
          <span>图空间</span>
          <strong>{fmtInt(governance.spaces.length)}</strong>
          <small>已登记</small>
        </Card>
        <Card className="metric-card" size="small">
          <span>已登记桥接</span>
          <strong>{fmtInt(governance.active_bridge_count)}</strong>
          <small>仅四类有限关系</small>
        </Card>
        <Card className="metric-card" size="small">
          <span>待处理冲突</span>
          <strong><Typography.Text type={governance.open_conflict_count ? "danger" : undefined}>
            {fmtInt(governance.open_conflict_count)}
          </Typography.Text></strong>
          <small>来源主张保留，不自动删除</small>
        </Card>
      </section>

      <Card
        className="chart-card"
        title="图空间目录"
        extra={<Tag>有限桥接</Tag>}
      >
        <Table<GraphGovernanceSpace & { node_count: number | null; edge_count: number | null }>
          className="stock-table" size="small"
          rowKey="key"
          dataSource={mergedSpaces}
          loading={loading}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "层级", dataIndex: "level", key: "level", width: 56 },
            { title: "图空间", dataIndex: "name", key: "name" },
            {
              title: "角色 / 集群", key: "role",
              render: (_v, row) => `${row.graph_role} · ${row.cluster_key}`,
              ellipsis: true,
            },
            {
              title: "节点数", dataIndex: "node_count", key: "node_count", width: 80,
              render: (v: number | null) => v !== null ? fmtInt(v) : "未加载",
            },
            {
              title: "边数", dataIndex: "edge_count", key: "edge_count", width: 80,
              render: (v: number | null) => v !== null ? fmtInt(v) : "未加载",
            },
            { title: "桥接", dataIndex: "active_bridge_count", key: "active_bridge_count", width: 60 },
            { title: "版本", dataIndex: "revision_count", key: "revision_count", width: 60 },
          ]}
          locale={{ emptyText: "无图空间。" }}
        />
      </Card>

      <Card
        className="chart-card"
        title="冲突收件箱"
        extra={
          <Tag className={conflicts.length ? "pending-tag" : "ready-tag"}>
            {conflicts.length ? "需人工处理" : "当前为空"}
          </Tag>
        }
        style={{ marginTop: 12 }}
      >
        <Table<GraphConflict>
          className="stock-table" size="small"
          rowKey="id"
          dataSource={conflicts}
          pagination={{ pageSize: 8 }}
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
          locale={{ emptyText: "暂无冲突；系统不会因路由而删除不同来源的主张。" }}
        />
      </Card>
    </>
  );
}
