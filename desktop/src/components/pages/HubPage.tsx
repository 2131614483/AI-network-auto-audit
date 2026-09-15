import { ArrowRightOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Row, Space, Spin, Tag, Typography } from "antd";
import * as echarts from "echarts";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest, asRecord, fmtInt, HealthLamp, type PageProps } from "./common";

type KnowledgeStats = {
  documents: number; chunks: number; embedding_chunks: number;
  embedding_coverage: number; vector_ready: boolean; embedding_model: string | null;
};
type RunsSummary = {
  runs_listed: number; runs_with_bundle: number; runs_without_bundle: number;
  bundles_unreadable: number; linked_to_project: number; unlinked: number;
};
type FailureItem = { error_kind: string; occurrences: number };
type FailureResponse = {
  totals: { failed: number; attempts: number };
  summary: { by_error_kind: Record<string, number>; groups: number };
  items: FailureItem[];
};
type EvolutionRing = { ring: number; name: string; status: "ok" | "partial" | "broken"; evidence?: Record<string, number>; detail: string };
type EvolutionResponse = {
  rings: EvolutionRing[];
  weakest: string | null;
  summary: { ok: number; partial: number; broken: number; loop_closed: boolean };
};

export default function HubPage({ tenantId, onNotice, onNavigate }: PageProps) {
  const [stats, setStats] = useState<KnowledgeStats>();
  const [runsSummary, setRunsSummary] = useState<RunsSummary>();
  const [failures, setFailures] = useState<FailureResponse>();
  const [evolution, setEvolution] = useState<EvolutionResponse>();
  const [approvalCount, setApprovalCount] = useState<number>();
  const [loading, setLoading] = useState(true);
  const chartRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    try {
      const [statsResp, runsResp, failuresResp, evolutionResp, approvalsResp] = await Promise.all([
        apiRequest({ path: "/api/v1/knowledge/stats", tenantId }),
        apiRequest<{ summary: RunsSummary }>({ path: "/api/v1/observability/runs", tenantId, query: { limit: 200 } }),
        apiRequest<FailureResponse>({ path: "/api/v1/observability/failures", tenantId, query: { recent_days: 30, limit: 2000 } }),
        apiRequest<EvolutionResponse>({ path: "/api/v1/experience/evolution", tenantId }),
        apiRequest<{ items: unknown[] }>({ path: "/api/v1/approvals", tenantId }),
      ]);
      setStats(statsResp as KnowledgeStats);
      setRunsSummary(runsResp.summary);
      setFailures(failuresResp);
      setEvolution(evolutionResp);
      setApprovalCount((approvalsResp.items ?? []).length);
    } catch (error) {
      onNotice?.(error instanceof Error ? `信息中枢加载部分失败：${error.message}` : "信息中枢加载部分失败。");
    } finally {
      setLoading(false);
    }
  }, [tenantId, onNotice]);

  useEffect(() => { void load(); }, [load]);

  // 失败趋势 mini bar：按 error_kind 聚合计数（近 30 日快照）。
  useEffect(() => {
    if (!chartRef.current || !failures) return;
    const chart = echarts.init(chartRef.current, "dark");
    const byKind = failures.summary.by_error_kind ?? {};
    const entries = Object.entries(byKind).sort((a, b) => b[1] - a[1]);
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 48, right: 12, top: 24, bottom: 24 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: entries.map(([kind]) => kind), axisLabel: { color: "rgba(255,255,255,0.65)" } },
      yAxis: { type: "value", splitLine: { lineStyle: { color: "rgba(255,255,255,0.12)" } }, axisLabel: { color: "rgba(255,255,255,0.65)" } },
      series: [{ type: "bar", data: entries.map(([, count]) => count), itemStyle: { color: "#f85149" }, barWidth: 28 }],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); chart.dispose(); };
  }, [failures]);

  const coverage = stats?.embedding_coverage ?? 0;
  const vectorState = !stats ? "partial" : !stats.vector_ready ? "broken" : coverage >= 0.95 ? "ok" : "partial";
  const evidenceState = !runsSummary ? "partial" : runsSummary.bundles_unreadable > 0 ? "broken" : runsSummary.runs_with_bundle > 0 ? "ok" : "partial";
  const suggestionCount = evolution?.rings.find((ring) => ring.ring === 4)?.evidence;
  const suggestionTotal = suggestionTotalOf(suggestionCount);
  const suggestionState = !evolution ? "partial" : suggestionTotal > 0 ? "ok" : "broken";

  const sixCards: Array<{ label: string; value: string; note: string; to: string }> = [
    { label: "运行产物", value: runsSummary ? fmtInt(runsSummary.runs_listed) : "—", note: `证据包 ${fmtInt(runsSummary?.runs_with_bundle)} · 未挂锚 ${fmtInt(runsSummary?.unlinked)}`, to: "runs" },
    { label: "失败", value: failures ? fmtInt(failures.totals.failed) : "—", note: `${failures ? fmtInt(failures.summary.groups) : "—"} 个签名簇（近 30 日）`, to: "diagnose" },
    { label: "文档", value: stats ? fmtInt(stats.documents) : "—", note: `分块 ${stats ? fmtInt(stats.chunks) : "—"} · 向量覆盖 ${(coverage * 100).toFixed(1)}%`, to: "knowledge" },
    { label: "经验环", value: evolution ? `${evolution.summary.ok}/${evolution.rings.length}` : "—", note: `半通 ${evolution?.summary.partial ?? 0} · 断 ${evolution?.summary.broken ?? 0}`, to: "evolution" },
    { label: "建议", value: fmtInt(suggestionTotal), note: suggestionTotal > 0 ? "已有候选关系" : "从未产出（环 4）", to: "suggestions" },
    { label: "审批决策", value: fmtInt(approvalCount), note: "控制平面审批账本", to: "approvals" },
  ];

  return (
    <div className="hub-page">
      <section className="detail-head">
        <div>
          <Typography.Title level={3}>信息中枢</Typography.Title>
          <Typography.Text type="secondary">系统现在什么状态、哪一环断了。所有数字均为只读快照；不假装正常——向量不足 / 证据缺失 / 断环都显形。</Typography.Text>
        </div>
        <Button size="small" onClick={() => void load()}>刷新</Button>
      </section>

      <Row gutter={[12, 12]}>
        {sixCards.map((card) => (
          <Col xs={12} sm={8} md={4} key={card.label}>
            <Card className="metric-card hub-count-card" size="small">
              <span>{card.label}</span>
              <strong>{card.value}</strong>
              <small>{card.note}</small>
              <Button type="link" size="small" style={{ padding: 0, float: "right" }} onClick={() => onNavigate?.(card.to)}>查看详情 <ArrowRightOutlined /></Button>
            </Card>
          </Col>
        ))}
      </Row>

      <Card className="chart-card" title="健康灯（诚实化口径）" extra={<Tag className="gateway-tag">只读快照 · 不补齐缺失</Tag>} style={{ marginTop: 12 }}>
        {loading && !stats ? <Spin /> : (
          <Space size="large" wrap>
            <HealthLamp state={vectorState} label="向量覆盖率" hint={!stats ? "未加载" : !stats.vector_ready ? "全文降级" : `${(coverage * 100).toFixed(1)}% · ${stats.embedding_model ?? "模型未配置"}`} />
            <HealthLamp state={evidenceState} label="证据包完整性" hint={!runsSummary ? "未加载" : runsSummary.bundles_unreadable > 0 ? `${runsSummary.bundles_unreadable} 个不可读` : `已挂包 ${runsSummary.runs_with_bundle}/${runsSummary.runs_listed}`} />
            <HealthLamp state={suggestionState} label="建议通道" hint={!evolution ? "未加载" : suggestionTotal > 0 ? "有产出" : "从未产出"} />
            <HealthLamp state="partial" label="策略集卫生" hint="未接线（无 dashboard 端点；209 个策略集需人工巡检）" />
          </Space>
        )}
      </Card>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} lg={12}>
          <Card className="chart-card" title="七环进化闭环" extra={<Button type="link" size="small" style={{ padding: 0 }} onClick={() => onNavigate?.("evolution")}>看板 <ArrowRightOutlined /></Button>}>
            {loading && !evolution ? <Spin /> : evolution ? (
              <>
                <Space wrap>
                  {evolution.rings.map((ring) => (
                    <Tag key={ring.ring} className={ring.status === "ok" ? "ready-tag" : ring.status === "partial" ? "pending-tag" : "risk-high"}>
                      {ring.ring}. {ring.name}
                    </Tag>
                  ))}
                </Space>
                <Alert
                  style={{ marginTop: 8 }}
                  showIcon
                  type={evolution.summary.loop_closed ? "success" : "warning"}
                  message={evolution.summary.loop_closed ? "闭环已通" : `最弱一环：${evolution.weakest ?? "未知"}`}
                  description={`已通 ${evolution.summary.ok} · 半通 ${evolution.summary.partial} · 断 ${evolution.summary.broken}；环 6/7 为代码事实断点，界面不掩盖。`}
                />
              </>
            ) : <div className="empty">进化闭环数据未加载。</div>}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card className="chart-card" title="失败趋势（近 30 日 · 按 error_kind）" extra={<Button type="link" size="small" style={{ padding: 0 }} onClick={() => onNavigate?.("diagnose")}>诊断 <ArrowRightOutlined /></Button>}>
            {failures ? <div ref={chartRef} style={{ height: 220 }} /> : <div className="empty">暂无失败数据。</div>}
          </Card>
        </Col>
      </Row>

      <Alert style={{ marginTop: 12 }} type="info" showIcon message="文档/经验的深度操作仍在各自专页：本中枢只放计数与深链，不重复实现。" />
    </div>
  );
}

function suggestionTotalOf(value: unknown): number {
  if (typeof value !== "object" || value === null) return 0;
  const record = value as Record<string, unknown>;
  const total = record.relation_suggestions;
  return typeof total === "number" ? total : 0;
}
