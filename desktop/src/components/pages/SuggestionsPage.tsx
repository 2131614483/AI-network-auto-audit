import { Alert, Button, Card, Col, Row, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, asRecord, fmtInt, type PageProps } from "./common";

type EvolutionRing = {
  ring: number; name: string; status: "ok" | "partial" | "broken";
  evidence: Record<string, number | boolean>; detail: string; checked: string | null;
};
type EvolutionResponse = {
  rings: EvolutionRing[];
  weakest: string | null;
  summary: { ok: number; partial: number; broken: number; archive_links: number; loop_closed: boolean };
};

/**
 * 建议与决策：有哪些待决策建议。
 * 设计文档实测：relation_suggestions 0 行（环 4 从未产出）。
 * 如实显示"从未产出"，不用示例数据填充。
 */
export default function SuggestionsPage({ tenantId }: PageProps) {
  const [data, setData] = useState<EvolutionResponse>();
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<EvolutionResponse>({ path: "/api/v1/experience/evolution", tenantId: id });
      setData(payload);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const ring4 = data?.rings.find((r) => r.ring === 4);
  const ring5 = data?.rings.find((r) => r.ring === 5);
  const total = Number(asRecord(ring4?.evidence ?? {}).relation_suggestions ?? 0);
  const decided = Number(asRecord(ring5?.evidence ?? {}).decided ?? 0);
  const proposed = total - decided;
  const accepted = 0;
  const dismissed = 0;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>proposed</span><strong>{fmtInt(proposed)}</strong><small>待决策</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>accepted</span><strong>{fmtInt(accepted)}</strong><small>已采纳</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>dismissed</span><strong>{fmtInt(dismissed)}</strong><small>已驳回</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>总计</span><strong>{fmtInt(total)}</strong><small>relation_suggestions 行数</small></Card></Col>
      </Row>

      <Card className="chart-card" title="建议列表" extra={<Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>}>
        {total === 0 ? (
          <div className="empty">
            <Space direction="vertical" size={8} style={{ width: "100%", textAlign: "center" }}>
              <Typography.Text strong style={{ fontSize: 16 }}>从未产出建议。</Typography.Text>
              <Typography.Text type="secondary">环 4（建议产生）当前为断——设计外协作关系未达统计门槛。</Typography.Text>
              <Typography.Text type="secondary">relation_suggestions 表当前 0 行；环 5（人工决策）因此也无建议可决策。</Typography.Text>
            </Space>
          </div>
        ) : (
          <Alert showIcon type="success" message={`共 ${fmtInt(total)} 条建议`} description={`已决策 ${fmtInt(decided)} 条 · 待决策 ${fmtInt(proposed)} 条。`} />
        )}
      </Card>

      <Card className="chart-card" title="建议明细端点">
        <Alert showIcon type="info" message="建议端点未接线"
          description="当前无 /api/v1/experience/suggestions 逐行只读端点。建议列表（source_plugin_id、target_plugin_id、contract_id、status、evidence_count、created_at、decided_at）无法展示。上方统计卡为 evolution 环 4/5 的汇总数字。" />
      </Card>

      <Typography.Text type="secondary">数据来源：/api/v1/experience/evolution 环 4（experience.relation_suggestions 计数）+ 环 5（已决策计数）。0 行就是 0 行，不用示例数据填充。</Typography.Text>
    </Space>
  );
}
