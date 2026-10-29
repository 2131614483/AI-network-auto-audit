import { Alert, Button, Card, Collapse, Input, Select, Space, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildPlanningBody } from "../../model/collaborativePlanning";
import { apiRequest, fmtTime } from "./common";

type Port = { port_id: string; schema_ref: string };
type Plugin = { plugin_id: string; name: string; inputs: Port[]; outputs: Port[] };
type PlanningEvent = { id: string; event_type: string; round_no: number; payload: Record<string, unknown> };
type AuditProcedure = { procedure_id: string; objective: string; method: string; required_evidence: string[]; acceptance_criteria: string[]; knowledge_evidence_ids: string[] };
type AuditBrief = { objective: string; scope: string; risk_hypotheses: string[]; procedures: AuditProcedure[]; assumptions: string[]; blockers: string[] };
type Session = {
  session_id: string; status: string; trace_id: string; created_at: string;
  model_access?: { mode: string; model: string; transport: string; exchange_dir?: string };
  request_json: { goal: string; domain?: string }; events?: PlanningEvent[];
  result_json?: { detail?: string; rounds?: number; query_count?: number; model_calls?: number;
    candidate?: { nodes: { node_id: string; plugin_id: string }[]; rationale: string; procedure_nodes?: Record<string, string[]> };
    audit_brief?: AuditBrief;
    review?: { procedures: { procedure_id: string; met: boolean; explanation: string }[] };
    assessment?: { passed: boolean; hard_issues: string[]; reviewer_issues: string[]; compiled_plan_hash?: string } };
};
const base = "/api/v1/topology/planning/sessions";
const statuses: Record<string, string> = {
  queued: "等待规划工作者", running: "规划中", accepted: "评估通过，待人工审批",
  blocked_input: "输入资料不可用", blocked_policy: "策略阻止", blocked_capability: "缺少能力",
  budget_exhausted: "达到规划预算", stalled: "无进一步进展", failed: "规划失败", cancelled: "已取消",
};

export default function CollaborativePlanningPanel({ tenantId }: { tenantId?: string }) {
  const [goal, setGoal] = useState("");
  const [domain, setDomain] = useState("audit");
  const [targets, setTargets] = useState<string[]>([]);
  const [bindings, setBindings] = useState("");
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<Session>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef<{ body: string; key: string } | undefined>(undefined);
  const outputs = useMemo(() => [...new Set(plugins.flatMap(p => p.outputs.map(port => port.port_id)))].sort(), [plugins]);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const results = await Promise.allSettled([
      apiRequest<{ items: Session[] }>({ path: base, tenantId }),
      apiRequest<{ items: Plugin[] }>({ path: "/api/v1/topology/planning/catalog", tenantId, query: { domain } }),
    ]);
    if (results[0].status === "fulfilled") setSessions(results[0].value.items);
    if (results[1].status === "fulfilled") setPlugins(results[1].value.items);
    const failure = results.find(result => result.status === "rejected");
    setError(failure?.status === "rejected" ? String(failure.reason) : "");
  }, [tenantId, domain]);

  const inspect = useCallback(async (id: string) => {
    if (!tenantId) return;
    try { setSelected(await apiRequest<Session>({ path: `${base}/${id}`, tenantId })); }
    catch (e) { setError(String(e)); }
  }, [tenantId]);

  useEffect(() => { setSelected(undefined); setSessions([]); setPlugins([]); void load(); }, [load]);
  useEffect(() => {
    if (!selected || !["queued", "running"].includes(selected.status)) return;
    const timer = window.setInterval(() => { void inspect(selected.session_id); }, 5000);
    return () => window.clearInterval(timer);
  }, [selected, inspect]);

  async function submit() {
    if (!tenantId) return;
    setBusy(true); setError("");
    try {
      const body = buildPlanningBody(goal, domain, targets, bindings, plugins);
      const serialized = JSON.stringify(body);
      if (pending.current?.body !== serialized) pending.current = { body: serialized, key: crypto.randomUUID() };
      const row = await apiRequest<Session>({ path: base, method: "POST", tenantId,
        body: { ...body, idempotency_key: pending.current.key } });
      pending.current = undefined;
      await load(); await inspect(row.session_id);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function process() {
    if (!tenantId || !selected) return;
    setBusy(true); setError("");
    try {
      await apiRequest({ path: `${base}/${selected.session_id}/process`, method: "POST", tenantId });
      await inspect(selected.session_id); await load();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function enablePlanning() {
    setBusy(true); setError("");
    try {
      const outcome = await window.auditControl.enableCollaborativePlanning();
      if (!outcome.enabled) return;
      await load();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  const result = selected?.result_json;
  const brief = result?.audit_brief ?? ([...(selected?.events ?? [])].reverse().find(event => event.event_type === "audit_brief")?.payload.brief as AuditBrief | undefined);
  return <Card title="多源协作规划" extra={<Button onClick={() => void load()}>刷新会话</Button>}>
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Typography.Text type="secondary">审计任务先由审计主脑查阅知识库、形成审计程序，再结合插件关系、能力目录和历史经验组网；评估发现依据不足时退回补查与修订。</Typography.Text>
      {error && <Alert type="error" showIcon message="协作规划请求未完成" description={error} />}
      <Typography.Text type="secondary">新会话使用桌面“AI 设置”中保存的 Debug 或外接 AI API 模式。</Typography.Text>
      <Alert type="info" showIcon message="安装版首次使用：配置模型后启用本机规划"
        description="请在 AI 设置保存外接 API，然后确认本工作区的模型使用权限。Debug 保留用于独立合成测试库，需当前 AI 会话逐次回填。" />
      <Button loading={busy} disabled={!tenantId} onClick={() => void enablePlanning()}>配置完成，启用本机规划</Button>
      {selected?.model_access && <Alert type="info" showIcon
        message={`本会话：${selected.model_access.mode === "debug" ? "Debug · 当前 AI 会话逐次回填" : selected.model_access.mode === "external" ? "外接 AI API" : "合成测试注入"} · ${selected.model_access.model}`}
        description={selected.model_access.mode === "debug" ? `本机请求与回填记录：${selected.model_access.exchange_dir}。等待当前会话回复，不会自动生成模拟答案。` : "本会话的接入配置已记录；切换全局模式后请新建会话。"} />}
      <Select aria-label="业务域" value={domain} onChange={value => { setDomain(value); setTargets([]); setBindings(""); }}
        options={[{ value: "audit", label: "审计" }, { value: "aiops", label: "模拟 AIOps" }, { value: "knowledge", label: "知识" }, { value: "quant", label: "模拟量化" }]} />
      <Input.TextArea aria-label="任务目标" placeholder="描述本次规划要完成的任务" value={goal} onChange={e => setGoal(e.target.value)} maxLength={2000} rows={2} />
      <Select aria-label="期望产物" mode="multiple" style={{ width: "100%" }} placeholder="选择可检查的期望产物" value={targets} onChange={setTargets} options={outputs.map(value => ({ value, label: value }))} />
      <Input.TextArea aria-label="输入工件绑定" value={bindings} onChange={e => setBindings(e.target.value)} rows={2}
        placeholder="输入工件绑定：每行填写 端口 ID,已登记工件 UUID。资料不足会明确挂起。" />
      <Button type="primary" disabled={!tenantId || goal.trim().length < 4 || targets.length === 0} loading={busy} onClick={() => void submit()}>提交规划会话</Button>
      <Table<Session> size="small" rowKey="session_id" dataSource={sessions} pagination={{ pageSize: 5 }}
        columns={[
          { title: "目标", render: (_, row) => row.request_json.goal },
          { title: "状态", render: (_, row) => <Tag>{statuses[row.status] ?? row.status}</Tag> },
          { title: "创建时间", dataIndex: "created_at", render: fmtTime },
          { title: "详情", render: (_, row) => <Button size="small" onClick={() => void inspect(row.session_id)}>查看依据与评估</Button> },
        ]} locale={{ emptyText: "尚无协作规划会话；未授权或未迁移时会显示明确错误。" }} />
      {selected && <Card size="small" title={statuses[selected.status] ?? selected.status}
        extra={<Button disabled={!["queued", "running"].includes(selected.status)} loading={busy} onClick={() => void process()}>立即运行规划</Button>}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text copyable>会话：{selected.session_id}</Typography.Text>
          <Typography.Text copyable>Trace：{selected.trace_id}</Typography.Text>
          {selected.status === "queued" && <Alert type="info" message="已持久保存。可立即运行规划，或由启用的专用后台工作者领取。" />}
          {result && <Alert type={selected.status === "accepted" ? "success" : "warning"} message={result.detail}
            description={`完成轮次 ${result.rounds ?? 0} · 来源查询 ${result.query_count ?? 0} · 模型调用 ${result.model_calls ?? 0}`} />}
          {selected.status === "accepted" && selected.request_json.domain === "audit" && !result?.audit_brief &&
            <Alert type="warning" message="此为补齐审计主脑前的历史方案。重新提交后，可按新的审计程序与验收标准复验。" />}
          {brief && <Card size="small" title="审计主脑 · 任务说明" style={{ width: "100%" }}>
            <Typography.Paragraph><strong>审计目标：</strong>{brief.objective}</Typography.Paragraph>
            <Typography.Paragraph><strong>范围：</strong>{brief.scope}</Typography.Paragraph>
            <Typography.Paragraph><strong>风险假设：</strong>{brief.risk_hypotheses.join("；")}</Typography.Paragraph>
            {brief.blockers.length > 0 && <Alert type="warning" message="审计依据待补齐" description={brief.blockers.join("；")} />}
            <Table<AuditProcedure> size="small" rowKey="procedure_id" dataSource={brief.procedures} pagination={false}
              columns={[
                { title: "审计程序", render: (_, procedure) => <><strong>{procedure.objective}</strong><div>{procedure.method}</div></> },
                { title: "所需证据", render: (_, procedure) => procedure.required_evidence.join("；") },
                { title: "验收标准", render: (_, procedure) => procedure.acceptance_criteria.join("；") },
                { title: "实现节点", render: (_, procedure) => result?.candidate?.procedure_nodes?.[procedure.procedure_id]?.join("、") || "待编排" },
                { title: "程序评审", render: (_, procedure) => {
                  const reviewed = result?.review?.procedures.find(item => item.procedure_id === procedure.procedure_id);
                  return reviewed ? <Tag title={reviewed.explanation}>{reviewed.met ? "通过" : "需修订"}</Tag> : "待评审";
                } },
              ]} />
            {brief.assumptions.length > 0 && <Typography.Paragraph type="secondary">适用前提：{brief.assumptions.join("；")}</Typography.Paragraph>}
          </Card>}
          {result?.candidate && <><Typography.Paragraph>{result.candidate.rationale}</Typography.Paragraph>
            <Table size="small" rowKey="node_id" dataSource={result.candidate.nodes} pagination={false}
              columns={[{ title: "节点", dataIndex: "node_id" }, { title: "插件", dataIndex: "plugin_id" }]} /></>}
          {result?.assessment && <Typography.Paragraph>
            {[...result.assessment.hard_issues, ...result.assessment.reviewer_issues].join("；") || "静态方案和独立评估通过；实际执行仍须另行审批与验收。"}
          </Typography.Paragraph>}
          <Collapse items={(selected.events ?? []).map(event => ({ key: event.id,
            label: `第 ${event.round_no} 轮 · ${event.event_type}`,
            children: <pre style={{ whiteSpace: "pre-wrap", maxHeight: 360, overflow: "auto" }}>{JSON.stringify(event.payload, null, 2)}</pre>,
          }))} />
        </Space>
      </Card>}
    </Space>
  </Card>;
}
