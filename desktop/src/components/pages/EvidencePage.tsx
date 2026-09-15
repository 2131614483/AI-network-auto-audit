import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Card, Space, Table, Tag, Typography, message } from "antd";
import { apiRequest, asRecord, EMPTY_NO_BUNDLE, fmtHashShort, fmtInt, fmtTime, type PageProps } from "./common";

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
 * 证据链与复验：证据能不能离线复验。
 * 提级页：从 RunsPage 提取证据包相关功能，聚焦"有证据包的运行 + 复验"。
 * 只读；复验只复算不落盘。
 */
export default function EvidencePage({ tenantId }: PageProps) {
  const [data, setData] = useState<RunsResponse>();
  const [loading, setLoading] = useState(false);
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

  /** 只展示有证据包的运行。 */
  const bundled = useMemo(() => (data?.items ?? []).filter((item) => item.bundle), [data]);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Card className="chart-card" title="证据包与离线复验" extra={<Space wrap>
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
      </Space>}>
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          <Typography.Text type="secondary">
            共 {fmtInt(data?.summary.runs_listed)} 个运行 · 有证据包 {fmtInt(data?.summary.runs_with_bundle)} · 无证据包 {fmtInt(data?.summary.runs_without_bundle)} · 孤儿证据包 {fmtInt(data?.summary.orphan_bundles)}（数据库已无对应运行）。
          </Typography.Text>
          <Table<RunItem>
            className="stock-table" size="small" rowKey="run_id" loading={loading}
            dataSource={bundled} pagination={{ pageSize: 20 }}
            columns={[
              { title: "运行", dataIndex: "run_id", key: "run_id", width: 110, render: (value: string) => <span title={value}>{value.slice(0, 8)}</span> },
              { title: "证据包大小", key: "size", width: 120, render: (_v, row) => row.bundle ? `${(row.bundle.size_bytes / 1024).toFixed(0)} KB` : "—" },
              { title: "成员数", key: "members", width: 90, render: (_v, row) => row.bundle ? fmtInt(row.bundle.members.length) : "—" },
              { title: "SHA-256", key: "sha", width: 140, render: (_v, row) => row.bundle?.path ? <span title={row.bundle.path}>{fmtHashShort(row.bundle.path, 12)}</span> : "—" },
              { title: "归档时间", dataIndex: "bundle.exported_at", key: "exported_at", width: 160, render: (_v, row) => row.bundle ? fmtTime(row.bundle.exported_at) : "—" },
              { title: "复验状态", key: "verify", width: 130, render: (_v, row) => {
                const vr = verifyResults[row.run_id];
                if (!vr) return <Tag className="pending-tag">未复验</Tag>;
                if (!vr.found) return <Tag className="risk-high">{vr.reason ?? "无包"}</Tag>;
                const ok = Boolean(asRecord(vr.verification ?? {}).verified);
                return <Tag className={ok ? "ready-tag" : "risk-high"}>{ok ? "复验一致" : "复验失配"}</Tag>;
              } },
              { title: "操作", key: "actions", width: 100, render: (_v, row) => row.bundle ? <Button size="small" onClick={() => void verify(row.run_id)}>复验</Button> : <Typography.Text type="secondary">无包</Typography.Text> },
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
            locale={{ emptyText: "无证据包。" }}
          />
        </Space>
      </Card>

      <Typography.Text type="secondary">复验调用 GET /api/v1/observability/bundles/{'{run_id}'}/verify——重算每个 manifest 的 sha256，不落盘。证据包导出端点 GET /api/v1/observability/evidence/{'{run_id}'} 返回 zip + sha manifest。</Typography.Text>
    </Space>
  );
}
