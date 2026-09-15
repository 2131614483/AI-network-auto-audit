import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Input, Space, Tag } from "antd";
import {
  CloseOutlined, LoadingOutlined, SendOutlined,
  BorderOutlined, FullscreenOutlined, MinusOutlined, RightOutlined,
} from "@ant-design/icons";
import type { CanvasChatResponse, ChatDraft, ChatMessage } from "../model/canvasChat";
import { seedInputsOf } from "../model/canvasChat";
import {
  CANVAS_STARTER_SOURCES,
  CANVAS_TASK_EXAMPLES,
  sourcePairsFor,
} from "../model/canvasStarter";

type Props = {
  tenantId?: string;
  runContext?: string | null;
  baseDraft?: ChatDraft | null;
  planningSourceKeys?: string[];
  onPlanningSourceKeysChange?: (keys: string[]) => void;
  onApplyDraft?: (draft: ChatDraft) => void;
  onNotice?: (message: string) => void;
};

type PanelMode = "docked" | "floating" | "maximized" | "minimized";

type StageEvent = {
  stage: "capability_recall" | "llm_draft" | "validate" | "compile";
  round?: number;
  max_rounds?: number;
  capabilities?: number;
  ok?: boolean;
  issues?: Array<{ code: string; message?: string }>;
};

type StageLine = {
  id: number;
  icon: "run" | "ok" | "fail" | "idle";
  text: string;
};

const STAGE_LABEL: Record<StageEvent["stage"], string> = {
  capability_recall: "召回能力目录",
  llm_draft: "模型生成草稿",
  validate: "校验能力白名单与数据边界",
  compile: "确定性编译",
};

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function messageKind(response: CanvasChatResponse): ChatMessage["kind"] {
  if (response.status === "draft_ready") return "draft";
  return "gap";
}

let stageSeq = 0;

export default function AIChatPanel({
  tenantId, runContext, baseDraft, planningSourceKeys = [], onPlanningSourceKeysChange, onApplyDraft, onNotice,
}: Props): React.JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sessionId, setSessionId] = useState<string>(`chat-${Math.random().toString(36).slice(2, 14)}`);
  const [mode, setMode] = useState<PanelMode>("docked");
  const [stages, setStages] = useState<StageLine[]>([]);
  const [floatingRect, setFloatingRect] = useState({ x: 520, y: 60, width: 400, height: 640 });
  const listRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<(() => void) | null>(null);

  const scrollBottom = () => {
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  };

  const pushMessage = (message: ChatMessage) => {
    setMessages((current) => [...current, message]);
    requestAnimationFrame(scrollBottom);
  };

  const appendStage = useCallback((event: StageEvent) => {
    setStages((current) => {
      const next = [...current];
      if (event.stage === "capability_recall") {
        next.push({ id: stageSeq++, icon: "ok", text: `${STAGE_LABEL[event.stage]}（命中 ${event.capabilities ?? 0} 项）` });
      } else if (event.stage === "llm_draft") {
        const round = event.round ?? 1;
        next.push({ id: stageSeq++, icon: "run", text: `${STAGE_LABEL[event.stage]}（第 ${round}/${event.max_rounds ?? round} 轮，模型思考中…）` });
      } else if (event.stage === "validate" || event.stage === "compile") {
        if (event.ok) {
          next.push({ id: stageSeq++, icon: "ok", text: `${STAGE_LABEL[event.stage]}（第 ${event.round ?? 1} 轮）通过` });
        } else {
          const codes = (event.issues ?? []).map((issue) => issue.code).join("、");
          next.push({ id: stageSeq++, icon: "fail", text: `${STAGE_LABEL[event.stage]}未通过${codes ? `：${codes}` : ""}` });
        }
      }
      return next.slice(-14);
    });
    requestAnimationFrame(scrollBottom);
  }, []);

  const finishStream = (response: CanvasChatResponse) => {
    setStages((current) => [...current, { id: stageSeq++, icon: "ok", text: `完成：${response.status === "draft_ready" ? "图谱流程已生成" : "缺口报告"}` }]);
    pushMessage({
      id: newId(),
      role: "assistant",
      kind: messageKind(response),
      content: response.reply_text,
      planKey: response.plan_key,
      revisions: response.revisions,
      draft: response.draft,
      issues: response.issues,
      intentId: response.intent_id,
      createdAt: new Date().toISOString(),
    });
    setSending(false);
  };

  const failStream = (detail: string, intentId?: string) => {
    setStages((current) => [...current, { id: stageSeq++, icon: "fail", text: "规划未完成" }]);
    pushMessage({
      id: newId(), role: "assistant", kind: "error",
      content: `规划未完成：${detail}${intentId && !detail.includes(intentId) ? `（失败记录：${intentId}）` : ""}`,
      createdAt: new Date().toISOString(),
    });
    setSending(false);
  };

  const send = (): void => {
    const text = input.trim();
    if (!text || sending) return;
    if (!tenantId) {
      pushMessage({
        id: newId(), role: "assistant", kind: "error", content: "当前没有可用租户，无法调用规划服务。",
        createdAt: new Date().toISOString(),
      });
      return;
    }
    pushMessage({ id: newId(), role: "user", kind: "text", content: text, createdAt: new Date().toISOString() });
    setInput("");
    const history = messages
      .slice(-8)
      .filter((message) => message.role === "user" || message.kind === "text" || message.kind === "gap")
      .map((message) => ({ role: message.role, content: message.content }));
    const seedInputs = baseDraft ? seedInputsOf(baseDraft) : sourcePairsFor(planningSourceKeys);
    if (!baseDraft && seedInputs.length === 0) {
      pushMessage({
        id: newId(), role: "assistant", kind: "error",
        content: "请先选择至少一个规划数据入口。AI 只能使用这里明确授权的输入端口组网。",
        createdAt: new Date().toISOString(),
      });
      return;
    }
    setSending(true);
    setStages([]);
    scrollBottom();
    const payload: Record<string, unknown> = {
      message: text,
      session_id: sessionId,
      history,
      idempotency_key: `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    };
    if (baseDraft) payload.base_draft = baseDraft;
    if (seedInputs.length) payload.data_sources = seedInputs;
    try {
      cancelRef.current = window.auditControl.requestStream({
        path: "/api/v1/topology/canvas/chat/stream",
        tenantId,
        body: payload,
        onEvent: ({ type, data }) => {
          if (type === "stage") appendStage(data as StageEvent);
          else if (type === "done") finishStream(data as CanvasChatResponse);
          else if (type === "error") {
            const error = data as { detail?: string; intent_id?: string };
            failStream(String(error?.detail ?? "规划失败"), error?.intent_id);
          }
        },
        onEnd: () => {
          setSending(false);
          cancelRef.current = null;
        },
        onError: (error) => {
          failStream(error.message);
          cancelRef.current = null;
        },
      });
    } catch {
      failStream("画布 AI 规划请求无法发起。");
    }
  };

  const restartSession = (): void => {
    cancelRef.current?.();
    setSessionId(`chat-${Math.random().toString(36).slice(2, 14)}`);
    setMessages([]);
    setStages([]);
  };

  useEffect(() => () => cancelRef.current?.(), []);

  // -- panel window modes -----------------------------------------------------
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number } | null>(null);
  const resizeRef = useRef<{ startX: number; startY: number; originW: number; originH: number } | null>(null);

  const startDrag = (event: React.PointerEvent) => {
    if (mode !== "floating") return;
    dragRef.current = { startX: event.clientX, startY: event.clientY, originX: floatingRect.x, originY: floatingRect.y };
    (event.target as Element).setPointerCapture?.(event.pointerId);
    setDragging(true);
  };
  const moveDrag = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    setFloatingRect((current) => ({
      ...current,
      x: Math.max(0, drag.originX + (event.clientX - drag.startX)),
      y: Math.max(0, drag.originY + (event.clientY - drag.startY)),
    }));
  };
  const endDrag = () => { dragRef.current = null; setDragging(false); };

  const startResize = (event: React.PointerEvent) => {
    if (mode !== "floating") return;
    resizeRef.current = { startX: event.clientX, startY: event.clientY, originW: floatingRect.width, originH: floatingRect.height };
    (event.target as Element).setPointerCapture?.(event.pointerId);
  };
  const moveResize = (event: React.PointerEvent) => {
    const resize = resizeRef.current;
    if (!resize) return;
    setFloatingRect((current) => ({
      ...current,
      width: Math.max(300, resize.originW + (event.clientX - resize.startX)),
      height: Math.max(380, resize.originH + (event.clientY - resize.startY)),
    }));
  };
  const endResize = () => { resizeRef.current = null; };

  const panelStyle = mode === "floating"
    ? { left: floatingRect.x, top: floatingRect.y, width: floatingRect.width, height: floatingRect.height }
    : undefined;

  if (mode === "minimized") {
    return (
      <button
        type="button"
        className="aichat-capsule"
        onClick={() => setMode("floating")}
        title="展开 AI 画布助手"
      >
        <RightOutlined /> AI 助手{sending ? <span className="aichat-capsule-dot" /> : null}
      </button>
    );
  }

  return (
    <div className={`aichat ${mode}${dragging ? " dragging" : ""}`} style={panelStyle}>
      <div
        className="aichat-head"
        ref={headerRef}
        onPointerDown={startDrag}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <span>AI 画布助手</span>
        <Tag className="aichat-tag">规划，不执行</Tag>
        <span className="aichat-head-actions">
          {mode === "maximized" ? (
            <button type="button" title="还原" onClick={() => setMode("floating")}><BorderOutlined /></button>
          ) : (
            <button type="button" title="最大化展开" onClick={() => setMode("maximized")}><FullscreenOutlined /></button>
          )}
          {mode !== "docked" ? (
            <button type="button" title="停靠右侧栏" onClick={() => setMode("docked")}><MinusOutlined /></button>
          ) : (
            <button type="button" title="浮动窗口" onClick={() => setMode("floating")}><BorderOutlined /></button>
          )}
          <button type="button" title="最小化" onClick={() => setMode("minimized")}><MinusOutlined /></button>
          {mode === "floating" ? (
            <button type="button" title="关闭（停靠）" onClick={() => setMode("docked")}><CloseOutlined /></button>
          ) : null}
        </span>
      </div>
      {runContext ? <div className="aichat-context">运行上下文：{runContext}</div> : null}
      {baseDraft ? <div className="aichat-context">基准草稿：{baseDraft.plan_key}（{baseDraft.nodes.length} 节点 · {baseDraft.edges.length} 边）</div> : null}
      {!baseDraft ? (
        <div className="aichat-source-picker">
          <div className="aichat-source-title">1. 选择规划数据入口</div>
          <div className="aichat-source-list">
            {CANVAS_STARTER_SOURCES.map((source) => (
              <label key={source.key} className="aichat-source-option" title={source.detail}>
                <Checkbox
                  checked={planningSourceKeys.includes(source.key)}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? [...new Set([...planningSourceKeys, source.key])]
                      : planningSourceKeys.filter((key) => key !== source.key);
                    onPlanningSourceKeysChange?.(next);
                  }}
                >
                  {source.label}
                </Checkbox>
              </label>
            ))}
          </div>
          <div className="aichat-source-note">仅授权规划端口；应用草稿后，运行前仍须绑定真实数据工件。</div>
          <div className="aichat-example-row">
            <span>2. 填入范例：</span>
            {CANVAS_TASK_EXAMPLES.map((example) => (
              <button
                type="button"
                key={example.key}
                disabled={example.key === "dual-ledger" && planningSourceKeys.length < 2}
                onClick={() => setInput(example.message)}
              >
                {example.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="aichat-list" ref={listRef}>
        {messages.length === 0 && stages.length === 0 ? (
          <div className="aichat-empty">
            {baseDraft ? "描述对当前草稿的调整意见。" : "选择数据入口后，描述要完成的任务。"}<br />
            例如：「校验日记账质量，并对异常候选生成调查计划」<br />
            AI 会生成可编译的图谱流程（节点 + 数据边），可应用到画布继续调整。
          </div>
        ) : null}
        {stages.length ? (
          <div className="aichat-stage">
            <div className="aichat-stage-title">实时执行</div>
            {stages.map((line) => (
              <div key={line.id} className={`aichat-stage-line ${line.icon}`}>
                {line.icon === "run" ? <LoadingOutlined /> : null}
                {line.icon === "ok" ? <span className="aichat-stage-ok">✓</span> : null}
                {line.icon === "fail" ? <span className="aichat-stage-fail">✗</span> : null}
                <span>{line.text}</span>
              </div>
            ))}
          </div>
        ) : null}
        {messages.map((message) => (
          <div key={message.id} className={`aichat-message aichat-${message.role}`}>
            <div className="aichat-bubble">
              <div className="aichat-text">{message.content}</div>
              {message.kind === "draft" && message.draft ? (
                <div className="aichat-draft">
                  <Space size={4} wrap>
                    <Tag className="ready-tag">plan_only</Tag>
                    <Tag>{message.draft.nodes.length} 节点</Tag>
                    <Tag>{message.draft.edges.length} 边</Tag>
                    <Tag>修订 {message.revisions ?? 0} 轮</Tag>
                  </Space>
                  <div className="aichat-draft-key">plan {message.planKey}</div>
                  {onApplyDraft ? (
                    <Button
                      type="primary"
                      size="small"
                      onClick={() => { onApplyDraft(message.draft!); onNotice?.(`已将图谱流程 ${message.planKey} 应用到画布（仅规划，未执行）。`); }}
                    >
                      应用到画布
                    </Button>
                  ) : null}
                </div>
              ) : null}
              {message.kind === "gap" && message.issues?.length ? (
                <div className="aichat-gap">
                  {message.issues.slice(0, 5).map((issue, index) => (
                    <div key={`${issue.code}-${index}`} className="aichat-gap-item">
                      <Tag className="pending-tag">{issue.code}</Tag>
                      <span>{issue.message ?? ""}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        ))}
        {sending ? (
          <div className="aichat-message aichat-assistant">
            <div className="aichat-bubble aichat-thinking"><LoadingOutlined /> 正在规划…（可随时取消）</div>
          </div>
        ) : null}
      </div>
      <div className="aichat-input">
        <Input.TextArea
          aria-label="画布 AI 任务描述"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onPressEnter={(event) => {
            if (!event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
          placeholder="描述任务或调整意见（Shift+Enter 换行）"
          autoSize={{ minRows: 2, maxRows: 5 }}
          maxLength={2000}
        />
        <div className="aichat-actions">
          <span className="aichat-hint">输出经能力白名单与确定性编译器校验；运行仍需审批/策略门。</span>
          {sending ? (
            <Button
              danger
              icon={<CloseOutlined />}
              onClick={() => {
                cancelRef.current?.();
                setSending(false);
                setStages((current) => [...current, { id: stageSeq++, icon: "fail", text: "已取消" }]);
              }}
            >
              取消
            </Button>
          ) : null}
          <Button type="primary" icon={<SendOutlined />} loading={sending} onClick={send}>
            发送
          </Button>
        </div>
      </div>
      {mode === "floating" ? (
        <div className="aichat-resize" onPointerDown={startResize} onPointerMove={moveResize} onPointerUp={endResize} onPointerCancel={endResize} />
      ) : null}
      {!tenantId ? <Alert className="aichat-warn" type="warning" showIcon message="未连接租户，聊天规划不可用。" /> : null}
    </div>
  );
}
