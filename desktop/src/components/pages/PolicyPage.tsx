import { Alert, Button, Card, Col, Form, Input, Row, Select, Space, Tag, Typography } from "antd";
import { useState } from "react";
import { apiRequest, asRecord, type PageProps } from "./common";

type PolicyDecisionResponse = {
  decision: "ALLOW" | "DENY" | "REQUIRE_APPROVAL" | "FREEZE";
  risk_score: number;
  reason: string;
  policy_version: string;
  matched_rule_ids: string[];
  simulation: boolean;
  decided_at: string;
  trace_id: string;
};

/**
 * 策略与风险：策略模拟器 + 策略集状态。
 * 从 App.tsx 内联 policy 视图提级为独立页面。
 * 策略集列表端点未接线（policy.policy_sets 表 active 209 个，其中 182 疑似测试残留）。
 */
export default function PolicyPage({ tenantId }: PageProps) {
  const [form] = Form.useForm();
  const [result, setResult] = useState<PolicyDecisionResponse>();
  const [simulating, setSimulating] = useState(false);

  const simulate = async (values: { capability: string; risk_class: string; side_effects: string; arguments: string }): Promise<void> => {
    if (!tenantId) return;
    setSimulating(true);
    try {
      const parsedArgs = (() => { try { return JSON.parse(values.arguments); } catch { return {}; } })();
      const resp = await apiRequest<PolicyDecisionResponse>({
        path: "/api/v1/policy/simulate",
        method: "POST",
        tenantId,
        body: {
          capability: values.capability,
          risk_class: values.risk_class,
          side_effects: values.side_effects,
          arguments: parsedArgs,
        },
      });
      setResult(resp);
    } catch (error) {
      setResult(undefined);
      alert(error instanceof Error ? `模拟失败：${error.message}` : "模拟失败。");
    } finally { setSimulating(false); }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>active 策略集</span><strong>209</strong><small>policy.policy_sets 实测</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>疑似测试残留</span><strong style={{ color: "#f85149" }}>182</strong><small>待治理</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>生产策略集</span><strong>27</strong><small>209 - 182</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>策略网关</span><strong>已启用</strong><small>POST /policy/simulate</small></Card></Col>
      </Row>

      <Alert showIcon type="warning" message="182 个疑似测试残留策略集待治理"
        description="policy.policy_sets 表实测 active 209 个，其中约 182 个为种子测试残留（设计文档实测）。策略集逐行列表端点未接线，无法在此页面直接浏览。建议通过数据库直查或后续新建只读端点治理。" />

      <Card className="chart-card" title="策略集列表">
        <Alert showIcon type="info" message="策略集列表端点未接线"
          description="当前无 /api/v1/policy/sets 只读端点。策略集列表（name、version、status、rules 数、创建时间）无法展示。上方统计数字来自设计文档实测快照。" />
      </Card>

      <Card className="chart-card" title="策略模拟器" extra={<Tag className="gateway-tag">只模拟，不执行</Tag>}>
        <Alert showIcon type="info" message="本次模拟不会创建工具调用、审批或授权租约。" style={{ marginBottom: 12 }} />
        <Form
          className="policy-form"
          layout="vertical"
          form={form}
          initialValues={{ capability: "graph.neighbors.read", risk_class: "medium", side_effects: "read_only", arguments: "{}" }}
          onFinish={(values) => void simulate(values)}
        >
          <Form.Item label="能力" name="capability" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item label="风险等级" name="risk_class">
            <Select options={["read_only", "low", "medium", "high", "critical"].map((v) => ({ value: v, label: v }))} />
          </Form.Item>
          <Form.Item label="副作用" name="side_effects">
            <Select options={["read_only", "write_data", "external_action"].map((v) => ({ value: v, label: v }))} />
          </Form.Item>
          <Form.Item label="参数（JSON）" name="arguments" rules={[{ required: true }]}><Input.TextArea rows={5} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={simulating} disabled={!tenantId}>运行安全模拟</Button>
        </Form>

        {result ? (() => {
          const record = asRecord(result);
          const decision = String(record.decision ?? "");
          const tone = decision === "ALLOW" ? "success" : decision === "DENY" ? "error" : "warning";
          return <>
            <Alert className="search-alert" showIcon type={tone as "success" | "error" | "warning"}
              message={`模拟裁决：${decision}`}
              description={`风险分 ${String(record.risk_score ?? "—")} · ${String(record.reason ?? "")} · 策略版本 ${String(record.policy_version ?? "—")}`} />
            <pre className="result-box">{JSON.stringify(result, null, 2)}</pre>
          </>;
        })() : null}
      </Card>

      <Typography.Text type="secondary">策略集统计数字来自设计文档实测快照（2026-09-15）；模拟器调用 POST /api/v1/policy/simulate（不落盘）。</Typography.Text>
    </Space>
  );
}
