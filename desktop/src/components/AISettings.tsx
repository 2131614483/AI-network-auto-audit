import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Descriptions, Input, Select, Space, Spin, Tag, Typography } from "antd";
import {
  buildUpdateBody,
  formFromPayload,
  isLegacySource,
  EMPTY_AI_SETTINGS_FORM,
  probeSummary,
  providerLabel,
  sourceLabel,
  type AIProbeResult,
  type AISettingsForm,
  type AISettingsPayload,
} from "../model/aiSettings";

type Props = {
  tenantId?: string;
  onNotice?: (message: string) => void;
};

const SETTINGS_PATH = "/api/v1/ai/settings";
const TEST_PATH = "/api/v1/ai/test";

// One row per editable field: label, the form key, and an optional hint.  The
// table keeps the JSX free of repetition and makes it obvious that every field
// the API accepts has somewhere to be typed.
type FieldSpec = {
  label: string;
  key: keyof AISettingsForm;
  hint?: string;
  /** Which resolved value the "来自" tag should describe. */
  sourceKey?: string;
  placeholder?: string;
};

const CHAT_FIELDS: FieldSpec[] = [
  { label: "接口地址 Base URL", key: "baseUrl", hint: "到 /v1 为止，客户端自行追加 /chat/completions", sourceKey: "base_url" },
  { label: "模型 Model", key: "model", sourceKey: "model" },
  { label: "超时（秒）", key: "timeout", hint: "云端长文本生成较慢，可放宽到 600", sourceKey: "timeout" },
  { label: "最大输出 Tokens", key: "maxTokens", hint: "留空使用服务端默认", sourceKey: "max_tokens" },
  { label: "推理强度 reasoning_effort", key: "reasoningEffort", hint: "如 low / medium / high，留空不发送", sourceKey: "reasoning_effort" },
  { label: "代理 Proxy", key: "proxy", hint: "如 http://127.0.0.1:7890，留空走系统代理", sourceKey: "proxy" },
  { label: "本地上下文窗口 num_ctx", key: "numCtx", hint: "仅本地 Ollama 生效", sourceKey: "num_ctx" },
];

const EMBEDDING_FIELDS: FieldSpec[] = [
  { label: "接口地址 Base URL", key: "embedBaseUrl", sourceKey: "embedding.base_url" },
  { label: "模型 Model", key: "embedModel", sourceKey: "embedding.model" },
  { label: "超时（秒）", key: "embedTimeout", sourceKey: "embedding.timeout" },
  { label: "向量维度", key: "embedDimensions", hint: "必须与数据库向量列一致，默认 1024", sourceKey: "embedding.dimensions" },
];

export default function AISettings({ tenantId, onNotice }: Props) {
  const [payload, setPayload] = useState<AISettingsPayload | null>(null);
  const [form, setForm] = useState<AISettingsForm>({ ...EMPTY_AI_SETTINGS_FORM });
  const [probe, setProbe] = useState<AIProbeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    if (!tenantId) return;
    setLoading(true);
    setError(null);
    try {
      const data = (await window.auditControl.request({ path: SETTINGS_PATH, tenantId })) as AISettingsPayload;
      setPayload(data);
      setForm(formFromPayload(data));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "读取 AI 配置失败。");
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (clearApiKey = false): Promise<void> => {
      if (!tenantId) return;
      setSaving(true);
      setError(null);
      try {
        const data = (await window.auditControl.request({
          path: SETTINGS_PATH,
          method: "PUT",
          tenantId,
          body: buildUpdateBody(form, { clearApiKey }),
        })) as AISettingsPayload;
        setPayload(data);
        setForm(formFromPayload(data));
        onNotice?.(`AI 配置已保存并立即生效（写入 ${data.env_file}）。`);
      } catch (failure) {
        const message = failure instanceof Error ? failure.message : "保存 AI 配置失败。";
        setError(message);
        onNotice?.(`保存 AI 配置失败：${message}`);
      } finally {
        setSaving(false);
      }
    },
    [form, onNotice, tenantId],
  );

  const runProbe = useCallback(async (): Promise<void> => {
    if (!tenantId) return;
    setTesting(true);
    setError(null);
    try {
      const result = (await window.auditControl.request({
        path: TEST_PATH,
        method: "POST",
        tenantId,
        body: { include_embedding: true },
      })) as AIProbeResult;
      setProbe(result);
      onNotice?.(probeSummary(result));
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : "连接测试失败。";
      setError(message);
    } finally {
      setTesting(false);
    }
  }, [onNotice, tenantId]);

  const update = (key: keyof AISettingsForm, value: string): void => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const sourceTag = (spec: FieldSpec) => {
    if (!payload || !spec.sourceKey) return null;
    const source = payload.sources[spec.sourceKey];
    if (!source) return null;
    const legacy = isLegacySource(source);
    return (
      <Tag className={legacy ? "planned-tag" : "gateway-tag"} title={`当前值来自 ${source}`}>
        {legacy ? `${sourceLabel(source)}（旧变量）` : sourceLabel(source)}
      </Tag>
    );
  };

  const rows = (specs: FieldSpec[]) =>
    specs.map((spec) => (
      <div className="ai-field" key={spec.key}>
        <div className="ai-field-label">
          <span>{spec.label}</span>
          {sourceTag(spec)}
        </div>
        <Input
          value={form[spec.key]}
          placeholder={spec.placeholder}
          onChange={(event) => update(spec.key, event.target.value)}
          allowClear
        />
        {spec.hint ? <small className="ai-field-hint">{spec.hint}</small> : null}
      </div>
    ));

  if (!tenantId) {
    return (
      <Card className="chart-card" title="AI 设置">
        <Alert type="warning" showIcon message="缺少租户上下文" description="控制平面尚未返回租户信息，无法读取 AI 配置。" />
      </Card>
    );
  }

  if (loading && !payload) {
    return (
      <Card className="chart-card" title="AI 设置">
        <Spin /> <Typography.Text type="secondary">正在读取 AI 配置…</Typography.Text>
      </Card>
    );
  }

  return (
    <>
      <section className="detail-head">
        <div>
          <Typography.Title level={4}>AI 设置</Typography.Title>
          <Typography.Text type="secondary">
            全项目唯一的模型接入入口：画布 AI 助手、AI 组网规划、知识库向量化共用这一份配置。保存后立即生效，无需重启。
          </Typography.Text>
        </div>
        <Space>
          <Button onClick={() => void load()} disabled={loading || saving}>重新读取</Button>
          <Button onClick={() => void runProbe()} loading={testing}>测试连接</Button>
          <Button type="primary" onClick={() => void save()} loading={saving}>保存并生效</Button>
        </Space>
      </section>

      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}

      {probe ? (
        <Alert
          type={probe.ok ? (probe.embedding_ok === false ? "warning" : "success") : "error"}
          showIcon
          style={{ marginBottom: 12 }}
          message={probeSummary(probe)}
          description={`${probe.base_url} · ${providerLabel(probe.provider)} · ${probe.model}`}
        />
      ) : null}

      <Card className="chart-card" title="当前生效配置" style={{ marginBottom: 12 }}>
        {payload ? (
          <Descriptions className="file-summary" size="small" column={2} bordered items={[
            { key: "provider", label: "通道", children: providerLabel(payload.chat.provider) },
            { key: "model", label: "对话模型", children: payload.chat.model },
            { key: "base", label: "地址", children: payload.chat.base_url, span: 2 },
            {
              key: "key",
              label: "API Key",
              children: payload.chat.api_key_set
                ? <Tag className="ready-tag">{payload.chat.api_key_masked}</Tag>
                : <Tag className="pending-tag">未设置</Tag>,
            },
            { key: "timeout", label: "超时", children: `${payload.chat.timeout} 秒` },
            {
              key: "embed",
              label: "向量模型",
              children: `${payload.embedding.model}（${payload.embedding.dimensions} 维 · ${providerLabel(payload.embedding.provider)}）`,
              span: 2,
            },
            {
              key: "envfile",
              label: "配置文件",
              children: payload.env_file_exists
                ? payload.env_file
                : `${payload.env_file}（尚未创建，保存后生成）`,
              span: 2,
            },
          ]} />
        ) : null}
      </Card>

      <Card className="chart-card" title="对话 / 规划模型" style={{ marginBottom: 12 }}>
        <div className="ai-settings-grid">
          <div className="ai-field">
            <div className="ai-field-label">
              <span>通道类型</span>
              {payload ? sourceTag({ label: "通道类型", key: "provider", sourceKey: "provider" }) : null}
            </div>
            <Select
              value={form.provider}
              onChange={(value) => update("provider", value)}
              options={(payload?.supported_chat_providers ?? ["openai_compat", "ollama"]).map((item) => ({
                value: item,
                label: providerLabel(item),
              }))}
            />
            <small className="ai-field-hint">切换到本地 Ollama 时不校验 API Key</small>
          </div>
          {rows(CHAT_FIELDS)}
          <div className="ai-field">
            <div className="ai-field-label">
              <span>API Key</span>
              {payload ? sourceTag({ label: "API Key", key: "apiKey", sourceKey: "api_key" }) : null}
            </div>
            <Input.Password
              value={form.apiKey}
              onChange={(event) => update("apiKey", event.target.value)}
              placeholder={payload?.chat.api_key_set ? `已保存 ${payload.chat.api_key_masked}（留空表示不修改）` : "未设置"}
              autoComplete="off"
            />
            <small className="ai-field-hint">
              密钥只写入本机 .env 并向前端回传掩码；留空保存不会覆盖已存密钥。{" "}
              {payload?.chat.api_key_set ? (
                <Button size="small" type="link" onClick={() => void save(true)} loading={saving}>
                  清除已保存的密钥
                </Button>
              ) : null}
            </small>
          </div>
        </div>
      </Card>

      <Card className="chart-card" title="向量 / Embedding 模型">
        <div className="ai-settings-grid">
          <div className="ai-field">
            <div className="ai-field-label">
              <span>通道类型</span>
              {payload ? sourceTag({ label: "通道类型", key: "embedProvider", sourceKey: "embedding.provider" }) : null}
            </div>
            <Select
              value={form.embedProvider}
              onChange={(value) => update("embedProvider", value)}
              options={(payload?.supported_embedding_providers ?? ["ollama", "openai_compat"]).map((item) => ({
                value: item,
                label: providerLabel(item),
              }))}
            />
            <small className="ai-field-hint">知识库检索使用；维度须与数据库向量列一致</small>
          </div>
          {rows(EMBEDDING_FIELDS)}
        </div>
      </Card>
    </>
  );
}
