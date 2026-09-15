import { CopyOutlined } from "@ant-design/icons";
import { Button, Card, Col, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest, fmtInt, fmtTime, type PageProps } from "./common";

type FailureSample = {
  attempt_id: string; run_id: string; trace_id: string | null; node_instance_id: string;
  plugin_id: string | null; plugin_version: string | null; capability: string | null;
  attempt_seq: number; status: string; plan_key: string | null; occurred_at: string | null; message: string;
};
type FailureGroup = {
  error_kind: string; signature: string; occurrences: number; recent_occurrences: number;
  runs: string[]; run_count: number; node_count: number;
  plugins: Array<[string, number]>; first_seen: string | null; last_seen: string | null;
  spans_days: number; recurrent: boolean; samples: FailureSample[]; trace_ids: string[];
};
type FailureResponse = {
  totals: { attempts: number; failed: number; runs_with_failure: number };
  summary: { scanned: number; groups: number; recurrent_groups: number; by_error_kind: Record<string, number>; recent_days: number };
  items: FailureGroup[];
};

/**
 * 运行诊断：为什么失败、还会不会再犯。聚类单位是归一化签名（不是 error_kind）。
 * 只给复现命令、不代执行。
 */
export default function DiagnosePage({ tenantId }: PageProps) {
  const [data, setData] = useState<FailureResponse>();
  const [loading, setLoading] = useState(false);
  const [selectedSignature, setSelectedSignature] = useState<string>();
  const [kindFilter, setKindFilter] = useState<string>();
  const [pluginFilter, setPluginFilter] = useState<string>();

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<FailureResponse>({ path: "/api/v1/observability/failures", tenantId: id, query: { recent_days: 30, limit: 2000 } });
      setData(payload);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const pluginOptions = useMemo(() => {
    const set = new Set<string>();
    data?.items.forEach((group) => group.plugins.forEach(([plugin]) => set.add(plugin)));
    return [...set].sort().map((value) => ({ value, label: value }));
  }, [data]);

  const clusters = useMemo(() => (data?.items ?? []).filter((group) => {
    if (kindFilter && group.error_kind !== kindFilter) return false;
    if (pluginFilter && !group.plugins.some(([plugin]) => plugin === pluginFilter)) return false;
    return true;
  }), [data, kindFilter, pluginFilter]);

  const selected = clusters.find((group) => group.signature === selectedSignature) ?? clusters[0];

  const copyCommand = (group: FailureGroup): void => {
    const command = `# 复现（只读定位，不自动执行）\n# trace: /api/v1/observability/trace/${group.trace_ids[0] ?? "<trace_id>"}\n# plan_key: ${group.samples[0]?.plan_key ?? "<plan_key>"}\n# run: ${group.runs[0] ?? "<run_id>"}`;
    void navigator.clipboard?.writeText(command);
    message.success("复现定位信息已复制（只给命令，不代执行）");
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Card className="chart-card" title="错误聚类（按归一化签名）" extra={<Space wrap>
        <Select allowClear placeholder="error_kind" style={{ width: 160 }} value={kindFilter} onChange={setKindFilter} options={Object.entries(data?.summary.by_error_kind ?? {}).map(([value, count]) => ({ value, label: `${value}（${count}）` }))} />
        <Select allowClear showSearch placeholder="plugin_id" style={{ width: 200 }} value={pluginFilter} onChange={setPluginFilter} options={pluginOptions} />
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
      </Space>}>
        <Typography.Text type="secondary">
          近 {data?.summary.recent_days ?? 30} 日扫描 {fmtInt(data?.summary.scanned)} 条失败 attempt → {fmtInt(data?.summary.groups)} 个签名簇；
          历史累计失败 {fmtInt(data?.totals.failed)} 条。回潮簇（修复后再次出现）已标红。
        </Typography.Text>
        {!clusters.length ? (
          <div className="empty">近 30 日无失败。历史累计失败 {fmtInt(data?.totals.failed)} 条（{Object.entries(data?.summary.by_error_kind ?? {}).map(([kind, count]) => `${kind} ${count}`).join(" / ") || "无"}）。</div>
        ) : (
          <Row gutter={[8, 8]} style={{ marginTop: 12 }}>
            {clusters.map((group) => (
              <Col xs={24} sm={12} xl={8} key={group.signature}>
                <Card
                  size="small"
                  className={selected?.signature === group.signature ? "selected-row" : ""}
                  style={{ cursor: "pointer", borderColor: group.recurrent ? "#f85149" : undefined }}
                  onClick={() => setSelectedSignature(group.signature)}
                  title={<Space wrap><Tag className={group.error_kind === "plugin_failed" ? "pending-tag" : "gateway-tag"}>{group.error_kind}</Tag>{group.recurrent ? <Tag className="risk-high">回潮</Tag> : null}</Space>}
                  extra={<Typography.Text strong>{group.occurrences} 次</Typography.Text>}
                >
                  <Typography.Paragraph ellipsis={{ rows: 2 }} style={{ marginBottom: 4 }}><code>{group.signature}</code></Typography.Paragraph>
                  <Typography.Text type="secondary">
                    涉及 {group.run_count} 个 run · {group.plugins.length} 个插件 · {group.node_count} 个节点
                  </Typography.Text>
                  <div><Typography.Text type="secondary">{fmtTime(group.first_seen)} → {fmtTime(group.last_seen)}（跨度 {group.spans_days} 天）</Typography.Text></div>
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Card>

      <Card className="chart-card" title={selected ? `失败明细 · ${selected.error_kind}` : "失败明细"} extra={selected ? <Button size="small" icon={<CopyOutlined />} onClick={() => copyCommand(selected)}>复制复现命令</Button> : null}>
        {selected ? <>
          <Typography.Paragraph ellipsis={{ rows: 2, expandable: true }}><strong>签名：</strong><code>{selected.signature}</code></Typography.Paragraph>
          <Table<FailureSample>
            className="stock-table" size="small" rowKey="attempt_id"
            dataSource={selected.samples} pagination={{ pageSize: 10 }}
            columns={[
              { title: "attempt", dataIndex: "attempt_id", key: "attempt_id", width: 110, render: (value: string) => <span title={value}>{value.slice(0, 8)}</span> },
              { title: "run", dataIndex: "run_id", key: "run_id", width: 110, render: (value: string) => <span title={value}>{value.slice(0, 8)}</span> },
              { title: "插件", dataIndex: "plugin_id", key: "plugin_id", ellipsis: true, render: (value: string | null, row) => value ? <span title={value}>{value}{row.plugin_version ? <small style={{ color: "rgba(255,255,255,0.45)" }}>@{row.plugin_version}</small> : null}</span> : "—" },
              { title: "错误信息", dataIndex: "message", key: "message", ellipsis: true, render: (value: string) => value || "—" },
              { title: "时间", dataIndex: "occurred_at", key: "occurred_at", width: 160, render: (value: string | null) => fmtTime(value) },
            ]}
            locale={{ emptyText: "该签名簇无样本 attempt。" }}
          />
        </> : <div className="empty">选择上方一个签名簇查看失败 attempt 明细。</div>}
      </Card>
    </Space>
  );
}
