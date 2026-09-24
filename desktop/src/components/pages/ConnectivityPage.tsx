import { Alert, Card, Descriptions, Select, Space, Switch, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, fmtInt, type PageProps } from "./common";

/**
 * 连通性与供需闭合：回答"网连起来了吗、谁悬空"。
 *
 * 数据来自 `GET /api/v1/connectivity/report`，而该端点与离线脚本
 * `scripts/report-connectivity.py` **共用同一份口径**
 * （`packages/catalog/connectivity_view.py`）——看板与 CI 基线闸门不会对同一张网
 * 给出相反结论。口径开关（lifecycle / invokes）会改变数字但**不可比**，所以口径由
 * 端点随结果一起返回，页面必须显式显示它。
 */
type ConnectivityMetrics = {
  edge_set: string;
  plugins: number;
  edges: number;
  isolated_nodes: number;
  no_incoming: number;
  no_outgoing: number;
  weakly_connected_components: number;
  component_sizes: number[];
  dead_end_outputs: number;
  unfillable_inputs: number;
  reusable_contracts: number;
  total_input_ports: number;
  unfillable_input_ports: number;
  seeds: number;
  pure_seed_nodes: number;
};
type IslandCategory = { category: string; label: string; count: number };
type Island = { plugin_id: string; role: string; category: string; category_zh: string; reason: string; detail: string };
type ConnectivityReport = {
  scope: { lifecycle: string | null; include_invokes: boolean; label: string };
  metrics: ConnectivityMetrics;
  islands_total: number;
  islands_present: IslandCategory[];
  islands: Island[];
};

const LIFECYCLE_OPTIONS = [
  { value: "", label: "全部（含 contract_only）" },
  { value: "verified", label: "仅 verified（可运行）" },
  { value: "contract_only", label: "仅 contract_only" },
];

export default function ConnectivityPage({ tenantId, onNotice }: PageProps) {
  const [report, setReport] = useState<ConnectivityReport>();
  const [lifecycle, setLifecycle] = useState("");
  const [includeInvokes, setIncludeInvokes] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string>();

  const load = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    try {
      const payload = await apiRequest<ConnectivityReport>({
        path: "/api/v1/connectivity/report",
        tenantId,
        query: { lifecycle: lifecycle || undefined, include_invokes: includeInvokes },
      });
      setReport(payload);
      setFailure(undefined);
    } catch (error) {
      // 端点不可达时如实说明原因，并保留离线脚本这条路 —— 不假装有数据。
      const message = error instanceof Error ? error.message : "连通性报告加载失败。";
      setFailure(message);
      setReport(undefined);
      onNotice?.(`连通性报告加载失败：${message}`);
    } finally {
      setLoading(false);
    }
  }, [tenantId, lifecycle, includeInvokes, onNotice]);

  useEffect(() => { void load(); }, [load]);

  const metrics = report?.metrics;

  const metricCards: Array<{ label: string; value: number | undefined; note: string }> = metrics ? [
    { label: "插件数", value: metrics.plugins, note: `输入端口 ${fmtInt(metrics.total_input_ports)}` },
    { label: "边数", value: metrics.edges, note: `图口径 ${metrics.edge_set}` },
    { label: "完全孤立", value: metrics.isolated_nodes, note: "无任何入边与出边" },
    { label: "无入边", value: metrics.no_incoming, note: "没有上游供给" },
    { label: "无出边", value: metrics.no_outgoing, note: "产出无人消费" },
    { label: "弱连通分量", value: metrics.weakly_connected_components, note: `最大分量 ${fmtInt(metrics.component_sizes[0])}` },
    { label: "死端输出契约", value: metrics.dead_end_outputs, note: "产出契约无人接收" },
    { label: "不可填输入契约", value: metrics.unfillable_inputs, note: `不可填输入端口 ${fmtInt(metrics.unfillable_input_ports)}` },
    { label: "种子点", value: metrics.seeds, note: `纯 seed 节点 ${fmtInt(metrics.pure_seed_nodes)}` },
    { label: "可复用契约", value: metrics.reusable_contracts, note: "被多个插件共用" },
  ] : [];

  return (
    <>
      <section className="detail-head">
        <div>
          <Typography.Title level={3}>连通性与供需闭合</Typography.Title>
          <Typography.Text type="secondary">
            插件 × 能力供需闭合矩阵 · 悬空插件 · 连通性评分（只读文件系统投影，不执行任何插件）
          </Typography.Text>
        </div>
        <Space>
          <Select
            aria-label="插件生命周期口径"
            value={lifecycle}
            onChange={setLifecycle}
            options={LIFECYCLE_OPTIONS}
            style={{ width: 210 }}
          />
          <Space size={6}>
            <Switch aria-label="计入 invokes 跨层调用" checked={includeInvokes} onChange={setIncludeInvokes} />
            <Typography.Text type="secondary">计入 invokes</Typography.Text>
          </Space>
        </Space>
      </section>

      {failure ? (
        <Alert
          type="error"
          showIcon
          banner
          message="连通性报告端点不可用"
          description={<Space direction="vertical" size={4}>
            <Typography.Text>{failure}</Typography.Text>
            <Typography.Text type="secondary">
              离线口径仍可用：<code>.\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py</code>
              （可加 <code>--json</code>、<code>--baseline path.json</code> 做 CI 回归闸门）
            </Typography.Text>
          </Space>}
        />
      ) : null}

      <section className="data-bar" style={{ marginTop: 12 }}>
        {metricCards.map((card) => (
          <Card className="metric-card" size="small" key={card.label}>
            <span>{card.label}</span>
            <strong>{card.value === undefined ? "—" : fmtInt(card.value)}</strong>
            <small>{card.note}</small>
          </Card>
        ))}
      </section>

      <Card
        className="chart-card"
        title="当前口径"
        style={{ marginTop: 12 }}
        extra={<Tag className="gateway-tag">只读快照</Tag>}
      >
        <Descriptions column={1} size="small" items={[
          {
            key: "scope",
            label: "口径",
            children: report ? <Tag className="ready-tag">{report.scope.label}</Tag> : (loading ? "加载中…" : "—"),
          },
          {
            key: "comparability",
            label: "可比性",
            children: "不同口径（lifecycle / 是否计入 invokes）下的数字**不可比**——CI 基线与页面必须用同一口径，否则会得出相反结论。",
          },
          {
            key: "stability",
            label: "排序稳定性",
            children: report ? `${report.scope.label} · 孤岛 ${fmtInt(report.islands_total)} 个，每个都有分类依据（不是"没连上"了事）` : "—",
          },
        ]} />
      </Card>

      <Card
        className="chart-card"
        title="悬空插件与孤岛"
        style={{ marginTop: 12 }}
        extra={report ? <Tag className={report.islands_total ? "pending-tag" : "ready-tag"}>
          {report.islands_present.map((entry) => `${entry.label} ${entry.count}`).join(" · ") || "无悬空插件"}
        </Tag> : null}
      >
        <Table<Island>
          className="stock-table"
          size="small"
          rowKey="plugin_id"
          loading={loading}
          dataSource={report?.islands ?? []}
          pagination={{ pageSize: 12 }}
          locale={{ emptyText: loading ? "正在计算连通性…" : "当前口径下没有悬空插件。" }}
          columns={[
            { title: "插件", dataIndex: "plugin_id", key: "plugin_id", ellipsis: true },
            { title: "角色", dataIndex: "role", key: "role", width: 110 },
            {
              title: "分类", dataIndex: "category_zh", key: "category_zh", width: 180,
              render: (value: string, row) => <Tag className={row.category === "missing_upstream" ? "risk-high" : "pending-tag"}>{value}</Tag>,
            },
            { title: "依据", dataIndex: "reason", key: "reason", ellipsis: true },
            { title: "细节", dataIndex: "detail", key: "detail", ellipsis: true, render: (value: string) => value || "—" },
          ]}
        />
      </Card>

      <Card className="chart-card" title="离线口径与 CI 闸门" style={{ marginTop: 12 }}>
        <Descriptions column={1} size="small" bordered items={[
          { key: "script", label: "报告脚本", children: <code>scripts/report-connectivity.py</code> },
          {
            key: "shared",
            label: "与页面的关系",
            children: "脚本与本页共用 packages/catalog/connectivity_view.py 一份口径实现，不会各自算出不同结论。",
          },
          {
            key: "baseline",
            label: "基线对比",
            children: <><code>--write-baseline path.json</code> 冻结口径；<code>--baseline path.json</code> 对比回归（有回归则非零退出）</>,
          },
          {
            key: "provenance",
            label: "溯源审计",
            children: <><code>--attempts runs.json</code> 校验"输入 sha256 == 上游输出 sha256"</>,
          },
        ]} />
      </Card>
    </>
  );
}
