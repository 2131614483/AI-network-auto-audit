import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Space, Table, Tag, Typography } from "antd";
import { apiRequest, fmtInt, fmtTime, type PageProps } from "./common";

type HealthReady = {
  status: "ok" | "degraded";
  database: "ok" | "error";
  migration_head: string;
  worker_heartbeats: number;
  worker_stale: number;
  outbox_backlog: number;
  stuck_tasks: number;
  checks: string[];
};
type FailureGroup = {
  error_kind: string;
  signature: string;
  occurrences: number;
  recent_occurrences: number;
  plugins: Array<[string, number]>;
  first_seen: string | null;
  last_seen: string | null;
  spans_days: number;
  recurrent: boolean;
};
type FailureResponse = {
  totals: { attempts: number; failed: number; runs_with_failure: number };
  summary: { by_error_kind: Record<string, number>; recent_days: number };
  items: FailureGroup[];
};
type OperationsDetailMission = {
  id: string; title: string; domain: string; autonomy_mode: string; status: string;
  created_at: string;
  workflows: Array<{
    id: string; workflow_key: string; workflow_version: string; status: string;
    started_at: string | null; finished_at: string | null;
    tasks: Array<{
      id: string; node_key: string | null; capability: string; status: string;
      started_at: string | null; finished_at: string | null;
    }>;
  }>;
};
type OperationsDetailResponse = { missions: OperationsDetailMission[] };

/**
 * 调度与租约：谁在跑、租约有没有过期。
 * 上半=Worker 心跳 + 运行中的任务；下半=租约过期统计（lease_expired 错误聚类）。
 */
export default function SchedulesPage({ tenantId }: PageProps) {
  const [health, setHealth] = useState<HealthReady>();
  const [missions, setMissions] = useState<OperationsDetailMission[]>([]);
  const [leaseFailures, setLeaseFailures] = useState<FailureGroup[]>([]);
  const [leaseTotal, setLeaseTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const [healthPayload, detailPayload, failuresPayload] = await Promise.all([
        apiRequest<HealthReady>({ path: "/api/v1/health/ready" }),
        apiRequest<OperationsDetailResponse>({ path: "/api/v1/ui/operations/detail", tenantId: id }),
        apiRequest<FailureResponse>({
          path: "/api/v1/observability/failures",
          tenantId: id,
          query: { recent_days: 30, limit: 2000 },
        }),
      ]);
      setHealth(healthPayload);
      setMissions(detailPayload.missions ?? []);
      const leaseGroups = (failuresPayload.items ?? []).filter(
        (g) => g.error_kind === "lease_expired",
      );
      setLeaseFailures(leaseGroups);
      setLeaseTotal(
        (failuresPayload.summary?.by_error_kind?.["lease_expired"] ?? 0),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const runningMissions = useMemo(
    () => missions.filter((m) => m.status === "running" || m.status === "pending" || m.status === "queued"),
    [missions],
  );

  const runningWorkflows = useMemo(
    () => missions.flatMap((m) =>
      (m.workflows ?? [])
        .filter((w) => w.status === "running" || w.status === "pending" || w.status === "queued")
        .map((w) => ({ ...w, mission_title: m.title, mission_domain: m.domain })),
    ),
    [missions],
  );

  return (
    <>
      <section className="detail-head">
        <div>
          <Typography.Title level={3}>调度与租约</Typography.Title>
          <Typography.Text type="secondary">谁在跑、Worker 心跳是否新鲜、租约有没有过期</Typography.Text>
        </div>
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
      </section>

      {/* Worker 心跳概览 */}
      <section className="data-bar">
        <Card className="metric-card" size="small">
          <span>Worker 心跳</span>
          <strong>{health?.worker_heartbeats ?? "—"}</strong>
          <small>{health?.worker_stale ? `${health.worker_stale} 个超时 (>90s)` : "全部新鲜"}</small>
        </Card>
        <Card className="metric-card" size="small">
          <span>Outbox 积压</span>
          <strong>{health?.outbox_backlog ?? "—"}</strong>
          <small>未发布事件数</small>
        </Card>
        <Card className="metric-card" size="small">
          <span>租约过期卡住</span>
          <strong><Typography.Text type={health?.stuck_tasks ? "danger" : undefined}>{health?.stuck_tasks ?? "—"}</Typography.Text></strong>
          <small>running 且 lease 已过期</small>
        </Card>
        <Card className="metric-card" size="small">
          <span>近 30 天租约过期</span>
          <strong><Typography.Text type={leaseTotal ? "danger" : undefined}>{fmtInt(leaseTotal)}</Typography.Text></strong>
          <small>error_kind=lease_expired 聚类</small>
        </Card>
      </section>

      {health && health.status === "degraded" ? (
        <Alert type="warning" showIcon banner message="就绪状态降级" description={health.checks.join("；")} />
      ) : null}

      {/* 当前运行中的 Mission */}
      <Card className="chart-card" title="运行中的 Mission" extra={<Tag className="gateway-tag">来自控制平面投影</Tag>}>
        <Table<OperationsDetailMission>
          className="stock-table" size="small" rowKey="id"
          dataSource={runningMissions}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "Mission", dataIndex: "title", key: "title" },
            { title: "领域", dataIndex: "domain", key: "domain" },
            {
              title: "自治模式", dataIndex: "autonomy_mode", key: "autonomy_mode",
              render: (v: string) => <Tag className="gateway-tag">{v}</Tag>,
            },
            {
              title: "状态", dataIndex: "status", key: "status",
              render: (v: string) => <Tag className={v === "running" ? "ready-tag" : "pending-tag"}>{v}</Tag>,
            },
            { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (v: string) => fmtTime(v) },
          ]}
          locale={{ emptyText: "当前无运行中的任务。" }}
        />
      </Card>

      {/* 运行中的 Workflow */}
      {runningWorkflows.length > 0 ? (
        <Card className="chart-card" title="运行中的工作流" style={{ marginTop: 12 }}>
          <Table
            className="stock-table" size="small"
            rowKey="id"
            dataSource={runningWorkflows}
            pagination={{ pageSize: 10 }}
            columns={[
              { title: "工作流", dataIndex: "workflow_key", key: "workflow_key" },
              { title: "版本", dataIndex: "workflow_version", key: "workflow_version" },
              { title: "所属 Mission", dataIndex: "mission_title", key: "mission_title", ellipsis: true },
              {
                title: "状态", dataIndex: "status", key: "status",
                render: (v: string) => <Tag className={v === "running" ? "ready-tag" : "pending-tag"}>{v}</Tag>,
              },
              { title: "开始时间", dataIndex: "started_at", key: "started_at", render: (v: string | null) => v ? fmtTime(v) : "—" },
            ]}
          />
        </Card>
      ) : null}

      {/* 租约过期统计 */}
      <Card
        className="chart-card"
        title="租约过期统计"
        extra={<Tag className={leaseTotal ? "pending-tag" : "ready-tag"}>{leaseTotal ? "需关注" : "无过期"}</Tag>}
        style={{ marginTop: 12 }}
      >
        {leaseFailures.length === 0 ? (
          <div className="empty">近 30 天无租约过期记录。</div>
        ) : (
          <Table<FailureGroup>
            className="stock-table" size="small"
            rowKey="signature"
            dataSource={leaseFailures}
            pagination={{ pageSize: 10 }}
            columns={[
              { title: "错误特征", dataIndex: "signature", key: "signature", ellipsis: true },
              { title: "发生次数", dataIndex: "occurrences", key: "occurrences", width: 90, render: (v: number) => fmtInt(v) },
              { title: "近 7 天", dataIndex: "recent_occurrences", key: "recent_occurrences", width: 80, render: (v: number) => fmtInt(v) },
              {
                title: "涉及插件", dataIndex: "plugins", key: "plugins", width: 200,
                render: (plugins: Array<[string, number]>) =>
                  plugins.length ? plugins.map(([name, count]) => <Tag key={name} style={{ marginBottom: 2 }}>{name} ×{count}</Tag>) : "—",
              },
              { title: "首次", dataIndex: "first_seen", key: "first_seen", width: 160, render: (v: string | null) => fmtTime(v) },
              { title: "最近", dataIndex: "last_seen", key: "last_seen", width: 160, render: (v: string | null) => fmtTime(v) },
              {
                title: "持续天数", dataIndex: "spans_days", key: "spans_days", width: 80,
                render: (v: number) => `${v} 天`,
              },
            ]}
          />
        )}
      </Card>
    </>
  );
}
