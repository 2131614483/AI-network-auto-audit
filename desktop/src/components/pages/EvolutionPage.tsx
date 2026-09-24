import { Alert, Card, Space, Steps, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, asRecord, fmtInt, type PageProps } from "./common";

type EvolutionRing = {
  ring: number; name: string; status: "ok" | "partial" | "broken";
  evidence: Record<string, EvidenceValue>; detail: string; checked: string | null;
};
/**
 * 实测数字的取值并不都是数字：环 7 的 planner_imports 是命中清单（字符串数组），
 * 环 2 的 wired_into_dag_path 是布尔。声明成 `number` 会让数组经
 * toLocaleString() 变成空串（页面上只剩 "planner_imports = "）。
 */
type EvidenceValue = number | boolean | string | string[] | null;
type EvolutionResponse = {
  rings: EvolutionRing[];
  weakest: string | null;
  summary: { ok: number; partial: number; broken: number; archive_links: number; loop_closed: boolean };
};

/** 按实际类型渲染实测值，未知类型显式落成字符串而不是留白。 */
function fmtEvidence(value: EvidenceValue): string {
  if (typeof value === "number") return fmtInt(value);
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) {
    if (value.length === 0) return "0";
    const shown = value.slice(0, 3).join("、");
    return value.length > 3 ? `${fmtInt(value.length)} 项（${shown}…）` : shown;
  }
  if (value === null) return "—";
  return value;
}

const RING_EXECUTORS: Record<number, string> = {
  1: "系统自动（observability）",
  2: "系统自动（experience/relations）",
  3: "系统自动 + 规则",
  4: "人工采纳",
  5: "策略网关（规则写面）",
  6: "真实运行度量",
  7: "回归检测",
};
const RING_WRITES: Record<number, string> = {
  1: "运行索引 / 失败聚类",
  2: "候选关系表",
  3: "冲突收件箱",
  4: "建议决策表",
  5: "策略规则集",
  6: "行为度量表",
  7: "闭环验证记录",
};

/**
 * 进化闭环：7 环看板，逐环给出实测数字与"缺什么才能通"。
 * 环 6/7 当前为代码事实断点，必须如实显示 broken，不用绿色掩盖。
 */
export default function EvolutionPage({ tenantId }: PageProps) {
  const [data, setData] = useState<EvolutionResponse>();
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const payload = await apiRequest<EvolutionResponse>({ path: "/api/v1/experience/evolution", tenantId: id });
      setData(payload);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <Card className="chart-card" title="七环进化闭环看板" extra={<Tag className="gateway-tag">只读快照</Tag>}>
        {loading && !data ? <div className="empty">正在加载进化闭环快照…</div> : data ? (
          <Steps direction="vertical" size="small" current={-1} items={data.rings.map((ring) => {
            const tone = ring.status === "ok" ? "finish" : ring.status === "partial" ? "process" : "error";
            const evidence = Object.entries(ring.evidence);
            return {
              title: <Space wrap><Tag className={ring.status === "ok" ? "ready-tag" : ring.status === "partial" ? "pending-tag" : "risk-high"}>{ring.status === "ok" ? "已通" : ring.status === "partial" ? "半通" : "断"}</Tag><strong>{ring.ring}. {ring.name}</strong></Space>,
              status: tone,
              description: (
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  <Typography.Text type="secondary">执行方：{RING_EXECUTORS[ring.ring] ?? "—"} · 写面：{RING_WRITES[ring.ring] ?? "—"}</Typography.Text>
                  {evidence.length ? (
                    <Typography.Text>实测数字：{evidence.map(([key, value]) => `${key} = ${fmtEvidence(value)}`).join(" · ")}</Typography.Text>
                  ) : <Typography.Text type="secondary">实测数字：从未产出 / 未接线</Typography.Text>}
                  <Typography.Text type="secondary">缺什么才能通：{ring.detail || "未知"}</Typography.Text>
                  {/* `checked` is the probe's provenance ("which table / source file /
                      AST query answered this ring"), not a timestamp — rendering it
                      through Date() produced "Invalid Date" on all seven rings. */}
                  {ring.checked ? <Typography.Text type="secondary">校验依据：{ring.checked}</Typography.Text> : null}
                </Space>
              ),
            };
          })} />
        ) : <div className="empty">进化闭环数据未加载。</div>}
      </Card>

      {data ? <Alert
        showIcon
        type={data.summary.loop_closed ? "success" : "warning"}
        message={data.summary.loop_closed ? "闭环已通：1→7 全部可观测。" : `最弱一环：${data.weakest ?? "未知"}`}
        description={`已通 ${data.summary.ok} 环 · 半通 ${data.summary.partial} 环 · 断 ${data.summary.broken} 环；archive_links ${fmtInt(data.summary.archive_links)}。环 6（行为改变证据）/ 环 7（闭环验证）为代码事实断点，界面如实标红。`}
      /> : null}
    </Space>
  );
}
