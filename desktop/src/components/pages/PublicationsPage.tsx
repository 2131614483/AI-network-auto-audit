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
 * 知识发布：哪些知识已发布。
 * 设计文档实测：change_sets 68 条（全 graph 类，experience 类 0 条）、releases 68（67 inactive / 1 active）。
 * 环 6 broken 的原因：experience 类变更集为 0。
 */
export default function PublicationsPage({ tenantId }: PageProps) {
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

  const ring6 = data?.rings.find((r) => r.ring === 6);
  const expChanges = Number(asRecord(ring6?.evidence ?? {}).experience_change_sets ?? 0);
  const allChanges = Number(asRecord(ring6?.evidence ?? {}).change_sets_all_types ?? 0);
  const releases = Number(asRecord(ring6?.evidence ?? {}).releases ?? 0);
  const graphChanges = allChanges - expChanges;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>change_sets 总数</span><strong>{fmtInt(allChanges)}</strong><small>knowledge.change_sets</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>graph 类</span><strong>{fmtInt(graphChanges)}</strong><small>change_type='graph'</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>experience 类</span><strong style={{ color: expChanges === 0 ? "#f85149" : undefined }}>{fmtInt(expChanges)}</strong><small>change_type='experience'</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>releases</span><strong>{fmtInt(releases)}</strong><small>knowledge.releases</small></Card></Col>
      </Row>

      <Card className="chart-card" title="发布状态总览" extra={<Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>}>
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          <Space wrap>
            <Tag className={ring6?.status === "ok" ? "ready-tag" : ring6?.status === "partial" ? "pending-tag" : "risk-high"}>
              {ring6?.status === "ok" ? "环 6 已通" : ring6?.status === "partial" ? "环 6 半通" : "环 6 断"}
            </Tag>
            <Tag className="pending-tag">graph 类变更集 {fmtInt(graphChanges)} 条</Tag>
            <Tag className={expChanges === 0 ? "risk-high" : "ready-tag"}>experience 类变更集 {fmtInt(expChanges)} 条</Tag>
            <Tag className="planned-tag">releases {fmtInt(releases)} 条</Tag>
          </Space>

          {expChanges === 0 ? (
            <Alert showIcon type="error" message="experience 类变更集 0 条——经验→知识发布通道从未产出"
              description={`环 6（知识发布）为 broken：change_sets 共 ${fmtInt(allChanges)} 条但 change_type='experience' 为 0。${ring6?.detail ?? ""}。releases ${fmtInt(releases)} 条均来自 graph 类变更集。`} />
          ) : (
            <Alert showIcon type="success" message="经验通道已有变更集"
              description={`experience 类变更集 ${fmtInt(expChanges)} 条 · 全部变更集 ${fmtInt(allChanges)} 条 · releases ${fmtInt(releases)} 条。`} />
          )}
        </Space>
      </Card>

      <Card className="chart-card" title="ChangeSet 明细">
        <Alert showIcon type="info" message="发布明细端点未接线"
          description="当前无 /api/v1/knowledge/change-sets 或 /api/v1/knowledge/releases 逐行只读端点。ChangeSet 列表（id、change_type、status、created_at、description）和 Release 列表（id、change_set_id、status、released_at、validation_result）无法展示。上方统计卡为 evolution 环 6 的汇总数字。" />
      </Card>

      <Typography.Text type="secondary">数据来源：/api/v1/experience/evolution 环 6（knowledge.change_sets 按 change_type 分组 + knowledge.releases 计数）。experience 类 0 条必须显式标注。</Typography.Text>
    </Space>
  );
}
