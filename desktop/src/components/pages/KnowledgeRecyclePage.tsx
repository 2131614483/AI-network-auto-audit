import { useCallback, useEffect, useState } from "react";
import { Button, Card, Modal, Space, Table, Tag, Typography } from "antd";
import { apiRequest, asRecord, fmtTime, type PageProps } from "./common";

type KnowledgeRecycleItem = {
  id: string;
  entity_id: string;
  title: string | null;
  source_uri: string | null;
  prior_status: string;
  reason: string;
  deleted_at: string;
  restored_at: string | null;
};

/**
 * 知识回收站（原 KnowledgePage 知识回收站标签提级）。
 * 已删除文档列表 + 恢复按钮（写操作经策略网关）。只读浏览，恢复为幂等写。
 */
export default function KnowledgeRecyclePage({ tenantId, onNotice }: PageProps) {
  const [modal] = Modal.useModal();
  const [items, setItems] = useState<KnowledgeRecycleItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = asRecord(await apiRequest({ path: "/api/v1/knowledge/recycle-bin", tenantId: id }));
      setItems((payload.items as KnowledgeRecycleItem[]) ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const restoreDocument = (item: KnowledgeRecycleItem) => {
    if (!tenantId) return;
    modal.confirm({
      title: "恢复知识文档？",
      content: `“${item.title ?? "未命名文档"}”将恢复到知识库和检索范围，保留原来的版本与来源。`,
      okText: "恢复文档",
      cancelText: "取消",
      onOk: async () => {
        setBusy(true);
        try {
          await apiRequest({
            path: `/api/v1/knowledge/recycle-bin/${item.id}/restore`,
            method: "POST",
            tenantId,
            body: { reason: "桌面端用户恢复知识文档" },
          });
          await load(tenantId);
          onNotice?.("文档已恢复到知识库和检索范围。");
        } catch (error) {
          onNotice?.(error instanceof Error ? error.message : "恢复知识文档失败。");
        } finally {
          setBusy(false);
        }
      },
    });
  };

  return (
    <Card
      className="chart-card"
      title="知识回收站"
      extra={
        <Space>
          <Tag className={items.length ? "pending-tag" : "ready-tag"}>
            {items.length ? `${items.length} 项待恢复` : "回收站为空"}
          </Tag>
          <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary">
        回收站中的文档已从知识库和检索结果中隐藏，但原始记录与版本保留，不会被硬删除。
      </Typography.Paragraph>
      <Table<KnowledgeRecycleItem>
        className="stock-table" size="small" rowKey="id"
        dataSource={items}
        loading={loading}
        pagination={{ pageSize: 12 }}
        columns={[
          {
            title: "文档", dataIndex: "title", key: "title",
            render: (v: string | null) => v ?? "未命名文档",
          },
          { title: "原状态", dataIndex: "prior_status", key: "prior_status", width: 100 },
          {
            title: "删除时间", dataIndex: "deleted_at", key: "deleted_at", width: 160,
            render: (v: string) => fmtTime(v),
          },
          { title: "原因", dataIndex: "reason", key: "reason", ellipsis: true },
          { title: "来源", dataIndex: "source_uri", key: "source_uri", ellipsis: true },
          {
            title: "操作", key: "actions", width: 120,
            render: (_v, row) => (
              <Button
                size="small" type="primary"
                disabled={!tenantId || busy}
                onClick={() => restoreDocument(row)}
              >
                恢复文档
              </Button>
            ),
          },
        ]}
        locale={{ emptyText: "回收站为空。" }}
      />
    </Card>
  );
}
