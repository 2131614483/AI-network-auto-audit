import { Alert, Card, Descriptions, Empty, Space, Tag, Typography } from "antd";
import { apiRequest, type PageProps } from "./common";

/**
 * 连通性与供需闭合（新页）。
 * 回答"网连起来了吗、谁悬空"。
 * 当前无专用 API 端点；展示脚本路径和使用说明，诚实标注未接线。
 */
export default function ConnectivityPage({ tenantId }: PageProps) {
  // 尝试探测是否有端点存在；404 或网络错误时走未接线态
  void apiRequest({ path: "/api/v1/connectivity/report", tenantId }).catch(() => {
    /* 预期：端点未接线 */
  });

  return (
    <>
      <section className="detail-head">
        <div>
          <Typography.Title level={3}>连通性与供需闭合</Typography.Title>
          <Typography.Text type="secondary">插件 × 能力供需闭合矩阵 · 悬空插件 · 连通性评分</Typography.Text>
        </div>
      </section>

      <Alert
        type="warning"
        showIcon
        banner
        message="连通性报告端点未接线"
        description="当前没有 /api/v1/connectivity/report 只读端点。以下为离线脚本口径说明。"
      />

      <Card
        className="chart-card"
        title="供需闭合矩阵"
        style={{ marginTop: 12 }}
        extra={<Tag className="pending-tag">待治理</Tag>}
      >
        <Empty description={
          <Space direction="vertical">
            <Typography.Text>连通性数据未接线。运行以下脚本生成报告：</Typography.Text>
            <pre className="result-box" style={{ textAlign: "left" }}>
{`.\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py
.\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py --lifecycle verified
.\\.venv\\Scripts\\python.exe scripts\\report-connectivity.py --include-invokes`}
            </pre>
          </Space>
        } />
      </Card>

      <Card
        className="chart-card"
        title="悬空插件与孤岛"
        style={{ marginTop: 12 }}
        extra={<Tag className="pending-tag">待治理</Tag>}
      >
        <Descriptions column={1} size="small" bordered items={[
          {
            key: "script",
            label: "报告脚本",
            children: <code>scripts/report-connectivity.py</code>,
          },
          {
            key: "metrics",
            label: "输出指标",
            children: "plugins / edges / isolated_nodes / no_incoming / no_outgoing / weakly_connected_components / dead_end_outputs / unfillable_inputs / seeds",
          },
          {
            key: "baseline",
            label: "基线对比",
            children: <><code>--write-baseline path.json</code> 冻结口径；<code>--baseline path.json</code> 对比回归（CI 闸门）</>,
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
