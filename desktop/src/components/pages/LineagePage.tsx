import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Col, Row, Select, Space, Table, Tag, Typography } from "antd";
import { apiRequest, fmtHashShort, fmtInt, fmtTime, type PageProps } from "./common";

type LifecycleItem = {
  plugin_id: string; name: string; capability: string; domain: string | null; domains: string[];
  protocol_lifecycle: string; executable: boolean; conflict: boolean;
  version: string | null; version_status: string | null; descriptor_sha256: string | null;
  runtime: string | null; entrypoint: string | null; side_effect_class: string | null; published_at: string | null;
};
type LifecycleSummary = {
  plugins: number; protocol_verified: number; protocol_contract_only: number;
  executable: number; conflict: number; version_registered: number;
  descriptor_sha256_present: number; allow_list_size: number; allow_list_unmatched: string[];
  by_domain: Record<string, number>;
};
type LifecycleResponse = { summary: LifecycleSummary; items: LifecycleItem[] };

type VersionRow = {
  version: string | null; version_status: string | null; descriptor_sha256: string | null;
  runtime: string | null; entrypoint: string | null; side_effect_class: string | null; published_at: string | null;
};
type VersionsResponse = { plugin_id: string; items: VersionRow[] };

/**
 * 版本与衍生谱系：插件/契约的版本从哪来。
 * 主表复用 /plugins/lifecycle（每个插件最新发布版本）；展开行按 plugin_id 拉取
 * /plugins/versions 的完整版本历史。只读：不发布、不回滚任何版本。
 */
export default function LineagePage({ tenantId }: PageProps) {
  const [data, setData] = useState<LifecycleResponse>();
  const [loading, setLoading] = useState(false);
  const [domain, setDomain] = useState<string>();
  const [versionStatus, setVersionStatus] = useState<string>();
  const [conflictOnly, setConflictOnly] = useState(false);
  const historyCache = useRef<Record<string, { loading: boolean; rows: VersionRow[] }>>({});
  const [, forceTick] = useState(0);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<LifecycleResponse>({
        path: "/api/v1/plugins/lifecycle", tenantId: id,
        query: { domain, conflict_only: conflictOnly, limit: 500 },
      });
      setData(payload);
    } finally { setLoading(false); }
  }, [tenantId, domain, conflictOnly]);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const rows = useMemo(() => {
    const base = data?.items ?? [];
    if (!versionStatus) return base;
    return base.filter((row) => (row.version_status ?? "—") === versionStatus);
  }, [data, versionStatus]);

  const publishedCount = useMemo(
    () => (data?.items ?? []).filter((row) => row.version_status === "published").length,
    [data],
  );
  const latestPublished = useMemo(() => {
    const times = (data?.items ?? []).map((row) => row.published_at).filter((v): v is string => Boolean(v));
    return times.length ? times.sort().slice(-1)[0] : null;
  }, [data]);

  const domainOptions = useMemo(
    () => Object.entries(data?.summary.by_domain ?? {}).map(([value, count]) => ({ value, label: `${value}（${count}）` })),
    [data],
  );
  const summary = data?.summary;

  const loadHistory = useCallback(async (id: string, pluginId: string) => {
    if (historyCache.current[pluginId]) return;
    historyCache.current[pluginId] = { loading: true, rows: [] };
    forceTick((n) => n + 1);
    try {
      const payload = await apiRequest<VersionsResponse>({ path: "/api/v1/plugins/versions", tenantId: id, query: { plugin_id: pluginId } });
      historyCache.current[pluginId] = { loading: false, rows: payload.items };
    } catch {
      historyCache.current[pluginId] = { loading: false, rows: [] };
    }
    forceTick((n) => n + 1);
  }, []);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>已登记版本</span><strong>{fmtInt(summary?.version_registered)}</strong><small>有 version 字段的插件</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>published 状态</span><strong>{fmtInt(publishedCount)}</strong><small>version_status = published</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>有 descriptor_sha256</span><strong>{fmtInt(summary?.descriptor_sha256_present)}</strong><small>描述符可校验</small></Card></Col>
        <Col xs={12} sm={6}><Card className="metric-card" size="small"><span>最近发布</span><strong style={{ fontSize: 18 }}>{latestPublished ? fmtTime(latestPublished) : "—"}</strong><small>max(published_at)</small></Card></Col>
      </Row>

      <Card className="chart-card" title="插件版本谱系表" extra={<Space wrap>
        <Select allowClear showSearch placeholder="域" style={{ width: 150 }} value={domain} onChange={setDomain} options={domainOptions} />
        <Select allowClear placeholder="版本状态" style={{ width: 150 }} value={versionStatus} onChange={setVersionStatus} options={[{ value: "published", label: "published" }, { value: "draft", label: "draft" }]} />
        <Button size="small" type={conflictOnly ? "primary" : "default"} danger={conflictOnly} onClick={() => setConflictOnly((v) => !v)}>仅冲突（{summary?.conflict ?? 0}）</Button>
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
      </Space>}>
        {summary && summary.allow_list_unmatched.length > 0 ? (
          <Alert banner type="warning" showIcon style={{ marginBottom: 8 }} message={`允许表 ${summary.allow_list_size} 项中有 ${summary.allow_list_unmatched.length} 项在插件目录中找不到对应记录（口径漂移）：${summary.allow_list_unmatched.join("、")}`} />
        ) : null}
        <Table<LifecycleItem>
          className="stock-table" size="small" rowKey="plugin_id" loading={loading}
          dataSource={rows} pagination={{ pageSize: 20 }}
          rowClassName={(row) => (row.conflict ? "selected-row" : "")}
          onExpand={(expanded, row) => { if (expanded && tenantId) void loadHistory(tenantId, row.plugin_id); }}
          expandedRowRender={(row) => {
            const cache = historyCache.current[row.plugin_id];
            if (!cache || cache.loading) return <Typography.Text type="secondary">正在加载版本历史…</Typography.Text>;
            if (!cache.rows.length) return <Typography.Text type="secondary">该插件无多版本历史记录（catalog.plugin_versions 0 行）。</Typography.Text>;
            return (
              <Table<VersionRow>
                className="nested-table" size="small" rowKey={(r) => `${r.version ?? "?"}-${r.published_at ?? "?"}`}
                dataSource={cache.rows} pagination={false}
                columns={[
                  { title: "版本", dataIndex: "version", key: "version", width: 100, render: (v: string | null) => v ?? "—" },
                  { title: "状态", dataIndex: "version_status", key: "version_status", width: 110, render: (v: string | null) => v ? <Tag className={v === "published" ? "ready-tag" : "pending-tag"}>{v}</Tag> : "—" },
                  { title: "描述符", dataIndex: "descriptor_sha256", key: "descriptor_sha256", width: 150, render: (v: string | null) => v ? <span title={v}>{fmtHashShort(v, 12)}…</span> : "—" },
                  { title: "runtime", dataIndex: "runtime", key: "runtime", width: 130, render: (v: string | null) => v ?? "—" },
                  { title: "entrypoint", dataIndex: "entrypoint", key: "entrypoint", ellipsis: true, render: (v: string | null) => v ?? "—" },
                  { title: "发布时间", dataIndex: "published_at", key: "published_at", width: 170, render: (v: string | null) => fmtTime(v) },
                ]}
              />
            );
          }}
          columns={[
            { title: "插件", dataIndex: "plugin_id", key: "plugin_id", ellipsis: true, render: (v: string) => <span title={v}>{v}</span> },
            { title: "当前版本", dataIndex: "version", key: "version", width: 90, render: (v: string | null) => v ?? "—" },
            { title: "版本状态", dataIndex: "version_status", key: "version_status", width: 110, render: (v: string | null) => v ? <Tag className={v === "published" ? "ready-tag" : "pending-tag"}>{v}</Tag> : "—" },
            { title: "描述符 (sha256)", dataIndex: "descriptor_sha256", key: "descriptor_sha256", width: 150, render: (v: string | null) => v ? <span title={v}>{fmtHashShort(v, 12)}…</span> : "—" },
            { title: "runtime", dataIndex: "runtime", key: "runtime", width: 130, render: (v: string | null) => v ?? "—" },
            { title: "entrypoint", dataIndex: "entrypoint", key: "entrypoint", ellipsis: true, render: (v: string | null) => v ?? "—" },
            { title: "发布时间", dataIndex: "published_at", key: "published_at", width: 170, render: (v: string | null) => fmtTime(v) },
          ]}
          locale={{ emptyText: "无版本记录。" }}
        />
      </Card>
      <Typography.Text type="secondary">主表为每个插件的最新发布版本（新运行解析到的那一行）；点击行展开拉取该插件在 catalog.plugin_versions 中的完整版本历史。冲突行（口径不一致）整行标红，不会用绿色掩盖。</Typography.Text>
    </Space>
  );
}
