import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Col, Row, Select, Space, Table, Tag, Typography } from "antd";
import { apiRequest, asRecord, fmtHashShort, fmtInt, type PageProps } from "./common";

type LifecycleItem = {
  plugin_id: string; name: string; capability: string; domain: string | null; domains: string[];
  protocol_lifecycle: string; executable: boolean; conflict: boolean;
  version: string | null; version_status: string | null; descriptor_sha256: string | null;
  runtime: string | null; entrypoint: string | null; side_effect_class: string | null; published_at: string | null;
};
type LifecycleSummary = {
  plugins: number; protocol_verified: number; protocol_contract_only: number;
  executable: number; conflict: number; version_registered: number;
  allow_list_size: number; allow_list_unmatched: number; by_domain: Record<string, number>;
};
type LifecycleResponse = { summary: LifecycleSummary; items: LifecycleItem[] };

/**
 * 生命周期与版本：plugin.protocol.json 与 runtime manifest 的对账结果。只读。
 * 口径冲突（声明 verified 但不可执行 / 反之）整行标红——诚实化，不掩盖。
 */
export default function PluginLifecyclePage({ tenantId }: PageProps) {
  const [data, setData] = useState<LifecycleResponse>();
  const [loading, setLoading] = useState(false);
  const [domain, setDomain] = useState<string>();
  const [lifecycle, setLifecycle] = useState<string>();
  const [conflictOnly, setConflictOnly] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<LifecycleResponse>({
        path: "/api/v1/plugins/lifecycle", tenantId: id,
        query: { domain, lifecycle, conflict_only: conflictOnly, limit: 300 },
      });
      setData(payload);
    } finally { setLoading(false); }
  }, [tenantId, domain, lifecycle, conflictOnly]);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const domainOptions = useMemo(() => Object.entries(data?.summary.by_domain ?? {}).map(([value, count]) => ({ value, label: `${value}（${count}）` })), [data]);
  const summary = data?.summary;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>插件总数</span><strong>{fmtInt(summary?.plugins)}</strong><small>目录索引</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>verified / contract_only</span><strong>{fmtInt(summary?.protocol_verified)} / {fmtInt(summary?.protocol_contract_only)}</strong><small>协议生命周期分布</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>可执行</span><strong>{fmtInt(summary?.executable)}</strong><small>已验证 + 入口存在</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>口径冲突</span><strong style={{ color: summary && summary.conflict > 0 ? "#f85149" : undefined }}>{fmtInt(summary?.conflict)}</strong><small>声明与运行态不一致</small></Card></Col>
      </Row>

      <Card className="chart-card" title="生命周期对账表" extra={<Space wrap>
        <Select allowClear showSearch placeholder="域" style={{ width: 150 }} value={domain} onChange={setDomain} options={domainOptions} />
        <Select allowClear placeholder="生命周期" style={{ width: 150 }} value={lifecycle} onChange={setLifecycle} options={[{ value: "verified", label: "verified" }, { value: "contract_only", label: "contract_only" }]} />
        <Button size="small" type={conflictOnly ? "primary" : "default"} danger={conflictOnly} onClick={() => setConflictOnly((value) => !value)}>仅冲突（{summary?.conflict ?? 0}）</Button>
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
      </Space>}>
        {summary && summary.allow_list_unmatched > 0 ? <Alert banner type="warning" showIcon style={{ marginBottom: 8 }} message={`允许表 ${summary.allow_list_size} 项中有 ${summary.allow_list_unmatched} 项在插件目录中找不到对应记录（口径漂移，需人工核对）。`} /> : null}
        <Table<LifecycleItem>
          className="stock-table" size="small" rowKey="plugin_id" loading={loading}
          dataSource={data?.items ?? []} pagination={{ pageSize: 20 }}
          rowClassName={(row) => (row.conflict ? "selected-row" : "")}
          columns={[
            { title: "插件", dataIndex: "plugin_id", key: "plugin_id", ellipsis: true, render: (value: string) => <span title={value}>{value}</span> },
            { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
            { title: "域", dataIndex: "domain", key: "domain", width: 110, render: (value: string | null) => value ?? "—" },
            { title: "协议生命周期", dataIndex: "protocol_lifecycle", key: "protocol_lifecycle", width: 130, render: (value: string) => <Tag className={value === "verified" ? "ready-tag" : "planned-tag"}>{value}</Tag> },
            { title: "可执行", dataIndex: "executable", key: "executable", width: 90, render: (value: boolean, row) => row.conflict ? <Tag className="risk-high">冲突</Tag> : <Tag className={value ? "ready-tag" : "pending-tag"}>{value ? "是" : "否"}</Tag> },
            { title: "版本", dataIndex: "version", key: "version", width: 90, render: (value: string | null) => value ?? "—" },
            { title: "描述符", dataIndex: "descriptor_sha256", key: "descriptor_sha256", width: 130, render: (value: string | null) => value ? <span title={value} style={{ cursor: "pointer" }} onClick={() => { void navigator.clipboard?.writeText(value); }}>{fmtHashShort(value, 12)}…</span> : "—" },
            { title: "发布时间", dataIndex: "published_at", key: "published_at", width: 150, render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—" },
          ]}
          locale={{ emptyText: "无插件记录。" }}
        />
      </Card>
      <Typography.Text type="secondary">口径冲突 = 协议声明 verified 但运行态不可执行（或反之）。冲突行必须人工消解，界面不会用绿色掩盖。</Typography.Text>
    </Space>
  );
}
