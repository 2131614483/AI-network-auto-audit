import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Col, Row, Space, Table, Tabs, Tag, Typography } from "antd";
import { apiRequest, fmtInt, fmtTime, type PageProps } from "./common";

type RuleItem = {
  name: string; kind: string; path: string; modified_at: string; status: string;
};
type PolicySetItem = {
  name: string; version: string; status: string; rules_count: number; created_at: string | null;
};
type RulesSummary = {
  contracts: number; skills: number; report_templates: number; policy_sets: number; missing_roots: string[];
};
type RulesResponse = {
  summary: RulesSummary;
  contracts: RuleItem[];
  policies: PolicySetItem[];
  report_templates: RuleItem[];
  skills: RuleItem[];
  trace_id: string;
};

function statusTag(status: string) {
  if (status === "verified") return <Tag className="ready-tag">verified</Tag>;
  if (status === "contract_only") return <Tag className="planned-tag">contract_only</Tag>;
  if (status === "active") return <Tag className="ready-tag">active</Tag>;
  return <Tag className="pending-tag">{status}</Tag>;
}

/**
 * 规则与模板（规则登记）：系统按什么规则做事。
 * 契约/报告模板/技能来自文件系统投影；策略集来自 policy.policy_sets（DB 为唯一真值源）。
 * 只读：不修改任何契约/策略/模板文件。缺数据源显式标注，不静默。
 */
export default function RulesPage({ tenantId }: PageProps) {
  const [data, setData] = useState<RulesResponse>();
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<RulesResponse>({ path: "/api/v1/rules/registry", tenantId: id });
      setData(payload);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const summary = data?.summary;
  const missing = summary?.missing_roots ?? [];

  const contractColumns = [
    { title: "契约名称", dataIndex: "name", key: "name", ellipsis: true, render: (v: string) => <strong>{v}</strong> },
    { title: "类型", dataIndex: "kind", key: "kind", width: 120, render: () => <Tag className="gateway-tag">JSON Schema</Tag> },
    { title: "来源路径", dataIndex: "path", key: "path", ellipsis: true, render: (v: string) => <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>{v}</Typography.Text> },
    { title: "最后修改", dataIndex: "modified_at", key: "modified_at", width: 170, render: (v: string) => fmtTime(v) },
    { title: "状态", dataIndex: "status", key: "status", width: 100, render: (v: string) => statusTag(v) },
  ];

  const skillColumns = [
    { title: "技能名", dataIndex: "name", key: "name", ellipsis: true, render: (v: string) => <strong>{v}</strong> },
    { title: "技能 ID", dataIndex: "kind", key: "kind", ellipsis: true, render: (v: string) => <Typography.Text style={{ fontFamily: "monospace" }}>{v}</Typography.Text> },
    { title: "来源路径", dataIndex: "path", key: "path", ellipsis: true, render: (v: string) => <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>{v}</Typography.Text> },
    { title: "最后修改", dataIndex: "modified_at", key: "modified_at", width: 170, render: (v: string) => fmtTime(v) },
    { title: "生命周期", dataIndex: "status", key: "status", width: 120, render: (v: string) => statusTag(v) },
  ];

  const templateColumns = [
    { title: "模板名称", dataIndex: "name", key: "name", ellipsis: true, render: (v: string) => <strong>{v}</strong> },
    { title: "类型", dataIndex: "kind", key: "kind", width: 120, render: () => <Tag className="planned-tag">报告模板</Tag> },
    { title: "来源路径", dataIndex: "path", key: "path", ellipsis: true, render: (v: string) => <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>{v}</Typography.Text> },
    { title: "最后修改", dataIndex: "modified_at", key: "modified_at", width: 170, render: (v: string) => fmtTime(v) },
  ];

  const policyColumns = [
    { title: "策略集", dataIndex: "name", key: "name", ellipsis: true, render: (v: string) => <strong>{v}</strong> },
    { title: "版本", dataIndex: "version", key: "version", width: 80 },
    { title: "规则数", dataIndex: "rules_count", key: "rules_count", width: 90, render: (v: number) => fmtInt(v) },
    { title: "状态", dataIndex: "status", key: "status", width: 100, render: (v: string) => statusTag(v) },
    { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 170, render: (v: string | null) => fmtTime(v) },
  ];

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>契约（JSON Schema）</span><strong>{fmtInt(summary?.contracts)}</strong><small>contracts/jsonschema/</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>策略集</span><strong>{fmtInt(summary?.policy_sets)}</strong><small>policy.policy_sets · active</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>报告模板</span><strong>{fmtInt(summary?.report_templates)}</strong><small>审计项目案例/</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>技能（plugin.protocol.json）</span><strong>{fmtInt(summary?.skills)}</strong><small>plugins/builtin/</small></Card></Col>
      </Row>

      {missing.length > 0 ? (
        <Alert banner type="warning" showIcon message={`以下规则数据源目录缺失或未接线：${missing.join("、")}。对应 Tab 将为空态。`} />
      ) : null}

      <Card className="chart-card" title="规则登记" extra={<Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>}>
        <Tabs
          defaultActiveKey="contracts"
          items={[
            {
              key: "contracts",
              label: `契约 (${summary?.contracts ?? 0})`,
              children: <Table<RuleItem> className="stock-table" size="small" rowKey="path" loading={loading} dataSource={data?.contracts ?? []} pagination={{ pageSize: 20 }} columns={contractColumns} locale={{ emptyText: "无契约登记。" }} />,
            },
            {
              key: "policies",
              label: `策略 (${summary?.policy_sets ?? 0})`,
              children: <Table<PolicySetItem> className="stock-table" size="small" rowKey="name" loading={loading} dataSource={data?.policies ?? []} pagination={{ pageSize: 20 }} columns={policyColumns} locale={{ emptyText: "无 active 策略集。" }} />,
            },
            {
              key: "templates",
              label: `报告模板 (${summary?.report_templates ?? 0})`,
              children: <Table<RuleItem> className="stock-table" size="small" rowKey="path" loading={loading} dataSource={data?.report_templates ?? []} pagination={{ pageSize: 20 }} columns={templateColumns} locale={{ emptyText: "无报告模板。" }} />,
            },
            {
              key: "skills",
              label: `技能 (${summary?.skills ?? 0})`,
              children: <Table<RuleItem> className="stock-table" size="small" rowKey="path" loading={loading} dataSource={data?.skills ?? []} pagination={{ pageSize: 20 }} columns={skillColumns} locale={{ emptyText: "无技能登记。" }} />,
            },
          ]}
        />
      </Card>
      <Typography.Text type="secondary">本页为只读登记：契约/模板/技能是文件系统投影（不建第二真值源），策略集直接读 policy.policy_sets。页面不会修改任何契约、策略、模板或技能文件。</Typography.Text>
    </Space>
  );
}
