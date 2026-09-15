import { CopyOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Space, Table, Tag, Typography, message } from "antd";
import { apiRequest, fmtInt, fmtTime, type PageProps } from "./common";

type CaseStage = { key: string; name: string; count: number };
type CaseReport = {
  path: string;
  lines: number;
  bytes: number;
  modified_at: string;
  m7_9_share: number;
  v11_ok: boolean;
};
type CaseInfo = {
  case: string;
  stages: CaseStage[];
  file_count: number;
  report_count: number;
  reports: CaseReport[];
};
type CasesResponse = { cases: CaseInfo[]; trace_id: string };

/**
 * 案例库：案例 × 阶段矩阵 + 报告质量门。只读。
 * 零值单元格必须显式显示「0」（灰底），9 列阶段齐全不得省略。
 */
export default function CasesPage({ tenantId, onNavigate }: PageProps) {
  const [data, setData] = useState<CasesResponse>();
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<CasesResponse>({ path: "/api/v1/cases", tenantId: id });
      setData(payload);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  // 收集全部阶段列（保持第一案例的顺序，补齐缺失列）
  const stageColumns = useMemo(() => {
    const order: string[] = [];
    const seen = new Set<string>();
    for (const c of data?.cases ?? []) {
      for (const s of c.stages) {
        if (!seen.has(s.key)) { seen.add(s.key); order.push(s.key); }
      }
    }
    return order.map((key) => {
      const name = data?.cases.find((c) => c.stages.some((s) => s.key === key))
        ?.stages.find((s) => s.key === key)?.name ?? key;
      return { key, name };
    });
  }, [data]);

  // 展开所有报告（带所属案例名）
  const allReports = useMemo(() => {
    const rows: Array<CaseReport & { caseName: string }> = [];
    for (const c of data?.cases ?? []) {
      for (const r of c.reports) rows.push({ ...r, caseName: c.case });
    }
    return rows;
  }, [data]);

  // 有无 report_count=0 的案例（高难度案例报告在根级）
  const zeroReportCases = useMemo(
    () => (data?.cases ?? []).filter((c) => c.report_count === 0).map((c) => c.case),
    [data],
  );

  const copyPath = (path: string) => {
    void navigator.clipboard?.writeText(path);
    message.success("报告路径已复制");
  };

  const stageCountMap = (c: CaseInfo): Record<string, number> => {
    const m: Record<string, number> = {};
    for (const s of c.stages) m[s.key] = s.count;
    return m;
  };

  const stageColDefs = stageColumns.map((sc) => ({
    title: sc.name,
    dataIndex: "stageCount",
    key: sc.key,
    width: 72,
    align: "center" as const,
    render: (_v: unknown, row: CaseInfo) => {
      const count = stageCountMap(row)[sc.key] ?? 0;
      const style = count === 0
        ? { background: "rgba(255,255,255,0.04)", color: "rgba(255,255,255,0.3)" }
        : { background: "rgba(22,119,255,0.18)", color: "#4da6ff", fontWeight: 700 };
      return <div style={{ ...style, padding: "6px 0", borderRadius: 3 }}>{count}</div>;
    },
  }));

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      {/* 案例 × 阶段矩阵 */}
      <Card className="chart-card" title="案例 × 阶段矩阵" extra={
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
      }>
        <Table<CaseInfo>
          className="stock-table" size="small" rowKey="case" loading={loading}
          dataSource={data?.cases ?? []} pagination={false}
          columns={[
            {
              title: "案例", dataIndex: "case", key: "case", width: 140,
              render: (value: string) => (
                <span style={{ cursor: "pointer", color: "#1677ff" }} onClick={() => onNavigate?.("library", value)}>
                  {value}
                </span>
              ),
            },
            ...stageColDefs,
            {
              title: "总文件数", key: "file_count", width: 90, align: "center",
              render: (_v: unknown, row: CaseInfo) => <strong>{fmtInt(row.file_count)}</strong>,
            },
          ]}
          locale={{ emptyText: "未找到案例。" }}
        />
        {zeroReportCases.length > 0 ? (
          <Alert
            type="info" showIcon style={{ marginTop: 8 }}
            message={`${zeroReportCases.join("、")}的审计报告位于 \`审计项目案例/\` 根级，未计入案例目录（report_count=0 是如实状态）。`}
          />
        ) : null}
      </Card>

      {/* 报告质量表 */}
      <Card className="chart-card" title="审计报告质量门">
        <Table<CaseReport & { caseName: string }>
          className="stock-table" size="small" rowKey="path" loading={loading}
          dataSource={allReports} pagination={false}
          columns={[
            {
              title: "报告名", dataIndex: "path", key: "path", ellipsis: true,
              render: (value: string) => (
                <span title={value} style={{ cursor: "pointer" }} onClick={() => copyPath(value)}>
                  <CopyOutlined style={{ marginRight: 4 }} />{value}
                </span>
              ),
            },
            { title: "所属案例", dataIndex: "caseName", key: "caseName", width: 120 },
            { title: "行数", dataIndex: "lines", key: "lines", width: 80, align: "right", render: (v: number) => fmtInt(v) },
            { title: "字节", dataIndex: "bytes", key: "bytes", width: 90, align: "right", render: (v: number) => fmtInt(v) },
            {
              title: "§7-9 占比", dataIndex: "m7_9_share", key: "m7_9_share", width: 100, align: "right",
              render: (v: number) => `${(v * 100).toFixed(1)}%`,
            },
            {
              title: "v1.1 达标", dataIndex: "v11_ok", key: "v11_ok", width: 100, align: "center",
              render: (v: boolean) => v ? <Tag className="ready-tag">✅ 达标</Tag> : <Tag className="pending-tag">⚠️ 未达标</Tag>,
            },
            { title: "最后修改", dataIndex: "modified_at", key: "modified_at", width: 160, render: (v: string) => fmtTime(v) },
          ]}
          locale={{ emptyText: "暂无审计报告。" }}
        />
      </Card>
      <Typography.Text type="secondary">
        矩阵零值单元格（灰底「0」）如实显示——该阶段无文件，不留空、不省略列。点击案例名可下钻到文档库过滤该案例全部文件。
      </Typography.Text>
    </Space>
  );
}
