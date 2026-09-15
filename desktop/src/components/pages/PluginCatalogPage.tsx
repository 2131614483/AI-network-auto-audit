import { Button, Card, Select, Space, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest, asRecord, fmtHashShort, type PageProps } from "./common";

type LifecycleItem = {
  plugin_id: string; name: string; capability: string; domain: string | null; domains: string[];
  protocol_lifecycle: string; executable: boolean; conflict: boolean;
  version: string | null; version_status: string | null;
  runtime: string | null; entrypoint: string | null; side_effect_class: string | null; published_at: string | null;
};
type LifecycleResponse = {
  summary: { plugins: number; protocol_verified: number; protocol_contract_only: number; executable: number; conflict: number; version_registered: number; by_domain: Record<string, number> };
  items: LifecycleItem[];
};

/**
 * 插件清单与契约：123 个插件的端口/生命周期一览（数据源 /api/v1/plugins/lifecycle）。
 * 与 PluginLifecyclePage 视角互补：本页聚焦清单与入口契约（entrypoint/runtime/side_effect），不提供写面。
 */
export default function PluginCatalogPage({ tenantId }: PageProps) {
  const [data, setData] = useState<LifecycleResponse>();
  const [loading, setLoading] = useState(false);
  const [domain, setDomain] = useState<string>();
  const [lifecycle, setLifecycle] = useState<string>();

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<LifecycleResponse>({
        path: "/api/v1/plugins/lifecycle", tenantId: id,
        query: { domain, lifecycle, limit: 300 },
      });
      setData(payload);
    } finally { setLoading(false); }
  }, [tenantId, domain, lifecycle]);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const domainOptions = useMemo(() => Object.keys(data?.summary.by_domain ?? {}).map((value) => ({ value, label: value })), [data]);

  return (
    <Card className="chart-card" title="插件清单与契约（只读索引）" extra={<Space wrap>
      <Select allowClear placeholder="域" style={{ width: 140 }} value={domain} onChange={(value) => setDomain(value)} options={domainOptions} />
      <Select allowClear placeholder="协议生命周期" style={{ width: 160 }} value={lifecycle} onChange={(value) => setLifecycle(value)} options={[{ value: "verified", label: "verified" }, { value: "contract_only", label: "contract_only" }]} />
      <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
    </Space>}>
      <Typography.Paragraph type="secondary">
        共 {data?.summary.plugins ?? "—"} 个插件目录项：协议 verified {data?.summary.protocol_verified ?? "—"} / contract_only {data?.summary.protocol_contract_only ?? "—"}；
        可执行 {data?.summary.executable ?? "—"}；已登记版本 {data?.summary.version_registered ?? "—"}。
        清单只描述端口与入口契约，不提供安装、执行或改生命周期写面。
      </Typography.Paragraph>
      <Table<LifecycleItem>
        className="stock-table" size="small" rowKey="plugin_id" loading={loading}
        dataSource={data?.items ?? []} pagination={{ pageSize: 20 }}
        rowClassName={(row) => (row.conflict ? "selected-row" : "")}
        columns={[
          { title: "插件", dataIndex: "plugin_id", key: "plugin_id", ellipsis: true, render: (value: string) => <span title={value}>{value}</span> },
          { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
          { title: "域", dataIndex: "domain", key: "domain", width: 110, render: (value: string | null) => value ?? "—" },
          { title: "协议生命周期", dataIndex: "protocol_lifecycle", key: "protocol_lifecycle", width: 130, render: (value: string) => <Tag className={value === "verified" ? "ready-tag" : "planned-tag"}>{value}</Tag> },
          { title: "可执行", dataIndex: "executable", key: "executable", width: 90, render: (value: boolean, row) => row.conflict ? <Tag className="risk-high">冲突</Tag> : <Tag className={value ? "ready-tag" : "pending-tag"}>{value ? "可执行" : "否"}</Tag> },
          { title: "版本", dataIndex: "version", key: "version", width: 90, render: (value: string | null) => value ?? "—" },
          { title: "运行时", dataIndex: "runtime", key: "runtime", width: 130, ellipsis: true, render: (value: string | null) => value ?? "—" },
          { title: "入口", dataIndex: "entrypoint", key: "entrypoint", ellipsis: true, render: (value: string | null) => <span title={value ?? undefined}>{value ? fmtHashShort(value, 28) : "—"}</span> },
          { title: "副作用类", dataIndex: "side_effect_class", key: "side_effect_class", width: 120, render: (value: string | null) => value ?? "—" },
          { title: "发布时间", dataIndex: "published_at", key: "published_at", width: 150, render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
        ]}
        locale={{ emptyText: "无插件记录。" }}
      />
    </Card>
  );
}
