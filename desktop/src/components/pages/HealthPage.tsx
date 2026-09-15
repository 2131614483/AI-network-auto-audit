import { Button, Card, Space, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, asRecord, fmtInt, type PageProps } from "./common";

type HealthReady = {
  status: "ok" | "degraded";
  database: "ok" | "error";
  migration_head: string;
  migration_expected: string;
  worker_heartbeats: number;
  worker_stale: number;
  outbox_backlog: number;
  stuck_tasks: number;
  checks: string[];
  trace_id: string;
};
type KnowledgeStats = {
  documents: number; chunks: number; embedding_chunks: number;
  embedding_model: string | null; embedding_coverage: number;
  waiting_extractor: number; failed_files: number; vector_ready: boolean;
};
type RunsSummary = {
  summary: { runs_listed: number; runs_with_bundle: number; runs_without_bundle: number; orphan_bundles: number };
};

type CheckState = "ok" | "warn" | "error" | "unwired";
type HealthCheckRow = {
  key: string; name: string; state: CheckState; detail: string; source: string;
};

const STATE_TAG: Record<CheckState, { className: string; label: string }> = {
  ok: { className: "ready-tag", label: "✅ 通过" },
  warn: { className: "pending-tag", label: "⚠️ 警告" },
  error: { className: "risk-high", label: "❌ 失败" },
  unwired: { className: "planned-tag", label: "未接线" },
};

/**
 * 健康与自检：环境就绪吗。
 * 组合现有端点状态，不新建专用健康端点。每项检查显式标注数据源。
 */
export default function HealthPage({ tenantId }: PageProps) {
  const [checks, setChecks] = useState<HealthCheckRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastChecked, setLastChecked] = useState<string>();

  const runChecks = useCallback(async (id?: string) => {
    setLoading(true);
    const rows: HealthCheckRow[] = [];
    let ready: HealthReady | undefined;
    let knowledge: KnowledgeStats | undefined;
    let runs: RunsSummary | undefined;

    // 1. Health ready（不需要 tenant）
    try {
      ready = await apiRequest<HealthReady>({ path: "/api/v1/health/ready", tenantId: id });
      rows.push({
        key: "db", name: "PostgreSQL 连接",
        state: ready.database === "ok" ? "ok" : "error",
        detail: ready.database === "ok" ? "数据库可达" : "数据库不可达",
        source: "GET /api/v1/health/ready",
      });
      rows.push({
        key: "migration", name: "迁移版本",
        state: ready.migration_head === ready.migration_expected ? "ok" : "warn",
        detail: `当前 ${ready.migration_head} · 期望 ${ready.migration_expected}`,
        source: "GET /api/v1/health/ready",
      });
      rows.push({
        key: "worker", name: "Worker 心跳",
        state: ready.worker_heartbeats > 0 && ready.worker_stale < ready.worker_heartbeats ? "ok" : "warn",
        detail: `${ready.worker_heartbeats} 个活跃 · ${ready.worker_stale} 个过期`,
        source: "GET /api/v1/health/ready",
      });
      rows.push({
        key: "outbox", name: "Outbox 积压",
        state: ready.outbox_backlog <= 500 ? "ok" : "warn",
        detail: `${fmtInt(ready.outbox_backlog)} 条未发布`,
        source: "GET /api/v1/health/ready",
      });
    } catch {
      rows.push({ key: "db", name: "PostgreSQL 连接", state: "error", detail: "health/ready 不可达", source: "GET /api/v1/health/ready" });
    }

    // 2. Knowledge stats（需要 tenant）
    if (id) {
      try {
        knowledge = await apiRequest<KnowledgeStats>({ path: "/api/v1/knowledge/stats", tenantId: id });
        rows.push({
          key: "vector", name: "pgvector 扩展",
          state: knowledge.vector_ready ? "ok" : "warn",
          detail: knowledge.vector_ready ? `向量就绪 · 模型 ${knowledge.embedding_model ?? "—"}` : "向量未就绪",
          source: "GET /api/v1/knowledge/stats (vector_ready)",
        });
        rows.push({
          key: "coverage", name: "向量覆盖率",
          state: knowledge.embedding_coverage >= 0.8 ? "ok" : knowledge.embedding_coverage >= 0.3 ? "warn" : "error",
          detail: `${(knowledge.embedding_coverage * 100).toFixed(1)}% · ${fmtInt(knowledge.embedding_chunks)}/${fmtInt(knowledge.chunks)} chunks`,
          source: "GET /api/v1/knowledge/stats (embedding_coverage)",
        });
        rows.push({
          key: "embedder", name: "AI 嵌入服务",
          state: knowledge.embedding_model ? "ok" : "warn",
          detail: knowledge.embedding_model ? `模型 ${knowledge.embedding_model}` : "嵌入模型未配置",
          source: "GET /api/v1/knowledge/stats (embedding_model)",
        });
      } catch {
        rows.push({ key: "vector", name: "pgvector 扩展", state: "error", detail: "knowledge/stats 不可达", source: "GET /api/v1/knowledge/stats" });
      }
    } else {
      rows.push({ key: "vector", name: "pgvector 扩展", state: "unwired", detail: "未连接租户", source: "需租户上下文" });
    }

    // 3. Evidence bundle completeness
    if (id) {
      try {
        runs = await apiRequest<RunsSummary>({ path: "/api/v1/observability/runs", tenantId: id, query: { limit: 500 } });
        const s = runs.summary;
        rows.push({
          key: "evidence", name: "证据包完整性",
          state: s.orphan_bundles === 0 ? "ok" : "warn",
          detail: `有包 ${fmtInt(s.runs_with_bundle)} · 无包 ${fmtInt(s.runs_without_bundle)} · 孤儿包 ${fmtInt(s.orphan_bundles)}`,
          source: "GET /api/v1/observability/runs",
        });
      } catch {
        rows.push({ key: "evidence", name: "证据包完整性", state: "error", detail: "observability/runs 不可达", source: "GET /api/v1/observability/runs" });
      }
    } else {
      rows.push({ key: "evidence", name: "证据包完整性", state: "unwired", detail: "未连接租户", source: "需租户上下文" });
    }

    // 4. Policy gateway（需要 tenant + POST simulate）
    if (id) {
      try {
        await apiRequest({
          path: "/api/v1/policy/simulate", method: "POST", tenantId: id,
          body: { capability: "health.probe", arguments: {}, risk_class: "read_only", side_effects: "read_only" },
        });
        rows.push({
          key: "policy", name: "策略网关",
          state: "ok", detail: "simulate 端点可达",
          source: "POST /api/v1/policy/simulate",
        });
      } catch {
        rows.push({ key: "policy", name: "策略网关", state: "warn", detail: "simulate 端点返回非 200（策略可能未放行 probe）", source: "POST /api/v1/policy/simulate" });
      }
    } else {
      rows.push({ key: "policy", name: "策略网关", state: "unwired", detail: "未连接租户", source: "需租户上下文" });
    }

    // 5. RLS（无法通过现有端点检查）
    rows.push({
      key: "rls", name: "RLS 行级安全",
      state: "unwired", detail: "无只读端点可验证 RLS 策略——需数据库直查",
      source: "未接线",
    });

    setChecks(rows);
    setLastChecked(new Date().toLocaleString("zh-CN"));
    setLoading(false);
  }, []);

  useEffect(() => { void runChecks(tenantId); }, [tenantId, runChecks]);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Card className="chart-card" title="健康与自检" extra={<Space wrap>
        <Typography.Text type="secondary">{lastChecked ? `上次检查：${lastChecked}` : "尚未检查"}</Typography.Text>
        <Button size="small" loading={loading} onClick={() => void runChecks(tenantId)}>重新检查</Button>
      </Space>}>
        <Table<HealthCheckRow>
          className="stock-table" size="small" rowKey="key" loading={loading}
          dataSource={checks} pagination={false}
          columns={[
            { title: "检查项", dataIndex: "name", key: "name", width: 180, render: (v: string) => <strong>{v}</strong> },
            { title: "状态", dataIndex: "state", key: "state", width: 120, render: (v: CheckState) => {
              const t = STATE_TAG[v];
              return <Tag className={t.className}>{t.label}</Tag>;
            } },
            { title: "详情", dataIndex: "detail", key: "detail", ellipsis: true },
            { title: "数据源", dataIndex: "source", key: "source", width: 240, render: (v: string) => <Typography.Text type="secondary">{v}</Typography.Text> },
          ]}
          locale={{ emptyText: "点击「重新检查」开始健康自检。" }}
        />
      </Card>
      <Typography.Text type="secondary">本页组合现有端点状态，不新建专用健康端点。每项检查标注了数据源。RLS 验证无只读端点可查，标为"未接线"。</Typography.Text>
    </Space>
  );
}
