import { useCallback, useEffect, useState } from "react";
import { Button, Card, Space, Table, Tag, Typography } from "antd";
import { apiRequest, asRecord, fmtTime, type PageProps } from "./common";

type KnowledgeAdapter = {
  key: string;
  label: string;
  supported_suffixes: string[];
  status: string;
  execution_mode: string;
  next_step: string;
};
type KnowledgePendingFile = {
  id: string;
  relative_path: string;
  source_uri: string;
  mime_type: string;
  size_bytes: number;
  status: "waiting_extractor" | "queued" | "processing" | "failed";
  error_detail?: string | null;
  created_at: string | null;
};

const STATUS_LABEL: Record<KnowledgePendingFile["status"], string> = {
  waiting_extractor: "待提交",
  queued: "排队中",
  processing: "解析中",
  failed: "解析失败",
};

const STATUS_TAG: Record<KnowledgePendingFile["status"], string> = {
  waiting_extractor: "ready-tag",
  queued: "gateway-tag",
  processing: "gateway-tag",
  failed: "pending-tag",
};

/**
 * 解析队列（原 KnowledgePage 本地解析队列标签提级）。
 * 待解析文件列表 + 本地适配器状态卡。只读。
 */
export default function KnowledgePendingPage({ tenantId }: PageProps) {
  const [pending, setPending] = useState<KnowledgePendingFile[]>([]);
  const [adapters, setAdapters] = useState<KnowledgeAdapter[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const [pendingPayload, adaptersPayload] = await Promise.all([
        apiRequest({ path: "/api/v1/knowledge/rich-media/pending", tenantId: id }),
        apiRequest({ path: "/api/v1/knowledge/adapters", tenantId: id }),
      ]);
      setPending((asRecord(pendingPayload).items as KnowledgePendingFile[]) ?? []);
      setAdapters((asRecord(adaptersPayload).items as KnowledgeAdapter[]) ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  return (
    <>
      {/* 适配器状态卡 */}
      <section className="data-bar">
        {adapters.length === 0 ? (
          <Card className="metric-card" size="small">
            <span>本地适配器</span>
            <strong>未接线</strong>
            <small>无已注册适配器</small>
          </Card>
        ) : (
          adapters.map((adapter) => (
            <Card className="metric-card" size="small" key={adapter.key}>
              <span>{adapter.label}</span>
              <strong>{adapter.status === "available" ? "就绪" : "待运行时"}</strong>
              <small>{adapter.supported_suffixes.join(" · ")}</small>
            </Card>
          ))
        )}
      </section>

      <Card
        className="chart-card"
        title="待解析文件队列"
        extra={
          <Space>
            <Tag className={pending.length ? "pending-tag" : "ready-tag"}>
              {pending.length ? `${pending.length} 个待处理` : "无待解析文件"}
            </Tag>
            <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
          </Space>
        }
      >
        <Table<KnowledgePendingFile>
          className="stock-table" size="small" rowKey="id"
          dataSource={pending}
          loading={loading}
          pagination={{ pageSize: 12 }}
          columns={[
            { title: "文件", dataIndex: "relative_path", key: "relative_path", ellipsis: true },
            {
              title: "类型", dataIndex: "mime_type", key: "mime_type", width: 140,
              render: (v: string) => v.replace("application/", ""),
            },
            {
              title: "大小", key: "size", width: 100,
              render: (_v, row) => `${(row.size_bytes / 1024).toFixed(1)} KB`,
            },
            {
              title: "状态", dataIndex: "status", key: "status", width: 100,
              render: (v: KnowledgePendingFile["status"]) => (
                <Tag className={STATUS_TAG[v]}>{STATUS_LABEL[v]}</Tag>
              ),
            },
            {
              title: "排队时间", dataIndex: "created_at", key: "created_at", width: 160,
              render: (v: string | null) => fmtTime(v),
            },
            {
              title: "说明", dataIndex: "error_detail", key: "error_detail", ellipsis: true,
              render: (v: string | null | undefined) => v ?? "—",
            },
          ]}
          locale={{ emptyText: "无待解析文件。" }}
        />

        {adapters.length > 0 ? (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>本地适配器详情</Typography.Title>
            <Table<KnowledgeAdapter>
              className="stock-table" size="small" rowKey="key"
              dataSource={adapters}
              pagination={false}
              columns={[
                { title: "接口", dataIndex: "label", key: "label" },
                {
                  title: "支持格式", dataIndex: "supported_suffixes", key: "supported_suffixes",
                  render: (v: string[]) => v.join(" · "),
                },
                {
                  title: "状态", dataIndex: "status", key: "status",
                  render: (v: string) => (
                    <Tag className={v === "available" ? "ready-tag" : "pending-tag"}>
                      {v === "available" ? "可用" : "等待本地运行时"}
                    </Tag>
                  ),
                },
                {
                  title: "执行方式", dataIndex: "execution_mode", key: "execution_mode",
                  render: (v: string) => v === "isolated_subprocess" ? "隔离子进程" : "未配置",
                },
                { title: "下一步", dataIndex: "next_step", key: "next_step", ellipsis: true },
              ]}
              locale={{ emptyText: "未声明本地适配器。" }}
            />
          </>
        ) : null}
      </Card>
    </>
  );
}
