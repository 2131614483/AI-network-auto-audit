import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Button, Card, Space, Table, Tag, Typography } from "antd";
import { apiRequest, fmtTime, type PageProps } from "./common";

type OperationsSummary = {
  counts: Record<string, number>;
  recent_decisions: Array<{
    capability: string; decision: string; risk_score: number; reason: string; trace_id: string; decided_at: string;
  }>;
  trace_id?: string;
};
type OperationsAgentRun = {
  id: string; role_key: string; model_key: string; status: string;
  token_input: number; token_output: number; cost: number;
  started_at: string | null; finished_at: string | null; error_detail: string | null;
};
type OperationsTaskRun = {
  id: string; node_key: string | null; capability: string; status: string;
  attempt: number; max_attempts: number; started_at: string | null; finished_at: string | null;
  error_detail: string | null; trace_id: string | null; agents: OperationsAgentRun[];
};
type OperationsWorkflowRun = {
  id: string; workflow_key: string; workflow_version: string; status: string;
  started_at: string | null; finished_at: string | null; trace_id: string | null; tasks: OperationsTaskRun[];
};
type OperationsDetailMission = {
  id: string; title: string; domain: string; autonomy_mode: string; status: string;
  created_at: string; workflows: OperationsWorkflowRun[];
};
type OperationsDetailResponse = { missions: OperationsDetailMission[]; trace_id?: string };

function runStatusTag(value: string): ReactNode {
  return (
    <Tag className={
      value === "completed" || value === "succeeded" || value === "ready"
        ? "ready-tag"
        : value === "failed"
          ? "pending-tag"
          : "gateway-tag"
    }>
      {value}
    </Tag>
  );
}

const METRICS: Array<[string, string]> = [
  ["Mission", "missions"],
  ["工作流运行", "workflow_runs"],
  ["任务运行", "task_runs"],
  ["Agent 运行", "agent_runs"],
];

/**
 * 任务编排（原 App.tsx operations 视图提级）。
 * Mission → Workflow → Task → Agent 的持久运行投影 + 最近策略裁决。只读。
 */
export default function OperationsPage({ tenantId }: PageProps) {
  const [summary, setSummary] = useState<OperationsSummary>();
  const [detail, setDetail] = useState<OperationsDetailMission[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const [summaryPayload, detailPayload] = await Promise.all([
        apiRequest<OperationsSummary>({ path: "/api/v1/ui/operations", tenantId: id }),
        apiRequest<OperationsDetailResponse>({ path: "/api/v1/ui/operations/detail", tenantId: id }),
      ]);
      setSummary(summaryPayload);
      setDetail(detailPayload.missions ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  return (
    <>
      <section className="detail-head">
        <div>
          <Typography.Title level={3}>任务编排</Typography.Title>
          <Typography.Text type="secondary">Mission → Workflow → Task → Agent 的持久运行投影</Typography.Text>
        </div>
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新运行投影</Button>
      </section>

      <section className="data-bar">
        {METRICS.map(([label, key]) => (
          <Card className="metric-card" size="small" key={key}>
            <span>{label}</span>
            <strong>{summary?.counts[key] ?? 0}</strong>
            <small>当前租户 · 实时数据库</small>
          </Card>
        ))}
      </section>

      <Card className="chart-card" title="最近策略裁决" extra={<Tag className="gateway-tag">来源 / 规则 / 结果</Tag>}>
        <Table
          className="stock-table" size="small"
          rowKey={(row) => `${row.trace_id}-${row.capability}`}
          dataSource={summary?.recent_decisions ?? []}
          pagination={{ pageSize: 12 }}
          columns={[
            { title: "时间", dataIndex: "decided_at", key: "decided_at", render: (v: string) => fmtTime(v) },
            { title: "能力", dataIndex: "capability", key: "capability" },
            {
              title: "结果", dataIndex: "decision", key: "decision",
              render: (v: string) => <Tag className={v === "ALLOW" ? "ready-tag" : "pending-tag"}>{v}</Tag>,
            },
            { title: "风险分", dataIndex: "risk_score", key: "risk_score" },
            { title: "规则说明", dataIndex: "reason", key: "reason", ellipsis: true },
            { title: "Trace", dataIndex: "trace_id", key: "trace_id", ellipsis: true },
          ]}
          locale={{ emptyText: "暂无策略裁决记录" }}
        />
      </Card>

      <Card className="chart-card" title="运行投影明细" extra={<Tag className="gateway-tag">Mission → Workflow → Task → Agent</Tag>}>
        <Table<OperationsDetailMission>
          className="stock-table" size="small" rowKey="id"
          dataSource={detail}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "Mission", dataIndex: "title", key: "title" },
            { title: "领域", dataIndex: "domain", key: "domain" },
            {
              title: "自治模式", dataIndex: "autonomy_mode", key: "autonomy_mode",
              render: (v: string) => <Tag className="gateway-tag">{v}</Tag>,
            },
            { title: "状态", dataIndex: "status", key: "status", render: runStatusTag },
            { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (v: string) => fmtTime(v) },
          ]}
          expandable={{
            expandedRowRender: (mission) => (
              <Table<OperationsWorkflowRun>
                className="nested-table" size="small" rowKey="id"
                dataSource={mission.workflows} pagination={false}
                columns={[
                  { title: "工作流", dataIndex: "workflow_key", key: "workflow_key" },
                  { title: "版本", dataIndex: "workflow_version", key: "workflow_version" },
                  { title: "状态", dataIndex: "status", key: "status", render: runStatusTag },
                  { title: "开始", dataIndex: "started_at", key: "started_at", render: (v: string | null) => v ? fmtTime(v) : "—" },
                  { title: "结束", dataIndex: "finished_at", key: "finished_at", render: (v: string | null) => v ? fmtTime(v) : "—" },
                  { title: "Trace", dataIndex: "trace_id", key: "trace_id", ellipsis: true, render: (v: string | null) => v ?? "—" },
                ]}
                expandable={{
                  expandedRowRender: (wf) => (
                    <Table<OperationsTaskRun>
                      className="nested-table" size="small" rowKey="id"
                      dataSource={wf.tasks} pagination={false}
                      columns={[
                        { title: "节点", dataIndex: "node_key", key: "node_key", render: (v: string | null) => v ?? "—" },
                        { title: "能力", dataIndex: "capability", key: "capability" },
                        { title: "状态", dataIndex: "status", key: "status", render: runStatusTag },
                        { title: "尝试", key: "attempt", render: (_v, row) => `${row.attempt}/${row.max_attempts}` },
                        { title: "开始", dataIndex: "started_at", key: "started_at", render: (v: string | null) => v ? fmtTime(v) : "—" },
                        { title: "结束", dataIndex: "finished_at", key: "finished_at", render: (v: string | null) => v ? fmtTime(v) : "—" },
                        { title: "错误", dataIndex: "error_detail", key: "error_detail", ellipsis: true, render: (v: string | null) => v ?? "—" },
                      ]}
                      expandable={{
                        expandedRowRender: (task) => (
                          <Table<OperationsAgentRun>
                            className="nested-table" size="small" rowKey="id"
                            dataSource={task.agents} pagination={false}
                            columns={[
                              { title: "角色", dataIndex: "role_key", key: "role_key" },
                              { title: "模型", dataIndex: "model_key", key: "model_key" },
                              { title: "状态", dataIndex: "status", key: "status", render: runStatusTag },
                              { title: "Token 入/出", key: "tokens", render: (_v, row) => `${row.token_input} / ${row.token_output}` },
                              { title: "成本", dataIndex: "cost", key: "cost", render: (v: number) => v.toFixed(4) },
                              { title: "开始", dataIndex: "started_at", key: "started_at", render: (v: string | null) => v ? fmtTime(v) : "—" },
                              { title: "结束", dataIndex: "finished_at", key: "finished_at", render: (v: string | null) => v ? fmtTime(v) : "—" },
                            ]}
                            locale={{ emptyText: "暂无 Agent 运行记录" }}
                          />
                        ),
                      }}
                      locale={{ emptyText: "暂无任务运行记录" }}
                    />
                  ),
                }}
                locale={{ emptyText: "暂无工作流运行记录" }}
              />
            ),
          }}
          locale={{ emptyText: "暂无 Mission 运行投影；点击刷新从控制平面拉取。" }}
        />
      </Card>
    </>
  );
}
