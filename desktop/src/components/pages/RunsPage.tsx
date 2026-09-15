import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Card, Space, Table, Tag, Typography, message } from "antd";
import { apiRequest, asRecord, EMPTY_NO_BUNDLE, EMPTY_UNLINKED, fmtHashShort, fmtInt, fmtTime, type PageProps } from "./common";

type RunBundle = {
  path: string; size_bytes: number; modified_at: string | null;
  exported_at: string | null; members: Array<string | Record<string, unknown>>; readable: boolean; verified: unknown;
};
type RunItem = {
  run_id: string; plan_key: string | null; trace_id: string | null; execution_hash: string | null;
  attempts: number; succeeded: number; failed: number; plugins: number; versioned_attempts: number;
  started_at: string | null; finished_at: string | null;
  bundle: RunBundle | null; project_id: string | null; archive_state: "linked" | "unlinked"; archived_at: string | null;
};
type RunsResponse = {
  items: RunItem[];
  orphan_bundles: string[];
  summary: { runs_listed: number; runs_with_bundle: number; runs_without_bundle: number; linked_to_project: number; unlinked: number; orphan_bundles: number };
};
type VerifyResponse = { run_id: string; found: boolean; reason?: string; path?: string; size_bytes?: number; verification?: Record<string, unknown> };

/**
 * 运行与产物库：跨运行跑出了什么、能否离线复验。只读；复验只复算不落盘。
 */
export default function RunsPage({ tenantId }: PageProps) {
  const [data, setData] = useState<RunsResponse>();
  const [loading, setLoading] = useState(false);
  const [evidenceFilter, setEvidenceFilter] = useState<"all" | "with" | "without">("all");
  const [anchorFilter, setAnchorFilter] = useState<"all" | "linked" | "unlinked">("all");
  const [verifyResults, setVerifyResults] = useState<Record<string, VerifyResponse>>({});

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<RunsResponse>({ path: "/api/v1/observability/runs", tenantId: id, query: { limit: 500 } });
      setData(payload);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const verify = async (runId: string): Promise<void> => {
    if (!tenantId) return;
    try {
      const result = await apiRequest<VerifyResponse>({ path: `/api/v1/observability/bundles/${runId}/verify`, tenantId });
      setVerifyResults((state) => ({ ...state, [runId]: result }));
      const verification = result.verification;
      const ok = Boolean(asRecord(verification ?? {}).verified);
      message[ok ? "success" : "warning"](`复验完成：${result.found ? (ok ? "一致" : "存在失配") : result.reason ?? "无证据包"}`);
    } catch (error) {
      message.error(error instanceof Error ? `复验失败：${error.message}` : "复验失败。");
    }
  };

  const filtered = useMemo(() => (data?.items ?? []).filter((item) => {
    if (evidenceFilter === "with" && !item.bundle) return false;
    if (evidenceFilter === "without" && item.bundle) return false;
    if (anchorFilter === "linked" && item.archive_state !== "linked") return false;
    if (anchorFilter === "unlinked" && item.archive_state !== "unlinked") return false;
    return true;
  }), [data, evidenceFilter, anchorFilter]);

  return (
    <Card className="chart-card" title="运行与产物库" extra={<Space wrap>
      <Button size="small" onClick={() => setEvidenceFilter("all")} type={evidenceFilter === "all" ? "primary" : "default"}>全部</Button>
      <Button size="small" onClick={() => setEvidenceFilter("with")} type={evidenceFilter === "with" ? "primary" : "default"}>有证据包</Button>
      <Button size="small" onClick={() => setEvidenceFilter("without")} type={evidenceFilter === "without" ? "primary" : "default"}>无证据包</Button>
      <Button size="small" onClick={() => setAnchorFilter("all")} type={anchorFilter === "all" ? "primary" : "default"}>全部锚</Button>
      <Button size="small" onClick={() => setAnchorFilter("unlinked")} type={anchorFilter === "unlinked" ? "primary" : "default"}>未挂锚</Button>
      <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
    </Space>}>
      <Space direction="vertical" style={{ width: "100%" }} size={8}>
        <Typography.Text type="secondary">
          共 {fmtInt(data?.summary.runs_listed)} 个运行 · 有证据包 {fmtInt(data?.summary.runs_with_bundle)} · 无证据包 {fmtInt(data?.summary.runs_without_bundle)} ·
          已挂项目锚 {fmtInt(data?.summary.linked_to_project)} · 未挂锚 {fmtInt(data?.summary.unlinked)} · 孤儿证据包 {fmtInt(data?.summary.orphan_bundles)}（数据库已无对应运行）。
        </Typography.Text>
        <Table<RunItem>
          className="stock-table" size="small" rowKey="run_id" loading={loading}
          dataSource={filtered} pagination={{ pageSize: 20 }}
          columns={[
            { title: "运行", dataIndex: "run_id", key: "run_id", width: 110, render: (value: string) => <span title={value}>{value.slice(0, 8)}</span> },
            { title: "链/计划", dataIndex: "plan_key", key: "plan_key", ellipsis: true, render: (value: string | null) => <span title={value ?? undefined}>{value ? fmtHashShort(value, 24) : "—"}</span> },
            { title: "节点", key: "nodes", width: 150, render: (_v, row) => `${row.succeeded} 成功 · ${row.failed} 失败 · ${row.attempts} 次尝试` },
            { title: "证据包", key: "bundle", width: 150, render: (_v, row) => row.bundle ? <Tag className="ready-tag" title={row.bundle.path}>有 · {(row.bundle.size_bytes / 1024).toFixed(0)} KB</Tag> : <Tag className="pending-tag" title={EMPTY_NO_BUNDLE}>{EMPTY_NO_BUNDLE}</Tag> },
            { title: "项目锚", dataIndex: "archive_state", key: "archive_state", width: 190, render: (value: string, row) => value === "linked" ? <Tag className="ready-tag" title={row.project_id ?? undefined}>已挂锚 {fmtHashShort(row.project_id ?? "", 12)}</Tag> : <Tag className="pending-tag" title={EMPTY_UNLINKED}>{EMPTY_UNLINKED}</Tag> },
            { title: "开始时间", dataIndex: "started_at", key: "started_at", width: 160, render: (value: string | null) => fmtTime(value) },
            { title: "操作", key: "actions", width: 180, render: (_v, row) => row.bundle ? <Space size={4}><Button size="small" onClick={() => void verify(row.run_id)}>复验</Button>{verifyResults[row.run_id] && !verifyResults[row.run_id].found ? <Tag className="risk-high">复验失败</Tag> : verifyResults[row.run_id] ? <Tag className={asRecord(verifyResults[row.run_id].verification ?? {}).verified ? "ready-tag" : "risk-high"}>{asRecord(verifyResults[row.run_id].verification ?? {}).verified ? "复验一致" : "复验失配"}</Tag> : null}</Space> : <Typography.Text type="secondary">无包可复验</Typography.Text> },
          ]}
          expandable={{
            expandedRowRender: (row) => {
              const verification = verifyResults[row.run_id]?.verification;
              return <Space direction="vertical" style={{ width: "100%" }}>
                {row.bundle ? <>
                  <Typography.Text strong>证据包清单（{row.bundle.members.length} 个成员）</Typography.Text>
                  <ul style={{ margin: 0, paddingLeft: 20 }}>
                    {row.bundle.members.map((member, index) => {
                      const name = typeof member === "string" ? member : String(asRecord(member).name ?? asRecord(member).path ?? `成员 ${index}`);
                      const size = typeof member === "string" ? null : asRecord(member).size_bytes;
                      return <li key={index}><code>{name}</code>{typeof size === "number" ? ` · ${(size / 1024).toFixed(1)} KB` : null}</li>;
                    })}
                  </ul>
                  {verification ? <pre className="result-box">{JSON.stringify(verification, null, 2)}</pre> : null}
                </> : <div className="empty">{EMPTY_NO_BUNDLE}（可能已被清理）。</div>}
              </Space>;
            },
          }}
          locale={{ emptyText: "暂无运行记录。" }}
        />
      </Space>
    </Card>
  );
}
