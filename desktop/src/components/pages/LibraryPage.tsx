import { CopyOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Card, Col, Input, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import { apiRequest, fmtInt, fmtTime, type PageProps } from "./common";

type LibraryItem = {
  path: string;
  title: string;
  kind: string;
  project: string | null;
  size_bytes: number;
  modified_at: string;
  related_run: string | null;
  related_case: string | null;
};
type LibrarySummary = {
  total: number;
  by_kind: Record<string, number>;
  by_project: Record<string, number>;
};
type LibraryIndexResponse = {
  summary: LibrarySummary;
  items: LibraryItem[];
  trace_id: string;
};

const KIND_OPTIONS = ["方案", "报告", "评估", "清单", "契约", "技能", "案例资料"];
const KIND_TAG_COLOR: Record<string, string> = {
  "方案": "blue",
  "报告": "green",
  "评估": "orange",
  "清单": "cyan",
  "契约": "purple",
  "技能": "magenta",
  "案例资料": "gold",
};
const SINCE_OPTIONS = [
  { value: "", label: "全部时间" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
  { value: "90d", label: "近 90 天" },
  { value: "180d", label: "近 180 天" },
];

function sinceToIso(preset: string): string | undefined {
  if (!preset) return undefined;
  const days = parseInt(preset, 10);
  if (Number.isNaN(days)) return undefined;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString();
}

/**
 * 文档资产库：docs/ 与 审计项目案例/ 的只读文件索引。
 * summary 始终统计全量语料，不随筛选缩水；关键词仅匹配路径与标题。
 * 不做全文检索、不移动/改名/删除任何文件。
 */
export default function LibraryPage({ tenantId, onNavigate }: PageProps) {
  const [data, setData] = useState<LibraryIndexResponse>();
  const [loading, setLoading] = useState(false);
  const [kind, setKind] = useState<string>();
  const [project, setProject] = useState<string>();
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const [sincePreset, setSincePreset] = useState("");

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<LibraryIndexResponse>({
        path: "/api/v1/library/index",
        tenantId: id,
        query: {
          kind: kind || undefined,
          project: project || undefined,
          q: q || undefined,
          since: sinceToIso(sincePreset),
        },
      });
      setData(payload);
    } finally {
      setLoading(false);
    }
  }, [tenantId, kind, project, q, sincePreset]);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const projectOptions = useMemo(() =>
    Object.entries(data?.summary.by_project ?? {}).map(([value, count]) => ({ value, label: `${value}（${count}）` })),
    [data]);

  const summary = data?.summary;

  const copyPath = (path: string) => {
    void navigator.clipboard?.writeText(path);
    message.success("路径已复制");
  };

  const applySearch = () => setQ(qInput.trim());

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      {/* 汇总卡 — 始终全量统计 */}
      <Row gutter={[8, 8]}>
        <Col xs={12} sm={6}>
          <Card className="metric-card" size="small">
            <span>文件总数</span>
            <strong>{fmtInt(summary?.total)}</strong>
            <small>全量索引（不随筛选缩水）</small>
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className="metric-card" size="small">
            <span>按类型</span>
            <strong>{fmtInt(Object.keys(summary?.by_kind ?? {}).length)}</strong>
            <small>{Object.entries(summary?.by_kind ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</small>
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className="metric-card" size="small">
            <span>按项目</span>
            <strong>{fmtInt(Object.keys(summary?.by_project ?? {}).length)}</strong>
            <small>{Object.entries(summary?.by_project ?? {}).slice(0, 3).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</small>
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className="metric-card" size="small">
            <span>当前筛选结果</span>
            <strong>{fmtInt(data?.items.length)}</strong>
            <small>表格显示的行数</small>
          </Card>
        </Col>
      </Row>

      <Card className="chart-card" title="文档文件索引" extra={<Space wrap>
        <Select allowClear placeholder="类型" style={{ width: 120 }} value={kind} onChange={setKind}
          options={KIND_OPTIONS.map((k) => ({ value: k, label: k }))} />
        <Select allowClear showSearch placeholder="项目" style={{ width: 180 }} value={project} onChange={setProject}
          options={projectOptions} />
        <Select placeholder="时间范围" style={{ width: 130 }} value={sincePreset} onChange={setSincePreset}
          options={SINCE_OPTIONS} />
        <Input.Search
          placeholder="仅搜索路径与标题"
          style={{ width: 220 }}
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          onSearch={applySearch}
          enterButton
        />
        <Button size="small" loading={loading} onClick={() => tenantId && void load(tenantId)}>刷新</Button>
      </Space>}>
        <Table<LibraryItem>
          className="stock-table" size="small" rowKey="path" loading={loading}
          dataSource={data?.items ?? []} pagination={{ pageSize: 25 }}
          columns={[
            {
              title: "路径", dataIndex: "path", key: "path", ellipsis: true,
              render: (value: string) => (
                <span title={value} style={{ cursor: "pointer" }} onClick={() => copyPath(value)}>
                  <CopyOutlined style={{ marginRight: 4 }} />{value}
                </span>
              ),
            },
            { title: "标题", dataIndex: "title", key: "title", ellipsis: true, render: (value: string) => value || "—" },
            {
              title: "类型", dataIndex: "kind", key: "kind", width: 90,
              render: (value: string) => <Tag color={KIND_TAG_COLOR[value] ?? "default"}>{value}</Tag>,
            },
            { title: "项目", dataIndex: "project", key: "project", width: 130, render: (value: string | null) => value ?? "—" },
            { title: "大小", dataIndex: "size_bytes", key: "size_bytes", width: 90, align: "right", render: (value: number) => `${(value / 1024).toFixed(1)} KB` },
            { title: "修改时间", dataIndex: "modified_at", key: "modified_at", width: 160, render: (value: string) => fmtTime(value) },
            {
              title: "关联 run", dataIndex: "related_run", key: "related_run", width: 100,
              render: (value: string | null) => value ? (
                <span style={{ cursor: "pointer", color: "#1677ff" }} title={value} onClick={() => onNavigate?.("runs")}>
                  {value.slice(0, 8)}
                </span>
              ) : "—",
            },
            { title: "关联案例", dataIndex: "related_case", key: "related_case", width: 120, render: (value: string | null) => value ?? "—" },
          ]}
          locale={{ emptyText: "未找到文档。" }}
        />
      </Card>
      <Typography.Text type="secondary">
        本页仅做路径 / 类型 / 时间 / 标题索引，不做全文检索（全文归 knowledge-search）；不移动、不改名、不删除任何文件。
      </Typography.Text>
    </Space>
  );
}
