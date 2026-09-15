import { Alert, Button, Card, Col, Row, Space, Table, Tabs, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, asRecord, fmtInt, fmtTime, type PageProps } from "./common";

type PlanningIntentRow = {
  intent_id: string;
  intent_text: string;
  capability_requirements: string[];
  plan_key: string;
  trace_id: string;
  created_at: string;
};
type BlueprintRow = Record<string, unknown>;
type PlanRow = Record<string, unknown>;
type IntentsResponse = { items: PlanningIntentRow[]; trace_id: string };
type GenericItemsResponse = { items: Record<string, unknown>[]; trace_id: string };

/**
 * 组网规划与意图：规划意图列表 + 规划蓝图 + 规划计划。只读。
 * 从 PluginsPage 提取，独立成页。
 */
export default function PlanningPage({ tenantId }: PageProps) {
  const [intents, setIntents] = useState<PlanningIntentRow[]>([]);
  const [blueprints, setBlueprints] = useState<BlueprintRow[]>([]);
  const [plans, setPlans] = useState<PlanRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [intentError, setIntentError] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const [intentsResp, blueprintsResp, plansResp] = await Promise.all([
        apiRequest<IntentsResponse>({ path: "/api/v1/topology/planning/intents", tenantId: id, query: { limit: 50 } }),
        apiRequest<GenericItemsResponse>({ path: "/api/v1/topology/blueprints", tenantId: id }),
        apiRequest<GenericItemsResponse>({ path: "/api/v1/topology/plans", tenantId: id }),
      ]);
      setIntents(intentsResp.items ?? []);
      setBlueprints(blueprintsResp.items ?? []);
      setPlans(plansResp.items ?? []);
      setIntentError(false);
    } catch {
      setIntents([]);
      setBlueprints([]);
      setPlans([]);
      setIntentError(true);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={8}><Card className="metric-card" size="small"><span>规划意图</span><strong>{fmtInt(intents.length)}</strong><small>planning_intents 记录数</small></Card></Col>
        <Col xs={12} sm={8}><Card className="metric-card" size="small"><span>规划蓝图</span><strong>{fmtInt(blueprints.length)}</strong><small>blueprints 目录</small></Card></Col>
        <Col xs={12} sm={8}><Card className="metric-card" size="small"><span>规划计划</span><strong>{fmtInt(plans.length)}</strong><small>plans 记录数</small></Card></Col>
      </Row>

      {intentError ? <Alert showIcon type="warning" message="组网规划端点未接线" description="无法从 /api/v1/topology/planning/intents 获取数据。端点可能未启用或策略未放行。" /> : null}

      <Card className="chart-card" title="规划意图列表" extra={<Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>}>
        <Table<PlanningIntentRow>
          className="stock-table" size="small" rowKey="intent_id" loading={loading}
          dataSource={intents} pagination={{ pageSize: 20 }}
          columns={[
            { title: "意图 ID", dataIndex: "intent_id", key: "intent_id", width: 120, render: (v: string) => <span title={v}>{v.slice(0, 8)}</span> },
            { title: "意图文本", dataIndex: "intent_text", key: "intent_text", ellipsis: true },
            { title: "能力需求", dataIndex: "capability_requirements", key: "capability_requirements", render: (v: string[]) => v?.length ? <Space wrap size={4}>{v.map((c) => <Tag key={c} className="planned-tag">{c}</Tag>)}</Space> : "—" },
            { title: "Plan Key", dataIndex: "plan_key", key: "plan_key", ellipsis: true, render: (v: string) => <span title={v}>{v}</span> },
            { title: "Trace ID", dataIndex: "trace_id", key: "trace_id", width: 120, render: (v: string) => <span title={v}>{v.slice(0, 8)}</span> },
            { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 160, render: (v: string | null) => fmtTime(v) },
          ]}
          locale={{ emptyText: "无规划意图记录。" }}
        />
      </Card>

      <Tabs
        items={[
          {
            key: "blueprints",
            label: `规划蓝图（${blueprints.length}）`,
            children: (
              <Table<BlueprintRow>
                className="stock-table" size="small" rowKey={(r) => String(r.id ?? r.name ?? JSON.stringify(r).slice(0, 32))}
                loading={loading} dataSource={blueprints} pagination={{ pageSize: 15 }}
                columns={[
                  { title: "ID", dataIndex: "id", key: "id", width: 180, render: (v: string) => <span title={v}>{v}</span> },
                  { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
                  { title: "摘要", dataIndex: "summary", key: "summary", ellipsis: true, render: (v: string) => v ?? "—" },
                  { title: "成熟度", dataIndex: "maturity", key: "maturity", width: 110, render: (v: string) => v ? <Tag className="planned-tag">{v}</Tag> : "—" },
                  { title: "风险等级", dataIndex: "risk_class", key: "risk_class", width: 100, render: (v: string) => v ?? "—" },
                ]}
                locale={{ emptyText: "无规划蓝图记录。" }}
              />
            ),
          },
          {
            key: "plans",
            label: `规划计划（${plans.length}）`,
            children: (
              <Table<PlanRow>
                className="stock-table" size="small" rowKey={(r) => String(r.plan_key ?? r.id ?? JSON.stringify(r).slice(0, 32))}
                loading={loading} dataSource={plans} pagination={{ pageSize: 15 }}
                columns={[
                  { title: "Plan Key", dataIndex: "plan_key", key: "plan_key", ellipsis: true, render: (v: string) => <span title={v}>{v ?? "—"}</span> },
                  { title: "模式", dataIndex: "mode", key: "mode", width: 110, render: (v: string) => v ?? "—" },
                  { title: "节点数", dataIndex: "node_count", key: "node_count", width: 90, render: (v: number) => fmtInt(v) },
                  { title: "边数", dataIndex: "edge_count", key: "edge_count", width: 90, render: (v: number) => fmtInt(v) },
                  { title: "创建时间", dataIndex: "created_at", key: "created_at", width: 160, render: (v: string | null) => fmtTime(v) },
                ]}
                locale={{ emptyText: "无规划计划记录。" }}
              />
            ),
          },
        ]}
      />
      <Typography.Text type="secondary">规划意图证据行由 topology.planning_intents 表追加写入；蓝图与计划为只读目录快照。</Typography.Text>
    </Space>
  );
}
