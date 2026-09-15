import { Button, Card, Space, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { apiRequest, asRecord, type PageProps } from "./common";

type GraphProposalNode = { node_key: string; node_type: string; label: string; space_key: string; operation: string };
type GraphProposal = { id: string; title: string; status: string; risk_class: string; graph_space_key: string | null; candidate_count: number; nodes: GraphProposalNode[]; submitted_at: string | null; approved_at: string | null; applied_at: string | null };

/**
 * 抽取提案与审批（原 graph 页"抽取提案"卡片提级）。
 * 端点 /graph/extractions/proposals · propose · {id}/{approve|reject} 保持原样。
 */
export default function GraphExtractPage({ tenantId, onNotice }: PageProps) {
  const [proposals, setProposals] = useState<GraphProposal[]>([]);
  const [busy, setBusy] = useState(false);
  const [spaceKey, setSpaceKey] = useState<string>();

  const load = useCallback(async (id: string) => {
    const payload = asRecord(await apiRequest({ path: "/api/v1/graph/extractions/proposals", tenantId: id, query: { limit: 50 } }));
    setProposals((payload.proposals as GraphProposal[]) ?? []);
  }, []);

  useEffect(() => { if (tenantId) void load(tenantId); }, [tenantId, load]);

  const generate = async (): Promise<void> => {
    if (!tenantId) return;
    setBusy(true);
    try {
      const key = spaceKey ?? "audit-l1";
      await apiRequest({ path: "/api/v1/graph/extractions/propose", method: "POST", tenantId, body: { space_key: key, limit_documents: 10 } });
      await load(tenantId);
      onNotice?.(`已把最近文档的候选节点/边装入影子 ChangeSet（${key}），未经放行不会写入活图。`);
    } catch (error) { onNotice?.(error instanceof Error ? `提案生成失败：${error.message}` : "提案生成失败。"); }
    finally { setBusy(false); }
  };

  const decide = async (proposalId: string, decision: "approve" | "reject"): Promise<void> => {
    if (!tenantId) return;
    setBusy(true);
    try {
      await apiRequest({ path: `/api/v1/graph/extractions/proposals/${proposalId}/${decision}`, method: "POST", tenantId });
      await load(tenantId);
      onNotice?.(decision === "approve" ? "提案已放行，节点已发布到目标图空间并留痕。" : "提案已驳回，未写入活图。");
    } catch (error) { onNotice?.(error instanceof Error ? `决策失败：${error.message}` : "决策失败。"); }
    finally { setBusy(false); }
  };

  return (
    <section className="graph-proposal-section">
      <Card className="chart-card" title="抽取提案" extra={<Space><Tag className="gateway-tag">ChangeSet 治理</Tag><Button size="small" loading={busy} disabled={!tenantId} onClick={() => void generate()}>从最近文档生成提案</Button></Space>}>
        <Table className="stock-table" size="small" rowKey="id" dataSource={proposals} pagination={{ pageSize: 5 }} columns={[{ title: "状态", dataIndex: "status", key: "status", width: 120, render: (value: string) => <Tag className={value === "applied" ? "ready-tag" : value === "rejected" ? "risk-high" : value === "draft" ? "pending-tag" : "gateway-tag"}>{value === "draft" ? "草稿(不含活图)" : value === "pending_review" ? "待审核" : value === "applied" ? "已放行" : value === "rejected" ? "已驳回" : value}</Tag> }, { title: "提案标题", dataIndex: "title", key: "title", ellipsis: true }, { title: "图空间", dataIndex: "graph_space_key", key: "graph_space_key", width: 150, render: (value: string | null) => value ?? "—" }, { title: "候选", dataIndex: "candidate_count", key: "candidate_count", width: 64 }, { title: "候选节点", key: "nodes", render: (_value, row: GraphProposal) => row.status === "draft" ? <span>{row.nodes.slice(0, 3).map((n) => `${n.node_type}:${n.label}`).join("、")}{row.candidate_count > 3 ? ` …` : ""}</span> : <span className="empty">{row.candidate_count || 0} 项</span>, ellipsis: true }, { title: "操作", key: "actions", width: 120, render: (_value, row: GraphProposal) => row.status === "draft" ? <Space><Button size="small" type="primary" loading={busy} disabled={!tenantId} onClick={() => void decide(row.id, "approve")}>放行</Button><Button size="small" danger loading={busy} disabled={!tenantId} onClick={() => void decide(row.id, "reject")}>驳回</Button></Space> : <Tag className={row.status === "applied" ? "ready-tag" : "risk-high"}>{row.status === "applied" ? "已发布" : "已撤销"}</Tag> }]} locale={{ emptyText: "暂无抽取提案；点击“从最近文档生成提案”把文本候选装入影子 ChangeSet，未经放行不写入活图。" }} />
        <Typography.Paragraph type="secondary" className="extraction-note">抽取器为确定性规则，无 LLM。候选先进入影子 ChangeSet，经校验与人工放行（validate → approve → release → activate）后才出现在 graph.nodes，并始终写入节点版本快照；冲突与越界主张转入冲突收件箱。</Typography.Paragraph>
      </Card>
    </section>
  );
}
