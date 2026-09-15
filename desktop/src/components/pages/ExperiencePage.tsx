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
 * 经验观测与统计：系统学到了什么。
 * 数据源：/api/v1/experience/evolution 环 2（观测行）和环 3（统计行）。
 * 明细端点（edge_stats / node_stats 逐行）未接线——如实标注。
 */
export default function ExperiencePage({ tenantId }: PageProps) {
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

  const ring2 = data?.rings.find((r) => r.ring === 2);
  const ring3 = data?.rings.find((r) => r.ring === 3);
  const edgeObs = Number(asRecord(ring2?.evidence ?? {}).edge_observations ?? 0);
  const nodeObs = Number(asRecord(ring2?.evidence ?? {}).node_observations ?? 0);
  const edgeStats = Number(asRecord(ring3?.evidence ?? {}).edge_stats ?? 0);
  const nodeStats = Number(asRecord(ring3?.evidence ?? {}).node_stats ?? 0);
  const wired = Boolean(asRecord(ring2?.evidence ?? {}).wired_into_dag_path);

  const noObs = edgeObs === 0 && nodeObs === 0;
  const noStats = edgeStats === 0 && nodeStats === 0;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>edge_observations</span><strong>{fmtInt(edgeObs)}</strong><small>边观测行数</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>node_observations</span><strong>{fmtInt(nodeObs)}</strong><small>节点观测行数</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>edge_stats</span><strong>{fmtInt(edgeStats)}</strong><small>边统计行数</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>node_stats</span><strong>{fmtInt(nodeStats)}</strong><small>节点统计行数</small></Card></Col>
      </Row>

      <Card className="chart-card" title="观测与统计状态" extra={<Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>}>
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          <Space wrap>
            <Tag className={wired ? "ready-tag" : "risk-high"}>投影器{wired ? "已挂接 DAG 路径" : "未挂接 DAG 路径"}</Tag>
            <Tag className={noObs ? "pending-tag" : "ready-tag"}>{noObs ? "无观测数据" : `观测 ${fmtInt(edgeObs + nodeObs)} 行`}</Tag>
            <Tag className={noStats ? "pending-tag" : "ready-tag"}>{noStats ? "无统计数据" : `统计 ${fmtInt(edgeStats + nodeStats)} 行`}</Tag>
          </Space>

          {noObs ? (
            <Alert showIcon type="warning" message="无经验观测数据——投影器已挂接但主库无新运行"
              description={ring2?.detail ?? "环 2（事实投影）为 partial：机制存在但本租户尚无运行产生观测行。"} />
          ) : (
            <Alert showIcon type="success" message="观测行已落库"
              description={`edge_observations ${fmtInt(edgeObs)} 行 · node_observations ${fmtInt(nodeObs)} 行。${ring2?.detail ?? ""}`} />
          )}

          {noStats ? (
            <Alert showIcon type="info" message="无经验统计数据"
              description={ring3?.detail ?? "环 3（统计聚合）为 partial：统计层可由观测重建，但当前快照行数为 0。"} />
          ) : (
            <Alert showIcon type="success" message="统计行已落库"
              description={`edge_stats ${fmtInt(edgeStats)} 行 · node_stats ${fmtInt(nodeStats)} 行。`} />
          )}
        </Space>
      </Card>

      <Card className="chart-card" title="经验明细（Top 经验边）">
        <Alert showIcon type="info" message="经验明细端点未接线"
          description="当前无 /api/v1/experience/edges 或 /api/v1/experience/stats 逐行只读端点。上方统计卡为 evolution 环 2/3 的汇总数字。如需查看 Top 经验边（源插件→目标插件、use_count、success_count、confidence、weight），需新建只读端点。" />
      </Card>

      <Typography.Text type="secondary">数据来源：/api/v1/experience/evolution 环 2（experience.node_observations / edge_observations）+ 环 3（experience.node_stats / edge_stats）。明细逐行查询未接线。</Typography.Text>
    </Space>
  );
}
